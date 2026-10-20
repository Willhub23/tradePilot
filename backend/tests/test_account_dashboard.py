from datetime import datetime,timedelta,timezone
from decimal import Decimal
from unittest.mock import Mock,patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from ctrader_open_api.messages import OpenApiMessages_pb2 as M,OpenApiModelMessages_pb2 as D
from app.database import Base
from app.db_models import BrokerExecutionRecord as B, StrategyEvaluationRecord as S
from app.services import account_dashboard_service as module
from app.services.account_dashboard_service import AccountDashboardService,AccountUnavailable,money
from app.api.account_routes import router
from app.integrations.ctrader.client import CTraderClient

NOW=datetime(2026,10,20,10,tzinfo=timezone.utc)
START=NOW-timedelta(days=1)


def deal(id=1,position=1,gross=10000,swap=-100,commission=-200,fee=50,**changes):
    d=D.ProtoOADeal(dealId=id,orderId=id+100,positionId=position,volume=100,filledVolume=100,
        symbolId=7,createTimestamp=int(NOW.timestamp()*1000)-100,executionTimestamp=int(NOW.timestamp()*1000),
        tradeSide=2,dealStatus=2,moneyDigits=2,commission=-99999)
    d.closePositionDetail.CopyFrom(D.ProtoOAClosePositionDetail(entryPrice=4200,grossProfit=gross,
        swap=swap,commission=commission,balance=100000,moneyDigits=2,pnlConversionFee=fee,closedVolume=100))
    for key,value in changes.items(): setattr(d,key,value)
    return d


@pytest.fixture
def account(monkeypatch,tmp_path):
    monkeypatch.setattr(module,'sleep',lambda _:None)
    engine=create_engine(f'sqlite:///{tmp_path / "account.db"}');Base.metadata.create_all(engine)
    sessions=sessionmaker(bind=engine);monkeypatch.setattr(module,'SessionLocal',sessions)
    client=Mock(account_id=49167599,environment='demo',authenticated=True,demo_verified=True,generation=1)
    client.wait_until_authenticated.return_value=True
    trader=D.ProtoOATrader(ctidTraderAccountId=client.account_id,balance=123456,depositAssetId=4,moneyDigits=2,
        managerBonus=0,ibBonus=0,nonWithdrawableBonus=0)
    responses={
        M.ProtoOATraderReq:M.ProtoOATraderRes(ctidTraderAccountId=client.account_id,trader=trader),
        M.ProtoOAAssetListReq:M.ProtoOAAssetListRes(ctidTraderAccountId=client.account_id,asset=[D.ProtoOAAsset(assetId=4,name='GBP')]),
        M.ProtoOAReconcileReq:M.ProtoOAReconcileRes(ctidTraderAccountId=client.account_id),
        M.ProtoOAGetPositionUnrealizedPnLReq:M.ProtoOAGetPositionUnrealizedPnLRes(ctidTraderAccountId=client.account_id,moneyDigits=2),
        M.ProtoOASymbolsListReq:M.ProtoOASymbolsListRes(ctidTraderAccountId=client.account_id,symbol=[D.ProtoOALightSymbol(symbolId=7,symbolName='XAUUSD')]),
        M.ProtoOADealListReq:M.ProtoOADealListRes(ctidTraderAccountId=client.account_id,hasMore=False,deal=[deal()])}
    client.send.side_effect=lambda req:responses[type(req)]
    service=AccountDashboardService(client,Mock(assert_owner=Mock()));service.start()
    yield service,responses,sessions
    service.stop();engine.dispose()


def test_currency_scaling_and_equity(account):
    s,r,_=account
    result=s.overview()
    assert result['currency']=='GBP' and Decimal(result['balance'])==Decimal('1234.56')
    assert result['unrealized_pnl']=='0' and result['equity']=='1234.56'
    assert result['account_id']=='49167599'
    assert not s.client.order_guard() and not s.client.close_guard()


@pytest.mark.parametrize('digits',[0,2,5,8])
def test_money_uses_reported_digits(digits):
    t=D.ProtoOATrader(balance=123456789,moneyDigits=digits)
    assert money(t,'balance')==Decimal(123456789)/Decimal(10)**digits


def test_missing_metadata_never_assumed_zero(account):
    s,r,_=account
    r[M.ProtoOATraderReq].trader.ClearField('moneyDigits')
    result=s.overview()
    assert result['balance'] is None and result['equity'] is None
    assert 'balance' in result['unavailable']


def test_bonus_equity_not_fabricated(account):
    s,r,_=account;r[M.ProtoOATraderReq].trader.ClearField('managerBonus')
    assert s.overview()['equity'] is None


def test_position_units_and_unrealized_pnl(account):
    s,r,_=account
    p=D.ProtoOAPosition(positionId=88,positionStatus=1,swap=-50,moneyDigits=2,price=4200,
        tradeData=D.ProtoOATradeData(symbolId=7,volume=12345,tradeSide=1,openTimestamp=int(NOW.timestamp()*1000)))
    r[M.ProtoOAReconcileReq].position.append(p)
    r[M.ProtoOAGetPositionUnrealizedPnLReq].positionUnrealizedPnL.append(D.ProtoOAPositionUnrealizedPnL(positionId=88,grossUnrealizedPnL=1000,netUnrealizedPnL=800))
    assert s.overview()['unrealized_pnl']=='8'
    position=s.positions()['items'][0]
    assert position['volume_units']=='123.45' and position['volume_lots'] is None
    assert position['symbol']=='XAUUSD' and position['swap']=='-0.5'


def test_mismatched_pnl_snapshot_is_unavailable(account):
    s,r,_=account
    r[M.ProtoOAGetPositionUnrealizedPnLReq].positionUnrealizedPnL.add(positionId=99,grossUnrealizedPnL=1,netUnrealizedPnL=1)
    assert s.overview()['unrealized_pnl'] is None


def test_closed_deal_net_not_double_counted(account):
    s,_,_=account
    row=s.trades(START,NOW+timedelta(seconds=1))['items'][0]
    assert row['net_after_reported_costs']=='96.5'
    assert row['volume_units']=='1' and row['filled_volume_units']=='1'
    assert row['attribution']=='broker_only' and row['linkage']==[]


def test_missing_optional_fee_and_scaling(account):
    s,r,_=account
    r[M.ProtoOADealListReq].deal[0].closePositionDetail.ClearField('pnlConversionFee')
    result=s.trades(START,NOW+timedelta(seconds=1))
    assert result['items'][0]['conversion_fee'] is None
    assert result['items'][0]['costs_complete'] is False
    assert result['items'][0]['net_after_reported_costs']=='97'
    s.cache.clear();r[M.ProtoOADealListReq].deal[0].closePositionDetail.ClearField('moneyDigits')
    assert s.performance(START,NOW+timedelta(seconds=1))['realized_net'] is None


def test_partial_closes_group_by_position_for_performance(account):
    s,r,_=account
    r[M.ProtoOADealListReq].deal.clear()
    for d in [deal(1,1,1000,0,0,0),deal(2,1,2000,0,0,0),deal(3,2,-1000,0,0,0)]: r[M.ProtoOADealListReq].deal.append(d)
    result=s.performance(START,NOW+timedelta(seconds=1))
    assert result['closing_deals']==3 and result['positions_with_realizations']==2
    assert result['win_rate']==50 and result['profit_factor']=='3'
    assert result['average_win']=='30' and result['average_loss']=='-10'
    assert result['realized_net']=='20'


def test_empty_history_no_false_zero_win_rate(account):
    s,r,_=account;r[M.ProtoOADealListReq].deal.clear()
    result=s.performance(START,NOW+timedelta(seconds=1))
    assert result['closing_deals']==0 and result['win_rate'] is None and result['profit_factor'] is None


def test_history_pagination_dedup_and_filtering(account):
    s,r,_=account
    r[M.ProtoOADealListReq].deal.extend([deal(),deal(2,executionTimestamp=int(NOW.timestamp()*1000)-1),deal(3,executionTimestamp=0)])
    result=s.trades(START,NOW+timedelta(seconds=1),page=2,page_size=1)
    assert result['total']==2 and result['items'][0]['deal_id']=='2'
    requests=[c.args[0] for c in s.client.send.call_args_list if isinstance(c.args[0],M.ProtoOADealListReq)]
    assert requests[0].fromTimestamp==int(START.timestamp()*1000)


def test_broker_truncation_splits_windows_without_skipping_boundaries(account):
    s,r,_=account;calls=[]
    original=s.client.send.side_effect
    def send(req):
        if isinstance(req,M.ProtoOADealListReq):
            calls.append((req.fromTimestamp,req.toTimestamp))
            if len(calls)==1: return M.ProtoOADealListRes(ctidTraderAccountId=s.client.account_id,hasMore=True)
        return original(req)
    s.client.send.side_effect=send
    s.trades(START,NOW+timedelta(seconds=1))
    assert len(calls)==3
    assert sorted(calls[1:])[0][1]+1==sorted(calls[1:])[1][0]


@pytest.mark.parametrize('state',['disconnect','live','unverified','lease'])
def test_no_stale_values_when_connection_unsafe(account,state):
    s,_,_=account;s.overview()
    if state=='disconnect':s.client.authenticated=False
    if state=='live':s.client.environment='live'
    if state=='unverified':s.client.demo_verified=False
    if state=='lease':s.store.assert_owner.side_effect=RuntimeError('lost')
    with pytest.raises((RuntimeError,AccountUnavailable)):s.overview()


def test_cache_avoids_duplicate_requests(account):
    s,_,_=account;s.overview();count=s.client.send.call_count
    assert s.overview()['cached'] is True and s.client.send.call_count==count
    s.client.generation+=1
    assert s.overview()['cached'] is False


def test_auth_failure_and_ownership_before_start():
    client,store=Mock(),Mock();client.wait_until_authenticated.return_value=False
    service=AccountDashboardService(client,store)
    with pytest.raises(AccountUnavailable):service.start()
    client.stop.assert_called_once();store.release.assert_called_once()
    client.reset_mock();store.acquire.side_effect=RuntimeError('owned')
    with pytest.raises(RuntimeError):service.start()
    client.start.assert_not_called()


def test_linkage_is_exact_and_does_not_modify_records(account):
    s,_,sessions=account
    with sessions() as db:
        db.add(B(request_id='r',symbol='XAUUSD',status='filled',request_data={},broker_position_id='1',created_at=NOW,updated_at=NOW))
        db.flush();db.add(S(id='evaluation',request_id='r',strategy_id='quick_trade_v1',symbol='XAUUSD',status='filled',data={},created_at=NOW,updated_at=NOW));db.commit()
    row=s.trades(START,NOW+timedelta(seconds=1))['items'][0]
    assert row['linkage'][0]['evaluation_id']=='evaluation'
    assert row['linkage'][0]['match']=='position'


@pytest.mark.parametrize('broker_request',[M.ProtoOANewOrderReq(),M.ProtoOAClosePositionReq(),M.ProtoOAAmendPositionSLTPReq(),M.ProtoOACancelOrderReq()])
def test_read_only_client_rejects_all_mutations(monkeypatch,broker_request):
    for k,v in {'CTRADER_CLIENT_ID':'id','CTRADER_CLIENT_SECRET':'secret','CTRADER_ACCESS_TOKEN':'token','CTRADER_ACCOUNT_ID':'49167599','CTRADER_ENVIRONMENT':'demo'}.items():monkeypatch.setenv(k,v)
    with patch('app.integrations.ctrader.client.Client'):
        client=CTraderClient(read_only=True)
    client._authenticated.set();client.demo_verified=True
    with pytest.raises(RuntimeError,match='Read-only'):client.send(broker_request)
    client._client.send.assert_not_called()


def test_api_filters_errors_and_read_only(account):
    s,_,_=account;app=FastAPI();app.include_router(router);app.state.account_dashboard=s
    with TestClient(app) as web:
        assert web.get('/api/account/overview').json()['currency']=='GBP'
        assert web.post('/api/account/positions').status_code==405
        assert web.get('/api/account/trades?since=2026-10-20T00:00:00').status_code==422
        assert web.get('/api/account/trades?page_size=101').status_code==422
        assert web.get('/api/account/trades?since=2025-01-01T00:00:00Z&until=2026-01-01T00:00:00Z').status_code==422
        s.client.authenticated=False
        assert web.get('/api/account/overview').status_code==503


def test_lifecycle_disabled_and_no_scheduler(monkeypatch):
    monkeypatch.delenv('ACCOUNT_DASHBOARD_ENABLED',raising=False)
    with patch('app.services.account_dashboard_lifecycle.build_account_service') as factory:
        from app.journal_main import app
        with TestClient(app) as web:
            assert web.get('/api/account/overview').status_code==503
            assert not hasattr(app.state,'trading_scheduler')
        factory.assert_not_called()


def test_lifecycle_one_connection_failure_and_cleanup(monkeypatch):
    monkeypatch.setenv('ACCOUNT_DASHBOARD_ENABLED','true')
    service=Mock(started=True)
    with patch('app.services.account_dashboard_lifecycle.build_account_service',return_value=service) as factory:
        from app.journal_main import app
        with TestClient(app):pass
        factory.assert_called_once();service.start.assert_called_once();service.stop.assert_called_once()
    service=Mock(started=False);service.start.side_effect=RuntimeError('owned')
    with patch('app.services.account_dashboard_lifecycle.build_account_service',return_value=service):
        with TestClient(app) as web:assert web.get('/api/account/overview').status_code==503
        service.stop.assert_not_called()


@pytest.mark.parametrize('kind',['rate_limit','incomplete','account_mismatch','transport'])
def test_broker_failures_do_not_return_stale_success(account,kind):
    s,r,_=account
    if kind=='rate_limit': r[M.ProtoOATraderReq]=M.ProtoOAErrorRes(ctidTraderAccountId=s.client.account_id,errorCode='REQUEST_FREQUENCY_EXCEEDED')
    if kind=='incomplete': r[M.ProtoOATraderReq].trader.ClearField('balance')
    if kind=='account_mismatch': r[M.ProtoOATraderReq].ctidTraderAccountId=123
    if kind=='transport': s.client.send.side_effect=TimeoutError('timeout')
    with pytest.raises((AccountUnavailable,TimeoutError)):s.overview()
    assert 'overview' not in s.cache


def test_expired_cache_is_not_used_after_failed_refresh(account):
    s,_,_=account;s.overview()
    generation,_,data=s.cache['overview'];s.cache['overview']=(generation,-1000,data)
    s.client.send.side_effect=TimeoutError()
    with pytest.raises(TimeoutError):s.overview()
    assert 'overview' not in s.cache


def test_same_millisecond_truncation_fails_closed(account):
    s,r,_=account;r[M.ProtoOADealListReq].hasMore=True
    with pytest.raises(AccountUnavailable,match='same millisecond'):
        s.trades(NOW,NOW+timedelta(milliseconds=1))


def test_read_only_view_scope_is_supported_and_live_is_refused(monkeypatch):
    from tests.test_ctrader_runtime import client as client_fixture,accounts
    client=client_fixture.__wrapped__(monkeypatch)
    client._read_only=True
    response=accounts(client);response.permissionScope=D.ProtoOAClientPermissionScope.SCOPE_VIEW
    with patch('app.integrations.ctrader.client.Protobuf.extract',return_value=response):client._on_accounts(Mock())
    assert client.demo_verified
    response.ctidTraderAccount[0].isLive=True
    with patch('app.integrations.ctrader.client.Protobuf.extract',return_value=response),patch('app.integrations.ctrader.client.reactor'):
        client._on_accounts(Mock())
    assert not client.demo_verified


def test_fractional_timestamp_bounds_do_not_include_earlier_deals(account):
    s,_,_=account
    s.trades(NOW+timedelta(microseconds=100),NOW+timedelta(seconds=1,microseconds=100))
    request=next(c.args[0] for c in s.client.send.call_args_list if isinstance(c.args[0],M.ProtoOADealListReq))
    assert request.fromTimestamp==int(NOW.timestamp()*1000)+1
    assert request.toTimestamp==int((NOW+timedelta(seconds=1)).timestamp()*1000)


@pytest.mark.parametrize('stored_account,expected',[(49167599,'tradepilot_linked_position_or_order'),(999,'broker_only'),(None,'unverified_tradepilot_id_match')])
def test_attribution_respects_available_account_scope(account,stored_account,expected):
    s,_,sessions=account
    with sessions() as db:
        db.add(B(request_id='scope',symbol='XAUUSD',status='filled',request_data={'account_id':stored_account} if stored_account else {},
            broker_order_id='101',created_at=NOW,updated_at=NOW));db.commit()
    row=s.trades(START,NOW+timedelta(seconds=1))['items'][0]
    assert row['attribution']==expected
    if row['linkage']: assert row['linkage'][0]['account_verified']==(stored_account==49167599)
