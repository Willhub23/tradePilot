from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.database import SessionLocal
from app.db_models import AgentDecisionRecord
from app.models.agent_models import AgentDecision, AgentDecisionHistory, AgentMarketContext


class AgentDecisionPersistenceService:
    def save_decision(
        self,
        context: AgentMarketContext,
        decision: AgentDecision,
    ) -> str:
        decision_id = str(uuid4())
        market_context = context.market_analysis.model_dump(mode="json")
        if context.strategy_evaluation is not None:
            market_context["strategy_evaluation"] = context.strategy_evaluation.model_dump(mode="json")
            market_context["opportunity_id"] = context.opportunity_id
        record = AgentDecisionRecord(
            id=decision_id,
            timestamp=datetime.now(timezone.utc),
            symbol=context.symbol,
            **decision.model_dump(),
            market_context=market_context,
            macro_context=context.macro_context,
        )
        with SessionLocal() as db:
            db.add(record)
            db.commit()
        return decision_id

    def get_recent_decisions(self, limit: int = 50) -> list[AgentDecisionHistory]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with SessionLocal() as db:
            records = db.scalars(
                select(AgentDecisionRecord)
                .order_by(AgentDecisionRecord.timestamp.desc(), AgentDecisionRecord.id.desc())
                .limit(limit)
            )
            return [AgentDecisionHistory.model_validate(record) for record in records]

    def get_decision(self, decision_id: str) -> AgentDecisionHistory | None:
        with SessionLocal() as db:
            record = db.get(AgentDecisionRecord, decision_id)
            return AgentDecisionHistory.model_validate(record) if record is not None else None
