from app.models.agent_models import AgentDecision


class DecisionValidator:
    def validate(self, decision: AgentDecision) -> AgentDecision:
        if decision.action == "hold":
            return decision.model_copy(
                update={
                    "trade_style": "none",
                    "invalidation_reason": None,
                    "target_reason": None,
                    "expected_horizon": None,
                }
            )

        if decision.action in {"long", "short"} and decision.trade_style == "none":
            return AgentDecision(
                action="hold",
                trade_style="none",
                confidence=0.0,
                thesis="Invalid trade decision: actionable direction requires a trade style.",
                invalidation_reason=None,
                target_reason=None,
                expected_horizon=None,
            )

        return decision
