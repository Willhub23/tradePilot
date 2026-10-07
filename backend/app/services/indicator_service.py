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
            average_gain = (
                (average_gain * (period - 1)) + gains[i]
            ) / period

            average_loss = (
                (average_loss * (period - 1)) + losses[i]
            ) / period

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

      macd_line = [
          fast_ema[i + offset] - slow_ema[i]
          for i in range(len(slow_ema))
      ]

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
            ema = (
                value * multiplier
                + ema_values[-1] * (1 - multiplier)
            )

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

      atr = initial_atr

      for true_range in true_ranges[period:]:
          atr = (
              (atr * (period - 1))
              + true_range
          ) / period

      return round(atr, 4)