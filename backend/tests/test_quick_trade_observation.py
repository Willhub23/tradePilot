"""Read-only observation uses isolated SQLite and mocked broker evidence only."""
from unittest.mock import Mock

import pytest

from app.config.strategy_config import StrategySelection
from app.services.ctrader_runtime_service import CTraderRuntimeService
from app.services.trading_scheduler import run_scheduled_cycle, create_trading_scheduler
from tests.test_quick_trade_integration import integrated, last
from tests.test_ctrader_runtime import setup


def observing(c):
    c.strategy_selection = StrategySelection(observation_enabled=True, slippage_allowance=.1)
    old = c.runtime
    runtime = CTraderRuntimeService(c.broker_provider.client, c.broker_provider,
        c.execution_persistence, store=old.store, observation_enabled=True)
    runtime.started, runtime.paused = True, True
    runtime.ready.set()
    runtime.recovered_generation = runtime.client.generation
    c.runtime = runtime
    return runtime


def test_paused_eligible_observation_persisted_without_ai_or_orders(integrated):
    c, sessions, _ = integrated
    runtime = observing(c)
    result = c.run('XAUUSD')
    row = last(sessions)
    assert row.status == 'eligible'
    assert row.data['mode'] == 'observation'
    assert row.data['entries_paused'] is True
    assert row.data['evaluation']['evaluation']['proposal']['max_holding_minutes'] == 30
    assert row.data['evaluation']['price_consistency']
    assert row.data['evaluation']['timeframe_observations']
    assert row.data['broker_snapshot']['bid'] == 4199.8
    assert row.data['timing']['total_observation_ms'] >= row.data['timing']['strategy_evaluation_ms'] >= 0
    assert row.opportunity_id == result.opportunity_id
    assert row.agent_decision_id is None and row.request_id is None
    assert not result.approved
    c.agent_service.make_decision.assert_not_called()
    c.broker_provider.open_position.assert_not_called()
    assert not runtime.client.order_guard() and not runtime.client.close_guard()
    with pytest.raises(RuntimeError, match='read-only'): runtime.resume()
    with pytest.raises(RuntimeError, match='observation_only'):
        runtime.before_open(Mock(symbol='XAUUSD'))


@pytest.mark.parametrize('state,reason', [('disconnected','broker_disconnected'),
    ('unauthenticated','broker_not_verified_demo'), ('recovering','broker_recovering'),
    ('critical','critical_unsafe_state'), ('generation','broker_recovering'),
    ('stopped','runtime_stopped')])
def test_unsafe_observation_blocked(integrated,state,reason):
    c,sessions,_ = integrated
    runtime=observing(c)
    if state == 'disconnected': runtime.client.authenticated=False
    if state == 'unauthenticated': runtime.client.demo_verified=False
    if state == 'recovering': runtime.ready.clear()
    if state == 'critical': runtime.critical_error='Missing protection'
    if state == 'generation': runtime.client.generation+=1
    if state == 'stopped': runtime.stopping.set()
    assert c.run('XAUUSD').execution_block_reason == reason
    assert last(sessions).status == 'blocked'
    c.analysis_service.analyze_multi_timeframe.assert_not_called()
    c.agent_service.make_decision.assert_not_called()
    c.broker_provider.open_position.assert_not_called()


def test_disconnection_during_collection_retains_diagnostics_but_blocks(integrated):
    c,sessions,_=integrated
    runtime=observing(c)
    analysis=c.analysis_service.analyze_multi_timeframe.return_value
    def disconnected(**kwargs):
        runtime.client.authenticated=False
        return analysis
    c.analysis_service.analyze_multi_timeframe.side_effect=disconnected
    c.run('XAUUSD')
    row=last(sessions)
    assert row.status == 'blocked'
    assert row.data['evaluation']
    assert row.data['block_reason'] == 'broker_disconnected'


def test_incomplete_input_is_persisted(integrated):
    c,sessions,_=integrated
    observing(c)
    c.analysis_service.analyze_multi_timeframe.side_effect=ConnectionRefusedError()
    c.run('XAUUSD')
    row=last(sessions)
    assert row.status == 'incomplete_input'
    assert row.data['input_error'] == 'ConnectionRefusedError'
    c.agent_service.make_decision.assert_not_called()


def test_scheduler_observes_while_paused_and_monitors_without_closing(integrated):
    c,sessions,_=integrated
    runtime=observing(c)
    manager=Mock()
    manager.manage_open_positions.side_effect=runtime.manage_positions
    run_scheduled_cycle(c,manager,'XAUUSD')
    assert last(sessions).status == 'eligible'
    runtime.provider.position_management.manage.assert_called_with(allow_close=False)
    scheduler=create_trading_scheduler(c,manager)
    jobs=scheduler.get_jobs()
    assert len(jobs)==1 and jobs[0].max_instances==1


def test_overlap_rejected_without_second_observation(integrated):
    c,sessions,_=integrated
    observing(c)
    def attempt_overlap(**kwargs):
        with pytest.raises(RuntimeError,match='already running'): c.run('XAUUSD')
        return analysis
    analysis=c.analysis_service.analyze_multi_timeframe.return_value
    c.analysis_service.analyze_multi_timeframe.side_effect=attempt_overlap
    c.run('XAUUSD')
    assert last(sessions).status == 'eligible'
    assert c.analysis_service.analyze_multi_timeframe.call_count==1


def test_observation_start_persists_pause_and_restart_stays_paused(setup):
    setup.store.load()
    setup.store.save(False,'keep critical latch')
    runtime=CTraderRuntimeService(setup.client,setup.provider,setup.persistence,
        store=setup.store,observation_enabled=True)
    try:
        runtime.start()
        assert runtime.store.load()=={'paused':True,'critical_error':'keep critical latch'}
        assert not runtime.order_allowed()
        with pytest.raises(RuntimeError,match='read-only'): runtime.resume()
    finally:
        runtime.stop()
    # A normal process after removal of the flag still loads a durable pause.
    setup.start()
    assert setup.paused and not setup.order_allowed()


def test_flag_is_opt_in_and_requires_costs(monkeypatch):
    monkeypatch.delenv('TRADING_STRATEGY',raising=False)
    monkeypatch.delenv('QUICK_TRADE_OBSERVATION_ENABLED',raising=False)
    assert not StrategySelection.from_env().observation_enabled
    monkeypatch.setenv('QUICK_TRADE_OBSERVATION_ENABLED','false')
    assert not StrategySelection.from_env().observation_enabled
    monkeypatch.setenv('QUICK_TRADE_OBSERVATION_ENABLED','true')
    monkeypatch.delenv('QUICK_TRADE_SLIPPAGE_ALLOWANCE',raising=False)
    with pytest.raises(ValueError,match='explicitly'): StrategySelection.from_env()
    monkeypatch.setenv('QUICK_TRADE_SLIPPAGE_ALLOWANCE','.1')
    assert StrategySelection.from_env().observation_enabled
    monkeypatch.setenv('QUICK_TRADE_OBSERVATION_ENABLED','typo')
    with pytest.raises(ValueError,match='true or false'): StrategySelection.from_env()


def test_persistence_failure_never_reaches_inference(integrated):
    c,_,_=integrated
    observing(c)
    c.quick_trade_cycle.persistence.save=Mock(side_effect=RuntimeError('database unavailable'))
    with pytest.raises(RuntimeError,match='database unavailable'): c.run('XAUUSD')
    c.analysis_service.analyze_multi_timeframe.assert_not_called()
    c.agent_service.make_decision.assert_not_called()
    c.broker_provider.open_position.assert_not_called()


def test_rejected_setup_keeps_specific_reasons(integrated):
    c,sessions,_=integrated
    observing(c)
    from tests.test_quick_trade_strategy import scenario
    c.analysis_service.analyze_multi_timeframe.return_value=scenario(timeframe_changes={"atr":10}).analysis
    c.run('XAUUSD')
    row=last(sessions)
    assert row.status == 'rejected'
    assert row.data['evaluation']['strategy_rejection_reasons']
    c.agent_service.make_decision.assert_not_called()
    c.broker_provider.open_position.assert_not_called()
