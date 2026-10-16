class MarketRegimeService:
    def classify(
        self,
        structure: str,
        trend_direction: str,
        trend_strength_label: str,
        structure_break: str,
        adx: float,
        plus_di: float,
        minus_di: float,
    ) -> str:

        if structure_break == "break_above":
            return "breakout_bullish"

        if structure_break == "break_below":
            return "breakout_bearish"

        if adx < 20:
            if trend_strength_label == "weak" or structure == "mixed":
                return "ranging"

            return "uncertain"

        if (
            structure == "bullish"
            and trend_direction == "bullish"
            and plus_di > minus_di
            and trend_strength_label in {"moderate", "strong"}
        ):
            return "trending_bullish"

        if (
            structure == "bearish"
            and trend_direction == "bearish"
            and minus_di > plus_di
            and trend_strength_label in {"moderate", "strong"}
        ):
            return "trending_bearish"

        return "uncertain"
