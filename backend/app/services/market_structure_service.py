class MarketStructureService:
    def find_swing_highs(
        self,
        candles,
        window: int = 2,
    ) -> list:
        swing_highs = []

        for i in range(window, len(candles) - window):
            current = candles[i]

            left = candles[i - window : i]
            right = candles[i + 1 : i + window + 1]

            is_swing_high = all(current.high > candle.high for candle in left + right)

            if is_swing_high:
                swing_highs.append(current)

        return swing_highs

    def find_swing_lows(
        self,
        candles,
        window: int = 2,
    ) -> list:
        swing_lows = []

        for i in range(window, len(candles) - window):
            current = candles[i]

            left = candles[i - window : i]
            right = candles[i + 1 : i + window + 1]

            is_swing_low = all(current.low < candle.low for candle in left + right)

            if is_swing_low:
                swing_lows.append(current)

        return swing_lows

    def classify_structure(
        self,
        swing_highs,
        swing_lows,
    ) -> str:
        high_pattern = self.classify_high_pattern(swing_highs)
        low_pattern = self.classify_low_pattern(swing_lows)

        if high_pattern == "uncertain" or low_pattern == "uncertain":
            return "uncertain"

        if high_pattern == "higher_high" and low_pattern == "higher_low":
            return "bullish"

        if high_pattern == "lower_high" and low_pattern == "lower_low":
            return "bearish"

        return "mixed"

    def analyze(
        self,
        candles,
        window: int = 2,
    ) -> dict:
        swing_highs = self.find_swing_highs(candles, window)
        swing_lows = self.find_swing_lows(candles, window)

        high_pattern = self.classify_high_pattern(swing_highs)
        low_pattern = self.classify_low_pattern(swing_lows)

        structure = self.classify_structure(
            swing_highs=swing_highs,
            swing_lows=swing_lows,
        )

        structure_break = self.detect_structure_break(
            candles=candles,
            swing_highs=swing_highs,
            swing_lows=swing_lows,
        )

        levels = self.get_key_levels(
            swing_highs=swing_highs,
            swing_lows=swing_lows,
        )

        distances = self.distance_to_levels(
            candles=candles,
            swing_highs=swing_highs,
            swing_lows=swing_lows,
        )

        latest_swing_high = swing_highs[-1] if swing_highs else None
        latest_swing_low = swing_lows[-1] if swing_lows else None

        return {
            "structure": structure,
            "high_pattern": high_pattern,
            "low_pattern": low_pattern,
            "structure_break": structure_break,
            "latest_swing_high": latest_swing_high,
            "latest_swing_low": latest_swing_low,
            "support": levels["support"],
            "resistance": levels["resistance"],
            "distance_to_swing_high": distances["distance_to_swing_high"],
            "distance_to_swing_low": distances["distance_to_swing_low"],
            "swing_highs": swing_highs,
            "swing_lows": swing_lows,
        }

    def detect_structure_break(
        self,
        candles,
        swing_highs,
        swing_lows,
    ) -> str:
        if not candles:
            return "none"

        latest_candle = candles[-1]

        latest_swing_high = swing_highs[-1] if swing_highs else None
        latest_swing_low = swing_lows[-1] if swing_lows else None

        if (
            latest_swing_high is not None
            and latest_candle.close > latest_swing_high.high
        ):
            return "break_above"

        if latest_swing_low is not None and latest_candle.close < latest_swing_low.low:
            return "break_below"

        return "none"

    def distance_to_levels(
        self,
        candles,
        swing_highs,
        swing_lows,
    ) -> dict:
        if not candles:
            return {
                "distance_to_swing_high": None,
                "distance_to_swing_low": None,
            }

        current_price = candles[-1].close

        latest_high = swing_highs[-1] if swing_highs else None
        latest_low = swing_lows[-1] if swing_lows else None

        return {
            "distance_to_swing_high": (
                round(latest_high.high - current_price, 5)
                if latest_high is not None
                else None
            ),
            "distance_to_swing_low": (
                round(current_price - latest_low.low, 5)
                if latest_low is not None
                else None
            ),
        }

    def get_key_levels(
        self,
        swing_highs,
        swing_lows,
    ) -> dict:
        return {
            "resistance": swing_highs[-1].high if swing_highs else None,
            "support": swing_lows[-1].low if swing_lows else None,
        }

    def classify_high_pattern(
        self,
        swing_highs,
    ) -> str:
        if len(swing_highs) < 2:
            return "uncertain"

        previous_high = swing_highs[-2]
        latest_high = swing_highs[-1]

        if latest_high.high > previous_high.high:
            return "higher_high"

        if latest_high.high < previous_high.high:
            return "lower_high"

        return "equal_high"

    def classify_low_pattern(
        self,
        swing_lows,
    ) -> str:
        if len(swing_lows) < 2:
            return "uncertain"

        previous_low = swing_lows[-2]
        latest_low = swing_lows[-1]

        if latest_low.low > previous_low.low:
            return "higher_low"

        if latest_low.low < previous_low.low:
            return "lower_low"

        return "equal_low"
