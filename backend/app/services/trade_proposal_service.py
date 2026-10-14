from app.models.agent_models import AgentDecision, AgentMarketContext
from app.models.trade_models import TradeProposal


class TradeProposalService:
    def create_proposal(
        self,
        context: AgentMarketContext,
        decision: AgentDecision,
        agent_decision_id: str | None = None,
    ) -> TradeProposal | None:
        if decision.action == "hold":
            return None

        return TradeProposal(
            agent_decision_id=agent_decision_id,
            symbol=context.symbol,
            direction=decision.action,
            trade_style=decision.trade_style,
            confidence=decision.confidence,
            thesis=decision.thesis,
        )
