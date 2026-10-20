from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class BrokerPosition(BaseModel):
    broker_position_id: str
    symbol: str
    direction: Literal["long", "short"]
    volume_lots: float
    entry_price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    opened_at: datetime
    status: Literal["open", "closed"]


class BrokerCloseResult(BaseModel):
    broker_position_id: str
    status: Literal["prepared", "submitted", "closed", "rejected", "unknown"]
    broker_order_id: str | None = None
    message: str | None = None


class BrokerPositionManagementResult(BaseModel):
    broker_position_id: str | None = None
    protection: Literal["verified", "unsafe", "unconfirmed", "not_applicable"]
    holding_policy: Literal["not_due", "expired", "unresolved", "not_applicable"]
    message: str | None = None
    close_result: BrokerCloseResult | None = None
