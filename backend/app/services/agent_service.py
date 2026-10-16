from app.models.agent_models import (
    AgentDecision,
    AgentMarketContext,
    PersistedAgentDecision,
)
from app.services.agent_decision_persistence_service import (
    AgentDecisionPersistenceService,
)
from app.providers.reasoning_base import ReasoningProvider
from app.services.decision_validator import DecisionValidator
from app.services.agent_decision_log_service import AgentDecisionLogService
from app.services.grounding_validator import GroundingValidator


class AgentService:
    def __init__(self, reasoning_provider: ReasoningProvider):
        self.reasoning_provider = reasoning_provider
        self.validator = DecisionValidator()
        self.logger = AgentDecisionLogService()
        self.grounding_validator = GroundingValidator()
        self.persistence = AgentDecisionPersistenceService()

    def make_decision(
        self,
        context: AgentMarketContext,
    ) -> PersistedAgentDecision:
        macro = context.macro_context

        if not macro["allow_new_trade"]:
            decision = AgentDecision(
                action="hold",
                trade_style="none",
                confidence=100,
                thesis="A high-impact macro event is too close to justify a new trade.",
                invalidation_reason=None,
                target_reason=None,
                expected_horizon=None,
            )

        else:
            decision = self.reasoning_provider.make_decision(context)
            validated_decision = self.validator.validate(decision)
            decision = self.grounding_validator.validate(
                context=context,
                decision=validated_decision,
            )

        decision_id = self.persistence.save_decision(context, decision)
        self.logger.log_decision(context=context, decision=decision)
        return PersistedAgentDecision(decision_id=decision_id, decision=decision)
