"""Broker-side protection verification and durable, explicit full-position closes."""

import logging
import math
from decimal import Decimal, localcontext
from datetime import datetime, timezone

from twisted.python.threadable import isInIOThread
from ctrader_open_api.messages import OpenApiMessages_pb2 as M
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOAExecutionType as E

from app.models.broker_models import (
    BrokerPosition,
    BrokerCloseResult,
    BrokerPositionManagementResult,
)
from app.models.execution_models import BrokerExecutionRequest
from app.services.broker_execution_persistence_service import (
    BrokerExecutionPersistenceService,
)
from app.services.ctrader_execution_event_service import optional

logger = logging.getLogger(__name__)


class CTraderPositionManagementService:
    def __init__(self, provider, persistence=None):
        self.safety_callback = None
        self.provider = provider
        self.client = provider.client
        self.persistence = persistence or BrokerExecutionPersistenceService()

    def _guard(self):
        if isInIOThread():
            raise RuntimeError("Position management must run outside the reactor")
        if self.client.environment != "demo" or not self.client.authenticated:
            raise RuntimeError(
                "Position management requires authenticated DEMO connection"
            )
        response = self.client.send(
            M.ProtoOAGetAccountListByAccessTokenReq(
                accessToken=self.client.access_token
            )
        )
        if not isinstance(response, M.ProtoOAGetAccountListByAccessTokenRes):
            raise RuntimeError("Cannot verify DEMO account")
        accounts = [
            a
            for a in response.ctidTraderAccount
            if a.ctidTraderAccountId == self.client.account_id
        ]
        if len(accounts) != 1 or optional(accounts[0], "isLive") is not False:
            raise RuntimeError("Account is live or DEMO status cannot be confirmed")

    @staticmethod
    def _result(row):
        return BrokerCloseResult(
            broker_position_id=row.broker_position_id,
            status=row.status,
            broker_order_id=row.broker_order_id,
            message=row.message,
        )

    def _source(self, position):
        row = self.persistence.find_position_execution(position.broker_position_id)
        if row is None or row.status not in ("filled", "pending"):
            raise ValueError("No confirmed execution metadata for position")
        request = BrokerExecutionRequest.model_validate(row.request_data)
        if (
            request.request_id != row.request_id
            or row.symbol != request.symbol
            or request.symbol != "XAUUSD"
            or position.symbol != request.symbol
            or position.direction != request.direction
            or not math.isfinite(position.volume_lots)
            or position.volume_lots <= 0
            or row.filled_volume_lots is None
            or position.volume_lots > row.filled_volume_lots + 1e-10
            or position.volume_lots > request.volume_lots + 1e-10
        ):
            raise ValueError("Position does not match persisted execution")
        if request.max_holding_minutes <= 0:
            raise ValueError("Invalid holding-time policy")
        return request

    def _protection(self, position, request):
        metadata = self.provider.get_symbol_metadata("XAUUSD")
        tick = self.provider.price_tick(metadata)
        sl_units, tp_units = self.provider.relative_protection_units(request, metadata)
        values = (position.entry_price, position.stop_loss, position.take_profit)
        if any(
            value is None or not math.isfinite(value) or value <= 0 for value in values
        ):
            raise ValueError("Missing or invalid broker SL/TP protection")
        with localcontext() as context:
            context.prec = 40
            entry, stop, target = (Decimal(str(value)) for value in values)
            side = 1 if position.direction == "long" else -1
            actual_sl = (entry - stop) * side
            actual_tp = (target - entry) * side
            planned_sl = (
                Decimal(str(request.reference_price)) - Decimal(str(request.stop_loss))
            ) * side
            noise = tick * Decimal("0.000001")
            if actual_sl <= 0 or actual_tp <= 0 or actual_sl > planned_sl + noise:
                raise ValueError(
                    "Broker protection is on the wrong side or exceeds planned SL risk"
                )
            # A fractional VWAP entry can put the expected absolute price between
            # ticks. Permit at most half a price tick, plus float serialization noise.
            tolerance = tick / 2 + noise
            if (
                abs(actual_sl - Decimal(sl_units) / 100000) > tolerance
                or abs(actual_tp - Decimal(tp_units) / 100000) > tolerance
            ):
                raise ValueError(
                    "Broker SL/TP differs from rounded distances at actual entry price"
                )

    def verify_fill(self, request, position_id):
        try:
            self._guard()
            positions = self.provider.get_open_positions()
            matches = [p for p in positions if p.broker_position_id == str(position_id)]
            if len(matches) != 1 or len(positions) != 1:
                raise ValueError("Filled position protection cannot be confirmed")
            position = matches[0]
            if (
                position.symbol != request.symbol
                or position.direction != request.direction
            ):
                raise ValueError("Filled position identity mismatch")
            self._protection(position, request)
            return "verified", None
        except Exception as exc:
            logger.critical(
                "Unsafe/unconfirmed protection position_id=%s: %s", position_id, exc
            )
            if self.safety_callback is not None:
                self.safety_callback(
                    f"Unsafe protection position_id={position_id}: {exc}"
                )
            return "unsafe", str(exc)

    def verify_execution(self, request_id):
        row = self.persistence.find_execution(request_id=request_id)
        if row is None or not row.broker_position_id:
            logger.critical(
                "Cannot confirm protection request_id=%s: missing position ID",
                request_id,
            )
            return
        self.verify_fill(
            BrokerExecutionRequest.model_validate(row.request_data),
            row.broker_position_id,
        )

    def _reconcile_closes(self, positions):
        ids = {p.broker_position_id for p in positions}
        results = []
        for row in self.persistence.get_unresolved_closes(self.client.account_id):
            if row.broker_position_id not in ids:
                result = BrokerCloseResult(
                    broker_position_id=row.broker_position_id, status="closed"
                )
                self.persistence.update_close(self.client.account_id, result)
                results.append(result)
        return results

    def manage(self, allow_close=True):
        try:
            self._guard()
            positions = self.provider.get_open_positions()
            completed = self._reconcile_closes(positions)
        except Exception as exc:
            logger.critical("Cannot confirm broker protection/state: %s", exc)
            return [
                BrokerPositionManagementResult(
                    protection="unconfirmed",
                    holding_policy="unresolved",
                    message=str(exc),
                )
            ]
        reports = [
            BrokerPositionManagementResult(
                broker_position_id=r.broker_position_id,
                protection="not_applicable",
                holding_policy="not_applicable",
                close_result=r,
            )
            for r in completed
        ]
        if len(positions) > 1 or any(p.symbol != "XAUUSD" for p in positions):
            logger.critical(
                "Unsupported account exposure: expected at most one XAUUSD position"
            )
            return reports + [
                BrokerPositionManagementResult(
                    protection="unconfirmed",
                    holding_policy="unresolved",
                    message="Unsupported account exposure",
                )
            ]
        for position in positions:
            report = BrokerPositionManagementResult(
                broker_position_id=position.broker_position_id,
                protection="unconfirmed",
                holding_policy="unresolved",
            )
            try:
                request = self._source(position)
                try:
                    self._protection(position, request)
                    report.protection = "verified"
                except Exception:
                    report.protection = "unsafe"
                    raise
                if position.opened_at.tzinfo is None:
                    raise ValueError("Broker opening timestamp lacks timezone")
                elapsed = (
                    datetime.now(timezone.utc) - position.opened_at
                ).total_seconds()
                if elapsed < 0:
                    raise ValueError("Broker opening timestamp is in the future")
                report.holding_policy = (
                    "expired"
                    if elapsed >= request.max_holding_minutes * 60
                    else "not_due"
                )
                existing = self.persistence.get_close(
                    self.client.account_id, position.broker_position_id
                )
                if existing:
                    report.close_result = self._result(existing)
                    if existing.status == "closed":
                        raise ValueError(
                            "Broker snapshot contradicts confirmed closed state"
                        )
                elif report.holding_policy == "expired" and allow_close:
                    report.close_result = self.close(position)
            except Exception as exc:
                logger.critical(
                    "Unresolved/unsafe position_id=%s: %s",
                    position.broker_position_id,
                    exc,
                )
                report.message = str(exc)
            reports.append(report)
        return reports

    def close(self, position):
        if not isinstance(position, BrokerPosition) or position.symbol != "XAUUSD":
            raise ValueError("Only an identified XAUUSD broker position can be closed")
        if (
            not position.broker_position_id.isdecimal()
            or int(position.broker_position_id) <= 0
        ):
            raise ValueError("Invalid broker position ID")
        self._guard()
        positions = self.provider.get_open_positions()
        self._reconcile_closes(positions)
        existing = self.persistence.get_close(
            self.client.account_id, position.broker_position_id
        )
        if existing:
            return self._result(
                existing
            )  # Includes rejection: no automatic retry policy.
        if len(positions) > 1 or any(p.symbol != "XAUUSD" for p in positions):
            raise ValueError("Unsupported account exposure")
        matches = [
            p for p in positions if p.broker_position_id == position.broker_position_id
        ]
        if not matches:
            self._source(
                position
            )  # Absence alone cannot establish ownership of an arbitrary ID.
            return BrokerCloseResult(
                broker_position_id=position.broker_position_id,
                status="closed",
                message="Position is already absent from broker open positions",
            )
        current = matches[0]
        if position.direction != current.direction or position.status != "open":
            raise ValueError("Close position identity mismatch")
        self._source(current)
        metadata = self.provider.get_symbol_metadata("XAUUSD")
        volume = position.volume_lots * metadata.lotSize
        current_volume = current.volume_lots * metadata.lotSize
        if (
            not math.isfinite(volume)
            or volume <= 0
            or volume > 2**63 - 1
            or not math.isclose(volume, round(volume), rel_tol=0, abs_tol=1e-7)
            or not math.isclose(volume, current_volume, rel_tol=0, abs_tol=1e-7)
        ):
            raise ValueError(
                "Close volume must equal verified current full position volume"
            )
        volume = round(volume)
        if not self.persistence.claim_close(
            self.client.account_id, position.broker_position_id, volume
        ):
            return self._result(
                self.persistence.get_close(
                    self.client.account_id, position.broker_position_id
                )
            )
        result = BrokerCloseResult(
            broker_position_id=position.broker_position_id, status="unknown"
        )
        try:
            response = self.client.send(
                M.ProtoOAClosePositionReq(
                    ctidTraderAccountId=self.client.account_id,
                    positionId=int(position.broker_position_id),
                    volume=volume,
                )
            )
            if optional(response, "ctidTraderAccountId") != self.client.account_id:
                raise ValueError("Close response account mismatch")
            if isinstance(response, M.ProtoOAExecutionEvent):
                order = optional(response, "order")
                ids = {
                    str(v)
                    for v in (
                        optional(order, "positionId"),
                        optional(optional(response, "position"), "positionId"),
                    )
                    if v
                }
                if (
                    ids != {position.broker_position_id}
                    or optional(order, "closingOrder") is not True
                ):
                    raise ValueError("Close response identity cannot be verified")
                if optional(order, "orderId"):
                    result.broker_order_id = str(order.orderId)
                if response.executionType == E.ORDER_REJECTED:
                    result.status = "rejected"
                elif response.executionType in (
                    E.ORDER_ACCEPTED,
                    E.ORDER_FILLED,
                    E.ORDER_PARTIAL_FILL,
                ):
                    result.status = (
                        "submitted"  # A fill alone may close only part of the position.
                    )
            elif isinstance(response, M.ProtoOAOrderErrorEvent):
                # Generic errors include transport/session failures; only an
                # explicit ORDER_REJECTED event proves broker rejection.
                result.message = optional(response, "errorCode")
            else:
                result.message = (
                    optional(response, "errorCode") or "Unrecognized close response"
                )
        except Exception as exc:
            result.message = str(exc)
        self.persistence.update_close(self.client.account_id, result)
        try:
            self._reconcile_closes(self.provider.get_open_positions())
        except Exception as exc:
            logger.warning(
                "Close reconciliation unresolved position_id=%s: %s",
                position.broker_position_id,
                exc,
            )
        return self._result(
            self.persistence.get_close(
                self.client.account_id, position.broker_position_id
            )
        )
