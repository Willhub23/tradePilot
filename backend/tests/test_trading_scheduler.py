import importlib
import logging
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType
from unittest.mock import Mock, call, create_autospec

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.api.agent_routes import create_agent_router
from app.models.agent_models import AgentDecision, AgentResult, PersistedAgentDecision
from app.models.market_models import MultiTimeframeAnalysis, TimeframeAnalysis
from app.models.macro_models import MacroEventRisk
from app.services import trading_cycle_service
from app.providers.broker_base import BrokerProvider
from app.services.position_manager_service import PositionManagerService
from app.services.trading_scheduler import create_trading_scheduler, run_scheduled_cycle
from tests.market_fixtures import market_structure_fields



@pytest.fixture
def cycle(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    analysis = Mock()
    analysis.analyze_multi_timeframe.return_value = MultiTimeframeAnalysis(
        symbol="XAUUSD", price=4180, price_timestamp=datetime.now(timezone.utc),
        timeframes=[TimeframeAnalysis(
            **market_structure_fields(current_price=4180.0),
            interval="1h", latest_candle_timestamp=datetime.now(timezone.utc),
            rsi=60, ema_20=4180, ema_50=4170, atr=10, score=65,
            bias="bullish", trend_direction="bullish", trend_strength=1.5,
            trend_strength_label="strong",
        )], overall_score=65, overall_bias="bullish", timeframe_alignment="aligned",
    )
    macro = Mock()
    macro.get_event_risk.return_value = MacroEventRisk(
        event_risk="low", next_event=None, minutes_until_event=None,
        event_time=None, allow_new_trade=True,
    )
    agent = Mock()
    agent.make_decision.return_value = PersistedAgentDecision(
        decision_id="decision-1",
        decision=AgentDecision(action="long", trade_style="swing", confidence=75, thesis="Trend"),
    )
    monkeypatch.setattr(trading_cycle_service, "AgentService", lambda **kwargs: agent)
    broker = create_autospec(BrokerProvider, instance=True, spec_set=True)
    broker.get_open_positions.return_value = []
    service = trading_cycle_service.TradingCycleService(analysis, macro, broker)
    service.market_session_service = Mock()
    service.market_session_service.is_market_open.return_value = True
    service.market_session_service.is_quote_fresh.return_value = True
    from app.models.paper_trading_models import PaperPosition
    service.broker_provider.open_position.return_value = PaperPosition(
        id="trade-1", symbol="XAUUSD", direction="long", entry_price=4180,
        opened_at=datetime.now(timezone.utc), status="open", confidence=75, thesis="Trend",
    )
    return service


@pytest.mark.parametrize("market_open,fresh,reason", [
    (True, True, None), (False, True, "market_closed"), (True, False, "stale_quote"),
])
def test_cycle_preserves_response_and_guards(cycle, market_open, fresh, reason):
    cycle.market_session_service.is_market_open.return_value = market_open
    cycle.market_session_service.is_quote_fresh.return_value = fresh
    result = cycle.run("XAUUSD")
    assert isinstance(result, AgentResult)
    assert result.decision_id == "decision-1"
    assert result.proposal.agent_decision_id == "decision-1"
    assert result.approved is True
    assert result.execution_block_reason == reason
    cycle.agent_service.make_decision.assert_called_once()
    assert cycle.broker_provider.open_position.call_count == (1 if reason is None else 0)
    cycle.broker_provider.get_open_positions.assert_called_once_with()
    if reason is None:
        cycle.broker_provider.open_position.assert_called_once_with(
            proposal=result.proposal, entry_price=4180,
            stop_loss=4165, take_profit=4200, max_holding_minutes=2880,
        )
    assert (result.position is not None) == (reason is None)


def test_manual_endpoint_delegates(cycle):
    result = cycle.run("XAUUSD")
    cycle.run = Mock(return_value=result)
    app = FastAPI()
    app.include_router(create_agent_router(cycle))
    with TestClient(app) as client:
        response = client.get("/api/agent/decision/XAUUSD")
    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")
    cycle.run.assert_called_once_with("XAUUSD")


def test_scheduler_skips_closed_market(caplog, position_manager):
    cycle = Mock()
    cycle.market_session_service.is_market_open.return_value = False
    with caplog.at_level(logging.INFO):
        run_scheduled_cycle(cycle, position_manager, "XAUUSD")
    cycle.run.assert_not_called()
    position_manager.manage_open_positions.assert_not_called()
    assert "skipped: market closed" in caplog.text


def test_scheduler_runs_open_market(caplog, position_manager):
    cycle = Mock()
    with caplog.at_level(logging.INFO):
        run_scheduled_cycle(cycle, position_manager, "XAUUSD")
    cycle.run.assert_called_once_with("XAUUSD")
    position_manager.manage_open_positions.assert_called_once_with()
    assert "started for XAUUSD" in caplog.text
    assert "completed for XAUUSD" in caplog.text


@pytest.mark.parametrize("failure", ["market", "llm", "database", "session", "position_manager"])
def test_scheduler_logs_failure_and_can_run_again(failure, caplog, position_manager):
    cycle = Mock()
    if failure == "session":
        target = cycle.market_session_service.is_market_open
    elif failure == "position_manager":
        target = position_manager.manage_open_positions
    else:
        target = cycle.run
    target.side_effect = RuntimeError(failure)
    run_scheduled_cycle(cycle, position_manager, "XAUUSD")
    assert "Scheduled trading cycle failed" in caplog.text
    assert failure in caplog.text
    assert caplog.records[-1].exc_info is not None
    target.side_effect = None
    if failure == "position_manager":
        cycle.run.assert_not_called()
    run_scheduled_cycle(cycle, position_manager, "XAUUSD")
    assert target.call_count == 2


@pytest.mark.parametrize("configured,minutes", [(None, 15), ("3", 3)])
def test_scheduler_config_and_delayed_start(monkeypatch, configured, minutes, position_manager):
    if configured is None:
        monkeypatch.delenv("TRADING_INTERVAL_MINUTES", raising=False)
    else:
        monkeypatch.setenv("TRADING_INTERVAL_MINUTES", configured)
    cycle = Mock()
    before = datetime.now(timezone.utc)
    scheduler = create_trading_scheduler(cycle, position_manager)
    assert not scheduler.running
    scheduler.start(paused=True)
    try:
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.args == (cycle, position_manager, "XAUUSD")
        assert job.id == "trading-cycle-XAUUSD"
        assert job.max_instances == 1
        assert job.coalesce is True
        assert job.trigger.interval == timedelta(minutes=minutes)
        assert job.next_run_time >= before + timedelta(minutes=minutes)
        cycle.run.assert_not_called()
    finally:
        scheduler.shutdown(wait=True)


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_invalid_interval(monkeypatch, value, position_manager):
    monkeypatch.setenv("TRADING_INTERVAL_MINUTES", value)
    with pytest.raises(ValueError):
        create_trading_scheduler(Mock(), position_manager)


def test_application_lifespan_starts_and_stops_scheduler(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # Replace external service wiring before importing the application.
    for name, services in [
        ("app.api.market_routes", ["analysis_service", "market_service"]),
        ("app.api.macro_routes", ["macro_service"]),
    ]:
        module = ModuleType(name)
        module.router = APIRouter()
        for service in services:
            setattr(module, service, Mock())
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    factory = Mock()
    monkeypatch.setattr(main, "create_trading_scheduler", factory)
    try:
        factory.assert_not_called()
        for _ in range(2):
            scheduler = Mock()
            factory.return_value = scheduler
            with TestClient(main.app) as client:
                scheduler.start.assert_called_once()
                scheduler.shutdown.assert_not_called()
                assert client.get("/api/health").status_code == 200
            scheduler.shutdown.assert_called_once_with(wait=True)
        assert factory.call_args_list == [
            call(main.trading_cycle_service, main.position_manager_service),
            call(main.trading_cycle_service, main.position_manager_service),
        ]
    finally:
        sys.modules.pop("app.main", None)


@pytest.fixture
def position_manager():
    manager = create_autospec(PositionManagerService, instance=True)
    manager.manage_open_positions.return_value = []
    return manager


def test_cycle_blocks_existing_broker_position(cycle):
    cycle.broker_provider.get_open_positions.return_value = [
        cycle.broker_provider.open_position.return_value
    ]
    result = cycle.run("XAUUSD")
    assert result.execution_block_reason == "position_already_open"
    assert result.position is None
    cycle.broker_provider.get_open_positions.assert_called_once_with()
    cycle.broker_provider.open_position.assert_not_called()


def test_cycle_requires_one_hour_analysis(cycle):
    cycle.analysis_service.analyze_multi_timeframe.return_value.timeframes = []
    result = cycle.run("XAUUSD")
    assert result.execution_block_reason == "missing_1h_analysis"
    cycle.broker_provider.open_position.assert_not_called()
