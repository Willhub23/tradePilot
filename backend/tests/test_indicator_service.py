from datetime import datetime, timezone

from app.models.market_models import MarketCandle
from app.services.indicator_service import IndicatorService


def make_candles(count: int = 30) -> list[MarketCandle]:
    candles = []

    for i in range(count):
        base = 100 + i

        candles.append(
            MarketCandle(
                timestamp=datetime.now(timezone.utc),
                open=base,
                high=base + 2,
                low=base - 2,
                close=base + 1,
                volume=None,
            )
        )

    return candles


def test_latest_atr_series_value_matches_atr():
    service = IndicatorService()

    candles = make_candles()

    atr = service.calculate_atr(candles)
    atr_series = service.calculate_atr_series(candles)

    assert atr_series[-1] == atr
