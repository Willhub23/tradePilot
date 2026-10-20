import logging
from datetime import datetime, timezone
from time import perf_counter_ns
from app.services.execution_timing import PROCESS_ID

from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAExecutionEvent,
    ProtoOAOrderErrorEvent,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOAExecutionType

from app.models.execution_models import BrokerExecutionResult
from app.services.broker_execution_persistence_service import (
    BrokerExecutionPersistenceService,
)

logger = logging.getLogger(__name__)


def optional(message, field):
    """Protobuf scalar defaults are not evidence that a field was sent."""
    if message is None:
        return None
    try:
        return getattr(message, field) if message.HasField(field) else None
    except (ValueError, AttributeError):
        return None


class CTraderExecutionEventService:
    def __init__(self, account_id: int, persistence=None):
        self.protection_verifier = None
        self.error_callback = None
        self.account_id = account_id
        self.persistence = persistence or BrokerExecutionPersistenceService()

    def process(self, event, *, received_at=None, received_monotonic_ns=None) -> None:
        received_at = received_at or datetime.now(timezone.utc)
        received_monotonic_ns = (
            received_monotonic_ns
            if received_monotonic_ns is not None
            else perf_counter_ns()
        )
        try:
            self._process(
                event,
                received_at=received_at,
                received_monotonic_ns=received_monotonic_ns,
            )
        except Exception:
            # Never acknowledge a failed write by inventing a new local status.
            logger.exception(
                "cTrader execution event persistence failed; reconciliation required"
            )
            if self.error_callback is not None:
                self.error_callback(
                    "Execution-event persistence failed; reconciliation required"
                )

    def _process(self, event, *, received_at=None, received_monotonic_ns=None) -> None:
        if optional(event, "ctidTraderAccountId") != self.account_id:
            logger.warning("Unmatched cTrader event account; reconciliation required")
            return
        if self._process_close_event(event):
            return
        order = optional(event, "order")
        deal = optional(event, "deal")
        position = optional(event, "position")
        request_id = optional(order, "clientOrderId") or None
        order_ids = {
            str(value)
            for value in (
                optional(order, "orderId"),
                optional(deal, "orderId"),
                optional(event, "orderId"),
            )
            if value
        }
        if len(order_ids) > 1:
            logger.warning("Conflicting cTrader order IDs: %s", order_ids)
            return
        order_id = next(iter(order_ids), None)
        if not request_id and not order_id:
            logger.warning("Unmatched cTrader event without order identifiers")
            return
        # Never fall back from an explicitly unknown clientOrderId to another order.
        record = (
            self.persistence.find_execution(request_id=request_id)
            if request_id
            else (self.persistence.find_execution(broker_order_id=order_id))
        )
        if record is None or record.symbol != "XAUUSD":
            logger.warning(
                "Unmatched cTrader event request_id=%s order_id=%s",
                request_id,
                order_id,
            )
            return
        if order_id:
            by_order = self.persistence.find_execution(broker_order_id=order_id)
            if by_order is not None and by_order.request_id != record.request_id:
                logger.warning(
                    "Conflicting cTrader correlation request_id=%s order_id=%s",
                    request_id,
                    order_id,
                )
                return

        if isinstance(event, ProtoOAOrderErrorEvent):
            # An error is not necessarily an order rejection (e.g. failed cancel).
            # Keep it unresolved unless the protocol explicitly confirms rejection.
            status = "unknown"
        elif isinstance(event, ProtoOAExecutionEvent):
            status = {
                ProtoOAExecutionType.ORDER_ACCEPTED: "submitted",
                ProtoOAExecutionType.ORDER_FILLED: "filled",
                ProtoOAExecutionType.ORDER_PARTIAL_FILL: "pending",
                ProtoOAExecutionType.ORDER_REJECTED: "rejected",
                ProtoOAExecutionType.ORDER_CANCELLED: "unknown",
                ProtoOAExecutionType.ORDER_EXPIRED: "unknown",
                ProtoOAExecutionType.ORDER_CANCEL_REJECTED: "unknown",
            }.get(optional(event, "executionType"))
            if status is None:
                logger.info("Ignoring unsupported cTrader execution type")
                return
        else:
            return

        position_ids = {
            str(value)
            for value in (
                optional(position, "positionId"),
                optional(order, "positionId"),
                optional(deal, "positionId"),
                optional(event, "positionId"),
            )
            if value
        }
        if len(position_ids) > 1:
            logger.warning("Conflicting cTrader position IDs: %s", position_ids)
            return

        volume_lots = None
        price = None
        if status in {"pending", "filled"}:
            total = optional(optional(order, "tradeData"), "volume")
            executed = optional(order, "executedVolume")
            # These volumes share protocol units. Their ratio avoids guessing an
            # instrument's lot size or making a network call from the event worker.
            if total and executed is not None and 0 <= executed <= total:
                volume_lots = record.request_data["volume_lots"] * executed / total
            else:
                logger.warning(
                    "Fill event lacks usable cumulative volume for %s; reconciliation required",
                    record.request_id,
                )
            price = optional(order, "executionPrice")
            # A single deal's price is not the cumulative average for multiple fills.
            if price is None and total and optional(deal, "filledVolume") == total:
                price = optional(deal, "executionPrice")

        self.persistence.update_result(
            BrokerExecutionResult(
                request_id=record.request_id,
                status=status,
                broker_event_at=(
                    datetime.fromtimestamp(stamp / 1000, timezone.utc)
                    if (
                        stamp := optional(deal, "executionTimestamp")
                        or optional(order, "utcLastUpdateTimestamp")
                    )
                    else None
                ),
                broker_event_time_source=(
                    "deal.executionTimestamp"
                    if optional(deal, "executionTimestamp")
                    else (
                        "order.utcLastUpdateTimestamp"
                        if optional(order, "utcLastUpdateTimestamp")
                        else None
                    )
                ),
                received_at=received_at,
                receipt_monotonic_ns=received_monotonic_ns,
                receipt_process_id=PROCESS_ID if received_at is not None else None,
                broker_order_id=order_id,
                broker_position_id=next(iter(position_ids), None),
                filled_volume_lots=volume_lots,
                fill_price=price,
                error_code=optional(event, "errorCode"),
                error_message=optional(event, "description"),
            )
        )
        if status in {"filled", "pending"} and self.protection_verifier is not None:
            try:
                self.protection_verifier(record.request_id)
            except Exception:
                logger.critical(
                    "Protection verification failed request_id=%s",
                    record.request_id,
                    exc_info=True,
                )

    def process_order_snapshot(self, request_id, order, deals):
        """Apply an exactly correlated OrderDetails snapshot; propagate failures."""
        from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
            ProtoOAOrderStatus as Status,
        )

        record = self.persistence.find_execution(request_id=request_id)
        order_id = optional(order, "orderId")
        if record is None or record.symbol != "XAUUSD" or not order_id:
            raise ValueError("Unknown recovery execution")
        if optional(order, "clientOrderId") not in (None, "", request_id):
            raise ValueError("Conflicting snapshot clientOrderId")
        by_order = self.persistence.find_execution(broker_order_id=str(order_id))
        if by_order is not None and by_order.request_id != request_id:
            raise ValueError("Ambiguous recovery order ID")
        total = optional(optional(order, "tradeData"), "volume")
        executed = optional(order, "executedVolume")
        unique_deals = {}
        positions = {str(value) for value in (optional(order, "positionId"),) if value}
        for deal in deals:
            if optional(deal, "orderId") != order_id or not optional(deal, "dealId"):
                raise ValueError("Uncorrelated order deal")
            previous = unique_deals.get(deal.dealId)
            if previous is not None and previous != deal:
                raise ValueError("Contradictory duplicate deal")
            unique_deals[deal.dealId] = deal
            if optional(deal, "positionId"):
                positions.add(str(deal.positionId))
        if len(positions) > 1:
            raise ValueError("Conflicting snapshot position IDs")
        if unique_deals:
            quantities = [optional(d, "filledVolume") for d in unique_deals.values()]
            if any(q is None or q < 0 for q in quantities):
                raise ValueError("Incomplete deal quantities")
            cumulative = sum(quantities)
            if executed is not None and cumulative != executed:
                raise ValueError("Order/deal cumulative volume disagreement")
            executed = cumulative
        if executed is not None and (not total or not 0 <= executed <= total):
            raise ValueError("Invalid cumulative order volume")
        status = optional(order, "orderStatus")
        if status == Status.ORDER_STATUS_FILLED:
            if executed is not None and executed != total:
                raise ValueError("Filled status contradicts cumulative volume")
            kind = ProtoOAExecutionType.ORDER_FILLED
        elif executed is not None and executed > 0:
            kind = ProtoOAExecutionType.ORDER_PARTIAL_FILL
        elif status == Status.ORDER_STATUS_ACCEPTED:
            kind = ProtoOAExecutionType.ORDER_ACCEPTED
        elif (
            status
            in (
                Status.ORDER_STATUS_REJECTED,
                Status.ORDER_STATUS_CANCELLED,
                Status.ORDER_STATUS_EXPIRED,
            )
            and executed == 0
        ):
            self.persistence.update_result(
                BrokerExecutionResult(
                    request_id=request_id,
                    status=(
                        "rejected"
                        if status == Status.ORDER_STATUS_REJECTED
                        else "cancelled"
                    ),
                    broker_order_id=str(order_id),
                    broker_position_id=next(iter(positions), None),
                    filled_volume_lots=0.0,
                )
            )
            return
        else:
            raise ValueError("Insufficient order outcome evidence")
        event = ProtoOAExecutionEvent(
            ctidTraderAccountId=self.account_id, executionType=kind
        )
        event.order.CopyFrom(order)
        event.order.clientOrderId = request_id
        if executed is not None:
            event.order.executedVolume = executed
        if positions:
            event.order.positionId = int(next(iter(positions)))
        if optional(order, "executionPrice") is None and executed and unique_deals:
            if all(
                optional(d, "executionPrice") is not None for d in unique_deals.values()
            ):
                event.order.executionPrice = (
                    sum(
                        d.executionPrice * d.filledVolume for d in unique_deals.values()
                    )
                    / executed
                )
        self._process(event)

    def _process_close_event(self, event):
        from app.models.broker_models import BrokerCloseResult
        from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
            ProtoOAPositionStatus,
        )

        order = optional(event, "order")
        position = optional(event, "position")
        closing = optional(order, "closingOrder") is True
        closed = (
            optional(position, "positionStatus")
            == ProtoOAPositionStatus.POSITION_STATUS_CLOSED
        )
        if not closing and not closed:
            return False
        ids = {
            str(v)
            for v in (
                optional(order, "positionId"),
                optional(position, "positionId"),
                optional(optional(event, "deal"), "positionId"),
            )
            if v
        }
        if len(ids) != 1:
            logger.warning("Unmatched closing event position IDs: %s", ids)
            return True
        position_id = ids.pop()
        row = self.persistence.get_close(self.account_id, position_id)
        if row is not None:
            if closed:
                # SL/TP or manual full closure also resolves the desired position state.
                self.persistence.update_close(
                    self.account_id,
                    BrokerCloseResult(broker_position_id=position_id, status="closed"),
                )
            elif (
                optional(order, "orderId") and str(order.orderId) == row.broker_order_id
            ):
                kind = optional(event, "executionType")
                if kind not in (
                    ProtoOAExecutionType.ORDER_REJECTED,
                    ProtoOAExecutionType.ORDER_ACCEPTED,
                    ProtoOAExecutionType.ORDER_FILLED,
                    ProtoOAExecutionType.ORDER_PARTIAL_FILL,
                ):
                    return True
                status = (
                    "rejected"
                    if kind == ProtoOAExecutionType.ORDER_REJECTED
                    else "submitted"
                )
                self.persistence.update_close(
                    self.account_id,
                    BrokerCloseResult(broker_position_id=position_id, status=status),
                )
        # Never correlate a closing order to the original opening execution.
        return True
