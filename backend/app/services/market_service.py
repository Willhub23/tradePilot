from app.models.market_models import MarketCandle, MarketQuote
from app.providers.base import MarketProvider


class MarketService:
    def __init__(self, provider: MarketProvider):
        self.provider = provider

    def get_price(self, symbol: str) -> MarketQuote:
        return self.provider.get_price(symbol)

    def get_candles(
        self,
        symbol: str,
        interval: str,
        output_size: int,
    ) -> list[MarketCandle]:
        return self.provider.get_candles(
            symbol=symbol,
            interval=interval,
            output_size=output_size,
        )