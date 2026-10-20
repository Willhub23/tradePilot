from typing import Protocol

from app.strategies.models import StrategyEvaluation, StrategyInput


class Strategy(Protocol):
    strategy_id: str

    def evaluate(self, data: StrategyInput) -> StrategyEvaluation: ...
