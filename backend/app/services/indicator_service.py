from app.models.market_models import MarketCandle


class IndicatorService:
    def calculate_rsi(
        self,
        candles: list[MarketCandle],
        period: int = 14,
    ) -> float:
        if len(candles) <= period:
            raise ValueError("Not enough candles to calculate RSI")

        closes = [candle.close for candle in candles]

        gains = []
        losses = []

        for i in range(1, len(closes)):
            change = closes[i] - closes[i - 1]

            gains.append(max(change, 0))
            losses.append(max(-change, 0))

        average_gain = sum(gains[:period]) / period
        average_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            average_gain = ((average_gain * (period - 1)) + gains[i]) / period

            average_loss = ((average_loss * (period - 1)) + losses[i]) / period

        if average_loss == 0:
            return 100.0

        relative_strength = average_gain / average_loss

        rsi = 100 - (100 / (1 + relative_strength))

        return round(rsi, 2)

    def calculate_macd(
        self,
        candles: list[MarketCandle],
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
    ) -> dict[str, float]:
        if len(candles) < slow_period + signal_period:
            raise ValueError("Not enough candles to calculate MACD")

        closes = [candle.close for candle in candles]

        fast_ema = self._calculate_ema_series(closes, fast_period)
        slow_ema = self._calculate_ema_series(closes, slow_period)

        offset = slow_period - fast_period

        macd_line = [fast_ema[i + offset] - slow_ema[i] for i in range(len(slow_ema))]

        signal_line = self._calculate_ema_series(
            macd_line,
            signal_period,
        )

        latest_macd = macd_line[-1]
        latest_signal = signal_line[-1]
        histogram = latest_macd - latest_signal

        return {
            "macd": round(latest_macd, 4),
            "signal": round(latest_signal, 4),
            "histogram": round(histogram, 4),
        }

    def _calculate_ema_series(
        self,
        values: list[float],
        period: int,
    ) -> list[float]:
        if len(values) < period:
            raise ValueError("Not enough values to calculate EMA")

        multiplier = 2 / (period + 1)

        initial_ema = sum(values[:period]) / period

        ema_values = [initial_ema]

        for value in values[period:]:
            ema = value * multiplier + ema_values[-1] * (1 - multiplier)

            ema_values.append(ema)

        return ema_values

    def calculate_ema(
        self,
        candles: list[MarketCandle],
        period: int,
    ) -> float:
        closes = [candle.close for candle in candles]

        ema_series = self._calculate_ema_series(
            closes,
            period,
        )

        return round(ema_series[-1], 4)

    def calculate_atr(
        self,
        candles: list[MarketCandle],
        period: int = 14,
    ) -> float:
        atr_series = self.calculate_atr_series(
            candles=candles,
            period=period,
        )

        return atr_series[-1]

    def calculate_atr_series(
        self,
        candles: list[MarketCandle],
        period: int = 14,
    ) -> list[float]:
        if len(candles) <= period:
            raise ValueError("Not enough candles to calculate ATR")

        true_ranges = []

        for i in range(1, len(candles)):
            current = candles[i]
            previous = candles[i - 1]

            high_low = current.high - current.low
            high_close = abs(current.high - previous.close)
            low_close = abs(current.low - previous.close)

            true_range = max(
                high_low,
                high_close,
                low_close,
            )

            true_ranges.append(true_range)

        initial_atr = sum(true_ranges[:period]) / period

        atr_values = [initial_atr]

        atr = initial_atr

        for true_range in true_ranges[period:]:
            atr = ((atr * (period - 1)) + true_range) / period

            atr_values.append(atr)

        return [round(value, 4) for value in atr_values]

    def calculate_adx(
        self,
        candles: list[MarketCandle],
        period: int = 14,
    ) -> dict:
        if len(candles) < period * 2:
            raise ValueError("Not enough candles to calculate ADX")

        true_ranges = []
        plus_dm = []
        minus_dm = []

        for i in range(1, len(candles)):
            current = candles[i]
            previous = candles[i - 1]

            high_diff = current.high - previous.high
            low_diff = previous.low - current.low

            plus_dm.append(high_diff if high_diff > low_diff and high_diff > 0 else 0.0)

            minus_dm.append(low_diff if low_diff > high_diff and low_diff > 0 else 0.0)

            true_range = max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )

            true_ranges.append(true_range)

        smoothed_tr = sum(true_ranges[:period])
        smoothed_plus_dm = sum(plus_dm[:period])
        smoothed_minus_dm = sum(minus_dm[:period])

        dx_values = []

        for i in range(period, len(true_ranges)):
            smoothed_tr = smoothed_tr - (smoothed_tr / period) + true_ranges[i]

            smoothed_plus_dm = (
                smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm[i]
            )

            smoothed_minus_dm = (
                smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm[i]
            )

            if smoothed_tr == 0:
                continue

            plus_di = 100 * (smoothed_plus_dm / smoothed_tr)

            minus_di = 100 * (smoothed_minus_dm / smoothed_tr)

            di_sum = plus_di + minus_di

            if di_sum == 0:
                dx = 0
            else:
                dx = 100 * abs(plus_di - minus_di) / di_sum

            dx_values.append((dx, plus_di, minus_di))

        if len(dx_values) < period:
            raise ValueError("Not enough DX values to calculate ADX")

        adx = sum(value[0] for value in dx_values[:period]) / period

        for dx, _, _ in dx_values[period:]:
            adx = ((adx * (period - 1)) + dx) / period

        _, plus_di, minus_di = dx_values[-1]

        return {
            "adx": round(adx, 2),
            "plus_di": round(plus_di, 2),
            "minus_di": round(minus_di, 2),
        }
