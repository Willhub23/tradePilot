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
