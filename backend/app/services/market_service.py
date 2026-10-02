from app.providers.mock_provider import MockMarketProvider


class MarketService:

    def __init__(self):
        self.provider = MockMarketProvider()

    def get_price(self, symbol: str) -> dict:
        return self.provider.get_price(symbol)