"""Opt-in runtime selection. Existing strategy remains the default."""

import os
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrategySelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    strategy: Literal["existing", "quick_trade"] = "existing"
    observation_enabled: bool = False
    slippage_allowance: float | None = Field(default=None, ge=0)
    max_price_drift: float = Field(default=0.25, gt=0)
    max_opportunity_age_seconds: float = Field(default=60, gt=0, le=60)

    @model_validator(mode="after")
    def explicit_cost(self):
        if (self.strategy == "quick_trade" or self.observation_enabled) and self.slippage_allowance is None:
            raise ValueError(
                "QUICK_TRADE_SLIPPAGE_ALLOWANCE must be explicitly configured"
            )
        return self

    @classmethod
    def from_env(cls):
        strategy = os.getenv("TRADING_STRATEGY", "existing")
        observation = os.getenv("QUICK_TRADE_OBSERVATION_ENABLED", "false").lower()
        if observation not in ("true", "false"):
            raise ValueError("QUICK_TRADE_OBSERVATION_ENABLED must be true or false")
        if strategy == "existing" and observation == "false":
            return cls()  # Dormant Quick Trade settings cannot change the default flow.
        return cls(
            strategy=strategy,
            observation_enabled=observation == "true",
            slippage_allowance=os.getenv("QUICK_TRADE_SLIPPAGE_ALLOWANCE"),
            max_price_drift=os.getenv("QUICK_TRADE_MAX_PRICE_DRIFT", ".25"),
            max_opportunity_age_seconds=os.getenv(
                "QUICK_TRADE_MAX_OPPORTUNITY_AGE_SECONDS", "60"
            ),
        )
