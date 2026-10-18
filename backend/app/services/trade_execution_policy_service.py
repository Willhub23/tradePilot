from dataclasses import dataclass

from app.models.trade_models import TradeProposal
from app.models.market_models import MultiTimeframeAnalysis


@dataclass
class TradeExecutionPlan:
    stop_loss: float
    take_profit: float
    max_holding_minutes: int
    risk_reward_ratio: float


class TradeExecutionPolicyService:
    def build_plan(
        self,
        proposal: TradeProposal,
        market_analysis: MultiTimeframeAnalysis,
        entry_price: float,
    ) -> TradeExecutionPlan:
        if proposal.trade_style == "intraday":
            interval = "15min"
            stop_multiplier = 1.25
            target_multiplier = 1.75
            max_holding_minutes = 360
        else:
            interval = "1h"
            stop_multiplier = 1.5
            target_multiplier = 2.0
            max_holding_minutes = 2880

        timeframe = next(
            (tf for tf in market_analysis.timeframes if tf.interval == interval),
            None,
        )

        if timeframe is None:
            raise ValueError(f"Missing {interval} analysis for trade execution plan")

        atr = timeframe.atr

        if proposal.direction == "long":
            stop_loss = entry_price - (stop_multiplier * atr)
            take_profit = entry_price + (target_multiplier * atr)
        else:
            stop_loss = entry_price + (stop_multiplier * atr)
            take_profit = entry_price - (target_multiplier * atr)

        return TradeExecutionPlan(
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_holding_minutes=max_holding_minutes,
            risk_reward_ratio=target_multiplier / stop_multiplier,
        )
