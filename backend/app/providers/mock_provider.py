from datetime import datetime, timezone


class MockMarketProvider:

    def get_price(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "price": 2650.50,
            "currency": "USD",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "mock"
        }