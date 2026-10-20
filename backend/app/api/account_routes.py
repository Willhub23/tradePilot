from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.services.account_dashboard_service import AccountUnavailable

router=APIRouter(prefix='/api/account',tags=['Broker Account — read only'])


def period(since: datetime | None=None, until: datetime | None=None):
    end=until or datetime.now(timezone.utc).replace(microsecond=0)
    start=since or end-timedelta(days=30)
    if start.utcoffset() is None or end.utcoffset() is None:
        raise HTTPException(422,'Timezone-aware dates required')
    start,end=start.astimezone(timezone.utc),end.astimezone(timezone.utc)
    if start>=end or end-start>timedelta(days=90) or start.timestamp()<0 or end.timestamp()*1000>2147483646000:
        raise HTTPException(422,'Use an increasing date range of at most 90 days within broker timestamp limits')
    return start,end


def service(request:Request):
    result=getattr(request.app.state,'account_dashboard',None)
    if result is None or not result.started:
        raise HTTPException(503,detail=getattr(request.app.state,'account_error',None) or 'Read-only account connection is disabled or unavailable')
    return result


def read(call):
    try: return call()
    except AccountUnavailable as exc: raise HTTPException(503,detail=str(exc)) from None
    except Exception: raise HTTPException(503,detail='Broker account data unavailable; no stale fallback returned') from None


@router.get('/overview')
def overview(account=Depends(service)):
    return read(account.overview)


@router.get('/positions')
def positions(account=Depends(service)):
    return read(account.positions)


@router.get('/trades')
def trades(page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),dates=Depends(period),account=Depends(service)):
    return read(lambda:account.trades(*dates,page=page,page_size=page_size))


@router.get('/performance')
def performance(dates=Depends(period),account=Depends(service)):
    return read(lambda:account.performance(*dates))
