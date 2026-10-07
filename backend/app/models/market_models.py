from datetime import datetime
from pydantic import BaseModel


class MarketQuote(BaseModel):
    symbol: str
    price: float
    currency: str
    timestamp: datetime
    source: str


class MarketCandle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


class IndicatorValue(BaseModel):
    symbol: str
    indicator: str
    value: float
    period: int
    interval: str


class MacdValue(BaseModel):
    symbol: str
    indicator: str
    macd: float
    signal: float
    histogram: float
    interval: str
    fast_period: int
    slow_period: int
    signal_period: int


class MarketAnalysis(BaseModel):
    symbol: str
    interval: str
    price: float
    rsi: float
    macd: MacdValue
    ema_20: float
    ema_50: float
    atr: float
    score: float
    bias: str
    score_breakdown: dict[str, float]
    signals: dict[str, str]
    trend_strength: float
    trend_strength_label: str


class TimeframeAnalysis(BaseModel):
    interval: str
    latest_candle_timestamp: datetime
    rsi: float
    ema_20: float
    ema_50: float
    atr: float
    score: float
    bias: str
    trend_direction: str
    trend_strength: float
    trend_strength_label: str


class MultiTimeframeAnalysis(BaseModel):
    symbol: str
    price: float
    price_timestamp: datetime
    timeframes: list[TimeframeAnalysis]
    overall_score: float
    overall_bias: str
    timeframe_alignment: str
