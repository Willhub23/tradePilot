from app.models.market_models import MarketCandle
from app.services.indicator_service import IndicatorService


class MovingAverageService:
    def __init__(
        self,
        indicator_service: IndicatorService,
    ):
        self.indicator_service = indicator_service

    def analyze(
        self,
        candles: list[MarketCandle],
        atr: float,
        periods: list[int] | None = None,
    ) -> dict:
        if periods is None:
            periods = [9, 20, 50, 100, 200]

        current_price = candles[-1].close

        emas = {
            period: self.indicator_service.calculate_ema(
                candles=candles,
                period=period,
            )
            for period in periods
        }

        price_position = {
            period: (
                "above"
                if current_price > ema
                else "below" if current_price < ema else "at"
            )
            for period, ema in emas.items()
        }

        atr_distances = {
            period: (round((current_price - ema) / atr, 3) if atr > 0 else None)
            for period, ema in emas.items()
        }

        stack = self._classify_stack(
            emas=emas,
            periods=periods,
        )

        trend_context = self._calculate_trend_context(
            emas=emas,
            periods=periods,
            atr=atr,
        )

        slopes = {
            period: self._calculate_ema_slope(
                candles=candles,
                period=period,
                lookback=5,
            )
            for period in periods
        }

        return {
            "emas": emas,
            "price_position": price_position,
            "atr_distances": atr_distances,
            "stack": stack,
            "trend_direction": trend_context["trend_direction"],
            "trend_strength": trend_context["trend_strength"],
            "trend_strength_label": trend_context["trend_strength_label"],
            "ema_alignment": trend_context["ema_alignment"],
            "ema_spacing": trend_context["ema_spacing"],
            "slopes": slopes,
        }

    def _classify_stack(
        self,
        emas: dict[int, float],
        periods: list[int],
    ) -> str:
        values = [emas[period] for period in periods]

        if all(values[i] > values[i + 1] for i in range(len(values) - 1)):
            return "bullish"

        if all(values[i] < values[i + 1] for i in range(len(values) - 1)):
            return "bearish"

        return "mixed"

    def _calculate_trend_context(
        self,
        emas: dict[int, float],
        periods: list[int],
        atr: float,
    ) -> dict:
        pair_directions = []
        normalized_distances = []

        for i in range(len(periods) - 1):
            fast_period = periods[i]
            slow_period = periods[i + 1]

            fast_ema = emas[fast_period]
            slow_ema = emas[slow_period]

            difference = fast_ema - slow_ema

            if difference > 0:
                pair_directions.append(1)
            elif difference < 0:
                pair_directions.append(-1)
            else:
                pair_directions.append(0)

            if atr > 0:
                normalized_distances.append(min(abs(difference) / atr, 1.0))

        alignment = sum(pair_directions) / len(pair_directions)

        average_spacing = (
            sum(normalized_distances) / len(normalized_distances)
            if normalized_distances
            else 0.0
        )

        strength = abs(alignment) * average_spacing

        if alignment > 0.25:
            direction = "bullish"
        elif alignment < -0.25:
            direction = "bearish"
        else:
            direction = "neutral"

        if strength >= 0.6:
            strength_label = "strong"
        elif strength >= 0.3:
            strength_label = "moderate"
        else:
            strength_label = "weak"

        return {
            "trend_direction": direction,
            "trend_strength": round(strength, 3),
            "trend_strength_label": strength_label,
            "ema_alignment": round(alignment, 3),
            "ema_spacing": round(average_spacing, 3),
        }

    def _calculate_ema_slope(
        self,
        candles: list[MarketCandle],
        period: int,
        lookback: int = 5,
    ) -> str:
        if len(candles) <= lookback:
            return "uncertain"

        current_ema = self.indicator_service.calculate_ema(
            candles=candles,
            period=period,
        )

        previous_ema = self.indicator_service.calculate_ema(
            candles=candles[:-lookback],
            period=period,
        )

        if current_ema > previous_ema:
            return "rising"

        if current_ema < previous_ema:
            return "falling"

        return "flat"
