from datetime import datetime

from pydantic import BaseModel


class DecisionLog(BaseModel):
    timestamp: datetime
    symbol: str
    interval: str
    price: float
    score: float
    bias: str

    rsi: float
    macd_histogram: float
    ema_20: float
    ema_50: float
    atr: float

    trend_direction: str
    trend_strength: float
    trend_strength_label: str

    score_breakdown: dict[str, float]
