from datetime import datetime, timedelta, timezone
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.db_models import StrategyEvaluationRecord as S, AgentDecisionRecord as A, BrokerExecutionRecord as B
from app.api.journal_routes import create_journal_router
from app.services import decision_journal_service as journal
NOW = datetime(2026,10,20,10,tzinfo=timezone.utc)

@pytest.fixture
def journal_client(monkeypatch,tmp_path):
    engine=create_engine(f'sqlite:///{tmp_path / "journal.db"}')
    Base.metadata.create_all(engine)
    sessions=sessionmaker(bind=engine)
    monkeypatch.setattr(journal,'SessionLocal',sessions)
    report={'outcome':'eligible','reference_price':4200,'timeframe_observations':{'structure':'bullish','atr':1.5},
        'evaluation':{'setup_type':'range_resistance_rejection','status':'eligible',
            'proposal':{'direction':'short','entry_reference':4200,'stop_loss':4202,'take_profit':4197,'max_holding_minutes':30},
            'conflicting_signals':[{'name':'structure','values':{'structure':'bullish'}}]}}
    with sessions() as db:
        db.add(A(id='ai',timestamp=NOW,symbol='XAUUSD',action='hold',trade_style='none',confidence=80,
            thesis='Wait',market_context={},macro_context={}))
        db.add(B(request_id='fill',symbol='XAUUSD',status='filled',request_data={'execution_tracking':{'submission_to_fill_confirmation_ms':123}},
            broker_order_id='order',broker_position_id='position',created_at=NOW,updated_at=NOW))
        db.flush()
        for i,(status,mode,decision,request) in enumerate([('eligible','observation',None,None),('rejected',None,None,None),
            ('incomplete_input',None,None,None),('skipped','observation',None,None),('ai_rejected',None,'ai',None),('submitted',None,None,'fill')]):
            data={'evaluation':report,'mode':mode}
            if status=='rejected': data={'evaluation':{'outcome':'rejected','evaluation':{'rejection_reasons':[
                {'code':'atr_excessive','message':'ATR high','measured':4,'limit':3},{'code':'atr_excessive','message':'Again'}]}}}
            if status=='incomplete_input': data={'input_error':'ConnectionRefusedError'}
            if status=='skipped': data={'mode':mode,'block_reason':'trading_paused'}
            db.add(S(id=str(i),strategy_id='quick_trade_v1',symbol='XAUUSD',status=status,created_at=NOW+timedelta(minutes=i),updated_at=NOW,data=data,agent_decision_id=decision,request_id=request))
        db.commit()
    app=FastAPI();app.include_router(create_journal_router())
    with TestClient(app) as client: yield client,sessions
    engine.dispose()

def test_pagination_read_only(journal_client):
    client,sessions=journal_client
    assert [r['id'] for r in client.get('/api/journal/evaluations?page_size=2').json()['items']]==['5','4']
    assert [r['id'] for r in client.get('/api/journal/evaluations?page_size=2&page=2').json()['items']]==['3','2']
    assert client.post('/api/journal/evaluations').status_code==405
    with sessions() as db: assert db.scalar(select(func.count()).select_from(S))==6

def test_summary_separates_stages(journal_client):
    client,_=journal_client
    s=client.get('/api/journal/summary').json()
    assert [s[k] for k in ['total','eligible','rejected','incomplete','executed','filled','blocked']]==[6,1,1,1,1,1,1]
    assert s['outcomes']['ai_hold']==1 and s['observation_only']==2
    assert s['rejection_frequencies']=={'atr_excessive':1}

def test_countertrend_and_related_records(journal_client):
    client,_=journal_client
    item=client.get('/api/journal/evaluations/0').json()
    assert item['outcome']=='eligible' and item['mode']=='observation'
    assert item['direction']=='short' and item['market_conditions']['structure']=='bullish'
    assert item['proposal']['take_profit']==4197 and item['conflicting_signals']
    assert item['ai_decision'] is None and item['broker_execution'] is None
    assert item['created_at'].endswith(('Z','+00:00'))
    assert client.get('/api/journal/evaluations/4').json()['ai_decision']['id']=='ai'
    fill=client.get('/api/journal/evaluations/5').json()
    assert fill['status']=='submitted' and fill['outcome']=='filled'
    assert fill['broker_execution']['broker_position_id']=='position'

@pytest.mark.parametrize('params,count',[({'outcome':'blocked'},1),({'outcome':'filled'},1),({'direction':'short'},3),
    ({'setup':'range_resistance_rejection'},3),({'since':NOW.isoformat(),'until':(NOW+timedelta(minutes=2)).isoformat()},2)])
def test_consistent_filters(journal_client,params,count):
    client,_=journal_client
    assert client.get('/api/journal/evaluations',params=params).json()['total']==count
    assert client.get('/api/journal/summary',params=params).json()['total']==count

@pytest.mark.parametrize('query',['page=0','page_size=101','direction=buy','outcome=nonsense','since=2026-10-20T00:00:00','since=2026-10-21T00:00:00Z&until=2026-10-20T00:00:00Z'])
def test_invalid_filters(journal_client,query):
    client,_=journal_client
    assert client.get('/api/journal/evaluations?'+query).status_code==422

def test_missing_and_sparse(journal_client):
    client,_=journal_client
    assert client.get('/api/journal/evaluations/missing').status_code==404
    sparse=client.get('/api/journal/evaluations/3').json()
    assert sparse['proposal'] is None and sparse['block_reason']=='trading_paused'

@pytest.mark.parametrize('status,outcome',[('rejected','broker_rejected'),('unknown','execution_uncertain'),('cancelled','broker_cancelled'),('prepared','execution_pending'),('submitted','submitted')])
def test_authoritative_execution_status(journal_client,status,outcome):
    client,sessions=journal_client
    with sessions() as db:
        row=db.get(B,'fill');row.status=status;db.commit()
    assert client.get('/api/journal/evaluations/5').json()['outcome']==outcome

def test_journal_only_no_scheduler(journal_client,monkeypatch):
    monkeypatch.delenv("ACCOUNT_DASHBOARD_ENABLED",raising=False)
    from app.journal_main import app
    with TestClient(app) as client:
        assert client.get('/api/journal/summary').json()['total']==6
        assert client.get('/api/health').json()['service']=='tradepilot-journal'
    assert not hasattr(app.state,'trading_scheduler')
