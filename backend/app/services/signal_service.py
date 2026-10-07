class SignalService:
    def calculate_score(
        self,
        rsi: float,
        macd_histogram: float,
        ema_20: float,
        ema_50: float,
        atr: float,
    ) -> dict:
        score = 50.0

        
        rsi_contribution = 0.0
        macd_contribution = 0.0
        trend_contribution = 0.0

        # RSI contribution: max +/- 15
        if rsi > 50:
            rsi_strength = min((rsi - 50) / 20, 1)
            rsi_contribution += rsi_strength * 15
        elif rsi < 50:
            rsi_strength = min((50 - rsi) / 20, 1)
            rsi_contribution -= rsi_strength * 15

        score += rsi_contribution

        # MACD contribution: normalize histogram using ATR
        if atr > 0:
            macd_strength = min(abs(macd_histogram) / atr, 1)

            if macd_histogram > 0:
                macd_contribution += macd_strength * 20
            elif macd_histogram < 0:
                macd_contribution -= macd_strength * 20

        score += macd_contribution

        # Trend contribution: EMA distance normalized by ATR
        if atr > 0:
            ema_distance = ema_20 - ema_50
            trend_strength = min(abs(ema_distance) / (atr * 3), 1)

            if ema_distance > 0:
                trend_contribution += trend_strength * 25
            elif ema_distance < 0:
                trend_contribution -= trend_strength * 25

        score += trend_contribution

        score = round(max(0, min(100, score)), 2)

        if score >= 65:
            bias = "bullish"
        elif score <= 35:
            bias = "bearish"
        else:
            bias = "neutral"

        return {
        "score": score,
        "bias": bias,
        "breakdown": {
            "base": 50,
            "rsi": round(rsi_contribution, 2),
            "macd": round(macd_contribution, 2),
            "trend": round(trend_contribution, 2),
    },
}