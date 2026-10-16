from app.models.market_models import MarketCandle


class PriceActionService:
    def analyze(
        self,
        candles: list[MarketCandle],
        atr: float,
    ) -> dict:
        latest = candles[-1]

        candle_context = self._analyze_candle(latest)
        displacement = self._detect_displacement(
            candle=latest,
            atr=atr,
        )
        fvgs = self._find_fair_value_gaps(
            candles=candles,
            lookback=50,
        )

        fvg_context = self._get_fvg_context(
            fvgs=fvgs,
            current_price=candles[-1].close,
            atr=atr,
        )

        return {
            "candle": candle_context,
            "displacement": displacement,
            "fvg_context": fvg_context,
            "fair_value_gaps": fvgs,
        }

    def _analyze_candle(
        self,
        candle: MarketCandle,
    ) -> dict:
        body = abs(candle.close - candle.open)
        candle_range = candle.high - candle.low

        upper_wick = candle.high - max(
            candle.open,
            candle.close,
        )

        lower_wick = (
            min(
                candle.open,
                candle.close,
            )
            - candle.low
        )

        if candle.close > candle.open:
            direction = "bullish"
        elif candle.close < candle.open:
            direction = "bearish"
        else:
            direction = "neutral"

        body_ratio = body / candle_range if candle_range > 0 else 0.0

        upper_wick_ratio = upper_wick / candle_range if candle_range > 0 else 0.0

        lower_wick_ratio = lower_wick / candle_range if candle_range > 0 else 0.0

        rejection = "none"

        if lower_wick > body * 2 and lower_wick_ratio >= 0.4:
            rejection = "bullish_rejection"

        elif upper_wick > body * 2 and upper_wick_ratio >= 0.4:
            rejection = "bearish_rejection"

        return {
            "direction": direction,
            "body_ratio": round(body_ratio, 3),
            "upper_wick_ratio": round(upper_wick_ratio, 3),
            "lower_wick_ratio": round(lower_wick_ratio, 3),
            "rejection": rejection,
        }

    def _detect_displacement(
        self,
        candle: MarketCandle,
        atr: float,
    ) -> dict:
        body = abs(candle.close - candle.open)
        candle_range = candle.high - candle.low

        if candle_range <= 0 or atr <= 0:
            return {
                "detected": False,
                "direction": "none",
                "strength": 0.0,
            }

        body_ratio = body / candle_range
        atr_ratio = candle_range / atr

        detected = body_ratio >= 0.65 and atr_ratio >= 1.0

        direction = "none"

        if detected:
            direction = "bullish" if candle.close > candle.open else "bearish"

        return {
            "detected": detected,
            "direction": direction,
            "strength": round(atr_ratio, 3),
        }

    def _find_fair_value_gaps(
        self,
        candles: list[MarketCandle],
        lookback: int = 50,
    ) -> list[dict]:
        recent = candles[-lookback:]
        fvgs = []

        for i in range(len(recent) - 2):
            first = recent[i]
            third = recent[i + 2]

            fvg = None

            if third.low > first.high:
                fvg = {
                    "direction": "bullish",
                    "lower": first.high,
                    "upper": third.low,
                    "size": round(
                        third.low - first.high,
                        4,
                    ),
                    "timestamp": third.timestamp,
                }

            elif third.high < first.low:
                fvg = {
                    "direction": "bearish",
                    "lower": third.high,
                    "upper": first.low,
                    "size": round(
                        first.low - third.high,
                        4,
                    ),
                    "timestamp": third.timestamp,
                }

            if fvg:
                following_candles = recent[i + 3 :]

                fvgs.append(
                    self._evaluate_fvg(
                        fvg=fvg,
                        following_candles=following_candles,
                    )
                )

        return fvgs

    def _evaluate_fvg(
        self,
        fvg: dict,
        following_candles: list[MarketCandle],
    ) -> dict:
        lower = fvg["lower"]
        upper = fvg["upper"]
        direction = fvg["direction"]

        state = "unfilled"

        for candle in following_candles:
            if direction == "bullish":
                if candle.low <= lower:
                    state = "filled"
                    break

                if candle.low < upper:
                    state = "partial"

            elif direction == "bearish":
                if candle.high >= upper:
                    state = "filled"
                    break

                if candle.high > lower:
                    state = "partial"

        return {
            **fvg,
            "state": state,
        }

    def _get_fvg_context(
        self,
        fvgs: list[dict],
        current_price: float,
        atr: float,
    ) -> dict:
        active = [
            fvg
            for fvg in fvgs
            if fvg["state"] != "filled" and atr > 0 and (fvg["size"] / atr) >= 0.10
        ]

        bullish = [fvg for fvg in active if fvg["direction"] == "bullish"]

        bearish = [fvg for fvg in active if fvg["direction"] == "bearish"]

        def distance_to_fvg(fvg: dict) -> float:
            if fvg["lower"] <= current_price <= fvg["upper"]:
                return 0.0

            if current_price > fvg["upper"]:
                return current_price - fvg["upper"]

            return fvg["lower"] - current_price

        nearest_bullish = min(bullish, key=distance_to_fvg) if bullish else None

        nearest_bearish = min(bearish, key=distance_to_fvg) if bearish else None

        def build_context(fvg: dict | None):
            if fvg is None:
                return None

            distance = distance_to_fvg(fvg)

            return {
                **fvg,
                "distance": round(distance, 4),
                "distance_atr": (round(distance / atr, 3) if atr > 0 else None),
                "price_inside": (fvg["lower"] <= current_price <= fvg["upper"]),
                "size_atr": round(fvg["size"] / atr, 3),
            }

        return {
            "nearest_bullish": build_context(nearest_bullish),
            "nearest_bearish": build_context(nearest_bearish),
            "active_count": len(active),
        }
