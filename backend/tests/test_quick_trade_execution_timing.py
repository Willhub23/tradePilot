from datetime import datetime,timezone,timedelta
from time import perf_counter_ns
from unittest.mock import Mock

import pytest
from app.models.execution_models import BrokerExecutionRequest,BrokerExecutionResult
from app.services.execution_timing import PROCESS_ID
from app.services.ctrader_execution_event_service import CTraderExecutionEventService
from app.providers.ctrader_broker_provider import CTraderBrokerProvider
from tests.test_ctrader_execution_events import persistence, event, Type
from tests.test_quick_trade_live_validation import metadata


def request(persistence,process_id=PROCESS_ID):
    start=perf_counter_ns()
    request=BrokerExecutionRequest(request_id='quick',agent_decision_id=None,opportunity_id='opportunity',
        strategy_id='quick_trade_v1',symbol='XAUUSD',direction='long',volume_lots=.01,
        reference_price=4200,stop_loss=4198.35,take_profit=4203,max_holding_minutes=30,
        trade_style='intraday',confidence=80,thesis='Test',
        timing={'process_id':process_id,'submission_monotonic_ns':start,
                'submission_started_at':datetime.now(timezone.utc).isoformat()})
    persistence.save_request(request)
    return request,start


def test_async_fill_receipt_and_broker_event_time_are_distinct(persistence):
    r,start=request(persistence)
    received=datetime.now(timezone.utc)
    message=event(Type.ORDER_FILLED,request_id=r.request_id,executed=100,price=4200)
    message.order.utcLastUpdateTimestamp=int((received-timedelta(seconds=1)).timestamp()*1000)
    handler=CTraderExecutionEventService(123,persistence)
    handler.process(message,received_at=received,received_monotonic_ns=start+125000000)
    row=persistence.find_execution(request_id=r.request_id)
    tracking=row.request_data['execution_tracking']
    assert tracking['submission_to_fill_confirmation_ms'] == 125
    assert tracking['fill_confirmation_received_at'] == received.isoformat()
    assert tracking['broker_event_time_source'] == 'order.utcLastUpdateTimestamp'
    assert tracking['broker_fill_event_at'] != tracking['fill_confirmation_received_at']
    handler.process(message,received_at=received+timedelta(seconds=1),received_monotonic_ns=start+500000000)
    assert persistence.find_execution(request_id=r.request_id).request_data['execution_tracking'] == tracking


def test_accepted_order_is_not_timed_as_a_fill(persistence):
    r,start=request(persistence)
    CTraderExecutionEventService(123,persistence).process(event(Type.ORDER_ACCEPTED,request_id=r.request_id))
    assert 'execution_tracking' not in persistence.find_execution(request_id=r.request_id).request_data


def test_restart_does_not_compare_unrelated_monotonic_clocks(persistence):
    r,start=request(persistence,process_id='previous-process')
    CTraderExecutionEventService(123,persistence).process(event(Type.ORDER_FILLED,request_id=r.request_id),
        received_at=datetime.now(timezone.utc),received_monotonic_ns=start+1000000)
    tracking=persistence.find_execution(request_id=r.request_id).request_data['execution_tracking']
    assert 'fill_confirmation_received_at' in tracking
    assert 'submission_to_fill_confirmation_ms' not in tracking


def test_recovery_snapshot_does_not_invent_a_fill_receipt_time(persistence):
    r,start=request(persistence)
    CTraderExecutionEventService(123,persistence)._process(event(Type.ORDER_FILLED,request_id=r.request_id))
    assert 'execution_tracking' not in persistence.find_execution(request_id=r.request_id).request_data


def test_expired_quick_trade_blocked_after_runtime_checks_before_transport(persistence):
    r,_=request(persistence)
    r=r.model_copy(update={'valid_until':datetime.now(timezone.utc)-timedelta(seconds=1)})
    provider=CTraderBrokerProvider(Mock())
    result=provider._open_position(r)
    assert result.status == 'rejected' and result.error_code == 'LOCAL_EXPIRED_OPPORTUNITY'
    provider.client.send.assert_not_called()


def test_out_of_order_workers_keep_earliest_actual_receipt(persistence):
    r,start=request(persistence)
    received=datetime.now(timezone.utc)
    handler=CTraderExecutionEventService(123,persistence)
    handler.process(event(Type.ORDER_FILLED,request_id=r.request_id),
                    received_at=received+timedelta(seconds=1),received_monotonic_ns=start+1000000000)
    handler.process(event(Type.ORDER_FILLED,request_id=r.request_id),
                    received_at=received,received_monotonic_ns=start+500000000)
    tracking=persistence.find_execution(request_id=r.request_id).request_data['execution_tracking']
    assert tracking['fill_confirmation_received_at'] == received.isoformat()
    assert tracking['submission_to_fill_confirmation_ms'] == 500


@pytest.mark.parametrize('change,reason',[({'ask':4200.5,'bid':4200.3},'price_drift_exceeded'),
    ({'ask':4200.1,'bid':4199.9},'original_protection_no_longer_valid'),
    ({'observed_at':datetime(2020,1,1,tzinfo=timezone.utc)},'stale_or_missing_broker_quote')])
def test_provider_rechecks_quote_after_runtime_inspection(persistence,change,reason):
    from unittest.mock import patch
    from app.models.execution_models import QuickTradeExecutionGuard
    from tests.test_quick_trade_input_adapter import snapshot
    r,_=request(persistence)
    r=r.model_copy(update={'valid_until':datetime.now(timezone.utc)+timedelta(seconds=30),
        'quick_trade_guard':QuickTradeExecutionGuard(original_entry=4200,stop_budget=1.65,
            max_price_drift=.25,additional_cost=.2)})
    provider=CTraderBrokerProvider(Mock())
    quote=snapshot(bid=4199.8,ask=4200,observed_at=datetime.now(timezone.utc)).model_copy(update=change)
    with patch('app.strategies.input_adapter.collect_broker_snapshot',return_value=quote):
        result=provider._open_position(r)
    assert result.status == 'rejected' and reason in result.error_message
    provider.client.send.assert_not_called()


def test_wall_clock_adjustment_does_not_replace_earlier_monotonic_receipt(persistence):
    r,start=request(persistence)
    received=datetime.now(timezone.utc)
    handler=CTraderExecutionEventService(123,persistence)
    handler.process(event(Type.ORDER_FILLED,request_id=r.request_id),received_at=received,
                    received_monotonic_ns=start+500000000)
    handler.process(event(Type.ORDER_FILLED,request_id=r.request_id),received_at=received-timedelta(seconds=5),
                    received_monotonic_ns=start+1000000000)
    tracking=persistence.find_execution(request_id=r.request_id).request_data['execution_tracking']
    assert tracking['submission_to_fill_confirmation_ms'] == 500
    assert tracking['fill_confirmation_received_at'] == received.isoformat()


def test_provider_submits_unchanged_quick_levels_using_existing_protocol_mapping(persistence):
    from unittest.mock import patch
    from app.models.execution_models import QuickTradeExecutionGuard
    from tests.test_quick_trade_input_adapter import snapshot
    r,_=request(persistence)
    r=r.model_copy(update={'valid_until':datetime.now(timezone.utc)+timedelta(seconds=30),
        'quick_trade_guard':QuickTradeExecutionGuard(original_entry=4200,stop_budget=1.65,
            max_price_drift=.25,additional_cost=.2)})
    client=Mock(account_id=123)
    provider=CTraderBrokerProvider(client)
    symbol=metadata(minVolume=1,maxVolume=100000,stepVolume=1)
    provider.get_symbol_metadata=Mock(return_value=symbol)
    response=event(Type.ORDER_ACCEPTED,request_id=r.request_id)
    client.send.return_value=response
    quote=snapshot(bid=4199.8,ask=4200,observed_at=datetime.now(timezone.utc))
    with patch('app.strategies.input_adapter.collect_broker_snapshot',return_value=quote):
        result=provider._open_position(r)
    assert result.status == 'submitted'
    wire=client.send.call_args.args[0]
    assert wire.relativeStopLoss == 165000 and wire.relativeTakeProfit == 300000
    assert wire.clientOrderId == r.request_id
    assert r.stop_loss == 4198.35 and r.take_profit == 4203 and r.max_holding_minutes == 30


def test_wire_rounding_cannot_violate_quick_trade_stop_bounds(persistence):
    from unittest.mock import patch
    from app.models.execution_models import QuickTradeExecutionGuard
    from tests.test_quick_trade_input_adapter import snapshot
    r,_=request(persistence)
    r=r.model_copy(update={'valid_until':datetime.now(timezone.utc)+timedelta(seconds=30),
        'quick_trade_guard':QuickTradeExecutionGuard(original_entry=4200,stop_budget=1.65,
            max_price_drift=.25,additional_cost=.2)})
    provider=CTraderBrokerProvider(Mock(account_id=123))
    provider.get_symbol_metadata=Mock(return_value=metadata(digits=0,slDistance=0,tpDistance=0,minVolume=1,maxVolume=100000,stepVolume=1))
    quote=snapshot(bid=4199.8,ask=4200,observed_at=datetime.now(timezone.utc))
    with patch('app.strategies.input_adapter.collect_broker_snapshot',return_value=quote):
        result=provider._open_position(r)
    assert result.status == 'rejected'
    assert 'original_protection_no_longer_valid' in result.error_message
    provider.client.send.assert_not_called()
