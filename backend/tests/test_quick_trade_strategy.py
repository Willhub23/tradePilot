from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.models.market_models import MultiTimeframeAnalysis, TimeframeAnalysis
from app.strategies import QuickTradeConfig, QuickTradeStrategy
from app.strategies.models import ExecutionEvidence, StrategyInput
from tests.market_fixtures import market_structure_fields

NOW = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)


def scenario(direction="long", *, timeframe_changes=None, market_changes=None, execution_changes=None, price_action_changes=None):
    long = direction == "long"
    fields = market_structure_fields(4200)
    fields.update(structure="bullish" if long else "bearish",
        high_pattern="higher_high" if long else "lower_high", low_pattern="higher_low" if long else "lower_low",
        support=4198.5 if long else 4196, resistance=4204 if long else 4201.5,
        ema_9=4199.8 if long else 4200.2, ema_20_slope="rising" if long else "falling",
        ema_50_slope="rising" if long else "falling", market_regime="trending_bullish" if long else "trending_bearish")
    fields.update(interval="5min", latest_candle_timestamp=NOW - timedelta(minutes=1),
        ema_20=4199.4 if long else 4200.6, ema_50=4199 if long else 4201,
        rsi=60 if long else 40, atr=1.5, score=65 if long else 35,
        bias="bullish" if long else "bearish", trend_direction="bullish" if long else "bearish",
        trend_strength=0.8, trend_strength_label="strong")
    # Synthetic latest-candle rejection is explicit pullback/reaction evidence.
    fields["price_action"]["rejection"] = "bullish_rejection" if long else "bearish_rejection"
    fields["price_action"].update(price_action_changes or {})
    fields.update(timeframe_changes or {})
    timeframe = TimeframeAnalysis(**fields)
    market = MultiTimeframeAnalysis(symbol="XAUUSD", price=4200, price_timestamp=NOW,
        timeframes=[timeframe], overall_score=65, overall_bias=timeframe.bias, timeframe_alignment="aligned")
    if market_changes:
        market = market.model_copy(update=market_changes, deep=True)
    evidence = dict(observed_at=NOW, source="synthetic broker snapshot", spread=.2,
                    additional_round_trip_cost=.1, min_stop_distance=.5, min_target_distance=.5)
    evidence.update(execution_changes or {})
    return StrategyInput(evaluated_at=NOW, analysis=market, execution=ExecutionEvidence(**evidence))


def codes(result):
    return {reason.code for reason in result.rejection_reasons}


def measured(result, signal):
    return next(item.values for item in result.supporting_signals if item.name == signal)


@pytest.mark.parametrize("direction,stop,target", [("long", 4198.35, 4203), ("short", 4201.65, 4197)])
def test_directional_short_term_opportunity(direction, stop, target):
    result = QuickTradeStrategy().evaluate(scenario(direction))
    assert result.status == "eligible"
    assert result.strategy_id == "quick_trade_v1"
    assert result.setup_type == "trend_pullback_continuation"
    assert result.invalidation_level == (4198.5 if direction == "long" else 4201.5)
    assert result.invalidation_condition and result.decision_reason
    assert result.proposal.direction == direction
    assert result.proposal.entry_reference == 4200
    assert result.proposal.stop_loss == pytest.approx(stop)
    assert result.proposal.take_profit == target
    assert result.proposal.stop_distance == 1.65
    assert result.proposal.target_distance == 3
    assert result.proposal.max_holding_minutes == 30
    assert result.proposal.estimated_round_trip_cost == .3
    assert result.proposal.net_reward_risk == pytest.approx(2.7 / 1.95)
    assert result.timeframe == "5min" and result.market_timestamp == NOW
    assert result.candle_timestamp == NOW - timedelta(minutes=1)
    assert not result.rejection_reasons and not result.conflicting_signals
    assert measured(result, "ema_momentum")["ema_20_slope"] == ("rising" if direction == "long" else "falling")


def test_range_without_reaction_is_rejected_without_inventing_direction():
    result = QuickTradeStrategy().evaluate(scenario(timeframe_changes={"structure": "mixed", "market_regime": "ranging"},
        price_action_changes={"rejection": "none"}))
    assert result.status == "rejected" and result.proposal is None
    assert "reaction_evidence_missing" in codes(result)
    assert result.conflicting_signals[0].values["structure"] == "mixed"


@pytest.mark.parametrize("change", [
    {"ema_20_slope": "falling"}, {"ema_50_slope": "flat"}, {"ema_9": 4199.2},
    {"ema_50": 4201}, {"trend_direction": "bearish"},
])
def test_conflicting_momentum_is_measured_without_universal_rejection(change):
    result = QuickTradeStrategy().evaluate(scenario(timeframe_changes=change))
    assert result.status == "eligible"
    assert "momentum_conflict" not in codes(result)
    conflict = next(s for s in result.conflicting_signals if s.name == "ema_momentum")
    for field, value in change.items(): assert conflict.values[field] == value


@pytest.mark.parametrize("direction,change", [
    ("long", {"structure_break": "break_below"}), ("short", {"structure_break": "break_above"}),
])
def test_counter_break_that_does_not_hold_is_rejected_specifically(direction, change):
    result = QuickTradeStrategy().evaluate(scenario(direction, timeframe_changes=change))
    assert "breakout_not_holding" in codes(result) and result.proposal is None


def test_ranging_market_is_not_automatically_rejected():
    result = QuickTradeStrategy().evaluate(scenario(timeframe_changes={"market_regime": "ranging"}))
    assert result.status == "eligible" and result.setup_type == "range_support_bounce"


def test_missing_analysis_has_specific_reason():
    result = QuickTradeStrategy().evaluate(StrategyInput(evaluated_at=NOW))
    assert codes(result) == {"market_analysis_missing"}
    assert result.market_timestamp is None and result.proposal is None


@pytest.mark.parametrize("field,value,code", [
    ("support", None, "setup_anchor_missing"), ("resistance", None, "opposing_structure_missing"),
    ("atr", 0, "required_measurement_invalid"), ("atr", float("nan"), "required_measurement_invalid"),
])
def test_missing_or_invalid_required_measurements(field, value, code):
    result = QuickTradeStrategy().evaluate(scenario(timeframe_changes={field: value}))
    assert code in codes(result)
    assert result.proposal is None


@pytest.mark.parametrize("field,value", [("ema_20", float("inf")), ("ema_9", -1)])
def test_unavailable_indicator_does_not_destroy_structural_reaction(field, value):
    result = QuickTradeStrategy().evaluate(scenario(timeframe_changes={field: value, "market_regime": "ranging"}))
    assert result.status == "eligible" and result.setup_type == "range_support_bounce"
    assert any(signal.name == "ema_momentum" for signal in result.conflicting_signals)


@pytest.mark.parametrize("mode", ["missing", "higher_only", "duplicate"])
def test_no_timeframe_fallback_or_ambiguous_selection(mode):
    data = scenario()
    timeframe = data.analysis.timeframes[0]
    rows = [] if mode == "missing" else [timeframe.model_copy(update={"interval": "1h"})] if mode == "higher_only" else [timeframe, timeframe]
    market = data.analysis.model_copy(update={"timeframes": rows})
    result = QuickTradeStrategy().evaluate(data.model_copy(update={"analysis": market}))
    assert "timeframe_missing_or_ambiguous" in codes(result)
    assert result.timeframe == "5min" and result.proposal is None


def test_non_xauusd_snapshot_rejected():
    assert "unsupported_symbol" in codes(QuickTradeStrategy().evaluate(scenario(market_changes={"symbol": "EURUSD"})))


@pytest.mark.parametrize("change", [{"atr": 4}, {"atr": .4}, {"atr_ratio": 2}, {"volatility_regime": "high"}])
def test_unsuitable_volatility(change):
    result = QuickTradeStrategy().evaluate(scenario(timeframe_changes=change))
    assert "volatility_unsuitable" in codes(result)
    assert result.proposal is None
    values = next(s.values for s in result.conflicting_signals if s.name == "volatility")
    if "atr" in change: assert values["atr"] == change["atr"]


def test_missing_atr_baseline_not_fabricated():
    result = QuickTradeStrategy().evaluate(scenario(timeframe_changes={"atr_ratio": None, "volatility_regime": "uncertain"}))
    assert result.status == "eligible"
    assert measured(result, "volatility")["atr_ratio"] is None


@pytest.mark.parametrize("ratio", [0, -1, float("nan")])
def test_invalid_available_atr_ratio_rejected(ratio):
    assert "atr_ratio_invalid" in codes(QuickTradeStrategy().evaluate(scenario(timeframe_changes={"atr_ratio": ratio})))


@pytest.mark.parametrize("direction,change,level", [
    ("long", {"resistance": 4201}, "resistance"), ("short", {"support": 4199}, "support"),
])
def test_nearby_opposing_structure_limits_target(direction, change, level):
    result = QuickTradeStrategy().evaluate(scenario(direction, timeframe_changes=change))
    assert f"nearby_{level}" in codes(result)
    assert "target_below_minimum" in codes(result)
    assert result.proposal is None
    assert measured(result, "candidate_levels")["target_distance"] == .85


def test_stop_too_close_not_clamped_to_minimum():
    result = QuickTradeStrategy().evaluate(scenario(timeframe_changes={"atr": .8, "support": 4199.8}))
    assert "stop_below_minimum" in codes(result)
    assert measured(result, "candidate_levels")["stop_distance"] == .8
    assert result.proposal is None


def test_stop_too_far_not_clamped_to_maximum():
    result = QuickTradeStrategy(QuickTradeConfig(max_structure_distance_atr=5)).evaluate(scenario(timeframe_changes={"support": 4194}))
    reason = next(r for r in result.rejection_reasons if r.code == "stop_above_maximum")
    assert (reason.measured, reason.limit) == (6.15, 5)
    assert "$6.15" in reason.message and result.proposal is None


def test_atr_target_above_bound_not_forced_to_five():
    result = QuickTradeStrategy().evaluate(scenario(timeframe_changes={"atr": 2.6, "resistance": 4210}))
    assert "target_above_maximum" in codes(result)
    assert measured(result, "candidate_levels")["target_distance"] == 5.2
    assert result.proposal is None


def test_atr_adapts_candidate_levels():
    strategy = QuickTradeStrategy()
    normal = strategy.evaluate(scenario()).proposal
    higher = strategy.evaluate(scenario(timeframe_changes={"atr": 2})).proposal
    assert normal is not None and higher is not None
    assert (normal.stop_distance, normal.target_distance) == (1.65, 3)
    assert (higher.stop_distance, higher.target_distance) == (2, 3.8)


def test_target_sits_before_actual_resistance():
    strategy = QuickTradeStrategy(QuickTradeConfig(min_net_reward_risk=1))
    result = strategy.evaluate(scenario(timeframe_changes={"resistance": 4202.5}))
    assert result.status == "eligible"
    assert result.proposal.take_profit == 4202.35
    assert result.proposal.target_distance == 2.35


@pytest.mark.parametrize("change,code", [({"resistance": 4199}, "price_outside_observed_range"),
    ({"support": 4205, "resistance": 4204}, "structure_levels_invalid")])
def test_stale_or_unbracketed_structure_rejected(change, code):
    assert code in codes(QuickTradeStrategy().evaluate(scenario(timeframe_changes=change)))


def test_ema_extension_only_changes_setup_classification():
    result = QuickTradeStrategy().evaluate(scenario(timeframe_changes={"ema_9": 4199, "ema_20": 4198, "ema_50": 4197}))
    assert result.status == "eligible" and result.setup_type == "range_support_bounce"
    values = measured(result, "ema_momentum")
    assert values["distance_to_ema20_atr"] == pytest.approx(2 / 1.5)


@pytest.mark.parametrize("change,code", [
    ({"spread": .6}, "spread_excessive"), ({"additional_round_trip_cost": 1}, "execution_cost_excessive"),
    ({"min_stop_distance": 1.66}, "broker_stop_minimum"), ({"min_target_distance": 3.01}, "broker_target_minimum"),
])
def test_execution_cost_or_broker_constraints_reject_without_adjusting_levels(change, code):
    result = QuickTradeStrategy().evaluate(scenario(execution_changes=change))
    assert code in codes(result) and result.proposal is None
    candidate = measured(result, "candidate_levels")
    assert candidate["stop_distance"] == 1.65 and candidate["target_distance"] == 3


def test_net_reward_risk_is_conservative_and_explicit():
    result = QuickTradeStrategy(QuickTradeConfig(min_net_reward_risk=1.5)).evaluate(scenario())
    reason = next(r for r in result.rejection_reasons if r.code == "net_reward_risk_insufficient")
    assert reason.measured == pytest.approx((3 - .3) / (1.65 + .3))
    assert reason.limit == 1.5


def test_no_execution_evidence_means_rejection_not_zero_cost():
    data = scenario().model_copy(update={"execution": None})
    assert "execution_evidence_missing" in codes(QuickTradeStrategy().evaluate(data))


@pytest.mark.parametrize("field", ["spread", "additional_round_trip_cost", "min_stop_distance", "min_target_distance"])
def test_unknown_execution_measurement_not_assumed_zero(field):
    result = QuickTradeStrategy().evaluate(scenario(execution_changes={field: None}))
    reason = next(r for r in result.rejection_reasons if r.code == "execution_measurement_missing")
    assert reason.measured == field
    assert result.proposal is None


def test_explicitly_known_zero_costs_and_limits_are_supported():
    result = QuickTradeStrategy().evaluate(scenario(execution_changes={"spread": 0, "additional_round_trip_cost": 0,
        "min_stop_distance": 0, "min_target_distance": 0}))
    assert result.status == "eligible" and result.proposal.estimated_round_trip_cost == 0


@pytest.mark.parametrize("name,seconds,code", [
    ("quote", -61, "quote_stale"), ("quote", 1, "quote_from_future"),
    ("candle", -601, "candle_stale"), ("candle", 1, "candle_from_future"),
    ("execution", -61, "execution_stale"), ("execution", 1, "execution_from_future"),
])
def test_temporal_evidence_guards(name, seconds, code):
    timestamp = NOW + timedelta(seconds=seconds)
    kwargs = {"market_changes": {"price_timestamp": timestamp}} if name == "quote" else (
        {"timeframe_changes": {"latest_candle_timestamp": timestamp}} if name == "candle" else {"execution_changes": {"observed_at": timestamp}})
    result = QuickTradeStrategy().evaluate(scenario(**kwargs))
    assert code in codes(result) and result.proposal is None


def test_naive_analysis_timestamp_is_specific_rejection():
    result = QuickTradeStrategy().evaluate(scenario(market_changes={"price_timestamp": NOW.replace(tzinfo=None)}))
    assert "quote_timezone_missing" in codes(result)
    assert result.market_timestamp is None


def test_selected_timeframe_config_is_used():
    strategy = QuickTradeStrategy(QuickTradeConfig(timeframe="15min", max_holding_minutes=20))
    result = strategy.evaluate(scenario(timeframe_changes={"interval": "15min"}))
    assert result.status == "eligible" and result.timeframe == "15min"
    assert result.proposal.max_holding_minutes == 20


@pytest.mark.parametrize("changes", [
    {"min_stop_distance": 6}, {"min_target_distance": 6}, {"min_atr": 4},
    {"max_holding_minutes": 31}, {"max_atr": float("inf")}, {"timeframe": "1h"},
    {"max_cost_fraction_of_target": 1},
])
def test_invalid_research_configuration(changes):
    with pytest.raises(ValidationError): QuickTradeConfig(**changes)


def test_repeated_evaluation_has_no_state_mutation_or_external_io():
    strategy, data = QuickTradeStrategy(), scenario()
    before = data.model_dump_json()
    config_before = strategy.config.model_dump_json()
    with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")), patch(
        "sqlalchemy.engine.Engine.connect", side_effect=AssertionError("database forbidden")):
        first = strategy.evaluate(data)
        second = strategy.evaluate(data)
    assert first.model_dump_json() == second.model_dump_json()
    assert data.model_dump_json() == before
    assert strategy.config.model_dump_json() == config_before
    assert first.evaluated_at == NOW


@pytest.mark.parametrize("changes", [{"spread": -1}, {"spread": float("nan")},
    {"additional_round_trip_cost": float("inf")}, {"min_stop_distance": -1}, {"source": "  "}])
def test_malformed_execution_evidence_cannot_be_treated_as_reliable(changes):
    with pytest.raises(ValidationError): scenario(execution_changes=changes)


def test_exact_broker_minimum_is_permitted():
    result = QuickTradeStrategy().evaluate(scenario(execution_changes={"min_stop_distance": 1.65, "min_target_distance": 3}))
    assert result.status == "eligible"


def test_exact_cost_fraction_limit_is_permitted():
    result = QuickTradeStrategy(QuickTradeConfig(min_net_reward_risk=1)).evaluate(
        scenario(execution_changes={"additional_round_trip_cost": .4}))
    assert result.status == "eligible" and result.proposal.estimated_round_trip_cost == .6


@pytest.mark.parametrize("changes,signal,value,forbidden", [
    ({"support": 4198.65}, "stop_distance", 1.5, "stop_below_minimum"),
    ({"atr": 2.5, "resistance": 4206}, "target_distance", 5, "target_above_maximum"),
    ({"atr": 1, "support": 4198.6}, "target_distance", 2, "target_below_minimum"),
    ({"support": 4195.15}, "stop_distance", 5, "stop_above_maximum"),
])
def test_dollar_bounds_are_inclusive_without_float_cancellation(changes, signal, value, forbidden):
    result = QuickTradeStrategy(QuickTradeConfig(max_structure_distance_atr=4)).evaluate(scenario(timeframe_changes=changes))
    assert measured(result, "candidate_levels")[signal] == value
    assert forbidden not in codes(result)


def test_non_utc_input_timestamps_refer_to_the_same_instant():
    offset = NOW.astimezone(timezone(timedelta(hours=2)))
    result = QuickTradeStrategy().evaluate(scenario(market_changes={"price_timestamp": offset}))
    assert result.status == "eligible" and result.market_timestamp == NOW


def test_result_evidence_lists_do_not_leak_across_evaluations():
    strategy = QuickTradeStrategy()
    first = strategy.evaluate(scenario())
    first.supporting_signals.clear()
    second = strategy.evaluate(scenario())
    assert second.status == "eligible" and second.supporting_signals


def range_scenario(direction="long", **kwargs):
    changes = dict(structure="mixed", trend_direction="neutral", market_regime="ranging",
                   high_pattern="equal_high", low_pattern="equal_low", ema_20_slope="flat",
                   ema_50_slope="falling" if direction == "long" else "rising")
    changes.update(kwargs.pop("timeframe_changes", {}))
    return scenario(direction, timeframe_changes=changes, **kwargs)


def breakout_scenario(direction="long", *, opposing=True, **kwargs):
    long = direction == "long"
    changes = dict(structure_break="break_above" if long else "break_below",
                   support=4196 if long else 4201.5, resistance=4198.5 if long else 4204)
    changes.update(kwargs.pop("timeframe_changes", {}))
    action = dict(rejection="none", displacement_detected=True,
                  displacement_direction="bullish" if long else "bearish", displacement_strength=1.2)
    action.update(kwargs.pop("price_action_changes", {}))
    data = scenario(direction, timeframe_changes=changes, price_action_changes=action, **kwargs)
    if opposing:
        higher = data.analysis.timeframes[0].model_copy(update={"interval": "15min", "support": 4196,
            "resistance": 4204, "latest_candle_timestamp": NOW - timedelta(minutes=10)})
        data = data.model_copy(update={"analysis": data.analysis.model_copy(
            update={"timeframes": [data.analysis.timeframes[0], higher]})})
    return data


@pytest.mark.parametrize("direction,kind,anchor", [
    ("long", "range_support_bounce", 4198.5), ("short", "range_resistance_rejection", 4201.5),
])
def test_valid_range_entries_use_actual_boundary_and_rejection(direction, kind, anchor):
    data = range_scenario(direction)
    result = QuickTradeStrategy().evaluate(data)
    assert result.status == "eligible" and result.setup_type == kind
    assert result.proposal.direction == direction
    assert result.invalidation_level == anchor
    assert str(anchor) in result.invalidation_condition
    assert ("below" if direction == "long" else "above") in result.invalidation_condition
    assert measured(result, "structural_reaction")["rejection"] == ("bullish_rejection" if direction == "long" else "bearish_rejection")
    assert any(s.name == "ema_momentum" for s in result.conflicting_signals)
    assert result.proposal.max_holding_minutes == 30


@pytest.mark.parametrize("direction", ["long", "short"])
def test_pullback_does_not_require_full_hh_hl_or_ema_alignment(direction):
    data = scenario(direction, timeframe_changes={"high_pattern": "equal_high", "low_pattern": "equal_low",
        "ema_50_slope": "flat", "ema_9": 4200, "structure": "mixed"})
    result = QuickTradeStrategy().evaluate(data)
    assert result.status == "eligible" and result.setup_type == "trend_pullback_continuation"
    assert any(s.name == "ema_momentum" for s in result.conflicting_signals)
    assert any(s.name == "structure_context" for s in result.conflicting_signals)


@pytest.mark.parametrize("direction,anchor", [("long", 4198.5), ("short", 4201.5)])
def test_observed_breakout_uses_broken_level_and_supplied_next_structure(direction, anchor):
    data = breakout_scenario(direction)
    result = QuickTradeStrategy().evaluate(data)
    assert result.status == "eligible" and result.setup_type == "breakout"
    assert result.proposal.direction == direction
    assert result.proposal.stop_distance == 1.65 and result.proposal.target_distance == 3
    assert result.invalidation_level == anchor
    assert "broken level" in result.invalidation_condition
    assert measured(result, "breakout_confirmation")["displacement_strength"] == 1.2
    reference = measured(result, "structural_references")
    assert reference["anchor_timeframe"] == "5min" and reference["opposing_timeframe"] == "15min"
    assert reference["opposing_level"] == (4204 if direction == "long" else 4196)


def test_breakout_can_override_opposing_ema_evidence_but_records_it():
    result = QuickTradeStrategy().evaluate(breakout_scenario(timeframe_changes={
        "ema_9": 4201, "ema_20": 4202, "ema_50": 4203, "ema_20_slope": "falling", "ema_50_slope": "falling",
        "structure": "bearish", "trend_direction": "bearish", "high_pattern": "lower_high", "low_pattern": "lower_low"}))
    assert result.status == "eligible" and result.setup_type == "breakout"
    assert any(s.name == "ema_momentum" for s in result.conflicting_signals)


@pytest.mark.parametrize("action", [
    {"displacement_detected": False}, {"displacement_direction": "bearish"},
    {"displacement_strength": 0}, {"displacement_strength": float("nan")},
])
def test_break_marker_without_matching_observed_displacement_rejected(action):
    result = QuickTradeStrategy().evaluate(breakout_scenario(price_action_changes=action))
    assert result.setup_type == "breakout" and result.proposal is None
    assert "breakout_displacement_missing" in codes(result)
    assert result.decision_reason


@pytest.mark.parametrize("direction", ["long", "short"])
def test_breakout_without_known_opposing_structure_does_not_extrapolate_target(direction):
    result = QuickTradeStrategy().evaluate(breakout_scenario(direction, opposing=False))
    assert result.setup_type == "breakout" and result.proposal is None
    assert "opposing_structure_missing" in codes(result)
    assert not any(s.name == "candidate_levels" for s in result.supporting_signals)


def test_nearest_supplied_opposing_level_cannot_be_skipped_for_a_bigger_target():
    data = breakout_scenario()
    selected, higher = data.analysis.timeframes
    near = higher.model_copy(update={"resistance": 4201})
    far = higher.model_copy(update={"interval": "1h", "resistance": 4210})
    data = data.model_copy(update={"analysis": data.analysis.model_copy(update={"timeframes": [selected, near, far]})})
    result = QuickTradeStrategy().evaluate(data)
    assert result.proposal is None and "nearby_resistance" in codes(result)
    assert measured(result, "structural_references")["opposing_level"] == 4201


@pytest.mark.parametrize("time", [NOW - timedelta(hours=2), NOW + timedelta(seconds=1), NOW.replace(tzinfo=None)])
def test_breakout_does_not_use_stale_or_future_or_untimed_opposing_levels(time):
    data = breakout_scenario()
    selected, higher = data.analysis.timeframes
    higher = higher.model_copy(update={"latest_candle_timestamp": time})
    data = data.model_copy(update={"analysis": data.analysis.model_copy(update={"timeframes": [selected, higher]})})
    assert "opposing_structure_missing" in codes(QuickTradeStrategy().evaluate(data))


def test_latest_broken_level_is_required_for_breakout_anchor():
    result = QuickTradeStrategy().evaluate(breakout_scenario(timeframe_changes={"resistance": None}))
    assert result.setup_type == "breakout" and "breakout_level_missing" in codes(result)
    assert result.proposal is None


def test_breakout_extension_uses_distance_from_broken_structure_not_ema():
    result = QuickTradeStrategy().evaluate(breakout_scenario(timeframe_changes={"resistance": 4197}))
    reason = next(r for r in result.rejection_reasons if r.code == "breakout_overextended")
    assert reason.measured == 2 and reason.limit == 1


@pytest.mark.parametrize("factory", [scenario, range_scenario, breakout_scenario])
def test_all_setup_types_preserve_volatility_and_execution_guards(factory):
    high = QuickTradeStrategy().evaluate(factory(timeframe_changes={"volatility_regime": "high"}))
    costly = QuickTradeStrategy().evaluate(factory(execution_changes={"additional_round_trip_cost": 1}))
    assert "volatility_unsuitable" in codes(high) and high.proposal is None
    assert "execution_cost_excessive" in codes(costly) and costly.proposal is None


@pytest.mark.parametrize("factory", [scenario, range_scenario, breakout_scenario])
def test_all_setups_preserve_bounds_broker_minimum_and_missing_execution_rejections(factory):
    result = QuickTradeStrategy().evaluate(factory())
    assert result.status == "eligible"
    assert 1.5 <= result.proposal.stop_distance <= 5 and 2 <= result.proposal.target_distance <= 5
    too_close = QuickTradeStrategy().evaluate(factory(execution_changes={"min_stop_distance": 2}))
    assert "broker_stop_minimum" in codes(too_close) and too_close.proposal is None
    data = factory().model_copy(update={"execution": None})
    assert "execution_evidence_missing" in codes(QuickTradeStrategy().evaluate(data))


def test_aligned_indicators_alone_are_not_an_entry():
    result = QuickTradeStrategy().evaluate(scenario(price_action_changes={"rejection": "none"}))
    assert "reaction_evidence_missing" in codes(result)
    assert result.setup_type is None and result.proposal is None


def test_rejection_in_middle_of_wide_range_is_not_a_structural_entry():
    result = QuickTradeStrategy().evaluate(range_scenario(timeframe_changes={"support": 4180, "resistance": 4220}))
    assert "reaction_too_far_from_level" in codes(result)
    assert "not a nearby structural entry" in result.decision_reason
    assert result.proposal is None


def test_touch_at_support_can_be_valid_with_observed_rejection():
    result = QuickTradeStrategy().evaluate(range_scenario(timeframe_changes={"support": 4200}))
    assert result.status == "eligible" and result.setup_type == "range_support_bounce"
    assert result.proposal.stop_distance == 1.5


def test_counter_displacement_invalidates_the_specific_rejection_setup():
    result = QuickTradeStrategy().evaluate(range_scenario(price_action_changes={
        "displacement_detected": True, "displacement_direction": "bearish", "displacement_strength": 1.2}))
    assert "reaction_counter_displacement" in codes(result)
    assert result.setup_type == "range_support_bounce" and result.proposal is None


def test_counter_rejection_invalidates_a_breakout():
    result = QuickTradeStrategy().evaluate(breakout_scenario(price_action_changes={"rejection": "bearish_rejection"}))
    assert "breakout_counter_rejection" in codes(result)
    assert result.proposal is None


def test_multiple_timeframe_scan_preserves_input_and_result_timestamps():
    data = breakout_scenario()
    before = data.model_dump_json()
    result = QuickTradeStrategy().evaluate(data)
    assert data.model_dump_json() == before
    assert result.evaluated_at == NOW and result.market_timestamp == NOW
    assert result.candle_timestamp == NOW - timedelta(minutes=1)
    assert result.strategy_id == "quick_trade_v1"


def test_structure_proximity_boundary_has_no_float_cancellation():
    result = QuickTradeStrategy(QuickTradeConfig(min_stop_distance=1)).evaluate(
        range_scenario(timeframe_changes={"atr": 1.1, "support": 4198.9}))
    assert result.status == "eligible"
    assert measured(result, "structural_reaction")["distance_to_reaction_level_atr"] == 1


def test_breakout_extension_boundary_has_no_float_cancellation():
    result = QuickTradeStrategy().evaluate(breakout_scenario(timeframe_changes={"atr": 1.1, "resistance": 4198.9}))
    assert "breakout_overextended" not in codes(result)
    assert measured(result, "breakout_confirmation")["distance_from_broken_level_atr"] == 1


def test_missing_candle_reaction_is_not_synthesized_from_indicators():
    data = scenario()
    selected = data.analysis.timeframes[0].model_copy(update={"price_action": None})
    data = data.model_copy(update={"analysis": data.analysis.model_copy(update={"timeframes": [selected]})})
    result = QuickTradeStrategy().evaluate(data)
    assert "reaction_evidence_missing" in codes(result) and result.proposal is None


def test_displacement_alone_is_not_a_structural_break():
    result = QuickTradeStrategy().evaluate(scenario(price_action_changes={"rejection": "none", "displacement_detected": True,
        "displacement_direction": "bullish", "displacement_strength": 1.5}))
    assert result.setup_type is None and "reaction_evidence_missing" in codes(result)
