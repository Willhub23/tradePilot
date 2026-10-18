from datetime import datetime, timezone

from app.integrations.ctrader.client import CTraderClient
from app.models.broker_models import BrokerPosition
from app.models.execution_models import (
    BrokerExecutionRequest,
    BrokerExecutionResult,
)
from app.models.paper_trading_models import PaperPosition
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOASymbolsListReq,
    ProtoOASymbolByIdReq,
    ProtoOAReconcileReq,
)

from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType,
    ProtoOAPositionStatus,
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
    def __init__(self, client: CTraderClient):
        self.client = client
        self._symbol_cache: dict[str, object] = {}

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

        metadata = details_response.symbol[0]
        self._symbol_cache[symbol_name] = metadata

        return metadata

    def get_open_positions(self) -> list[BrokerPosition]:
        reconcile_request = ProtoOAReconcileReq()
        reconcile_request.ctidTraderAccountId = self.client.account_id

        response = self.client.send(reconcile_request)

        if isinstance(response, (ProtoOAErrorRes, ProtoOAOrderErrorEvent)):
            raise RuntimeError(f"cTrader reconciliation failed: {response.errorCode}")

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
        order_request = self.build_market_order_request(request)

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

        return BrokerExecutionResult(
            request_id=request.request_id,
            status=status,
            broker_order_id=broker_order_id,
            broker_position_id=broker_position_id,
            filled_volume_lots=filled_volume_lots,
            fill_price=fill_price,
        )

    def close_position(
        self,
        position: PaperPosition,
        exit_price: float,
        close_reason: str | None = None,
    ) -> PaperPosition:
        raise NotImplementedError

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

    def build_market_order_request(
        self,
        request: BrokerExecutionRequest,
    ):
        metadata = self.get_symbol_metadata(request.symbol)

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

        sl_distance = abs(request.reference_price - request.stop_loss)
        tp_distance = abs(request.take_profit - request.reference_price)

        order.relativeStopLoss = int(round(sl_distance * 100_000))

        order.relativeTakeProfit = int(round(tp_distance * 100_000))

        return order
