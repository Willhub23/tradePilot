from decimal import Decimal
from unittest.mock import Mock,patch
import pytest

from app.providers.twelve_data_provider import TwelveDataProvider
from tests.test_quick_trade_integration import integrated,last
from tests.test_quick_trade_input_adapter import snapshot
from tests.test_quick_trade_strategy import scenario


@pytest.mark.parametrize('direction,bid,ask',[('long',4198.94,4199.04),('short',4200.96,4201.06)])
def test_material_source_discrepancy_blocks_before_ai_without_rebasing(integrated,direction,bid,ask):
    c,sessions,_=integrated
    analysis=scenario(direction).analysis
    original=analysis.model_dump_json()
    c.analysis_service.analyze_multi_timeframe.return_value=analysis
    quote=snapshot(bid=bid,ask=ask)
    with patch('app.services.quick_trade_cycle_service.collect_broker_snapshot',return_value=quote):
        result=c.run('XAUUSD')
    assert result.execution_block_reason=='quick_trade_blocked'
    c.agent_service.make_decision.assert_not_called()
    c.broker_provider.open_position.assert_not_called()
    report=last(sessions).data['evaluation']
    assert report['outcome']=='blocked'
    assert report['evaluation']['status']=='eligible'  # Entry rules unchanged.
    assert report['input_failure_reasons'][-1]['code']=='analysis_broker_price_discrepancy'
    assert report['evaluation']['proposal']['entry_reference']==4200
    assert analysis.model_dump_json()==original
    assert last(sessions).data['execution_guard_config']['max_price_drift']==.25


def test_mismatched_rejected_setup_retains_genuine_rejection(integrated):
    c,sessions,_=integrated
    c.analysis_service.analyze_multi_timeframe.return_value=scenario(timeframe_changes={'atr':4}).analysis
    with patch('app.services.quick_trade_cycle_service.collect_broker_snapshot',return_value=snapshot(bid=4198.9,ask=4199)):
        result=c.run('XAUUSD')
    assert result.execution_block_reason=='quick_trade_rejected'
    assert last(sessions).data['evaluation']['price_consistency']['absolute_mid_gap']==1.05
    assert any(r['code']=='volatility_unsuitable' for r in last(sessions).data['evaluation']['strategy_rejection_reasons'])


def test_side_specific_quote_not_midpoint_is_execution_authority(integrated):
    c,_,_=integrated
    # Midpoint is within .25 but the LONG executable ask is .30 away.
    with patch('app.services.quick_trade_cycle_service.collect_broker_snapshot',return_value=snapshot(bid=4200.1,ask=4200.3)):
        result=c.run('XAUUSD')
    assert result.execution_block_reason=='quick_trade_blocked'
    c.agent_service.make_decision.assert_not_called()


def test_provider_receipt_timestamp_preserved_on_cache_hit():
    response=Mock()
    response.json.return_value={'price':'4125.75295'}
    provider=TwelveDataProvider('mock-key')
    with patch('app.providers.twelve_data_provider.httpx.get',return_value=response) as get:
        first=provider.get_price('XAUUSD')
        second=provider.get_price('XAUUSD')
    assert first is second and first.timestamp==second.timestamp
    assert first.timestamp.tzinfo is not None and first.price==4125.75295
    assert get.call_count==1
    assert get.call_args.kwargs['params']['symbol']=='XAU/USD'
