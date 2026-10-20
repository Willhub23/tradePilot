"""Read-only instrumentation around the deterministic strategy; no runtime imports."""

from datetime import datetime, timezone
from time import perf_counter_ns
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, Field

from app.strategies.models import StrategyEvaluation, StrategyInput, StrategyRejection
from app.strategies.quick_trade import QuickTradeStrategy

_INPUT_FAILURE_CODES = {
    "market_analysis_missing",
    "timeframe_missing_or_ambiguous",
    "required_measurement_invalid",
    "atr_ratio_invalid",
    "structure_levels_invalid",
    "opposing_structure_missing",
    "breakout_level_missing",
    "setup_anchor_missing",
    "execution_evidence_missing",
    "execution_measurement_missing",
    *(
        f"{name}_{issue}"
        for name in ("quote", "candle", "execution")
        for issue in ("timezone_missing", "from_future", "stale")
    ),
}


class LifecycleTiming(BaseModel):
    strategy_evaluation_ms: float = Field(ge=0)
    ai_reasoning_started_at: AwareDatetime | None = None
    ai_reasoning_completed_at: AwareDatetime | None = None
    broker_submitted_at: AwareDatetime | None = None
    fill_confirmed_at: AwareDatetime | None = None


class ValidationReport(BaseModel):
    source: str
    replay: bool
    outcome: str
    missing_inputs: list[str]
    input_failure_reasons: list[StrategyRejection]
    strategy_rejection_reasons: list[StrategyRejection]
    price_consistency: dict | None = None
    reference_price: float | None
    timeframe_observations: dict | None = None
    execution_observations: dict | None = None
    opportunity_id: str | None = None
    detected_at: AwareDatetime | None = None
    timing: LifecycleTiming
    evaluation: StrategyEvaluation


def validate_snapshot(
    data: StrategyInput,
    *,
    source: str,
    replay: bool = False,
    strategy: QuickTradeStrategy | None = None,
) -> ValidationReport:
    """Replay uses the caller's capture-time evaluated_at, never rewrites observations.

    Opportunity IDs identify eligible *validation* observations, not orders. Detection
    time is UTC now; market timestamp and evaluation-as-of remain in evaluation.
    """
    strategy = strategy or QuickTradeStrategy()
    missing = []
    market = data.analysis
    selected = []
    if market is None:
        missing.append("analysis")
    else:
        for label, stamp in [("analysis.price_timestamp", market.price_timestamp)] + [
            (
                f"analysis.{tf.interval}.latest_candle_timestamp",
                tf.latest_candle_timestamp,
            )
            for tf in market.timeframes
            if tf.interval == strategy.config.timeframe
        ]:
            if stamp is None or stamp.tzinfo is None or stamp.utcoffset() is None:
                missing.append(label + ".timezone")
        selected = [
            tf for tf in market.timeframes if tf.interval == strategy.config.timeframe
        ]
        if len(selected) != 1:
            missing.append(
                f"analysis.timeframes.{strategy.config.timeframe} (exactly one required)"
            )
        elif selected[0].atr is None:
            missing.append("analysis.atr")
    if data.execution is None:
        missing.extend(
            "execution." + key
            for key in (
                "observed_at",
                "source",
                "spread",
                "additional_round_trip_cost",
                "min_stop_distance",
                "min_target_distance",
            )
        )
    else:
        missing.extend(
            "execution." + key
            for key in (
                "spread",
                "additional_round_trip_cost",
                "min_stop_distance",
                "min_target_distance",
            )
            if getattr(data.execution, key) is None
        )
    start = perf_counter_ns()
    evaluation = strategy.evaluate(data)
    elapsed = (perf_counter_ns() - start) / 1_000_000
    eligible = evaluation.status == "eligible" and not missing
    return ValidationReport(
        source=source,
        replay=replay,
        outcome="incomplete_input" if missing else evaluation.status,
        missing_inputs=missing,
        reference_price=market.price if market else None,
        input_failure_reasons=[
            r for r in evaluation.rejection_reasons if r.code in _INPUT_FAILURE_CODES
        ],
        strategy_rejection_reasons=[
            r
            for r in evaluation.rejection_reasons
            if r.code not in _INPUT_FAILURE_CODES
        ],
        timeframe_observations=(
            selected[0].model_dump(mode="json") if len(selected) == 1 else None
        ),
        execution_observations=(
            data.execution.model_dump(mode="json") if data.execution else None
        ),
        opportunity_id=str(uuid4()) if eligible else None,
        detected_at=datetime.now(timezone.utc) if eligible else None,
        timing=LifecycleTiming(strategy_evaluation_ms=elapsed),
        evaluation=evaluation,
    )
