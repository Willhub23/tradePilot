from datetime import datetime, timezone

import httpx

from app.models.market_models import MarketCandle, MarketQuote


class TwelveDataProvider:
    BASE_URL = "https://api.twelvedata.com"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def get_price(self, symbol: str) -> MarketQuote:
        formatted_symbol = self._format_symbol(symbol)

        response = httpx.get(
            f"{self.BASE_URL}/price",
            params={
                "symbol": formatted_symbol,
                "apikey": self.api_key,
            },
            timeout=10.0,
        )

        response.raise_for_status()
        data = response.json()

        if "price" not in data:
            raise ValueError(f"Invalid Twelve Data response: {data}")

        return MarketQuote(
            symbol=symbol,
            price=float(data["price"]),
            currency="USD",
            timestamp=datetime.now(timezone.utc),
            source="twelve_data",
        )

    def get_candles(
        self,
        symbol: str,
        interval: str,
        output_size: int,
    ) -> list[MarketCandle]:
        formatted_symbol = self._format_symbol(symbol)

        response = httpx.get(
            f"{self.BASE_URL}/time_series",
            params={
                "symbol": formatted_symbol,
                "interval": interval,
                "outputsize": output_size,
                "timezone": "UTC",
                "apikey": self.api_key,
            },
            timeout=10.0,
        )

        response.raise_for_status()
        data = response.json()

        if "values" not in data:
            raise ValueError(f"Invalid Twelve Data response: {data}")

        candles = []

        for item in data["values"]:
            candle = MarketCandle(
                timestamp=datetime.fromisoformat(item["datetime"]),
                open=float(item["open"]),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                volume=(
                    float(item["volume"]) if item.get("volume") is not None else None
                ),
            )

            candles.append(candle)

        candles.reverse()

        return candles

    def _format_symbol(self, symbol: str) -> str:
        if symbol == "XAUUSD":
            return "XAU/USD"

        return symbol
