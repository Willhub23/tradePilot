from datetime import timezone
import json
from unittest.mock import patch
from uuid import UUID

import pytest

from app.strategies.validation import validate_snapshot
from scripts.validate_quick_trade import captured_inputs, evaluate_raw, main
from tests.test_quick_trade_strategy import scenario


def test_eligible_observation_has_identity_utc_timestamp_and_monotonic_timing():
    data = scenario()
    original = data.model_dump_json()
    with patch("app.strategies.validation.perf_counter_ns", side_effect=[1000000, 3500000]):
        report = validate_snapshot(data, source="synthetic", replay=True)
    assert report.outcome == "eligible"
    assert UUID(report.opportunity_id)
    assert report.detected_at.utcoffset().total_seconds() == 0
    assert report.timing.strategy_evaluation_ms == 2.5
    assert report.reference_price == data.analysis.price
    assert report.evaluation.market_timestamp == data.analysis.price_timestamp
    assert report.evaluation.evaluated_at == data.evaluated_at
    assert report.evaluation.proposal.stop_loss == 4198.35
    assert report.timing.ai_reasoning_started_at is None
    assert report.timing.ai_reasoning_completed_at is None
    assert report.timing.broker_submitted_at is None
    assert report.timing.fill_confirmed_at is None
    assert data.model_dump_json() == original


def test_missing_execution_separate_from_genuine_rejection():
    data = scenario(timeframe_changes={"atr": 8}).model_copy(update={"execution": None})
    report = validate_snapshot(data, source="capture")
    assert report.outcome == "incomplete_input"
    assert "execution.spread" in report.missing_inputs
    assert "execution.min_stop_distance" in report.missing_inputs
    assert report.evaluation.status == "rejected"
    assert report.evaluation.rejection_reasons
    assert report.opportunity_id is None and report.detected_at is None


def test_complete_inputs_can_be_genuinely_rejected():
    report = validate_snapshot(scenario(timeframe_changes={"atr": 8}), source="synthetic")
    assert report.outcome == "rejected"
    assert not report.missing_inputs
    assert report.evaluation.rejection_reasons


def test_naive_candle_timezone_is_reported_not_guessed():
    data = scenario()
    data.analysis.timeframes[0].latest_candle_timestamp = data.evaluated_at.replace(tzinfo=None)
    report = validate_snapshot(data, source="capture")
    assert report.outcome == "incomplete_input"
    assert "analysis.5min.latest_candle_timestamp.timezone" in report.missing_inputs
    assert report.evaluation.candle_timestamp is None
    assert report.timeframe_observations["atr"] == 1.5
    assert report.timeframe_observations["latest_candle_timestamp"] == "2026-10-19T10:00:00"


@pytest.mark.parametrize("field", ["spread", "additional_round_trip_cost", "min_stop_distance", "min_target_distance"])
def test_individual_execution_gaps(field):
    report = validate_snapshot(scenario(execution_changes={field: None}), source="capture")
    assert report.outcome == "incomplete_input"
    assert report.missing_inputs == ["execution." + field]


def test_missing_analysis():
    report = validate_snapshot(scenario().model_copy(update={"analysis": None}), source="capture")
    assert report.missing_inputs == ["analysis"]
    assert report.evaluation.proposal is None


def test_snapshot_json_and_log_replay(tmp_path):
    raw = scenario().model_dump(mode="json")
    json_path = tmp_path / "snapshot.json"
    json_path.write_text(json.dumps(raw))
    loaded = list(captured_inputs(json_path, 2))
    result = evaluate_raw(loaded[0], "json", replay=True)
    assert result["outcome"] == "eligible"
    log = tmp_path / "runtime.log"
    line = "2026-10-19 10:00:00,000Z INFO service Agent market context " + json.dumps({"market_analysis": raw["analysis"]})
    log.write_text("ignored other log\n" + line + "\n" + line)
    loaded = list(captured_inputs(log, 1))
    assert len(loaded) == 1
    assert loaded[0]["evaluated_at"].tzinfo == timezone.utc
    result = evaluate_raw(loaded[0], "log", replay=True)
    assert result["outcome"] == "incomplete_input"
    assert result["evaluation"]["evaluated_at"].startswith("2026-10-19T10:00:00")


def test_old_replay_does_not_refresh_execution_evidence():
    raw = scenario(execution_changes={"observed_at": "2020-01-01T00:00:00Z"}).model_dump(mode="json")
    report = evaluate_raw(raw, "capture", replay=True)
    assert report["outcome"] == "rejected"
    assert "execution_stale" in {r["code"] for r in report["evaluation"]["rejection_reasons"]}


def test_malformed_capture_is_input_error():
    report = evaluate_raw({"symbol": "XAUUSD", "price": 4200}, "incomplete-json", replay=True)
    assert report["outcome"] == "invalid_input"
    assert "input_error" in report
    assert "evaluation" not in report


def test_current_uses_existing_get_endpoint_without_runtime_imports(capsys):
    with patch("scripts.validate_quick_trade.httpx.get") as get:
        get.return_value.json.return_value = scenario().analysis.model_dump(mode="json")
        reports = main(["--current"])
    get.assert_called_once_with("http://127.0.0.1:8000/api/market/XAUUSD/multi-timeframe", timeout=90)
    assert reports[0]["outcome"] == "incomplete_input"
    assert json.loads(capsys.readouterr().out)[0]["reference_price"] == 4200


def test_current_unavailable_is_not_strategy_rejection(capsys):
    import httpx
    with patch("scripts.validate_quick_trade.httpx.get", side_effect=httpx.ConnectError("offline")):
        reports = main(["--current"])
    assert reports[0]["outcome"] == "data_unavailable"
    assert "evaluation" not in reports[0]


def test_workflow_has_no_database_or_network_side_effects():
    with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
         patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("database forbidden")):
        assert validate_snapshot(scenario(), source="synthetic").outcome == "eligible"
