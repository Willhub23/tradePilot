from typing import Literal

from pydantic import BaseModel


class BrokerExecutionRequest(BaseModel):
    request_id: str
    agent_decision_id: str | None

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
        "pending",
        "unknown",
    ]

    broker_order_id: str | None = None
    broker_position_id: str | None = None

    filled_volume_lots: float | None = None
    fill_price: float | None = None

    error_code: str | None = None
    error_message: str | None = None
