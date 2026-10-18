from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.integrations.ctrader.client import CTraderClient
import pytest

from app.models.execution_models import BrokerExecutionRequest
from app.providers.ctrader_broker_provider import CTraderBrokerProvider

from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAReconcileReq,
    ProtoOAReconcileRes,
    ProtoOAExecutionEvent,
    ProtoOASymbolsListReq,
    ProtoOANewOrderReq,
    ProtoOAOrderErrorEvent,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAPositionStatus,
    ProtoOATradeSide,
    ProtoOAExecutionType,
)


@pytest.fixture
def provider():
    client = Mock()
    client.account_id = 12345

    provider = CTraderBrokerProvider(client)

    # XAUUSD metadata from our demo integration
    provider.get_symbol_metadata = Mock(return_value=SimpleNamespace(lotSize=10000))

    return provider


def configure_responses(provider, positions):
    reconcile_response = ProtoOAReconcileRes()
    reconcile_response.ctidTraderAccountId = provider.client.account_id

    for position in positions:
        reconcile_response.position.add().CopyFrom(position)

    symbols_response = SimpleNamespace(
        symbol=[SimpleNamespace(symbolId=41, symbolName="XAUUSD")]
    )

    def send(request):
        if isinstance(request, ProtoOAReconcileReq):
            return reconcile_response

        if isinstance(request, ProtoOASymbolsListReq):
            return symbols_response

        raise AssertionError(f"Unexpected request: {type(request)}")

    provider.client.send.side_effect = send


def make_position(
    direction=ProtoOATradeSide.BUY,
    volume=100,
    with_sl_tp=True,
):
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
        ProtoOAPosition,
    )

    position = ProtoOAPosition()
    position.positionId = 98765
    position.positionStatus = ProtoOAPositionStatus.POSITION_STATUS_OPEN
    position.price = 4180.0

    position.tradeData.symbolId = 41
    position.tradeData.volume = volume
    position.tradeData.tradeSide = direction
    position.tradeData.openTimestamp = 1792000000000

    if with_sl_tp:
        position.stopLoss = 4165.0
        position.takeProfit = 4200.0

    return position


def test_empty_reconciliation(provider):
    configure_responses(provider, [])

    assert provider.get_open_positions() == []


@pytest.mark.parametrize(
    "trade_side, expected_direction",
    [
        (ProtoOATradeSide.BUY, "long"),
        (ProtoOATradeSide.SELL, "short"),
    ],
)
def test_position_direction(provider, trade_side, expected_direction):
    position = make_position(direction=trade_side)
    configure_responses(provider, [position])

    result = provider.get_open_positions()

    assert len(result) == 1
    assert result[0].direction == expected_direction
    assert result[0].broker_position_id == "98765"
    assert result[0].symbol == "XAUUSD"


def test_position_volume_and_prices(provider):
    position = make_position(volume=100)
    configure_responses(provider, [position])

    result = provider.get_open_positions()[0]

    assert result.volume_lots == pytest.approx(0.01)
    assert result.entry_price == 4180.0
    assert result.stop_loss == 4165.0
    assert result.take_profit == 4200.0
    assert result.status == "open"


def test_position_without_sl_tp(provider):
    position = make_position(with_sl_tp=False)
    configure_responses(provider, [position])

    result = provider.get_open_positions()[0]

    assert result.stop_loss is None
    assert result.take_profit is None


def test_position_timestamp(provider):
    position = make_position()
    configure_responses(provider, [position])

    result = provider.get_open_positions()[0]

    assert result.opened_at == datetime.fromtimestamp(
        1792000000,
        tz=timezone.utc,
    )


def test_successful_market_order_fill(provider):
    request = BrokerExecutionRequest(
        request_id="test-fill-001",
        agent_decision_id="test-decision",
        symbol="XAUUSD",
        direction="long",
        trade_style="swing",
        confidence=70,
        thesis="Testing cTrader fill mapping",
        volume_lots=0.01,
        reference_price=4180,
        stop_loss=4165,
        take_profit=4200,
        max_holding_minutes=2880,
    )

    # Order construction has already been tested separately.
    provider.build_market_order_request = Mock(return_value=ProtoOANewOrderReq())

    response = ProtoOAExecutionEvent()
    response.ctidTraderAccountId = provider.client.account_id
    response.executionType = ProtoOAExecutionType.ORDER_FILLED

    response.order.orderId = 12345
    response.position.positionId = 98765

    response.deal.filledVolume = 100
    response.deal.executionPrice = 4180.50

    provider.client.send.return_value = response

    result = provider.open_position(request)

    assert result.status == "filled"
    assert result.request_id == "test-fill-001"
    assert result.broker_order_id == "12345"
    assert result.broker_position_id == "98765"
    assert result.filled_volume_lots == pytest.approx(0.01)
    assert result.fill_price == pytest.approx(4180.50)


@pytest.mark.parametrize(
    "execution_type",
    [
        ProtoOAExecutionType.ORDER_ACCEPTED,
        ProtoOAExecutionType.ORDER_FILLED,
        ProtoOAExecutionType.ORDER_PARTIAL_FILL,
        ProtoOAExecutionType.ORDER_REJECTED,
    ],
)
def test_execution_event_received(execution_type, capsys):
    client = CTraderClient.__new__(CTraderClient)

    event = ProtoOAExecutionEvent()
    event.executionType = execution_type

    with patch(
        "app.integrations.ctrader.client.Protobuf.extract",
        return_value=event,
    ):
        client._on_message_received(None, Mock())

    output = capsys.readouterr().out

    assert "CTRADER EXECUTION EVENT:" in output
