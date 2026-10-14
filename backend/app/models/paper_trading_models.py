from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class PaperPosition(BaseModel):
    id: str
    symbol: str
    direction: Literal["long", "short"]
    entry_price: float
    opened_at: datetime
    status: Literal["open", "closed"]
    confidence: float
    thesis: str

    current_price: float | None = None
    unrealized_pnl: float = 0.0
    closed_at: datetime | None = None
    exit_price: float | None = None
    realized_pnl: float | None = None

    stop_loss: float | None = None
    take_profit: float | None = None
    max_holding_minutes: int | None = None
    close_reason: str | None = None
