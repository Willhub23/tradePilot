from app.models.market_models import (
    MacdValue,
    MarketAnalysis,
    MultiTimeframeAnalysis,
    TimeframeAnalysis,
)
from app.services.market_service import MarketService
from app.services.indicator_service import IndicatorService
from app.services.signal_service import SignalService
from app.services.market_structure_service import MarketStructureService
from app.services.volatility_service import VolatilityService
from app.services.market_regime_service import MarketRegimeService
from app.services.moving_average_service import MovingAverageService
from app.services.price_action_service import PriceActionService


class AnalysisService:
    def __init__(
        self,
        market_service: MarketService,
        indicator_service: IndicatorService,
        signal_service: SignalService,
        market_structure_service: MarketStructureService,
        volatility_service: VolatilityService,
        market_regime_service: MarketRegimeService,
        moving_average_service: MovingAverageService,
        price_action_service: PriceActionService,
    ):
        self.market_service = market_service
        self.indicator_service = indicator_service
        self.signal_service = signal_service
        self.market_structure_service = market_structure_service
        self.volatility_service = volatility_service
        self.market_regime_service = market_regime_service
        self.moving_average_service = moving_average_service
        self.price_action_service = price_action_service

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
        structure = metrics["market_structure"]

        volatility = metrics["volatility"]

        rsi_signal = self._get_rsi_signal(metrics["rsi"])

        ma = metrics["moving_average_context"]

        price_action = self._build_price_action_context(metrics["price_action"])

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
            structure=structure["structure"],
            high_pattern=structure["high_pattern"],
            low_pattern=structure["low_pattern"],
            structure_break=structure["structure_break"],
            support=structure["support"],
            resistance=structure["resistance"],
            distance_to_swing_high=structure["distance_to_swing_high"],
            distance_to_swing_low=structure["distance_to_swing_low"],
            atr_ratio=volatility["atr_ratio"],
            volatility_regime=volatility["volatility_regime"],
            market_regime=metrics["market_regime"],
            ema_9=ma["emas"][9],
            ema_100=ma["emas"][100],
            ema_200=ma["emas"][200],
            ema_stack=ma["stack"],
            price_vs_ema_20=ma["price_position"][20],
            price_vs_ema_50=ma["price_position"][50],
            price_vs_ema_200=ma["price_position"][200],
            distance_to_ema_20_atr=ma["atr_distances"][20],
            distance_to_ema_50_atr=ma["atr_distances"][50],
            distance_to_ema_200_atr=ma["atr_distances"][200],
            ema_alignment=ma["ema_alignment"],
            ema_spacing=ma["ema_spacing"],
            ema_20_slope=ma["slopes"][20],
            ema_50_slope=ma["slopes"][50],
            ema_200_slope=ma["slopes"][200],
            adx=metrics["adx_result"]["adx"],
            plus_di=metrics["adx_result"]["plus_di"],
            minus_di=metrics["adx_result"]["minus_di"],
            price_action=price_action,
        )

    def analyze_multi_timeframe(
        self,
        symbol: str,
    ) -> MultiTimeframeAnalysis:
        symbol = symbol.upper()

        quote = self.market_service.get_price(symbol)

        intervals = ["5min", "15min", "1h", "4h"]
        timeframe_results = []

        for interval in intervals:

            metrics = self._calculate_timeframe_metrics(
                symbol=symbol,
                interval=interval,
            )

            structure = metrics["market_structure"]

            volatility = metrics["volatility"]

            latest_candle = metrics["candles"][-1]

            ma = metrics["moving_average_context"]

            price_action = self._build_price_action_context(metrics["price_action"])

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
                    structure=structure["structure"],
                    high_pattern=structure["high_pattern"],
                    low_pattern=structure["low_pattern"],
                    structure_break=structure["structure_break"],
                    support=structure["support"],
                    resistance=structure["resistance"],
                    distance_to_swing_high=structure["distance_to_swing_high"],
                    distance_to_swing_low=structure["distance_to_swing_low"],
                    atr_ratio=volatility["atr_ratio"],
                    volatility_regime=volatility["volatility_regime"],
                    market_regime=metrics["market_regime"],
                    ema_9=ma["emas"][9],
                    ema_100=ma["emas"][100],
                    ema_200=ma["emas"][200],
                    ema_stack=ma["stack"],
                    price_vs_ema_20=ma["price_position"][20],
                    price_vs_ema_50=ma["price_position"][50],
                    price_vs_ema_200=ma["price_position"][200],
                    distance_to_ema_20_atr=ma["atr_distances"][20],
                    distance_to_ema_50_atr=ma["atr_distances"][50],
                    distance_to_ema_200_atr=ma["atr_distances"][200],
                    ema_alignment=ma["ema_alignment"],
                    ema_spacing=ma["ema_spacing"],
                    ema_20_slope=ma["slopes"][20],
                    ema_50_slope=ma["slopes"][50],
                    ema_200_slope=ma["slopes"][200],
                    adx=metrics["adx_result"]["adx"],
                    plus_di=metrics["adx_result"]["plus_di"],
                    minus_di=metrics["adx_result"]["minus_di"],
                    price_action=price_action,
                )
            )

        weights = {"5min": 0.10, "15min": 0.20, "1h": 0.30, "4h": 0.40}

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
            output_size=250,
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

        atr_series = self.indicator_service.calculate_atr_series(
            candles=candles,
            period=14,
        )

        volatility = self.volatility_service.calculate_regime(
            atr_values=atr_series[-20:],
        )

        moving_average_context = self.moving_average_service.analyze(
            candles=candles,
            atr=atr,
            periods=[9, 20, 50, 100, 200],
        )

        adx_result = self.indicator_service.calculate_adx(
            candles=candles,
            period=14,
        )

        price_action = self.price_action_service.analyze(candles=candles, atr=atr)

        market_structure = self.market_structure_service.analyze(candles)

        trend_direction = moving_average_context["trend_direction"]
        trend_strength = moving_average_context["trend_strength"]
        trend_strength_label = moving_average_context["trend_strength_label"]

        market_regime = self.market_regime_service.classify(
            structure=market_structure["structure"],
            trend_direction=trend_direction,
            trend_strength_label=trend_strength_label,
            structure_break=market_structure["structure_break"],
            adx=adx_result["adx"],
            plus_di=adx_result["plus_di"],
            minus_di=adx_result["minus_di"],
        )
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
            "market_structure": market_structure,
            "volatility": volatility,
            "market_regime": market_regime,
            "moving_average_context": moving_average_context,
            "adx_result": adx_result,
            "price_action": price_action,
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

    def _build_price_action_context(
        self,
        price_action: dict,
    ) -> dict:
        candle = price_action["candle"]
        displacement = price_action["displacement"]
        fvg_context = price_action["fvg_context"]

        def build_fvg(fvg: dict | None):
            if fvg is None:
                return None

            return {
                "lower": fvg["lower"],
                "upper": fvg["upper"],
                "distance_atr": fvg["distance_atr"],
                "size_atr": fvg["size_atr"],
                "state": fvg["state"],
                "price_inside": fvg["price_inside"],
            }

        return {
            "rejection": candle["rejection"],
            "displacement_detected": displacement["detected"],
            "displacement_direction": displacement["direction"],
            "displacement_strength": displacement["strength"],
            "nearest_bullish_fvg": build_fvg(fvg_context["nearest_bullish"]),
            "nearest_bearish_fvg": build_fvg(fvg_context["nearest_bearish"]),
            "active_fvg_count": fvg_context["active_count"],
        }
