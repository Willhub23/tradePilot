"""Read-only journal projection over existing records; no runtime dependencies."""
from datetime import timezone
from collections import Counter

from sqlalchemy import select, func, case, or_
from app.database import SessionLocal
from app.db_models import StrategyEvaluationRecord as S, AgentDecisionRecord as A, BrokerExecutionRecord as B
from app.services.strategy_diagnostics import obj


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def columns(record):
    if record is None:
        return None
    return {c.name: utc(v) if hasattr(v := getattr(record, c.name), 'tzinfo') else v
            for c in record.__table__.columns}


class DecisionJournalService:
    @staticmethod
    def query(*, since=None, until=None, outcome=None, setup=None, direction=None):
        report = S.data['evaluation']
        evaluation = report['evaluation']
        raw = report['outcome'].as_string()
        category = case(
            (B.status == 'filled', 'filled'),
            (B.status == 'rejected', 'broker_rejected'),
            (B.status == 'cancelled', 'broker_cancelled'),
            (B.status == 'unknown', 'execution_uncertain'),
            (B.status == 'prepared', 'execution_pending'),
            (or_(B.broker_order_id.is_not(None), B.status.in_(['submitted', 'pending'])), 'submitted'),
            (A.action == 'hold', 'ai_hold'),
            (S.status == 'ai_rejected', 'ai_rejected'),
            (S.status.in_(['blocked', 'skipped', 'expired', 'risk_rejected']), 'blocked'),
            (S.status.in_(['incomplete_input', 'error']), 'incomplete'),
            (raw == 'incomplete_input', 'incomplete'),
            else_=func.coalesce(raw, S.status))
        q = select(S, A, B, category.label('outcome')).outerjoin(A, S.agent_decision_id == A.id).outerjoin(B, S.request_id == B.request_id)
        if since is not None: q = q.where(S.created_at >= since)
        if until is not None: q = q.where(S.created_at < until)
        if outcome is not None: q = q.where(category == outcome)
        if setup is not None: q = q.where(evaluation['setup_type'].as_string() == setup)
        if direction is not None: q = q.where(evaluation['proposal']['direction'].as_string() == direction)
        return q

    @staticmethod
    def item(row, detail=False):
        record, agent, execution, outcome = row
        data = obj(record.data)
        report = obj(data.get('evaluation'))
        evaluation = obj(report.get('evaluation'))
        reasons = []
        for group in (evaluation.get('rejection_reasons'), report.get('input_failure_reasons')):
            for reason in group or []:
                if isinstance(reason, dict) and reason not in reasons: reasons.append(reason)
        return {
            'id': record.id, 'created_at': utc(record.created_at), 'updated_at': utc(record.updated_at),
            'strategy_id': record.strategy_id, 'symbol': record.symbol,
            'mode': 'observation' if data.get('mode') == 'observation' else 'execution_pipeline',
            'status': record.status, 'outcome': outcome, 'strategy_outcome': report.get('outcome'),
            'setup_type': evaluation.get('setup_type'),
            'direction': obj(evaluation.get('proposal')).get('direction'),
            'reference_price': report.get('reference_price'), 'market_timestamp': evaluation.get('market_timestamp'),
            'detected_at': report.get('detected_at'), 'opportunity_id': record.opportunity_id,
            'decision_id': record.agent_decision_id, 'request_id': record.request_id,
            'decision_reason': evaluation.get('decision_reason'), 'block_reason': data.get('block_reason'),
            'rejection_reasons': reasons, 'proposal': evaluation.get('proposal'),
            **({'supporting_signals': evaluation.get('supporting_signals') or [],
                'conflicting_signals': evaluation.get('conflicting_signals') or [],
                'invalidation_condition': evaluation.get('invalidation_condition'),
                'market_conditions': report.get('timeframe_observations'),
                'execution_evidence': report.get('execution_observations'),
                'broker_snapshot': data.get('broker_snapshot'), 'costs': data.get('costs'),
                'price_consistency': report.get('price_consistency'), 'missing_inputs': report.get('missing_inputs') or [],
                'input_error': data.get('input_error'),
                'timing': data.get('timing') or report.get('timing') or {},
                'ai_decision': columns(agent), 'broker_execution': columns(execution),
                'raw_data': data} if detail else {})}

    def history(self, *, page=1, page_size=20, **filters):
        q = self.query(**filters)
        with SessionLocal() as db:
            total = db.scalar(select(func.count()).select_from(q.subquery()))
            rows = db.execute(q.order_by(S.created_at.desc(), S.id.desc()).offset((page-1)*page_size).limit(page_size)).all()
            return {'items': [self.item(r) for r in rows], 'total': total, 'page': page, 'page_size': page_size}

    def detail(self, evaluation_id):
        with SessionLocal() as db:
            row = db.execute(self.query().where(S.id == evaluation_id)).first()
            return self.item(row, detail=True) if row else None

    def summary(self, **filters):
        # Same filters and stage semantics as history. Summaries never run a strategy.
        with SessionLocal() as db:
            rows = db.execute(self.query(**filters)).all()
            items = [self.item(row) for row in rows]
        counts = Counter(i['outcome'] for i in items)
        reasons = Counter(code for item in items for code in
            {r.get('code') for r in item['rejection_reasons'] if r.get('code')})
        return {'total': len(items), 'eligible': counts['eligible'], 'rejected': counts['rejected'],
            'incomplete': counts['incomplete'], 'executed': counts['submitted']+counts['filled'],
            'filled': counts['filled'], 'blocked': counts['blocked'], 'outcomes': dict(counts),
            'observation_only': sum(i['mode']=='observation' for i in items),
            'rejection_frequencies': dict(reasons.most_common()),
            'count_semantics': 'Latest pipeline stage; submitted is not filled. Eligible is a proposal, not approval. Reason codes counted once per evaluation.'}
