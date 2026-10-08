import json
from datetime import datetime, timezone
from pathlib import Path

from app.models.decision_models import DecisionLog
from app.models.market_models import MarketAnalysis


class DecisionLogService:
    def __init__(
        self,
        log_path: str = "data/decision_logs.jsonl",
    ):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    def log_analysis(
        self,
        analysis: MarketAnalysis,
    ) -> DecisionLog:
        decision = DecisionLog(
            timestamp=datetime.now(timezone.utc),
            symbol=analysis.symbol,
            interval=analysis.interval,
            price=analysis.price,
            score=analysis.score,
            bias=analysis.bias,
            rsi=analysis.rsi,
            macd_histogram=analysis.macd.histogram,
            ema_20=analysis.ema_20,
            ema_50=analysis.ema_50,
            atr=analysis.atr,
            trend_direction=analysis.signals["trend"],
            trend_strength=analysis.trend_strength,
            trend_strength_label=analysis.trend_strength_label,
            score_breakdown=analysis.score_breakdown,
        )

        with self.log_path.open(
            "a",
            encoding="utf-8",
        ) as file:
            file.write(decision.model_dump_json() + "\n")

        return decision
