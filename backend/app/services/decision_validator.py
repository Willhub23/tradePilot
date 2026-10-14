from app.models.agent_models import AgentDecision


class DecisionValidator:
    def validate(
        self,
        decision: AgentDecision,
    ) -> AgentDecision:
        if decision.action == "hold":
            decision.trade_style = "none"
            decision.invalidation_reason = None
            decision.target_reason = None
            decision.expected_horizon = None

        return decision
