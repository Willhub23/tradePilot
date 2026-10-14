from datetime import datetime, timezone
from time import monotonic

import httpx

from app.models.market_models import MarketCandle, MarketQuote


class MarketDataRateLimitError(Exception):
    pass


class TwelveDataProvider:
    BASE_URL = "https://api.twelvedata.com"

    PRICE_CACHE_SECONDS = 30

    CANDLE_CACHE_SECONDS = {
        "5min": 240,
        "15min": 840,
        "1h": 3540,
    }

    def __init__(self, api_key: str):
        self.api_key = api_key

        self._price_cache: dict[str, tuple[float, MarketQuote]] = {}
        self._candle_cache: dict[
            tuple[str, str, int],
            tuple[float, list[MarketCandle]],
        ] = {}

    def get_price(self, symbol: str) -> MarketQuote:
        cached = self._price_cache.get(symbol)

        if cached is not None:
            cached_at, quote = cached

            if monotonic() - cached_at < self.PRICE_CACHE_SECONDS:
                return quote

        formatted_symbol = self._format_symbol(symbol)

        response = httpx.get(
            f"{self.BASE_URL}/price",
            params={
                "symbol": formatted_symbol,
                "apikey": self.api_key,
            },
            timeout=10.0,
        )

        if response.status_code == 429:
            raise MarketDataRateLimitError("Twelve Data rate limit reached")

        response.raise_for_status()
        data = response.json()

        if "price" not in data:
            raise ValueError(f"Invalid Twelve Data response: {data}")

        quote = MarketQuote(
            symbol=symbol,
            price=float(data["price"]),
            currency="USD",
            timestamp=datetime.now(timezone.utc),
            source="twelve_data",
        )

        self._price_cache[symbol] = (
            monotonic(),
            quote,
        )

        return quote

    def get_candles(
        self,
        symbol: str,
        interval: str,
        output_size: int,
    ) -> list[MarketCandle]:
        cache_key = (
            symbol,
            interval,
            output_size,
        )

        cached = self._candle_cache.get(cache_key)

        ttl = self.CANDLE_CACHE_SECONDS.get(interval, 60)

        if cached is not None:
            cached_at, candles = cached

            if monotonic() - cached_at < ttl:
                return candles

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

        if response.status_code == 429:
            raise MarketDataRateLimitError("Twelve Data rate limit reached")

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

        self._candle_cache[cache_key] = (
            monotonic(),
            candles,
        )

        return candles

    def _format_symbol(self, symbol: str) -> str:
        if symbol == "XAUUSD":
            return "XAU/USD"

        return symbol
