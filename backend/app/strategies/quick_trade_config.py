from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class QuickTradeConfig(BaseModel):
    """Experimental research parameters, not profitability claims or runtime risk policy."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    timeframe: Literal["5min", "15min"] = "5min"
    min_target_distance: float = Field(default=2.0, gt=0)
    max_target_distance: float = Field(default=5.0, gt=0)
    min_stop_distance: float = Field(default=1.5, gt=0)
    max_stop_distance: float = Field(default=5.0, gt=0)
    max_holding_minutes: int = Field(default=30, gt=0, le=30)
    stop_atr_multiplier: float = Field(default=1.0, gt=0)
    target_atr_multiplier: float = Field(default=2.0, gt=0)
    structure_buffer_atr: float = Field(default=0.1, ge=0)
    min_atr: float = Field(default=0.75, gt=0)
    max_atr: float = Field(default=3.0, gt=0)
    max_atr_ratio: float = Field(default=1.75, gt=0)
    max_structure_distance_atr: float = Field(default=1.0, gt=0)
    max_breakout_distance_atr: float = Field(default=1.0, gt=0)
    max_opposing_level_age_seconds: int = Field(default=3600, gt=0)
    max_entry_distance_atr: float = Field(default=1.0, gt=0)
    max_spread: float = Field(default=0.5, ge=0)
    max_cost_fraction_of_target: float = Field(default=0.2, gt=0, lt=1)
    min_net_reward_risk: float = Field(default=1.2, gt=0)
    max_quote_age_seconds: int = Field(default=60, gt=0)
    max_candle_age_seconds: int = Field(default=600, gt=0)
    max_execution_age_seconds: int = Field(default=60, gt=0)

    @model_validator(mode="after")
    def check_bounds(self):
        for lower, upper in (
            (self.min_target_distance, self.max_target_distance),
            (self.min_stop_distance, self.max_stop_distance),
            (self.min_atr, self.max_atr),
        ):
            if lower > upper:
                raise ValueError("Minimum cannot exceed maximum")
        return self
