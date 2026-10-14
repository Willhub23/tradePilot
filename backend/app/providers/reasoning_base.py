from typing import Protocol

from app.models.agent_models import AgentDecision, AgentMarketContext


class ReasoningProvider(Protocol):
    def make_decision(
        self,
        context: AgentMarketContext,
    ) -> AgentDecision: ...
