from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.models.market_models import MultiTimeframeAnalysis


class ExecutionEvidence(BaseModel):
    """Caller-supplied observations, all distances/costs in XAUUSD price units.

    additional_round_trip_cost excludes spread and includes reliable commission /
    slippage estimates converted upstream to price units for the intended volume.
    None means unknown; zero must be an explicitly supplied observation.
    Broker minimum distances must already be normalized from broker metadata.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", allow_inf_nan=False, str_strip_whitespace=True
    )

    observed_at: AwareDatetime
    source: str = Field(min_length=1)
    spread: float | None = Field(default=None, ge=0)
    additional_round_trip_cost: float | None = Field(default=None, ge=0)
    min_stop_distance: float | None = Field(default=None, ge=0)
    min_target_distance: float | None = Field(default=None, ge=0)


class StrategyInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evaluated_at: (
        AwareDatetime  # Explicit clock: no wall-clock reads during evaluation.
    )
    analysis: MultiTimeframeAnalysis | None = None
    execution: ExecutionEvidence | None = None


class StrategySignal(BaseModel):
    name: str
    values: dict[str, float | str | None]


class StrategyRejection(BaseModel):
    code: str
    message: str
    measured: float | str | None = None
    limit: float | str | None = None


class StrategyTradeProposal(BaseModel):
    """Research candidate only. Not an execution request or a risk approval."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    direction: Literal["long", "short"]
    entry_reference: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    stop_distance: float = Field(gt=0)
    target_distance: float = Field(gt=0)
    max_holding_minutes: int = Field(gt=0, le=30)
    estimated_round_trip_cost: float = Field(ge=0)
    net_reward_risk: float = Field(gt=0)


SetupType = Literal[
    "range_support_bounce",
    "range_resistance_rejection",
    "trend_pullback_continuation",
    "breakout",
]


class StrategyEvaluation(BaseModel):
    strategy_id: str
    symbol: str | None
    status: Literal["eligible", "rejected"]
    proposal: StrategyTradeProposal | None = None
    setup_type: SetupType | None = None
    decision_reason: str = ""
    invalidation_condition: str | None = None
    invalidation_level: float | None = None
    supporting_signals: list[StrategySignal] = Field(default_factory=list)
    conflicting_signals: list[StrategySignal] = Field(default_factory=list)
    rejection_reasons: list[StrategyRejection] = Field(default_factory=list)
    timeframe: str
    market_timestamp: AwareDatetime | None
    candle_timestamp: AwareDatetime | None
    evaluated_at: AwareDatetime

    @model_validator(mode="after")
    def check_outcome(self):
        if self.status == "eligible":
            if self.proposal is None or self.rejection_reasons:
                raise ValueError(
                    "Eligible evaluation requires a proposal and no rejections"
                )
        elif self.proposal is not None or not self.rejection_reasons:
            raise ValueError(
                "Rejected evaluation requires reasons and no actionable proposal"
            )
        return self
