from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext

from app.integrations.ctrader.client import CTraderClient
from app.models.broker_models import BrokerPosition, BrokerCloseResult
from app.models.execution_models import (
    BrokerExecutionRequest,
    BrokerExecutionResult,
)
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOASymbolsListReq,
    ProtoOASymbolByIdReq,
    ProtoOAReconcileReq,
)

from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType,
    ProtoOAPositionStatus,
    ProtoOASymbolDistanceType,
)

from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAErrorRes,
    ProtoOAOrderErrorEvent,
)

from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOANewOrderReq
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAOrderType,
    ProtoOATradeSide,
)


class CTraderBrokerProvider:
    manages_broker_positions = True

    def __init__(self, client: CTraderClient, persistence=None):
        self.runtime = None
        self.client = client
        self._symbol_cache: dict[str, object] = {}
        from app.services.ctrader_position_management_service import CTraderPositionManagementService
        self.position_management = CTraderPositionManagementService(self, persistence)
        self.client.set_position_protection_verifier(self.position_management.verify_execution)

    def manage_positions(self):
        if self.runtime is not None:
            return self.runtime.manage_positions()
        return self.position_management.manage()

    def recover_executions(self, persistence=None):
        """Explicit worker-thread recovery; does not start/authenticate the client."""
        from app.services.ctrader_recovery_service import CTraderRecoveryService
        return CTraderRecoveryService(self, persistence).recover()

    def get_symbol_metadata(self, symbol_name: str):
        symbol_name = symbol_name.upper()

        if symbol_name in self._symbol_cache:
            return self._symbol_cache[symbol_name]

        symbols_request = ProtoOASymbolsListReq()
        symbols_request.ctidTraderAccountId = self.client.account_id

        symbols_response = self.client.send(symbols_request)

        symbol = next(
            (s for s in symbols_response.symbol if s.symbolName.upper() == symbol_name),
            None,
        )

        if symbol is None:
            raise ValueError(f"Symbol not found: {symbol_name}")

        details_request = ProtoOASymbolByIdReq()
        details_request.ctidTraderAccountId = self.client.account_id
        details_request.symbolId.append(symbol.symbolId)

        details_response = self.client.send(details_request)

        if not details_response.symbol:
            raise ValueError(f"No symbol metadata returned for {symbol_name}")

        matches = [item for item in details_response.symbol if item.symbolId == symbol.symbolId]
        if len(matches) != 1:
            raise ValueError(f"Ambiguous or mismatched symbol metadata for {symbol_name}")
        metadata = matches[0]
        self.price_tick(metadata)
        self._symbol_cache[symbol_name] = metadata

        return metadata

    def get_pending_order_ids(self):
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAReconcileRes
        response = self.client.send(ProtoOAReconcileReq(ctidTraderAccountId=self.client.account_id))
        if not isinstance(response, ProtoOAReconcileRes) or response.ctidTraderAccountId != self.client.account_id:
            raise RuntimeError("Cannot verify broker pending orders")
        if any(not order.HasField("orderId") or order.orderId <= 0 for order in response.order):
            raise RuntimeError("Invalid broker pending-order identifiers")
        return [str(order.orderId) for order in response.order]

    def get_open_positions(self) -> list[BrokerPosition]:
        reconcile_request = ProtoOAReconcileReq()
        reconcile_request.ctidTraderAccountId = self.client.account_id

        response = self.client.send(reconcile_request)

        if isinstance(response, (ProtoOAErrorRes, ProtoOAOrderErrorEvent)):
            raise RuntimeError(f"cTrader reconciliation failed: {response.errorCode}")

        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAReconcileRes
        if not isinstance(response, ProtoOAReconcileRes) or response.ctidTraderAccountId != self.client.account_id:
            raise ValueError("Invalid broker position snapshot/account")
        ids = [p.positionId for p in response.position]
        if len(ids) != len(set(ids)) or any(i <= 0 for i in ids):
            raise ValueError("Ambiguous broker position identifiers")
        for p in response.position:
            if not p.HasField("positionStatus") or p.positionStatus not in (
                ProtoOAPositionStatus.POSITION_STATUS_OPEN, ProtoOAPositionStatus.POSITION_STATUS_CLOSED
            ):
                raise ValueError("Incomplete broker position state")

        symbols_request = ProtoOASymbolsListReq()
        symbols_request.ctidTraderAccountId = self.client.account_id

        symbols_response = self.client.send(symbols_request)

        if isinstance(symbols_response, ProtoOAErrorRes):
            raise RuntimeError(
                f"cTrader symbol lookup failed: {symbols_response.errorCode}"
            )

        symbol_names = {
            symbol.symbolId: symbol.symbolName for symbol in symbols_response.symbol
        }

        positions = []

        for position in response.position:
            if position.positionStatus != ProtoOAPositionStatus.POSITION_STATUS_OPEN:
                continue

            trade = position.tradeData
            symbol_name = symbol_names.get(trade.symbolId)

            if symbol_name is None:
                raise ValueError(f"Unknown cTrader symbol ID: {trade.symbolId}")

            metadata = self.get_symbol_metadata(symbol_name)
            if (not trade.HasField("volume") or trade.volume <= 0 or metadata.lotSize <= 0
                    or not trade.HasField("tradeSide") or trade.tradeSide not in (
                        ProtoOATradeSide.BUY, ProtoOATradeSide.SELL)):
                raise ValueError("Invalid broker position volume or direction")

            if not trade.HasField("openTimestamp"):
                raise ValueError(
                    f"Missing opening timestamp for position {position.positionId}"
                )

            positions.append(
                BrokerPosition(
                    broker_position_id=str(position.positionId),
                    symbol=symbol_name,
                    direction=(
                        "long" if trade.tradeSide == ProtoOATradeSide.BUY else "short"
                    ),
                    volume_lots=trade.volume / metadata.lotSize,
                    entry_price=position.price,
                    stop_loss=(
                        position.stopLoss if position.HasField("stopLoss") else None
                    ),
                    take_profit=(
                        position.takeProfit if position.HasField("takeProfit") else None
                    ),
                    opened_at=datetime.fromtimestamp(
                        trade.openTimestamp / 1000,
                        tz=timezone.utc,
                    ),
                    status="open",
                )
            )

        return positions

    def open_position(
        self,
        request: BrokerExecutionRequest,
    ) -> BrokerExecutionResult:
        if self.runtime is not None:
            with self.runtime.operation():
                try:
                    self.runtime.before_open(request)
                except Exception as exc:
                    return BrokerExecutionResult(request_id=request.request_id, status="rejected",
                        error_code="LOCAL_BLOCKED", error_message=str(exc))
                return self._open_position(request)
        return self._open_position(request)

    def _open_position(self, request):
        if getattr(request, "opportunity_id", None) is not None:
            try:
                if (request.valid_until is None or request.valid_until.tzinfo is None
                        or datetime.now(timezone.utc) >= request.valid_until):
                    return BrokerExecutionResult(request_id=request.request_id, status="rejected",
                        error_code="LOCAL_EXPIRED_OPPORTUNITY", error_message="Quick Trade opportunity expired before broker send")
                from app.strategies.input_adapter import collect_broker_snapshot
                from app.strategies.execution_validation import validate_preserved_levels
                guard = request.quick_trade_guard
                if guard is None:
                    raise ValueError("Missing Quick Trade execution guard")
                snapshot = collect_broker_snapshot(self, as_of=datetime.now(timezone.utc))
                if (snapshot.observed_at is None or not 0 <= (datetime.now(timezone.utc)-snapshot.observed_at).total_seconds() <= 60):
                    raise ValueError("stale_or_missing_broker_quote")
                entry = snapshot.ask if request.direction == "long" else snapshot.bid
                validate_preserved_levels(direction=request.direction, entry=entry,
                    original_entry=guard.original_entry, stop_loss=request.stop_loss,
                    take_profit=request.take_profit, stop_budget=guard.stop_budget,
                    snapshot=snapshot, additional_cost=guard.additional_cost, max_price_drift=guard.max_price_drift)
                if datetime.now(timezone.utc) >= request.valid_until:
                    raise ValueError("opportunity_expired_during_final_quote_check")
            except Exception as exc:
                return BrokerExecutionResult(request_id=request.request_id, status="rejected",
                    error_code="LOCAL_QUICK_TRADE_BLOCKED", error_message=str(exc))
        try:
            order_request = self.build_market_order_request(request)
            if getattr(request, "opportunity_id", None) is not None:
                # The actual wire grid must satisfy the same bounds after rounding.
                side = 1 if request.direction == "long" else -1
                validate_preserved_levels(direction=request.direction, entry=request.reference_price,
                    original_entry=guard.original_entry,
                    stop_loss=request.reference_price-side*order_request.relativeStopLoss/100000,
                    take_profit=request.reference_price+side*order_request.relativeTakeProfit/100000,
                    stop_budget=guard.stop_budget, snapshot=snapshot,
                    additional_cost=guard.additional_cost, max_price_drift=guard.max_price_drift)
        except ValueError as exc:
            # Nothing was submitted. Return a definitive local failure so the
            # existing persistence pipeline does not leave a prepared request.
            return BrokerExecutionResult(request_id=request.request_id, status="rejected",
                error_code="LOCAL_INVALID_REQUEST", error_message=str(exc))

        try:
            response = self.client.send(order_request)

        except Exception as exc:
            return BrokerExecutionResult(
                request_id=request.request_id,
                status="unknown",
                error_message=str(exc),
            )

        if isinstance(response, (ProtoOAOrderErrorEvent, ProtoOAErrorRes)):
            return BrokerExecutionResult(
                request_id=request.request_id,
                status="rejected",
                error_code=response.errorCode,
                error_message=response.description,
            )

        order = getattr(response, "order", None)
        position = getattr(response, "position", None)
        deal = getattr(response, "deal", None)

        broker_order_id = (
            str(order.orderId)
            if order is not None and getattr(order, "orderId", None)
            else None
        )

        broker_position_id = (
            str(position.positionId)
            if position is not None and getattr(position, "positionId", None)
            else None
        )

        fill_price = None
        filled_volume_lots = None

        if deal is not None:
            if getattr(deal, "executionPrice", None):
                fill_price = deal.executionPrice

            if getattr(deal, "filledVolume", None):
                metadata = self.get_symbol_metadata(request.symbol)
                filled_volume_lots = deal.filledVolume / metadata.lotSize

        execution_type = getattr(response, "executionType", None)

        if execution_type == ProtoOAExecutionType.ORDER_FILLED:
            status = "filled"
        elif execution_type == ProtoOAExecutionType.ORDER_ACCEPTED:
            status = "submitted"
        elif execution_type == ProtoOAExecutionType.ORDER_REJECTED:
            status = "rejected"
        elif execution_type == ProtoOAExecutionType.ORDER_PARTIAL_FILL:
            status = "pending"
        else:
            status = "unknown"

        protection_status, protection_message = "unchecked", None
        if status == "filled":
            protection_status, protection_message = self.position_management.verify_fill(request, broker_position_id)

        return BrokerExecutionResult(
            request_id=request.request_id,
            status=status,
            protection_status=protection_status,
            protection_message=protection_message,
            broker_order_id=broker_order_id,
            broker_position_id=broker_position_id,
            filled_volume_lots=filled_volume_lots,
            fill_price=fill_price,
        )

    def close_position(
        self,
        position: BrokerPosition,
        exit_price: float | None = None,
        close_reason: str | None = None,
    ) -> BrokerCloseResult:
        return self.position_management.close(position)

    def convert_lots_to_volume(
        self,
        volume_lots: float,
        symbol_metadata,
    ) -> int:
        protocol_volume = int(round(volume_lots * symbol_metadata.lotSize))

        if protocol_volume < symbol_metadata.minVolume:
            raise ValueError("Volume below minimum")

        if protocol_volume > symbol_metadata.maxVolume:
            raise ValueError("Volume above maximum")

        if (
            protocol_volume - symbol_metadata.minVolume
        ) % symbol_metadata.stepVolume != 0:
            raise ValueError("Volume does not match step size")

        return protocol_volume

    @staticmethod
    def price_tick(metadata) -> Decimal:
        # Required proto2 fields otherwise read as zero even when absent.
        digits = getattr(metadata, "digits", None)
        if hasattr(metadata, "HasField") and not metadata.HasField("digits"):
            digits = None
        if type(digits) is not int or not 0 <= digits <= 10:
            raise ValueError("Missing or invalid symbol digits")
        return Decimal(1).scaleb(-digits)

    @classmethod
    def relative_protection_units(cls, request, metadata) -> tuple[int, int]:
        """Round down on the common symbol/protocol grid; never widen SL risk."""
        tick = cls.price_tick(metadata)
        if request.symbol != "XAUUSD" or request.direction not in ("long", "short"):
            raise ValueError("Relative protection requires an XAUUSD long/short request")
        try:
            with localcontext() as context:
                context.prec = 40
                entry, stop, target = (Decimal(str(value)) for value in (
                    request.reference_price, request.stop_loss, request.take_profit))
                if any(not value.is_finite() or value <= 0 for value in (entry, stop, target)):
                    raise ValueError("Entry, SL and TP prices must be finite and positive")
                side = 1 if request.direction == "long" else -1
                distances = ((entry - stop) * side, (target - entry) * side)
                if any(distance <= 0 for distance in distances):
                    raise ValueError("SL and TP must be positive distances on the correct side of entry")
                # The wire format cannot represent increments smaller than 1e-5,
                # even when the broker reports a finer symbol price precision.
                quantum = max(tick, Decimal("0.00001"))
                rounded = [distance.quantize(quantum, rounding=ROUND_DOWN) for distance in distances]
                for field, distance in zip(("slDistance", "tpDistance"), rounded):
                    units = distance * 100000
                    if not 0 < units <= 2**63 - 1:
                        raise ValueError("Relative protection rounds to zero or exceeds int64 range")
                    minimum = getattr(metadata, field, 0)
                    if hasattr(metadata, "HasField") and not metadata.HasField(field):
                        minimum = 0
                    if type(minimum) is not int or minimum < 0:
                        raise ValueError(f"Invalid symbol {field}")
                    if minimum:
                        # The protobuf's default is POINTS. Do not guess the
                        # scale of a percentage-based constraint.
                        mode = getattr(metadata, "distanceSetIn", None)
                        if mode != ProtoOASymbolDistanceType.SYMBOL_DISTANCE_IN_POINTS:
                            raise ValueError("Unsupported or missing broker minimum-distance units")
                        if distance < Decimal(minimum) * tick:
                            raise ValueError(f"Rounded protection violates broker {field}")
                return tuple(int(distance * 100000) for distance in rounded)
        except (InvalidOperation, TypeError) as exc:
            raise ValueError("Invalid relative protection prices") from exc

    def build_market_order_request(
        self,
        request: BrokerExecutionRequest,
    ):
        metadata = self.get_symbol_metadata(request.symbol)
        sl_units, tp_units = self.relative_protection_units(request, metadata)

        volume = self.convert_lots_to_volume(
            request.volume_lots,
            metadata,
        )

        order = ProtoOANewOrderReq()

        order.ctidTraderAccountId = self.client.account_id
        order.symbolId = metadata.symbolId
        order.orderType = ProtoOAOrderType.MARKET
        order.tradeSide = (
            ProtoOATradeSide.BUY
            if request.direction == "long"
            else ProtoOATradeSide.SELL
        )
        order.volume = volume
        order.clientOrderId = request.request_id

        order.relativeStopLoss = sl_units
        order.relativeTakeProfit = tp_units

        return order
