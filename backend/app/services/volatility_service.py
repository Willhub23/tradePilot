class VolatilityService:
    def calculate_regime(
        self,
        atr_values: list[float],
    ) -> dict:
        if not atr_values:
            return {
                "current_atr": None,
                "baseline_atr": None,
                "atr_ratio": None,
                "volatility_regime": "uncertain",
            }

        current_atr = atr_values[-1]

        if len(atr_values) < 2:
            return {
                "current_atr": current_atr,
                "baseline_atr": None,
                "atr_ratio": None,
                "volatility_regime": "uncertain",
            }

        baseline_values = atr_values[:-1]
        baseline_atr = sum(baseline_values) / len(baseline_values)

        if baseline_atr <= 0:
            return {
                "current_atr": current_atr,
                "baseline_atr": baseline_atr,
                "atr_ratio": None,
                "volatility_regime": "uncertain",
            }

        atr_ratio = current_atr / baseline_atr

        if atr_ratio < 0.75:
            regime = "low"
        elif atr_ratio <= 1.25:
            regime = "normal"
        elif atr_ratio <= 1.75:
            regime = "elevated"
        else:
            regime = "high"

        return {
            "current_atr": round(current_atr, 5),
            "baseline_atr": round(baseline_atr, 5),
            "atr_ratio": round(atr_ratio, 3),
            "volatility_regime": regime,
        }
