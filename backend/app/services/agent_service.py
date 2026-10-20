from datetime import datetime, timezone
from time import perf_counter_ns

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
        reasoning_timing = None

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
            started_at = datetime.now(timezone.utc)
            started_ns = perf_counter_ns()
            decision = self.reasoning_provider.make_decision(context)
            if context.strategy_evaluation is not None:
                reasoning_timing = {"started_at": started_at.isoformat(),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "duration_ms": (perf_counter_ns()-started_ns)/1e6}
            validated_decision = self.validator.validate(decision)
            decision = self.grounding_validator.validate(
                context=context,
                decision=validated_decision,
            )

        if context.strategy_evaluation is not None and decision.action != "hold":
            candidate = context.strategy_evaluation.proposal
            if (candidate is None or decision.action != candidate.direction
                    or decision.trade_style != "intraday"):
                decision = AgentDecision(action="hold", trade_style="none", confidence=0,
                    thesis="AI assessment did not approve the supplied Quick Trade proposal unchanged.")

        print("=== FINAL AGENT DECISION ===")
        print(decision.model_dump_json(indent=2))

        decision_id = self.persistence.save_decision(context, decision)
        self.logger.log_decision(context=context, decision=decision)
        return PersistedAgentDecision(decision_id=decision_id, decision=decision, reasoning_timing=reasoning_timing)
