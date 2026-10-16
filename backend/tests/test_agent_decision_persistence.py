import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import agent_routes
from app.services import trading_cycle_service
from app.api.paper_routes import create_paper_router
from app.db_models import AgentDecisionRecord, PaperTradeRecord
from app.models.agent_models import AgentDecision, AgentMarketContext
from app.models.macro_models import MacroEventRisk
from app.models.market_models import MultiTimeframeAnalysis, TimeframeAnalysis
from app.services.agent_decision_persistence_service import AgentDecisionPersistenceService
from app.services.agent_service import AgentService
from app.services.paper_trading_service import PaperTradingService
from app.providers.local_paper_broker_provider import LocalPaperBrokerProvider
from app.services.position_manager_service import PositionManagerService

from tests.market_fixtures import market_structure_fields

pytestmark = pytest.mark.usefixtures("isolated_sessions")


@pytest.fixture
def context():
    now = datetime.now(timezone.utc)
    return AgentMarketContext(
        symbol="XAUUSD",
        market_analysis=MultiTimeframeAnalysis(
            symbol="XAUUSD", price=4180, price_timestamp=now, timeframes=[TimeframeAnalysis(
            **market_structure_fields(current_price=4180.0),
            interval="1h", latest_candle_timestamp=datetime.now(timezone.utc),
            rsi=60, ema_20=4180, ema_50=4170, atr=10, score=65,
            bias="bullish", trend_direction="bullish", trend_strength=1.5,
            trend_strength_label="strong",
        )],
            overall_score=65, overall_bias="bullish", timeframe_alignment="aligned",
        ),
        macro_risk=MacroEventRisk(
            event_risk="medium", next_event="US Initial Jobless Claims",
            minutes_until_event=120, event_time=now, allow_new_trade=True,
        ),
    )


def decision(action="long"):
    return AgentDecision(
        action=action, trade_style="none" if action == "hold" else "swing",
        confidence=75, thesis="Price trend supports this decision.",
        invalidation_reason=None if action == "hold" else "Trend reversal",
        target_reason=None if action == "hold" else "Continuation",
        expected_horizon=None if action == "hold" else "1-2 days",
    )


@pytest.mark.parametrize("action", ["hold", "long", "short"])
def test_save_and_reload_context(context, action, isolated_sessions):
    original = decision(action)
    decision_id = AgentDecisionPersistenceService().save_decision(context, original)
    assert str(UUID(decision_id)) == decision_id
    record = AgentDecisionPersistenceService().get_decision(decision_id)
    assert record.id == decision_id
    assert record.symbol == context.symbol
    assert record.timestamp is not None
    for name, value in original.model_dump().items():
        assert getattr(record, name) == value
    assert record.market_context == context.market_analysis.model_dump(mode="json")
    assert record.macro_context == context.macro_risk.model_dump(mode="json")
    with isolated_sessions() as db:
        assert db.get(AgentDecisionRecord, decision_id).action == action
    assert AgentDecisionPersistenceService().get_decision("missing") is None


def test_recent_order_and_limit(context, isolated_sessions):
    service = AgentDecisionPersistenceService()
    ids = [service.save_decision(context, decision(action)) for action in ["hold", "long", "short"]]
    with isolated_sessions() as db:
        for index, decision_id in enumerate(ids):
            db.get(AgentDecisionRecord, decision_id).timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index)
        db.commit()
    restarted = AgentDecisionPersistenceService()
    assert [r.id for r in restarted.get_recent_decisions(2)] == ids[::-1][:2]
    assert len(restarted.get_recent_decisions()) == 3
    with pytest.raises(ValueError):
        restarted.get_recent_decisions(0)


@pytest.mark.parametrize("case", ["hold", "long", "short", "grounded", "macro"])
def test_agent_persists_final_decision(context, case, monkeypatch, tmp_path, isolated_sessions):
    monkeypatch.chdir(tmp_path)
    raw = decision(case if case in {"hold", "long", "short"} else "long")
    if case == "grounded":
        raw.thesis = "The macro event could push gold higher."
    if case == "macro":
        context.macro_risk.allow_new_trade = False
    provider = Mock()
    provider.make_decision.return_value = raw
    result = AgentService(provider).make_decision(context)
    assert provider.make_decision.call_count == (0 if case == "macro" else 1)
    if case in {"grounded", "macro"}:
        assert result.decision.action == "hold"
    stored = AgentDecisionPersistenceService().get_decision(result.decision_id)
    assert stored.action == result.decision.action
    assert stored.thesis == result.decision.thesis
    assert stored.confidence == result.decision.confidence
    with isolated_sessions() as db:
        assert db.scalar(select(func.count()).select_from(AgentDecisionRecord)) == 1
    log = json.loads((tmp_path / "data/agent_decisions.jsonl").read_text())
    assert log["decision"] == result.decision.model_dump(mode="json")


@pytest.fixture
def client(context, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    provider = Mock()
    provider.make_decision.return_value = decision()
    monkeypatch.setattr(trading_cycle_service, "LocalReasoningProvider", lambda: provider)
    analysis = Mock()
    analysis.analyze_multi_timeframe.return_value = context.market_analysis
    macro = Mock()
    macro.get_event_risk.return_value = context.macro_risk
    paper = PaperTradingService()
    app = FastAPI()
    cycle = trading_cycle_service.TradingCycleService(analysis, macro, LocalPaperBrokerProvider(paper))
    monkeypatch.setattr(cycle.market_session_service, "is_market_open", lambda: True)
    app.include_router(agent_routes.create_agent_router(cycle))
    market = Mock()
    market.get_price.return_value = SimpleNamespace(price=4180.0)
    position_manager = Mock(spec=PositionManagerService)
    position_manager.manage_open_positions.return_value = []
    app.include_router(create_paper_router(paper, market, position_manager))
    with TestClient(app) as client:
        yield client, provider


@pytest.mark.parametrize("action", ["long", "short", "hold", "grounded"])
def test_route_links_only_approved_final_decision(client, action, isolated_sessions):
    http, provider = client
    provider.make_decision.return_value = decision("long" if action == "grounded" else action)
    if action == "grounded":
        provider.make_decision.return_value.thesis = "The macro event could push gold higher."
    response = http.get("/api/agent/decision/XAUUSD")
    assert response.status_code == 200
    body = response.json()
    assert provider.make_decision.call_count == 1
    with isolated_sessions() as db:
        record = db.get(AgentDecisionRecord, body["decision_id"])
        assert record.action == body["decision"]["action"]
        if action in {"long", "short"}:
            assert body["approved"] is True
            assert body["proposal"]["agent_decision_id"] == record.id
            trade = db.get(PaperTradeRecord, body["position"]["id"])
            assert trade.agent_decision_id == record.id
        else:
            assert body["decision"]["action"] == "hold"
            assert body["proposal"] is None
            assert body["position"] is None
            assert body["approved"] is False
            assert db.scalar(select(func.count()).select_from(PaperTradeRecord)) == 0


def test_history_api(client, context):
    http, provider = client
    service = AgentDecisionPersistenceService()
    first = service.save_decision(context, decision("hold"))
    second = service.save_decision(context, decision("short"))
    history = http.get("/api/agent/history")
    assert history.status_code == 200
    assert {r["id"] for r in history.json()} == {first, second}
    assert len(http.get("/api/agent/history?limit=1").json()) == 1
    detail = http.get(f"/api/agent/history/{first}")
    assert detail.status_code == 200
    assert detail.json()["market_context"] == context.market_analysis.model_dump(mode="json")
    assert http.get("/api/agent/history/missing").status_code == 404
    assert http.get("/api/agent/history?limit=0").status_code == 422
    provider.make_decision.assert_not_called()


def test_manual_trade_has_no_decision(client, isolated_sessions):
    http, _ = client
    response = http.post("/api/paper/test-open")
    assert response.status_code == 200
    with isolated_sessions() as db:
        trade = db.get(PaperTradeRecord, response.json()["id"])
        assert trade.agent_decision_id is None
