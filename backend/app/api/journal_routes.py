from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from app.services.decision_journal_service import DecisionJournalService


def filters(since: datetime | None = None, until: datetime | None = None,
            outcome: Literal['eligible','rejected','incomplete','blocked','ai_hold','ai_rejected','submitted','filled','started','error','broker_rejected','broker_cancelled','execution_uncertain','execution_pending'] | None = None,
            setup: str | None = Query(None, max_length=100), direction: Literal['long','short'] | None = None):
    for value in (since, until):
        if value is not None and value.utcoffset() is None:
            raise HTTPException(422, 'Date filters require timezone-aware timestamps')
    if since and until and since >= until:
        raise HTTPException(422, 'until must be after since (exclusive upper bound)')
    return dict(since=since.astimezone(timezone.utc) if since else None,
                until=until.astimezone(timezone.utc) if until else None,
                outcome=outcome, setup=setup, direction=direction)


def create_journal_router(service=None):
    service = service or DecisionJournalService()
    router = APIRouter(prefix='/api/journal', tags=['Decision Journal'])

    @router.get('/evaluations')
    def history(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), selected=Depends(filters)):
        return service.history(page=page, page_size=page_size, **selected)

    @router.get('/summary')
    def summary(selected=Depends(filters)):
        return service.summary(**selected)

    @router.get('/evaluations/{evaluation_id}')
    def detail(evaluation_id: str):
        result = service.detail(evaluation_id)
        if result is None: raise HTTPException(404, 'Strategy evaluation not found')
        return result

    return router
