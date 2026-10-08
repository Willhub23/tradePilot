import os

from dotenv import load_dotenv
from fastapi import APIRouter, Query

from app.models.market_models import (
    IndicatorValue,
    MarketCandle,
    MarketQuote,
    MacdValue,
    MarketAnalysis,
    MultiTimeframeAnalysis,
)

from app.services.decision_log_service import DecisionLogService
from app.services.analysis_service import AnalysisService
from app.providers.twelve_data_provider import TwelveDataProvider
from app.services.market_service import MarketService
from app.services.indicator_service import IndicatorService
from app.services.signal_service import SignalService

load_dotenv()


router = APIRouter(prefix="/api/market", tags=["Market"])

api_key = os.getenv("TWELVE_DATA_API_KEY")

if not api_key:
    raise RuntimeError("TWELVE_DATA_API_KEY is not configured")

market_service = MarketService(provider=TwelveDataProvider(api_key))

indicator_service = IndicatorService()

signal_service = SignalService()
decision_log_service = DecisionLogService()


analysis_service = AnalysisService(
    market_service=market_service,
    indicator_service=indicator_service,
    signal_service=signal_service,
)


@router.get("/{symbol}", response_model=MarketQuote)
def get_market_price(symbol: str):
    return market_service.get_price(symbol.upper())


@router.get(
    "/{symbol}/candles",
    response_model=list[MarketCandle],
)
def get_market_candles(
    symbol: str,
    interval: str = Query(default="5min"),
    output_size: int = Query(default=100, ge=1, le=5000),
):
    return market_service.get_candles(
        symbol=symbol.upper(),
        interval=interval,
        output_size=output_size,
    )


@router.get(
    "/{symbol}/rsi",
    response_model=IndicatorValue,
)
def get_rsi(
    symbol: str,
    interval: str = Query(default="5min"),
    period: int = Query(default=14, ge=2, le=100),
):
    candles = market_service.get_candles(
        symbol=symbol.upper(),
        interval=interval,
        output_size=100,
    )

    rsi = indicator_service.calculate_rsi(
        candles=candles,
        period=period,
    )

    return IndicatorValue(
        symbol=symbol.upper(),
        indicator="RSI",
        value=rsi,
        period=period,
        interval=interval,
    )


@router.get(
    "/{symbol}/macd",
    response_model=MacdValue,
)
def get_macd(
    symbol: str,
    interval: str = Query(default="5min"),
    fast_period: int = Query(default=12, ge=2),
    slow_period: int = Query(default=26, ge=3),
    signal_period: int = Query(default=9, ge=2),
):
    candles = market_service.get_candles(
        symbol=symbol.upper(),
        interval=interval,
        output_size=100,
    )

    result = indicator_service.calculate_macd(
        candles=candles,
        fast_period=fast_period,
        slow_period=slow_period,
        signal_period=signal_period,
    )

    return MacdValue(
        symbol=symbol.upper(),
        indicator="MACD",
        macd=result["macd"],
        signal=result["signal"],
        histogram=result["histogram"],
        interval=interval,
        fast_period=fast_period,
        slow_period=slow_period,
        signal_period=signal_period,
    )


@router.get(
    "/{symbol}/analysis",
    response_model=MarketAnalysis,
)
def get_market_analysis(
    symbol: str,
    interval: str = Query(default="5min"),
):
    analysis = analysis_service.analyze_timeframe(
        symbol=symbol,
        interval=interval,
    )

    decision_log_service.log_analysis(analysis)

    return analysis


@router.get(
    "/{symbol}/multi-timeframe",
    response_model=MultiTimeframeAnalysis,
)
def get_multi_timeframe_analysis(
    symbol: str,
):
    return analysis_service.analyze_multi_timeframe(
        symbol=symbol,
    )
