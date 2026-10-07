from app.models.market_models import (
    MacdValue,
    MarketAnalysis,
    MultiTimeframeAnalysis,
    TimeframeAnalysis,
)
from app.services.market_service import MarketService
from app.services.indicator_service import IndicatorService
from app.services.signal_service import SignalService


class AnalysisService:
    def __init__(
        self,
        market_service: MarketService,
        indicator_service: IndicatorService,
        signal_service: SignalService,
    ):
        self.market_service = market_service
        self.indicator_service = indicator_service
        self.signal_service = signal_service

    def analyze_timeframe(
        self,
        symbol: str,
        interval: str,
    ) -> MarketAnalysis:
        symbol = symbol.upper()

        quote = self.market_service.get_price(symbol)

        metrics = self._calculate_timeframe_metrics(
            symbol=symbol,
            interval=interval,
        )

        rsi_signal = self._get_rsi_signal(metrics["rsi"])

        macd_signal = (
            "bullish"
            if metrics["macd_result"]["macd"] > metrics["macd_result"]["signal"]
            else "bearish"
        )

        macd = MacdValue(
            symbol=symbol,
            indicator="MACD",
            macd=metrics["macd_result"]["macd"],
            signal=metrics["macd_result"]["signal"],
            histogram=metrics["macd_result"]["histogram"],
            interval=interval,
            fast_period=12,
            slow_period=26,
            signal_period=9,
        )

        return MarketAnalysis(
            symbol=symbol,
            interval=interval,
            price=quote.price,
            rsi=metrics["rsi"],
            macd=macd,
            ema_20=metrics["ema_20"],
            ema_50=metrics["ema_50"],
            atr=metrics["atr"],
            trend_strength=round(metrics["trend_strength"], 2),
            trend_strength_label=metrics["trend_strength_label"],
            score=metrics["signal_result"]["score"],
            bias=metrics["signal_result"]["bias"],
            score_breakdown=metrics["signal_result"]["breakdown"],
            signals={
                "rsi": rsi_signal,
                "macd": macd_signal,
                "trend": metrics["trend_direction"],
            },
        )

    def analyze_multi_timeframe(
        self,
        symbol: str,
    ) -> MultiTimeframeAnalysis:
        symbol = symbol.upper()

        quote = self.market_service.get_price(symbol)

        intervals = ["5min", "15min", "1h"]
        timeframe_results = []

        for interval in intervals:

            metrics = self._calculate_timeframe_metrics(
                symbol=symbol,
                interval=interval,
            )

            latest_candle = metrics["candles"][-1]

            timeframe_results.append(
                TimeframeAnalysis(
                    interval=interval,
                    latest_candle_timestamp=latest_candle.timestamp,
                    rsi=metrics["rsi"],
                    ema_20=metrics["ema_20"],
                    ema_50=metrics["ema_50"],
                    atr=metrics["atr"],
                    score=metrics["signal_result"]["score"],
                    bias=metrics["signal_result"]["bias"],
                    trend_direction=metrics["trend_direction"],
                    trend_strength=round(metrics["trend_strength"], 2),
                    trend_strength_label=metrics["trend_strength_label"],
                )
            )

        weights = {
            "5min": 0.20,
            "15min": 0.30,
            "1h": 0.50,
        }

        overall_score = sum(
            timeframe.score * weights[timeframe.interval]
            for timeframe in timeframe_results
        )

        overall_score = round(overall_score, 2)

        if overall_score >= 65:
            overall_bias = "bullish"
        elif overall_score <= 35:
            overall_bias = "bearish"
        else:
            overall_bias = "neutral"

        biases = [timeframe.bias for timeframe in timeframe_results]

        if all(bias == biases[0] for bias in biases):
            timeframe_alignment = "aligned"
        elif biases.count("bullish") >= 2 or biases.count("bearish") >= 2:
            timeframe_alignment = "mostly_aligned"
        else:
            timeframe_alignment = "mixed"

        return MultiTimeframeAnalysis(
            symbol=symbol,
            price=quote.price,
            price_timestamp=quote.timestamp,
            timeframes=timeframe_results,
            overall_score=overall_score,
            overall_bias=overall_bias,
            timeframe_alignment=timeframe_alignment,
        )

    def _calculate_timeframe_metrics(
        self,
        symbol: str,
        interval: str,
    ):
        candles = self.market_service.get_candles(
            symbol=symbol,
            interval=interval,
            output_size=100,
        )

        rsi = self.indicator_service.calculate_rsi(
            candles=candles,
            period=14,
        )

        previous_rsi = self.indicator_service.calculate_rsi(
            candles=candles[:-1],
            period=14,
        )

        rsi_change = round(rsi - previous_rsi, 2)

        macd_result = self.indicator_service.calculate_macd(
            candles=candles,
            fast_period=12,
            slow_period=26,
            signal_period=9,
        )

        ema_20 = self.indicator_service.calculate_ema(
            candles=candles,
            period=20,
        )

        ema_50 = self.indicator_service.calculate_ema(
            candles=candles,
            period=50,
        )

        atr = self.indicator_service.calculate_atr(
            candles=candles,
            period=14,
        )

        trend_direction = "bullish" if ema_20 > ema_50 else "bearish"

        ema_distance = ema_20 - ema_50

        if atr > 0:
            trend_strength = abs(ema_distance) / atr
        else:
            trend_strength = 0

        trend_strength_label = self._get_trend_strength_label(trend_strength)

        signal_result = self.signal_service.calculate_score(
            rsi=rsi,
            rsi_change=rsi_change,
            macd_histogram=macd_result["histogram"],
            ema_20=ema_20,
            ema_50=ema_50,
            atr=atr,
        )

        return {
            "candles": candles,
            "rsi": rsi,
            "previous_rsi": previous_rsi,
            "rsi_change": rsi_change,
            "macd_result": macd_result,
            "ema_20": ema_20,
            "ema_50": ema_50,
            "atr": atr,
            "trend_direction": trend_direction,
            "trend_strength": trend_strength,
            "trend_strength_label": trend_strength_label,
            "signal_result": signal_result,
        }

    def _get_rsi_signal(
        self,
        rsi: float,
    ) -> str:
        if rsi >= 70:
            return "overbought"

        if rsi >= 60:
            return "bullish"

        if rsi <= 30:
            return "oversold"

        if rsi <= 40:
            return "bearish"

        return "neutral"

    def _get_trend_strength_label(
        self,
        trend_strength: float,
    ) -> str:
        if trend_strength >= 1.0:
            return "strong"

        if trend_strength >= 0.4:
            return "moderate"

        return "weak"
