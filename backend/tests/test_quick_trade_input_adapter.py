from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json

import pytest
from ctrader_open_api.messages import OpenApiMessages_pb2 as M
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOASymbol, ProtoOASymbolDistanceType as Distance

from app.providers.ctrader_broker_provider import CTraderBrokerProvider
from app.providers.twelve_data_provider import TwelveDataProvider
from app.strategies.input_adapter import (
    BrokerEvidenceSnapshot, adapt_strategy_input, collect_broker_snapshot, input_from_services,
)
from app.strategies.validation import validate_snapshot
from scripts.validate_quick_trade import evaluate_raw, main
from tests.test_quick_trade_strategy import scenario, NOW


def provider(metadata=None):
    return SimpleNamespace(
        client=Mock(account_id=123, environment="demo", authenticated=True, demo_verified=True),
        get_symbol_metadata=Mock(return_value=metadata or ProtoOASymbol(
            symbolId=41, digits=2, pipPosition=1, slDistance=50, tpDistance=75)),
        price_tick=CTraderBrokerProvider.price_tick,
    )


def spot(**changes):
    fields = dict(ctidTraderAccountId=123, symbolId=41, bid=419990000, ask=420010000,
                  timestamp=int(NOW.timestamp()*1000))
    fields.update(changes)
    return M.ProtoOASpotEvent(**fields)


def snapshot(**changes):
    fields = dict(source="captured-demo", observed_at=NOW, bid=4199.9, ask=4200.1,
                  min_stop_distance=.5, min_target_distance=.75, additional_round_trip_cost=.1)
    fields.update(changes)
    return BrokerEvidenceSnapshot(**fields)


def test_known_utc_candles_normalized_without_mutation_or_time_refresh():
    analysis = scenario().analysis
    analysis.timeframes[0].latest_candle_timestamp = NOW.replace(tzinfo=None)
    before = analysis.model_dump_json()
    data = adapt_strategy_input(analysis, evaluated_at=NOW, market_source="twelve_data")
    assert data.analysis.timeframes[0].latest_candle_timestamp == NOW
    assert data.analysis.price_timestamp == analysis.price_timestamp
    assert analysis.model_dump_json() == before
    unknown = adapt_strategy_input(analysis, evaluated_at=NOW)
    assert unknown.analysis.timeframes[0].latest_candle_timestamp.tzinfo is None


def test_aware_offsets_convert_to_utc_preserving_instant():
    analysis = scenario().analysis
    offset = timezone(timedelta(hours=2))
    analysis.price_timestamp = NOW.astimezone(offset)
    analysis.timeframes[0].latest_candle_timestamp = NOW.astimezone(offset)
    data = adapt_strategy_input(analysis, evaluated_at=NOW.astimezone(offset), broker_snapshot=snapshot(observed_at=NOW.astimezone(offset)))
    assert data.analysis.price_timestamp == NOW
    assert data.analysis.price_timestamp.tzinfo == timezone.utc
    assert data.execution.observed_at.tzinfo == timezone.utc
    assert data.evaluated_at.tzinfo == timezone.utc


def test_unknown_quote_timezone_not_replaced_with_now():
    analysis = scenario().analysis
    analysis.price_timestamp = NOW.replace(tzinfo=None)
    report = validate_snapshot(adapt_strategy_input(analysis, evaluated_at=NOW, market_source="twelve_data"), source="capture")
    assert "analysis.price_timestamp.timezone" in report.missing_inputs
    assert report.input_failure_reasons[0].code == "quote_timezone_missing"


def test_spread_decimal_arithmetic_and_unknown_cost():
    p = provider()
    captured = collect_broker_snapshot(p, as_of=NOW, spot_event=spot())
    data = adapt_strategy_input(scenario().analysis, evaluated_at=NOW, broker_snapshot=captured)
    assert data.execution.spread == .2
    assert data.execution.min_stop_distance == .5
    assert data.execution.min_target_distance == .75
    assert data.execution.additional_round_trip_cost is None
    report = validate_snapshot(data, source="broker")
    assert report.missing_inputs == ["execution.additional_round_trip_cost"]
    assert captured.unavailable
    p.client.send.assert_not_called()
    p.get_symbol_metadata.assert_called_once_with("XAUUSD")


@pytest.mark.parametrize("digits,stop", [(1, 5), (2, .5), (3, .05)])
def test_metadata_points_use_actual_digits(digits, stop):
    p = provider(ProtoOASymbol(symbolId=41, digits=digits, pipPosition=1, slDistance=50, tpDistance=50))
    assert collect_broker_snapshot(p, as_of=NOW, spot_event=spot()).min_stop_distance == stop


def test_missing_and_percentage_constraints_never_become_zero():
    p = provider(ProtoOASymbol(symbolId=41, digits=2, pipPosition=1))
    captured = collect_broker_snapshot(p, as_of=NOW, spot_event=spot())
    assert captured.min_stop_distance is None and captured.min_target_distance is None
    p.get_symbol_metadata.return_value = ProtoOASymbol(symbolId=41, digits=2, pipPosition=1,
        slDistance=1, tpDistance=1, distanceSetIn=Distance.SYMBOL_DISTANCE_IN_PERCENTAGE)
    captured = collect_broker_snapshot(p, as_of=NOW, spot_event=spot())
    assert captured.min_stop_distance is None
    assert any("percentage" in field for field in captured.unavailable)


def test_explicit_zero_constraint_is_known_zero():
    p = provider(ProtoOASymbol(symbolId=41, digits=2, pipPosition=1, slDistance=0, tpDistance=0))
    assert collect_broker_snapshot(p, as_of=NOW, spot_event=spot()).min_stop_distance == 0


@pytest.mark.parametrize("changes", [{"ctidTraderAccountId": 999}, {"symbolId": 999}])
def test_wrong_quote_identity_rejected(changes):
    with pytest.raises(ValueError, match="account/symbol"):
        collect_broker_snapshot(provider(), as_of=NOW, spot_event=spot(**changes))


def test_one_sided_spot_is_not_a_spread():
    event = spot()
    event.ClearField("ask")
    captured = collect_broker_snapshot(provider(), as_of=NOW, spot_event=event)
    assert captured.observed_at is None and captured.bid is None
    assert "quote:" in captured.unavailable[-1]


@pytest.mark.parametrize("changes", [{"environment": "live"}, {"authenticated": False}, {"demo_verified": False}])
def test_collection_requires_existing_verified_demo(changes):
    p = provider()
    for k,v in changes.items(): setattr(p.client, k, v)
    with pytest.raises(ValueError, match="DEMO"):
        collect_broker_snapshot(p, as_of=NOW)
    p.client.send.assert_not_called()
    p.get_symbol_metadata.assert_not_called()


def test_missing_digits_fail_closed():
    p = provider(ProtoOASymbol(symbolId=41, pipPosition=1))
    with pytest.raises(ValueError, match="digits"):
        collect_broker_snapshot(p, as_of=NOW, spot_event=spot())


@pytest.mark.parametrize("quote_kind", ["market", "broker"])
def test_stale_quote_remains_stale_after_adaptation(quote_kind):
    data = scenario()
    old = NOW - timedelta(minutes=5)
    if quote_kind == "market": data.analysis.price_timestamp = old
    captured = snapshot(observed_at=old if quote_kind == "broker" else NOW)
    report = validate_snapshot(adapt_strategy_input(data.analysis, evaluated_at=NOW, broker_snapshot=captured), source="old")
    assert report.outcome == "rejected"
    assert ("quote_stale" if quote_kind == "market" else "execution_stale") in {r.code for r in report.input_failure_reasons}


@pytest.mark.parametrize("changes,code", [({"ask": 4200.9}, "spread_excessive"), ({"min_stop_distance": 2}, "broker_stop_minimum"), ({"min_target_distance": 4}, "broker_target_minimum")])
def test_reliable_broker_values_reach_existing_strategy_checks(changes, code):
    data = adapt_strategy_input(scenario().analysis, evaluated_at=NOW, broker_snapshot=snapshot(**changes))
    report = validate_snapshot(data, source="test")
    assert code in {r.code for r in report.strategy_rejection_reasons}


def test_crossed_quote_rejected():
    with pytest.raises(ValueError, match="ask"):
        adapt_strategy_input(scenario().analysis, evaluated_at=NOW, broker_snapshot=snapshot(ask=4199))


def tick_response(tick, timestamp=None, account=123):
    response = M.ProtoOAGetTickDataRes(ctidTraderAccountId=account, hasMore=False)
    response.tickData.add(tick=tick, timestamp=timestamp if timestamp is not None else int(NOW.timestamp()*1000))
    # Only the newest tick is absolute; later relative timestamps must not be used.
    response.tickData.add(tick=100, timestamp=-1)
    return response


def test_read_only_tick_requests_on_existing_connection():
    p = provider()
    p.client.send.side_effect = [tick_response(419990000), tick_response(420010000)]
    captured = collect_broker_snapshot(p, as_of=NOW)
    assert captured.bid == 4199.9 and captured.ask == 4200.1
    assert captured.observed_at == NOW
    for call in p.client.send.call_args_list:
        request = call.args[0]
        assert isinstance(request, M.ProtoOAGetTickDataReq)
        assert request.symbolId == 41 and request.ctidTraderAccountId == 123
        assert request.toTimestamp - request.fromTimestamp == 60000
    p.client.start.assert_not_called()


def test_asynchronous_ticks_not_fabricated_as_simultaneous_quote():
    p = provider()
    p.client.send.side_effect = [tick_response(419990000), tick_response(420010000, int(NOW.timestamp()*1000)-1)]
    captured = collect_broker_snapshot(p, as_of=NOW)
    assert captured.bid is None and captured.observed_at is None
    assert "matching-timestamp" in captured.unavailable[-1]
    assert captured.min_stop_distance == .5


def test_wrong_tick_account_rejected():
    p = provider()
    p.client.send.return_value = tick_response(419990000, account=999)
    with pytest.raises(ValueError, match="account"):
        collect_broker_snapshot(p, as_of=NOW)


def test_existing_analysis_service_is_reused_without_broker_start():
    service = Mock()
    service.analyze_multi_timeframe.return_value = scenario().analysis
    data, captured = input_from_services(service, evaluated_at=NOW)
    service.analyze_multi_timeframe.assert_called_once_with("XAUUSD")
    assert data.analysis.price == 4200 and captured is None


def test_captured_real_snapshot_normalization_and_honest_rejection():
    raw = json.loads((Path(__file__).parent / "fixtures/quick_trade_xauusd_snapshot.json").read_text())
    result = evaluate_raw(raw, "captured-XAUUSD", replay=True)
    assert result["reference_price"] == 4144.04708
    assert result["evaluation"]["market_timestamp"] == "2026-10-19T19:09:30.567742Z"
    assert result["evaluation"]["candle_timestamp"] == "2026-10-19T19:05:00Z"
    assert not any("timezone" in item for item in result["missing_inputs"])
    assert result["outcome"] == "incomplete_input"
    assert result["strategy_rejection_reasons"][0]["code"] == "reaction_evidence_missing"
    assert result["evaluation"]["proposal"] is None


def test_provider_normalizes_known_utc_candles_at_source():
    response = Mock()
    response.json.return_value = {"values": [dict(datetime="2026-10-19T10:00:00", open="1", high="2", low="1", close="2")]}
    with patch("app.providers.twelve_data_provider.httpx.get", return_value=response) as get:
        candle = TwelveDataProvider("mock-key").get_candles("XAUUSD", "5min", 1)[0]
    assert candle.timestamp == NOW
    assert candle.timestamp.tzinfo == timezone.utc
    assert get.call_args.kwargs["params"]["timezone"] == "UTC"


def test_snapshot_cli_with_verified_evidence(tmp_path, capsys):
    raw = scenario().model_dump(mode="json")
    raw.pop("execution")
    path = tmp_path / "market.json"
    path.write_text(json.dumps(raw))
    broker_path = tmp_path / "broker.json"
    broker_path.write_text(snapshot().model_dump_json())
    report = main(["--snapshots", str(path), "--broker-snapshot", str(broker_path)])[0]
    assert report["outcome"] == "eligible"
    assert report["execution_observations"]["spread"] == .2
    assert report["broker_observations"]["source"] == "captured-demo"
