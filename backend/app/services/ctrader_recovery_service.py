"""Explicit, read-only broker recovery. Call from a worker after authentication."""

import logging
from datetime import datetime, timedelta, timezone

from twisted.python.threadable import isInIOThread
from ctrader_open_api.messages import OpenApiMessages_pb2 as messages

from app.services.broker_execution_persistence_service import (
    BrokerExecutionPersistenceService,
)
from app.services.ctrader_execution_event_service import (
    CTraderExecutionEventService,
    optional,
)

logger = logging.getLogger(__name__)


class CTraderRecoveryService:
    def __init__(self, provider, persistence=None):
        self.runtime = getattr(provider, "runtime", None)
        self.client = provider.client  # Reuse the authenticated persistent connection.
        self.persistence = persistence or BrokerExecutionPersistenceService()
        self.events = CTraderExecutionEventService(
            self.client.account_id, self.persistence
        )
        from app.services.ctrader_position_management_service import (
            CTraderPositionManagementService,
        )

        self.events.protection_verifier = CTraderPositionManagementService(
            provider, self.persistence
        ).verify_execution

    def _send(self, request, response_type):
        if self.runtime is not None and self.runtime.stopping.is_set():
            raise RuntimeError("Recovery interrupted by runtime shutdown")
        response = self.client.send(request)
        if not isinstance(response, response_type):
            raise RuntimeError(
                f"Broker recovery response: {type(response).__name__} "
                f"{optional(response, 'errorCode')}"
            )
        if optional(response, "ctidTraderAccountId") != self.client.account_id:
            raise ValueError("Recovery response account mismatch")
        return response

    def recover(self):
        """Return remaining unresolved records; never submit, modify, or close orders."""
        if isInIOThread():
            raise RuntimeError("Recovery must run outside the Twisted reactor thread")
        if self.client.environment != "demo":
            raise ValueError("Recovery is restricted to the demo connection")
        if not self.client.authenticated:
            raise RuntimeError("Recovery requires successful account authentication")
        records = [r for r in self.persistence.get_unresolved() if r.symbol == "XAUUSD"]
        if not records:
            return []
        try:
            current = self._send(
                messages.ProtoOAReconcileReq(
                    ctidTraderAccountId=self.client.account_id
                ),
                messages.ProtoOAReconcileRes,
            )
        except Exception:
            logger.exception("Recovery snapshot unavailable; executions remain blocked")
            return records
        for record in records:
            try:
                order_id = record.broker_order_id
                if not order_id:
                    candidates = [
                        o
                        for o in current.order
                        if optional(o, "clientOrderId") == record.request_id
                    ]
                    # Search history even when a pending order matches: detect ambiguity.
                    candidates += self._history(record)
                    ids = {optional(o, "orderId") for o in candidates}
                    ids.discard(None)
                    if len(ids) != 1:
                        logger.warning(
                            "Recovery unmatched/ambiguous request_id=%s order_ids=%s",
                            record.request_id,
                            ids,
                        )
                        continue
                    order_id = str(ids.pop())
                details = self._send(
                    messages.ProtoOAOrderDetailsReq(
                        ctidTraderAccountId=self.client.account_id,
                        orderId=int(order_id),
                    ),
                    messages.ProtoOAOrderDetailsRes,
                )
                order = details.order
                if optional(order, "orderId") != int(order_id):
                    raise ValueError("Order details ID mismatch")
                client_id = optional(order, "clientOrderId")
                if client_id and client_id != record.request_id:
                    raise ValueError("Order details clientOrderId mismatch")
                # The explicit details request establishes correlation when the server
                # omits clientOrderId; do not infer it from symbol, time, or position.
                self.events.process_order_snapshot(
                    record.request_id, order, details.deal
                )
            except Exception:
                logger.exception(
                    "Recovery incomplete request_id=%s order_id=%s; remains blocked",
                    record.request_id,
                    record.broker_order_id,
                )
        return self.persistence.get_unresolved()

    def _history(self, record):
        start = (
            record.created_at.replace(tzinfo=timezone.utc)
            if record.created_at.tzinfo is None
            else record.created_at
        )
        # Small overlapping boundary accommodates timestamp precision, not an expiry.
        start -= timedelta(days=1)
        end = datetime.now(timezone.utc)
        matches = []
        while start < end:
            stop = min(start + timedelta(days=7), end)
            response = self._send(
                messages.ProtoOAOrderListReq(
                    ctidTraderAccountId=self.client.account_id,
                    fromTimestamp=int(start.timestamp() * 1000),
                    toTimestamp=int(stop.timestamp() * 1000),
                ),
                messages.ProtoOAOrderListRes,
            )
            if optional(response, "hasMore") is not False:
                raise ValueError(
                    "Incomplete order history; identifier cannot be verified"
                )
            matches.extend(
                o
                for o in response.order
                if optional(o, "clientOrderId") == record.request_id
            )
            start = stop
        return matches
