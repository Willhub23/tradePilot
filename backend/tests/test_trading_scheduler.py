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
from app.services import trading_cycle_service
from app.providers.broker_base import BrokerProvider
from app.services.position_manager_service import PositionManagerService
from app.services.trading_scheduler import create_trading_scheduler, run_scheduled_cycle
from tests.market_fixtures import market_structure_fields
from app.models.execution_models import BrokerExecutionResult
from app.models.paper_trading_models import PaperPosition


@pytest.fixture
def cycle(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    analysis = Mock()
    analysis.analyze_multi_timeframe.return_value = MultiTimeframeAnalysis(
        symbol="XAUUSD",
        price=4180,
        price_timestamp=datetime.now(timezone.utc),
        timeframes=[
            TimeframeAnalysis(
                **market_structure_fields(current_price=4180.0),
                interval="1h",
                latest_candle_timestamp=datetime.now(timezone.utc),
                rsi=60,
                ema_20=4180,
                ema_50=4170,
                atr=10,
                score=65,
                bias="bullish",
                trend_direction="bullish",
                trend_strength=1.5,
                trend_strength_label="strong",
            )
        ],
        overall_score=65,
        overall_bias="bullish",
        timeframe_alignment="aligned",
    )
    macro = Mock()
    macro.get_events.return_value = []
    agent = Mock()
    agent.make_decision.return_value = PersistedAgentDecision(
        decision_id="decision-1",
        decision=AgentDecision(
            action="long", trade_style="swing", confidence=75, thesis="Trend"
        ),
    )
    monkeypatch.setattr(trading_cycle_service, "AgentService", lambda **kwargs: agent)
    broker = create_autospec(BrokerProvider, instance=True, spec_set=True)
    broker.get_open_positions.return_value = []
    service = trading_cycle_service.TradingCycleService(analysis, macro, broker)
    service.market_session_service = Mock()
    service.market_session_service.is_market_open.return_value = True
    service.market_session_service.is_quote_fresh.return_value = True

    broker.open_position.return_value = BrokerExecutionResult(
        request_id="request-1",
        status="filled",
        broker_order_id=None,
        broker_position_id="trade-1",
        filled_volume_lots=0.01,
        fill_price=4180.0,
    )
    return service


@pytest.mark.parametrize(
    "market_open,fresh,reason",
    [
        (True, True, None),
        (False, True, "market_closed"),
        (True, False, "stale_quote"),
    ],
)
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

    assert cycle.broker_provider.open_position.call_count == (
        1 if reason is None else 0
    )
    cycle.broker_provider.get_open_positions.assert_called_once_with()

    if reason is None:
        cycle.broker_provider.open_position.assert_called_once()

        request = cycle.broker_provider.open_position.call_args.kwargs["request"]

        assert request.agent_decision_id == "decision-1"
        assert request.symbol == "XAUUSD"
        assert request.direction == result.proposal.direction
        assert request.trade_style == result.proposal.trade_style
        assert request.confidence == result.proposal.confidence
        assert request.reference_price == 4180
        assert request.stop_loss == 4165
        assert request.take_profit == 4200
        assert request.max_holding_minutes == 2880
        assert request.volume_lots == 0.01

        assert result.position is None
        assert (result.execution_result is not None) == (reason is None)


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


@pytest.mark.parametrize(
    "failure", ["market", "llm", "database", "session", "position_manager"]
)
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


@pytest.mark.parametrize("configured,minutes", [(None, 5), ("3", 3)])
def test_scheduler_config_and_delayed_start(
    monkeypatch, configured, minutes, position_manager
):
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
        PaperPosition(
            id="trade-1",
            symbol="XAUUSD",
            direction="long",
            trade_style="swing",
            entry_price=4180.0,
            opened_at=datetime.now(timezone.utc),
            status="open",
            confidence=70.0,
            thesis="test",
        )
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


def test_cycle_persists_execution(cycle):
    persistence = Mock()
    persistence.get_unresolved.return_value = []
    cycle.execution_persistence = persistence

    original_open_position = cycle.broker_provider.open_position

    def execute(request):
        return BrokerExecutionResult(
            request_id=request.request_id,
            status="filled",
            broker_position_id="trade-1",
            filled_volume_lots=0.01,
            fill_price=4180.0,
        )

    original_open_position.side_effect = execute

    result = cycle.run("XAUUSD")

    persistence.save_request.assert_called_once()
    persistence.update_result.assert_called_once()

    saved_request = persistence.save_request.call_args.args[0]
    saved_result = persistence.update_result.call_args.args[0]

    assert saved_request.symbol == "XAUUSD"
    assert saved_request.request_id == saved_result.request_id
    assert result.execution_result.status == "filled"


def test_cycle_blocks_unresolved_execution(cycle):
    persistence = Mock()
    cycle.execution_persistence = persistence

    persistence.get_unresolved.return_value = [
        Mock(
            symbol="XAUUSD",
            status="submitted",
        )
    ]

    result = cycle.run("XAUUSD")

    assert result.execution_block_reason == "unresolved_broker_execution"
    assert result.execution_result is None

    cycle.broker_provider.open_position.assert_not_called()
    persistence.save_request.assert_not_called()


def test_cycle_builds_macro_context_and_evidence_from_events(cycle, monkeypatch):
    from app.models.macro_models import MacroEvent
    from app.services.evidence_priority_service import EvidencePriorityService
    from tests.macro_fixtures import NOW

    clock = Mock()
    clock.now.return_value = NOW
    monkeypatch.setattr(trading_cycle_service, "datetime", clock)
    cycle.macro_service.get_events.return_value = [
        MacroEvent(
            name="CPI",
            country="US",
            impact="high",
            event_time=NOW + timedelta(minutes=10),
            category="inflation",
        )
    ]
    cycle.run("XAUUSD")
    context = cycle.agent_service.make_decision.call_args.args[0]
    assert context.macro_context["state"] == "event_imminent"
    assert context.macro_context["event_name"] == "CPI"
    assert context.macro_context["allow_new_trade"] is False
    assert context.evidence_priority == EvidencePriorityService().prioritize(
        context.macro_context
    )
    assert "macro_risk" not in context.model_dump()
    cycle.macro_service.get_events.assert_called_once_with()
    cycle.macro_service.get_event_risk.assert_not_called()
