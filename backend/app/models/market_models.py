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


class FvgContext(BaseModel):
    price_inside: bool
    lower: float
    upper: float
    distance_atr: float | None
    size_atr: float | None
    state: str


class PriceActionContext(BaseModel):
    rejection: str
    displacement_detected: bool
    displacement_direction: str
    displacement_strength: float

    nearest_bullish_fvg: FvgContext | None
    nearest_bearish_fvg: FvgContext | None
    active_fvg_count: int


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

    structure: str
    high_pattern: str
    low_pattern: str
    structure_break: str
    support: float | None
    resistance: float | None
    distance_to_swing_high: float | None
    distance_to_swing_low: float | None

    atr_ratio: float | None
    volatility_regime: str
    market_regime: str

    ema_9: float
    ema_100: float
    ema_200: float

    ema_stack: str

    price_vs_ema_20: str
    price_vs_ema_50: str
    price_vs_ema_200: str

    distance_to_ema_20_atr: float | None
    distance_to_ema_50_atr: float | None
    distance_to_ema_200_atr: float | None

    ema_alignment: float
    ema_spacing: float

    ema_20_slope: str
    ema_50_slope: str
    ema_200_slope: str

    adx: float
    plus_di: float
    minus_di: float

    price_action: PriceActionContext


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

    structure: str
    high_pattern: str
    low_pattern: str
    structure_break: str
    support: float | None
    resistance: float | None
    distance_to_swing_high: float | None
    distance_to_swing_low: float | None

    atr_ratio: float | None
    volatility_regime: str
    market_regime: str

    ema_9: float
    ema_100: float
    ema_200: float

    ema_stack: str

    price_vs_ema_20: str
    price_vs_ema_50: str
    price_vs_ema_200: str

    distance_to_ema_20_atr: float | None
    distance_to_ema_50_atr: float | None
    distance_to_ema_200_atr: float | None

    ema_alignment: float
    ema_spacing: float

    ema_20_slope: str
    ema_50_slope: str
    ema_200_slope: str

    adx: float
    plus_di: float
    minus_di: float

    price_action: PriceActionContext


class MultiTimeframeAnalysis(BaseModel):
    symbol: str
    price: float
    price_timestamp: datetime
    timeframes: list[TimeframeAnalysis]
    overall_score: float
    overall_bias: str
    timeframe_alignment: str
