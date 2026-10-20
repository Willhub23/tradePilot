from typing import Literal
from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict


class QuickTradeExecutionGuard(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    original_entry: float = Field(gt=0)
    stop_budget: float = Field(gt=0)
    max_price_drift: float = Field(gt=0)
    additional_cost: float = Field(ge=0)


class BrokerExecutionRequest(BaseModel):
    request_id: str
    agent_decision_id: str | None
    quick_trade_guard: QuickTradeExecutionGuard | None = None
    opportunity_id: str | None = None
    strategy_id: str | None = None
    valid_until: datetime | None = None
    timing: dict | None = None

    symbol: str
    direction: Literal["long", "short"]
    order_type: Literal["market"] = "market"

    volume_lots: float
    reference_price: float

    stop_loss: float
    take_profit: float
    max_holding_minutes: int

    trade_style: Literal["intraday", "swing"]
    confidence: float
    thesis: str


class BrokerExecutionResult(BaseModel):
    request_id: str

    status: Literal[
        "submitted",
        "filled",
        "rejected",
        "cancelled",
        "pending",
        "unknown",
    ]

    broker_order_id: str | None = None
    broker_position_id: str | None = None

    filled_volume_lots: float | None = None
    fill_price: float | None = None

    error_code: str | None = None
    error_message: str | None = None

    broker_event_at: datetime | None = None
    broker_event_time_source: str | None = None
    received_at: datetime | None = None
    receipt_monotonic_ns: int | None = None
    receipt_process_id: str | None = None

    protection_status: Literal["unchecked", "verified", "unsafe"] = "unchecked"
    protection_message: str | None = None
