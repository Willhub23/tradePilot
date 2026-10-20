from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import threading
import time
from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select, event
from sqlalchemy.orm import sessionmaker

from app.config.strategy_config import StrategySelection
from app.database import Base
from app.db_models import StrategyEvaluationRecord, BrokerExecutionRecord, AgentDecisionRecord
from app.models.agent_models import AgentDecision, PersistedAgentDecision
from app.models.execution_models import BrokerExecutionResult
from app.services.trading_cycle_service import TradingCycleService
from app.services.quick_trade_cycle_service import QuickTradeCycleService
from app.services.ctrader_runtime_service import CTraderRuntimeService
from app.services.broker_execution_persistence_service import BrokerExecutionPersistenceService
from app.services.agent_decision_persistence_service import AgentDecisionPersistenceService
from tests.test_quick_trade_strategy import scenario, NOW
from tests.test_quick_trade_input_adapter import snapshot
from tests.test_quick_trade_live_validation import metadata


@pytest.fixture
def integrated(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    engine = create_engine(f'sqlite:///{tmp_path / "quick.db"}')
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _): connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    for module in ('strategy_evaluation_persistence_service', 'broker_execution_persistence_service', 'agent_decision_persistence_service'):
        monkeypatch.setattr(f'app.services.{module}.SessionLocal',sessions)
    monkeypatch.setattr('app.services.quick_trade_cycle_service.now',lambda: NOW)
    analysis,macro,broker = Mock(),Mock(),Mock()
    analysis.analyze_multi_timeframe.return_value = scenario().analysis
    macro.get_events.return_value = []
    broker.client.environment = 'demo'
    broker.client.authenticated = True
    broker.client.demo_verified = True
    broker.client.generation = 1
    broker.client.account_id = 123
    broker.get_open_positions.return_value = []
    broker.get_pending_order_ids.return_value = []
    broker.position_management.manage.return_value = []
    broker.get_symbol_metadata.return_value = metadata()
    broker.price_tick.return_value = Decimal('.01')
    persistence = BrokerExecutionPersistenceService()
    c = TradingCycleService(analysis,macro,broker,persistence,
        strategy_selection=StrategySelection(strategy='quick_trade',slippage_allowance=.1))
    runtime = CTraderRuntimeService(broker.client,broker,persistence,store=Mock(assert_owner=Mock()))
    runtime.started, runtime.paused = True, False
    runtime.ready.set()
    runtime.recovered_generation = 1
    c.runtime = runtime
    c.market_session_service = Mock()
    c.market_session_service.is_market_open.return_value = True
    c.market_session_service.is_quote_fresh.return_value = True
    c.quick_trade_cycle = QuickTradeCycleService(c)
    observed = snapshot(bid=4199.8,ask=4200,additional_round_trip_cost=None)
    monkeypatch.setattr('app.services.quick_trade_cycle_service.collect_broker_snapshot',Mock(return_value=observed))
    monkeypatch.setattr('app.services.quick_trade_cycle_service.verified_commission_estimate',Mock(return_value={'round_trip_price_cost':.1}))
    def decide(context):
        decision = AgentDecision(action='long',trade_style='intraday',confidence=80,thesis='Approve supplied reaction')
        return PersistedAgentDecision(decision_id=AgentDecisionPersistenceService().save_decision(context,decision),decision=decision)
    c.agent_service = Mock()
    c.agent_service.make_decision.side_effect = decide
    def opened(*, request):
        runtime.before_open(request)
        return BrokerExecutionResult(request_id=request.request_id,status='submitted',broker_order_id='41')
    broker.open_position.side_effect = opened
    yield c,sessions,observed
    engine.dispose()


def last(sessions):
    with sessions() as db: return db.scalars(select(StrategyEvaluationRecord)).one()


def test_default_strategy_and_explicit_opt_in(monkeypatch):
    monkeypatch.delenv('TRADING_STRATEGY',raising=False)
    assert StrategySelection.from_env().strategy == 'existing'
    with pytest.raises(ValidationError): StrategySelection(strategy='quick_trade')
    with pytest.raises(ValidationError): StrategySelection(strategy='other')


def test_eligible_proposal_preserved_and_correlated(integrated):
    c,sessions,_ = integrated
    result = c.run('XAUUSD')
    assert result.execution_result.status == 'submitted'
    context = c.agent_service.make_decision.call_args.args[0]
    assert context.strategy_evaluation.setup_type == 'trend_pullback_continuation'
    request = c.broker_provider.open_position.call_args.kwargs['request']
    assert request.stop_loss == 4198.35 and request.take_profit == 4203
    assert request.max_holding_minutes == 30 and request.volume_lots == .01
    assert request.opportunity_id == result.opportunity_id == context.opportunity_id
    row = last(sessions)
    assert row.agent_decision_id == result.decision_id
    assert row.request_id == request.request_id
    assert row.opportunity_id == request.opportunity_id
    assert row.data['evaluation']['evaluation']['supporting_signals']
    assert row.data['timing']['detection_ms'] >= 0
    assert row.data['timing']['ai_decision_stage_ms'] >= 0
    assert row.data['timing']['risk_validation_ms'] >= 0
    assert row.data['timing']['order_submission_ms'] >= 0
    with sessions() as db:
        assert db.get(AgentDecisionRecord,result.decision_id).market_context['opportunity_id'] == request.opportunity_id
        record = db.get(BrokerExecutionRecord,request.request_id)
        assert record.request_data['max_holding_minutes'] == 30
        assert record.request_data['timing']['submission_monotonic_ns'] > 0
        assert 'execution_tracking' not in record.request_data  # No fabricated fill time.


def test_rejected_strategy_is_persisted_without_ai(integrated):
    c,sessions,_ = integrated
    c.analysis_service.analyze_multi_timeframe.return_value = scenario(price_action_changes={'rejection':'none'}).analysis
    result = c.run('XAUUSD')
    assert result.execution_block_reason == 'quick_trade_rejected'
    c.agent_service.make_decision.assert_not_called()
    c.broker_provider.open_position.assert_not_called()
    row = last(sessions)
    assert row.status == 'rejected'
    assert row.data['evaluation']['strategy_rejection_reasons']
    assert row.agent_decision_id is None and row.opportunity_id is None


def test_incomplete_costs_do_not_call_ai(integrated):
    c,sessions,_ = integrated
    with patch('app.services.quick_trade_cycle_service.verified_commission_estimate',return_value={'round_trip_price_cost':None}):
        result = c.run('XAUUSD')
    assert result.execution_block_reason == 'quick_trade_incomplete_input'
    assert 'execution.additional_round_trip_cost' in last(sessions).data['evaluation']['missing_inputs']
    c.agent_service.make_decision.assert_not_called()


@pytest.mark.parametrize('reason',['position','unresolved','paused','disconnected','critical'])
def test_exposure_or_safety_skips_new_entry_analysis(integrated,reason):
    c,sessions,_ = integrated
    if reason == 'position': c.broker_provider.get_open_positions.return_value = [Mock()]
    if reason == 'unresolved': c.execution_persistence = Mock(get_unresolved=Mock(return_value=[Mock()]))
    if reason == 'paused': c.runtime.paused = True
    if reason == 'disconnected': c.broker_provider.client.authenticated = False
    if reason == 'critical': c.runtime.critical_error = 'unsafe SL/TP'
    result = c.run('XAUUSD')
    assert result.execution_block_reason
    c.analysis_service.analyze_multi_timeframe.assert_not_called()
    c.agent_service.make_decision.assert_not_called()
    assert last(sessions).status == 'skipped'


def test_persistence_failure_prevents_analysis_and_orders(integrated):
    c,_,_ = integrated
    c.quick_trade_cycle.persistence.save = Mock(side_effect=RuntimeError('database unavailable'))
    with pytest.raises(RuntimeError): c.run('XAUUSD')
    c.agent_service.make_decision.assert_not_called()
    c.broker_provider.open_position.assert_not_called()


def test_macro_can_hold_without_changing_candidate(integrated):
    c,sessions,_ = integrated
    original = c.agent_service.make_decision.side_effect
    def held(context):
        result = original(context)
        return result.model_copy(update={'decision':AgentDecision(action='hold',trade_style='none',confidence=100,thesis='Macro hold')})
    c.agent_service.make_decision.side_effect = held
    c.run('XAUUSD')
    c.broker_provider.open_position.assert_not_called()
    assert last(sessions).status == 'ai_rejected'


@pytest.mark.parametrize('action,style',[('short','intraday'),('long','swing'),('hold','none')])
def test_ai_cannot_substitute_unrelated_proposal(integrated,action,style):
    c,sessions,_ = integrated
    original = c.agent_service.make_decision.side_effect
    def changed(context):
        result = original(context)
        return result.model_copy(update={'decision':AgentDecision(action=action,trade_style=style,confidence=80,thesis='Different plan')})
    c.agent_service.make_decision.side_effect = changed
    c.run('XAUUSD')
    c.broker_provider.open_position.assert_not_called()
    assert last(sessions).status == 'ai_rejected'


def test_risk_rejection_uses_existing_risk_service(integrated):
    c,sessions,_ = integrated
    c.risk_service = Mock(approve=Mock(return_value=False))
    c.run('XAUUSD')
    assert last(sessions).status == 'risk_rejected'
    c.broker_provider.open_position.assert_not_called()


def test_pause_and_monitoring_responsive_while_ai_is_waiting(integrated):
    c,sessions,_ = integrated
    started,finish = threading.Event(),threading.Event()
    original = c.agent_service.make_decision.side_effect
    def wait(context):
        started.set()
        assert finish.wait(5)
        return original(context)
    c.agent_service.make_decision.side_effect = wait
    outputs=[]
    thread=threading.Thread(target=lambda: outputs.append(c.run('XAUUSD')))
    thread.start()
    assert started.wait(5)
    start=time.perf_counter()
    c.runtime.pause()
    c.runtime.manage_positions()  # Monitoring/management can acquire the lock during inference.
    assert time.perf_counter()-start < 1
    finish.set()
    thread.join(5)
    assert not thread.is_alive()
    assert outputs[0].execution_block_reason == 'trading_paused'
    c.broker_provider.open_position.assert_not_called()


def test_pause_resume_epoch_invalidates_inflight_opportunity(integrated):
    c,_,_ = integrated
    original=c.agent_service.make_decision.side_effect
    def paused(context):
        c.runtime.safety_epoch += 1
        return original(context)
    c.agent_service.make_decision.side_effect=paused
    assert c.run('XAUUSD').execution_block_reason == 'runtime_changed_during_reasoning'
    c.broker_provider.open_position.assert_not_called()


def test_price_drift_rejected_after_ai(integrated):
    c,_,observed = integrated
    with patch('app.services.quick_trade_cycle_service.collect_broker_snapshot',side_effect=[observed,observed.model_copy(update={'ask':4200.3,'bid':4200.1})]):
        result=c.run('XAUUSD')
    assert result.execution_block_reason == 'price_drift_exceeded'
    c.broker_provider.open_position.assert_not_called()


def test_no_widening_original_stop_risk(integrated):
    c,_,observed=integrated
    with patch('app.services.quick_trade_cycle_service.collect_broker_snapshot',side_effect=[observed,observed.model_copy(update={'ask':4200.1,'bid':4199.9})]):
        result=c.run('XAUUSD')
    assert result.execution_block_reason == 'original_protection_no_longer_valid'
    c.broker_provider.open_position.assert_not_called()


def test_stale_broker_quote_after_ai_does_not_submit(integrated):
    c,_,observed=integrated
    with patch('app.services.quick_trade_cycle_service.collect_broker_snapshot',side_effect=[observed,observed.model_copy(update={'observed_at':NOW-timedelta(minutes=5)})]):
        result=c.run('XAUUSD')
    assert result.execution_block_reason == 'strategy_no_longer_eligible'
    c.broker_provider.open_position.assert_not_called()


def test_setup_disappearing_after_ai_does_not_submit(integrated):
    c,_,_=integrated
    c.analysis_service.analyze_multi_timeframe.side_effect=[scenario().analysis,scenario(price_action_changes={'rejection':'none'}).analysis]
    assert c.run('XAUUSD').execution_block_reason == 'strategy_no_longer_eligible'


def test_final_risk_gate_rechecked(integrated):
    c,_,_=integrated
    c.risk_service=Mock(approve=Mock(side_effect=[True,False]))
    assert c.run('XAUUSD').execution_block_reason == 'risk_rejected'
    c.broker_provider.open_position.assert_not_called()


def test_recovery_during_ai_blocks_submission(integrated):
    c,_,_=integrated
    original=c.agent_service.make_decision.side_effect
    def disconnect(context):
        result=original(context)
        c.runtime.ready.clear()
        return result
    c.agent_service.make_decision.side_effect=disconnect
    assert c.run('XAUUSD').execution_block_reason == 'broker_recovering'


def test_paper_mode_cannot_execute_quick_trade(integrated):
    c,sessions,_=integrated
    c.runtime=None
    assert c.run('XAUUSD').execution_block_reason == 'quick_trade_requires_demo_runtime'
    c.agent_service.make_decision.assert_not_called()


def test_default_demo_flow_skips_ai_for_existing_position(integrated):
    c,_,_=integrated
    c.strategy_selection=StrategySelection()
    c.broker_provider.get_open_positions.return_value=[Mock()]
    assert c.run('XAUUSD').execution_block_reason == 'position_already_open'
    c.agent_service.make_decision.assert_not_called()


def test_opportunity_age_limit_blocks_slow_reasoning(integrated):
    c,_,_=integrated
    c.strategy_selection=StrategySelection(strategy='quick_trade',slippage_allowance=.1,max_opportunity_age_seconds=.000001)
    assert c.run('XAUUSD').execution_block_reason == 'opportunity_expired'
    c.broker_provider.open_position.assert_not_called()


def test_no_second_manual_cycle_during_inference(integrated):
    c,_,_=integrated
    original=c.agent_service.make_decision.side_effect
    def concurrent(context):
        with pytest.raises(RuntimeError,match='already running'): c.run('XAUUSD')
        return original(context)
    c.agent_service.make_decision.side_effect=concurrent
    c.run('XAUUSD')
    assert c.broker_provider.open_position.call_count == 1


def test_unresolved_created_during_ai_blocks_submission(integrated):
    c,_,_=integrated
    original=c.agent_service.make_decision.side_effect
    def uncertain(context):
        result=original(context)
        c.execution_persistence=Mock(get_unresolved=Mock(return_value=[Mock()]))
        return result
    c.agent_service.make_decision.side_effect=uncertain
    assert c.run('XAUUSD').execution_block_reason == 'unresolved_broker_execution'
    c.broker_provider.open_position.assert_not_called()


def test_new_position_during_ai_blocks_submission(integrated):
    c,_,_=integrated
    original=c.agent_service.make_decision.side_effect
    def exposed(context):
        result=original(context)
        c.broker_provider.get_open_positions.return_value=[Mock()]
        return result
    c.agent_service.make_decision.side_effect=exposed
    assert c.run('XAUUSD').execution_block_reason == 'position_already_open'
    c.broker_provider.open_position.assert_not_called()


def test_broker_evidence_error_is_recorded_without_inference(integrated):
    c,sessions,_=integrated
    with patch('app.services.quick_trade_cycle_service.collect_broker_snapshot',side_effect=TimeoutError()):
        result=c.run('XAUUSD')
    assert result.execution_block_reason == 'quick_trade_evidence_unavailable'
    assert last(sessions).status == 'incomplete_input'
    c.agent_service.make_decision.assert_not_called()


def test_ai_reviewer_cannot_change_direction_before_persistence(integrated):
    from app.services.agent_service import AgentService
    from app.models.agent_models import AgentMarketContext
    from app.strategies.validation import validate_snapshot
    c,sessions,_=integrated
    reasoning=Mock()
    reasoning.make_decision.return_value=AgentDecision(action='short',trade_style='swing',confidence=80,thesis='Invented swing')
    agent=AgentService(reasoning)
    context=AgentMarketContext(symbol='XAUUSD',market_analysis=scenario().analysis,
        macro_context={'allow_new_trade':True,'event_name':None},evidence_priority={},
        strategy_evaluation=validate_snapshot(scenario(),source='test').evaluation,opportunity_id='opp')
    result=agent.make_decision(context)
    assert result.decision.action == 'hold'
    assert result.reasoning_timing['duration_ms'] >= 0
    with sessions() as db:
        assert db.get(AgentDecisionRecord,result.decision_id).action == 'hold'


def test_prompt_reviews_actual_proposal_instead_of_generic_swing(integrated):
    from app.providers.local_reasoning_provider import LocalReasoningProvider
    from app.models.agent_models import AgentMarketContext
    from app.strategies.validation import validate_snapshot
    context=AgentMarketContext(symbol='XAUUSD',market_analysis=scenario().analysis,
        macro_context={'allow_new_trade':True,'event_name':None},evidence_priority={},
        strategy_evaluation=validate_snapshot(scenario(),source='test').evaluation,opportunity_id='opportunity')
    response=Mock()
    response.json.return_value={'message':{'content':AgentDecision(action='hold',trade_style='none',confidence=60,thesis='Conflicting').model_dump_json()}}
    with patch('app.providers.local_reasoning_provider.httpx.post',return_value=response) as post:
        LocalReasoningProvider().make_decision(context)
    messages=post.call_args.kwargs['json']['messages']
    assert 'proposal review' in messages[0]['content']
    assert '4198.35' in messages[1]['content'] and '4203' in messages[1]['content']
    assert 'supporting_signals' in messages[1]['content']


def test_migration_verifies_strategy_schema_only_when_selected(tmp_path,monkeypatch):
    from scripts.migrate_database import verify_database, _tables
    engine=create_engine(f'sqlite:///{tmp_path / "migration.db"}')
    for table in Base.metadata.sorted_tables:
        if table.name != 'strategy_evaluations': table.create(engine)
    monkeypatch.setenv('TRADING_STRATEGY','existing')
    verify_database(engine)
    with pytest.raises(RuntimeError,match='strategy_evaluations'): verify_database(engine,require_strategy=True)
    StrategyEvaluationRecord.__table__.create(engine)
    verify_database(engine,require_strategy=True)
    StrategyEvaluationRecord.__table__.create(engine,checkfirst=True)
    engine.dispose()


def test_unresolved_position_close_skips_ai(integrated):
    c,_,_=integrated
    c.execution_persistence.get_unresolved_closes=Mock(return_value=[Mock()])
    assert c.run('XAUUSD').execution_block_reason == 'unresolved_position_close'
    c.agent_service.make_decision.assert_not_called()


def test_macro_becoming_unsafe_after_ai_blocks_order(integrated):
    c,_,_=integrated
    original=c.macro_context_service.build_context
    calls=[]
    def macro(**kwargs):
        context=original(**kwargs)
        calls.append(context)
        if len(calls) == 2: context['allow_new_trade']=False
        return context
    c.macro_context_service.build_context=macro
    assert c.run('XAUUSD').execution_block_reason == 'macro_blocks_new_trade'
    c.broker_provider.open_position.assert_not_called()


def test_closed_session_skips_ai(integrated):
    c,_,_=integrated
    c.market_session_service.is_market_open.return_value=False
    assert c.run('XAUUSD').execution_block_reason == 'market_closed'
    c.agent_service.make_decision.assert_not_called()


def test_dormant_quick_trade_environment_does_not_change_default(monkeypatch):
    monkeypatch.setenv('TRADING_STRATEGY','existing')
    monkeypatch.setenv('QUICK_TRADE_SLIPPAGE_ALLOWANCE','not configured yet')
    assert StrategySelection.from_env() == StrategySelection()


def test_position_opened_during_macro_fetch_skips_ai(integrated):
    c,_,_=integrated
    original=c.macro_service.get_events.side_effect
    def macro():
        c.broker_provider.get_open_positions.return_value=[Mock()]
        return []
    c.macro_service.get_events.side_effect=macro
    assert c.run('XAUUSD').execution_block_reason == 'position_already_open'
    c.agent_service.make_decision.assert_not_called()


@pytest.mark.parametrize('kind,direction,expected',[('range','long','range_support_bounce'),
    ('breakout','long','breakout'),('pullback','short','trend_pullback_continuation')])
def test_all_existing_setup_types_and_short_direction_use_same_pipeline(integrated,kind,direction,expected):
    from tests.test_quick_trade_strategy import range_scenario,breakout_scenario
    c,_,_=integrated
    factory={'range':range_scenario,'breakout':breakout_scenario,'pullback':scenario}[kind]
    data=factory(direction)
    c.analysis_service.analyze_multi_timeframe.return_value=data.analysis
    def approve(context):
        decision=AgentDecision(action=direction,trade_style='intraday',confidence=80,thesis='Approve supplied proposal')
        return PersistedAgentDecision(decision_id=AgentDecisionPersistenceService().save_decision(context,decision),decision=decision)
    c.agent_service.make_decision.side_effect=approve
    quote=snapshot(bid=4199.8 if direction=='long' else 4200,ask=4200 if direction=='long' else 4200.2)
    with patch('app.services.quick_trade_cycle_service.collect_broker_snapshot',return_value=quote):
        result=c.run('XAUUSD')
    assert result.execution_result.status == 'submitted'
    context=c.agent_service.make_decision.call_args.args[0]
    assert context.strategy_evaluation.setup_type == expected
    request=c.broker_provider.open_position.call_args.kwargs['request']
    assert request.direction == direction
    assert request.stop_loss == context.strategy_evaluation.proposal.stop_loss
    assert request.take_profit == context.strategy_evaluation.proposal.take_profit
