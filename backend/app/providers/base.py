from typing import Protocol

from app.models.market_models import MarketCandle, MarketQuote


class MarketProvider(Protocol):
    def get_price(self, symbol: str) -> MarketQuote:
        ...

    def get_candles(
        self,
        symbol: str,
        interval: str,
        output_size: int,
    ) -> list[MarketCandle]:
        ...