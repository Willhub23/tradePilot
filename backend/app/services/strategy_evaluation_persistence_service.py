from datetime import datetime, timezone
from sqlalchemy import select
from app.database import SessionLocal
from app.db_models import StrategyEvaluationRecord


class StrategyEvaluationPersistenceService:
    def save(self, evaluation_id, *, status, data, opportunity_id=None, decision_id=None, request_id=None):
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            row = db.get(StrategyEvaluationRecord, evaluation_id, with_for_update=True)
            if row is None:
                row = StrategyEvaluationRecord(id=evaluation_id, strategy_id="quick_trade_v1", symbol="XAUUSD",
                                               created_at=now)
                db.add(row)
            row.status, row.updated_at, row.data = status, now, data
            if opportunity_id: row.opportunity_id = opportunity_id
            if decision_id: row.agent_decision_id = decision_id
            if request_id: row.request_id = request_id
            db.commit()

    def get_recent(self, limit=50):
        if limit < 1: raise ValueError("limit must be positive")
        with SessionLocal() as db:
            return list(db.scalars(select(StrategyEvaluationRecord).order_by(
                StrategyEvaluationRecord.created_at.desc()).limit(limit)).all())
