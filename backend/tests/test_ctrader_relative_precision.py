from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from ctrader_open_api.messages import OpenApiMessages_pb2 as M
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOASymbol, ProtoOASymbolDistanceType as DistanceType, ProtoOATradeSide,
    ProtoOAExecutionType,
)

from app.models.execution_models import BrokerExecutionRequest
from app.providers.ctrader_broker_provider import CTraderBrokerProvider
from tests.test_ctrader_execution_events import persistence


@pytest.fixture
def execution_request():
    return BrokerExecutionRequest(
        request_id="precision-1", agent_decision_id=None, symbol="XAUUSD", direction="long",
        volume_lots=0.01, reference_price=4170.77579, stop_loss=4158.44024,
        take_profit=4187.22319, max_holding_minutes=2880, trade_style="swing",
        confidence=75, thesis="Precision regression",
    )


def metadata(digits=2):
    return ProtoOASymbol(symbolId=41, digits=digits, pipPosition=1, lotSize=10000,
                         minVolume=1, maxVolume=100000, stepVolume=1)


@pytest.fixture
def provider():
    client = Mock(account_id=12345)
    service = CTraderBrokerProvider(client)
    service.get_symbol_metadata = Mock(return_value=metadata())
    return service


def test_exact_rejected_request(provider, execution_request):
    order = provider.build_market_order_request(execution_request)
    assert order.relativeStopLoss == 1233000  # 12.33555 -> 12.33 at digits=2.
    assert order.relativeTakeProfit == 1644000  # 16.44740 -> 16.44.
    assert order.volume == 100
    assert order.symbolId == 41
    assert order.tradeSide == ProtoOATradeSide.BUY
    assert order.clientOrderId == execution_request.request_id
    assert order.ctidTraderAccountId == 12345
    assert not order.HasField("stopLoss") and not order.HasField("takeProfit")
    provider.client.send.assert_not_called()


@pytest.mark.parametrize("digits,sl,tp", [
    (0, 1200000, 1600000), (1, 1230000, 1640000), (2, 1233000, 1644000),
    (3, 1233500, 1644700), (4, 1233550, 1644740), (5, 1233555, 1644740),
    (6, 1233555, 1644740),
])
def test_metadata_precision_and_protocol_alignment(provider, execution_request, digits, sl, tp):
    provider.get_symbol_metadata.return_value = metadata(digits)
    order = provider.build_market_order_request(execution_request)
    assert (order.relativeStopLoss, order.relativeTakeProfit) == (sl, tp)
    tick = Decimal(1).scaleb(-digits)
    for units in (order.relativeStopLoss, order.relativeTakeProfit):
        assert Decimal(units) / 100000 % tick == 0
    assert Decimal(order.relativeStopLoss) / 100000 <= Decimal(str(execution_request.reference_price)) - Decimal(str(execution_request.stop_loss))


@pytest.mark.parametrize("direction,stop,target,side", [
    ("long", 4158.44024, 4187.22319, ProtoOATradeSide.BUY),
    ("short", 4183.11134, 4154.32839, ProtoOATradeSide.SELL),
])
def test_long_and_short_direction_and_risk_budget(provider, execution_request, direction, stop, target, side):
    execution_request = execution_request.model_copy(update={"direction": direction, "stop_loss": stop, "take_profit": target})
    order = provider.build_market_order_request(execution_request)
    assert order.tradeSide == side
    risk = abs(Decimal(str(execution_request.reference_price)) - Decimal(str(stop)))
    assert Decimal(order.relativeStopLoss) / 100000 <= risk
    assert order.relativeStopLoss == 1233000 and order.relativeTakeProfit == 1644000


@pytest.mark.parametrize("direction,stop,target", [
    ("long", 4180, 4187), ("long", 4150, 4160),
    ("short", 4150, 4160), ("short", 4180, 4187),
    ("long", 4170.77579, 4187), ("short", 4180, 4170.77579),
])
def test_wrong_side_or_zero_distances_rejected(provider, execution_request, direction, stop, target):
    bad = execution_request.model_copy(update={"direction": direction, "stop_loss": stop, "take_profit": target})
    with pytest.raises(ValueError, match="correct side"):
        provider.build_market_order_request(bad)
    provider.client.send.assert_not_called()


@pytest.mark.parametrize("entry,stop,target,sl,tp", [
    (4180.1, 4167.77, 4196.54, 1233000, 1644000),
    (0.1 + 0.2, 0.1, 0.5, 20000, 19000),
    (100, 87.66000000000001, 116.44999999999999, 1233000, 1644000),
])
def test_float_artifacts_do_not_widen_risk(provider, execution_request, entry, stop, target, sl, tp):
    execution_request = execution_request.model_copy(update={"reference_price": entry, "stop_loss": stop, "take_profit": target})
    order = provider.build_market_order_request(execution_request)
    assert (order.relativeStopLoss, order.relativeTakeProfit) == (sl, tp)
    assert Decimal(order.relativeStopLoss) / 100000 <= Decimal(str(entry)) - Decimal(str(stop))


@pytest.mark.parametrize("bad_metadata", [
    None, SimpleNamespace(), SimpleNamespace(digits=None), SimpleNamespace(digits=True),
    SimpleNamespace(digits="2"), SimpleNamespace(digits=2.0), SimpleNamespace(digits=-1),
    SimpleNamespace(digits=11), ProtoOASymbol(symbolId=41, pipPosition=1),
])
def test_missing_invalid_digits_fail_closed(provider, execution_request, bad_metadata):
    provider.get_symbol_metadata.return_value = bad_metadata
    with pytest.raises(ValueError, match="digits"):
        provider.build_market_order_request(execution_request)
    provider.client.send.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("reference_price", float("nan")), ("stop_loss", float("inf")),
    ("take_profit", float("-inf")), ("stop_loss", 0), ("reference_price", -1),
])
def test_nonfinite_or_nonpositive_prices_rejected(provider, execution_request, field, value):
    with pytest.raises(ValueError, match="finite and positive"):
        provider.build_market_order_request(execution_request.model_copy(update={field: value}))
    provider.client.send.assert_not_called()


@pytest.mark.parametrize("field,value", [("stop_loss", 4170.77), ("take_profit", 4170.78)])
def test_quantized_distance_zero_rejected(provider, execution_request, field, value):
    with pytest.raises(ValueError, match="rounds to zero"):
        provider.build_market_order_request(execution_request.model_copy(update={field: value}))


def test_protocol_overflow_rejected(provider, execution_request):
    bad = execution_request.model_copy(update={"reference_price": 1e15, "stop_loss": 1, "take_profit": 2e15})
    with pytest.raises(ValueError, match="int64"):
        provider.build_market_order_request(bad)


@pytest.mark.parametrize("field", ["slDistance", "tpDistance"])
def test_broker_minimum_not_met_after_rounding_is_rejected(provider, execution_request, field):
    symbol = metadata()
    setattr(symbol, field, 1234 if field == "slDistance" else 1645)
    provider.get_symbol_metadata.return_value = symbol
    # Default protobuf distanceSetIn is POINTS even if the optional field is absent.
    with pytest.raises(ValueError, match=field):
        provider.build_market_order_request(execution_request)
    provider.client.send.assert_not_called()


def test_broker_point_minimum_exactly_met(provider, execution_request):
    symbol = metadata()
    symbol.slDistance, symbol.tpDistance = 1233, 1644
    provider.get_symbol_metadata.return_value = symbol
    assert provider.build_market_order_request(execution_request).relativeStopLoss == 1233000


def test_unknown_constraint_units_not_guessed(provider, execution_request):
    symbol = metadata()
    symbol.slDistance = 1
    symbol.distanceSetIn = DistanceType.SYMBOL_DISTANCE_IN_PERCENTAGE
    provider.get_symbol_metadata.return_value = symbol
    with pytest.raises(ValueError, match="minimum-distance units"):
        provider.build_market_order_request(execution_request)


def test_local_rejection_is_persistable_without_unresolved_request(provider, execution_request, persistence):
    bad = execution_request.model_copy(update={"stop_loss": 4170.77})
    persistence.save_request(bad)
    result = provider.open_position(bad)
    assert result.status == "rejected" and result.error_code == "LOCAL_INVALID_REQUEST"
    assert result.broker_order_id is None
    provider.client.send.assert_not_called()
    persistence.update_result(result)
    assert persistence.find_execution(request_id=bad.request_id).status == "rejected"
    assert all(row.request_id != bad.request_id for row in persistence.get_unresolved())


@pytest.mark.parametrize("digits", [0, 2, 3, 5])
def test_symbol_lookup_uses_broker_digits_and_caches(provider, digits):
    symbols = M.ProtoOASymbolsListRes(ctidTraderAccountId=12345)
    symbols.symbol.add(symbolId=41, symbolName="XAUUSD")
    details = M.ProtoOASymbolByIdRes(ctidTraderAccountId=12345)
    details.symbol.add().CopyFrom(metadata(digits))
    provider.client.send.side_effect = [symbols, details]
    first = CTraderBrokerProvider.get_symbol_metadata(provider, "XAUUSD")
    second = CTraderBrokerProvider.get_symbol_metadata(provider, "XAUUSD")
    assert first.digits == digits and first is second
    assert provider.client.send.call_count == 2
    assert provider.client.send.call_args.args[0].symbolId == [41]


@pytest.mark.parametrize("mode", ["wrong_symbol", "missing_digits", "duplicate"])
def test_unusable_symbol_lookup_is_not_cached(provider, mode):
    symbols = M.ProtoOASymbolsListRes(ctidTraderAccountId=12345)
    symbols.symbol.add(symbolId=41, symbolName="XAUUSD")
    details = M.ProtoOASymbolByIdRes(ctidTraderAccountId=12345)
    symbol = details.symbol.add()
    symbol.CopyFrom(metadata())
    if mode == "wrong_symbol": symbol.symbolId = 99
    if mode == "missing_digits": symbol.ClearField("digits")
    if mode == "duplicate": details.symbol.add().CopyFrom(symbol)
    provider.client.send.side_effect = [symbols, details]
    with pytest.raises(ValueError):
        CTraderBrokerProvider.get_symbol_metadata(provider, "XAUUSD")
    assert not provider._symbol_cache


@pytest.mark.parametrize("kind,status", [
    (ProtoOAExecutionType.ORDER_ACCEPTED, "submitted"),
    (ProtoOAExecutionType.ORDER_FILLED, "filled"),
    (ProtoOAExecutionType.ORDER_PARTIAL_FILL, "pending"),
    (ProtoOAExecutionType.ORDER_REJECTED, "rejected"),
])
def test_corrected_request_preserves_execution_mapping(provider, execution_request, kind, status):
    response = M.ProtoOAExecutionEvent(ctidTraderAccountId=12345, executionType=kind)
    response.order.orderId = 42
    response.position.positionId = 77
    provider.client.send.return_value = response
    provider.position_management.verify_fill = Mock(return_value=("verified", None))
    result = provider.open_position(execution_request)
    assert result.status == status
    assert result.broker_order_id == "42" and result.broker_position_id == "77"
    sent = provider.client.send.call_args.args[0]
    assert isinstance(sent, M.ProtoOANewOrderReq)
    assert (sent.relativeStopLoss, sent.relativeTakeProfit, sent.volume) == (1233000, 1644000, 100)


def test_uncertain_transport_still_unknown(provider, execution_request):
    provider.client.send.side_effect = TimeoutError("uncertain")
    assert provider.open_position(execution_request).status == "unknown"
    assert provider.client.send.call_count == 1


@pytest.mark.parametrize("digits", [0, 1, 2, 3, 4, 5, 6])
def test_post_fill_verification_uses_same_rounded_distances(provider, execution_request, digits):
    symbol = metadata(digits)
    provider.get_symbol_metadata.return_value = symbol
    order = provider.build_market_order_request(execution_request)
    tick = Decimal(1).scaleb(-digits)
    entry = Decimal("4171.12345")  # Actual fill differs from the requested reference.
    position = SimpleNamespace(broker_position_id="77", symbol="XAUUSD", direction="long",
        entry_price=float(entry),
        stop_loss=float((entry - Decimal(order.relativeStopLoss) / 100000).quantize(tick)),
        take_profit=float((entry + Decimal(order.relativeTakeProfit) / 100000).quantize(tick)))
    provider.position_management._guard = Mock()
    provider.get_open_positions = Mock(return_value=[position])
    assert provider.position_management.verify_fill(execution_request, "77") == ("verified", None)
    provider.client.send.assert_not_called()


def test_short_post_fill_verification(provider, execution_request):
    execution_request = execution_request.model_copy(update={"direction": "short",
        "stop_loss": 4183.11134, "take_profit": 4154.32839})
    position = SimpleNamespace(direction="short", entry_price=4171.12,
                              stop_loss=4183.45, take_profit=4154.68)
    provider.position_management._protection(position, execution_request)


@pytest.mark.parametrize("field,level", [
    ("stop_loss", 4158.78), ("stop_loss", 4158.80),  # One tick too wide / too tight.
    ("take_profit", 4187.55), ("take_profit", 4187.57),
    ("stop_loss", 4171.13), ("take_profit", 4171.11),  # Wrong side.
])
def test_post_fill_material_or_wrong_side_protection_rejected(provider, execution_request, field, level):
    position = SimpleNamespace(direction="long", entry_price=4171.12,
                              stop_loss=4158.79, take_profit=4187.56)
    setattr(position, field, level)
    with pytest.raises(ValueError):
        provider.position_management._protection(position, execution_request)


def test_post_fill_half_tick_tolerance_cannot_widen_original_budget(provider, execution_request):
    execution_request = execution_request.model_copy(update={"stop_loss": 4158.44479})
    # Actual SL distance 12.334 is within half a tick of encoded 12.33, but exceeds
    # the original 12.331 risk budget. This must remain unsafe.
    position = SimpleNamespace(direction="long", entry_price=4170.774,
                              stop_loss=4158.44, take_profit=4187.21)
    with pytest.raises(ValueError, match="exceeds planned SL risk"):
        provider.position_management._protection(position, execution_request)


def test_post_fill_does_not_accept_old_unquantized_distances(provider, execution_request):
    position = SimpleNamespace(direction="long", entry_price=4171.12,
                              stop_loss=4158.78445, take_profit=4187.5674)
    with pytest.raises(ValueError):
        provider.position_management._protection(position, execution_request)


def test_fractional_vwap_and_permitted_absolute_tick_rounding(provider, execution_request):
    position = SimpleNamespace(direction="long", entry_price=4170.77579,
                              stop_loss=4158.45, take_profit=4187.22)
    provider.position_management._protection(position, execution_request)
