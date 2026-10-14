from app.models.agent_models import AgentDecision, AgentMarketContext


class MockReasoningProvider:
    def make_decision(
        self,
        context: AgentMarketContext,
    ) -> AgentDecision:
        return AgentDecision(
            action="hold",
            trade_style="none",
            confidence=0,
            thesis="Mock reasoning provider is active.",
            invalidation_reason=None,
            target_reason=None,
            expected_horizon=None,
        )
