import importlib
import sys
from threading import Event
from types import ModuleType
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from ctrader_open_api.messages import OpenApiMessages_pb2 as M
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOAClientPermissionScope as Scope

from app.database import Base
from app.db_models import BrokerRuntimeState, BrokerPositionCloseRecord
from app.services import broker_runtime_store as stores
from app.services.ctrader_runtime_service import CTraderRuntimeService
from app.models.broker_models import BrokerPositionManagementResult
from app.integrations.ctrader.client import CTraderClient
from scripts.migrate_database import verify_database, verify_table, migrate_database


@pytest.fixture
def setup(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(stores, "SessionLocal", sessionmaker(bind=engine))
    store = stores.BrokerRuntimeStore(123)
    store.acquire, store.assert_owner, store.release = Mock(), Mock(), Mock()
    client = Mock(account_id=123, authenticated=True, demo_verified=True, generation=1)
    client.wait_until_authenticated.return_value = True
    provider, persistence = Mock(), Mock()
    provider.position_management.manage.return_value = []
    provider.get_open_positions.return_value = []
    provider.get_pending_order_ids.return_value = []
    persistence.get_unresolved.return_value = []
    persistence.get_unresolved_closes.return_value = []
    runtime = CTraderRuntimeService(client, provider, persistence, store)
    yield runtime
    if runtime.started: runtime.stop()
    engine.dispose()


def test_start_paused_recovery_before_trading(setup):
    setup.start()
    setup.provider.recover_executions.assert_called_once_with(setup.persistence)
    assert setup.status()["demo_verified"] is True
    assert setup.block_reason() == "trading_paused"
    setup.resume()
    assert setup.order_allowed()
    setup.provider.position_management.manage.assert_called_with(allow_close=False)


def test_authentication_failure_cleanup(setup):
    setup.client.wait_until_authenticated.return_value = False
    with pytest.raises(RuntimeError, match="authentication"):
        setup.start()
    assert not setup.order_allowed()
    setup.provider.recover_executions.assert_not_called()
    setup.client.stop.assert_called_once()
    setup.store.release.assert_called_once()


def test_recovery_failure_blocks_then_recovers(setup):
    setup.provider.recover_executions.side_effect = TimeoutError("history unavailable")
    setup.start()
    assert not setup.ready.is_set()
    setup.provider.recover_executions.side_effect = None
    setup.resume()
    assert setup.order_allowed()


def test_reconnect_requires_recovery(setup):
    setup.start()
    setup.resume()
    setup.client.authenticated = False
    setup.client.generation += 1
    setup.connection_changed()
    assert not setup.order_allowed()
    setup.client.authenticated = True
    assert not setup.order_allowed()
    setup.recover()
    assert setup.order_allowed()
    assert setup.provider.recover_executions.call_count >= 3
    setup.client.start.assert_called_once()


def test_connection_change_during_recovery_blocks(setup):
    setup.start()
    def change(_):
        setup.client.generation += 1
        setup.connection_changed()
    setup.provider.recover_executions.side_effect = change
    with pytest.raises(RuntimeError, match="Connection changed"):
        setup.resume()
    assert not setup.order_allowed()


def test_missing_protection_latched_and_survives_restart(setup):
    setup.start()
    setup.resume()
    report = BrokerPositionManagementResult(broker_position_id="77", protection="unsafe",
        holding_policy="unresolved", message="Missing SL/TP")
    setup.provider.position_management.manage.return_value = [report]
    with pytest.raises(RuntimeError): setup.manage_positions()
    assert not setup.order_allowed()
    assert setup.store.load()["critical_error"] == "Missing SL/TP"
    with pytest.raises(RuntimeError): setup.resume()
    setup.provider.position_management.manage.return_value = []
    setup.recover()
    assert not setup.order_allowed()  # Healthy now, but operator acknowledgement required.
    setup.resume()
    assert setup.store.load()["critical_error"] is None
    assert setup.order_allowed()


def test_pause_persistent_and_management_available(setup):
    setup.start()
    setup.resume()
    setup.pause()
    assert not setup.client.order_guard()
    assert setup.store.load()["paused"] is True
    setup.manage_positions()
    setup.provider.position_management.manage.assert_called_with(allow_close=True)


def test_only_one_runtime_owner(setup):
    setup.start()
    with pytest.raises(RuntimeError, match="already"):
        setup.start()
    assert setup.client.start.call_count == 1


def test_shutdown_disables_entries_and_stops_worker(setup):
    setup.start()
    setup.resume()
    setup.stop()
    assert not setup.order_allowed()
    assert not setup.worker.is_alive()
    setup.client.stop.assert_called_once()
    setup.store.release.assert_called_once()


@pytest.mark.parametrize("reason", ["paused", "critical", "disconnected", "recovering", "symbol", "positions", "unresolved", "close"])
def test_preflight_blocks(setup, reason):
    setup.start()
    setup.resume()
    request = Mock(request_id="new", symbol="XAUUSD")
    if reason == "paused": setup.pause()
    if reason == "critical": setup.critical("unsafe")
    if reason == "disconnected": setup.client.authenticated = False
    if reason == "recovering": setup.ready.clear()
    if reason == "symbol": request.symbol = "EURUSD"
    if reason == "positions": setup.provider.get_open_positions.return_value = [Mock()]
    if reason == "unresolved": setup.persistence.get_unresolved.return_value = [Mock(request_id="old")]
    if reason == "close": setup.persistence.get_unresolved_closes.return_value = [Mock()]
    with pytest.raises(RuntimeError): setup.before_open(request)


def test_migration_schema_idempotence_and_preservation(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO broker_runtime_state VALUES ('123', true, 'keep', CURRENT_TIMESTAMP)"))
    verify_database(engine)
    Base.metadata.create_all(engine)
    verify_database(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT critical_error FROM broker_runtime_state")) == "keep"
    engine.dispose()


def test_migration_rejects_incompatible_schema():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE broker_position_closes (account_id TEXT PRIMARY KEY)"))
        with pytest.raises(RuntimeError, match="Incompatible"):
            verify_table(connection, BrokerPositionCloseRecord.__table__)
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        migrate_database(engine)
    engine.dispose()


@pytest.fixture
def client(monkeypatch):
    for key, value in {"CTRADER_CLIENT_ID": "id", "CTRADER_CLIENT_SECRET": "secret",
        "CTRADER_ACCESS_TOKEN": "token", "CTRADER_ACCOUNT_ID": "123", "CTRADER_ENVIRONMENT": "demo"}.items():
        monkeypatch.setenv(key, value)
    with patch("app.integrations.ctrader.client.Client"):
        result = CTraderClient(Mock())
    result._started = True
    result._client.isConnected = True
    result.state_listener = Mock()
    return result


def accounts(client, live=False):
    response = M.ProtoOAGetAccountListByAccessTokenRes(accessToken=client.access_token, permissionScope=Scope.SCOPE_TRADE)
    response.ctidTraderAccount.add(ctidTraderAccountId=123, isLive=live)
    return response


@pytest.mark.parametrize("mode", ["live", "missing_flag", "wrong_id", "token", "scope", "duplicate"])
def test_account_evidence_rejected(client, mode):
    response = accounts(client)
    if mode == "live": response.ctidTraderAccount[0].isLive = True
    if mode == "missing_flag": response.ctidTraderAccount[0].ClearField("isLive")
    if mode == "wrong_id": response.ctidTraderAccount[0].ctidTraderAccountId = 456
    if mode == "token": response.accessToken = "wrong"
    if mode == "scope": response.permissionScope = Scope.SCOPE_VIEW
    if mode == "duplicate": response.ctidTraderAccount.add(ctidTraderAccountId=123, isLive=False)
    with patch("app.integrations.ctrader.client.Protobuf.extract", return_value=response), patch("app.integrations.ctrader.client.reactor"):
        client._on_accounts(Mock())
    assert not client.authenticated and not client.demo_verified
    client._client.send.assert_not_called()


def test_valid_account_authentication(client):
    with patch("app.integrations.ctrader.client.Protobuf.extract", return_value=accounts(client)):
        client._on_accounts(Mock())
    assert client.demo_verified and not client.authenticated
    with patch("app.integrations.ctrader.client.Protobuf.extract", return_value=M.ProtoOAAccountAuthRes(ctidTraderAccountId=123)):
        client._on_account_authenticated(Mock())
    assert client.authenticated
    client.state_listener.assert_called()


def test_mismatched_account_auth_response(client):
    client.demo_verified = True
    with patch("app.integrations.ctrader.client.Protobuf.extract", return_value=M.ProtoOAAccountAuthRes(ctidTraderAccountId=999)), patch("app.integrations.ctrader.client.reactor"):
        client._on_account_authenticated(Mock())
    assert not client.authenticated


def test_live_configuration_rejected(client, monkeypatch):
    monkeypatch.setenv("CTRADER_ENVIRONMENT", "live")
    with patch("app.integrations.ctrader.client.Client") as factory:
        with pytest.raises(ValueError, match="only"):
            CTraderClient(Mock())
        factory.assert_not_called()


def test_reactor_send_rechecks_gate(client):
    client.demo_verified = True
    client._authenticated.set()
    client.order_guard = Mock(side_effect=[True, False])
    with patch("app.integrations.ctrader.client.reactor.callFromThread", side_effect=lambda fn: fn()):
        with pytest.raises(RuntimeError, match="gate"):
            client.send(M.ProtoOANewOrderReq(ctidTraderAccountId=123))
    client._client.send.assert_not_called()


def test_disconnect_invalidates_verification(client):
    client.demo_verified = True
    client._authenticated.set()
    generation = client.generation
    client._on_disconnected(client._client, "lost")
    assert not client.authenticated and not client.demo_verified
    assert client.generation == generation + 1


def test_runtime_routes_authenticated(setup, monkeypatch):
    from app.api.runtime_routes import router
    setup.start()
    app = FastAPI()
    app.state.ctrader_runtime = setup
    app.include_router(router)
    monkeypatch.setenv("RUNTIME_CONTROL_TOKEN", "control")
    with TestClient(app) as web:
        assert web.post("/api/runtime/resume").status_code == 401
        headers = {"Authorization": "Bearer control"}
        assert web.get("/api/runtime/status", headers=headers).json()["paused"] is True
        assert web.post("/api/runtime/resume", headers=headers).json()["new_orders_enabled"] is True
        assert web.post("/api/runtime/pause", headers=headers).json()["new_orders_enabled"] is False


@pytest.mark.parametrize("observation", [False, True])
def test_main_ctrader_startup_order_and_cleanup(monkeypatch, tmp_path, observation):
    monkeypatch.setenv("QUICK_TRADE_OBSERVATION_ENABLED", str(observation).lower())
    monkeypatch.setenv("QUICK_TRADE_SLIPPAGE_ALLOWANCE", ".1")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRADING_BROKER", "ctrader_demo")
    monkeypatch.setenv("RUNTIME_CONTROL_TOKEN", "control")
    for name, services in [("app.api.market_routes", ["analysis_service", "market_service"]),
                            ("app.api.macro_routes", ["macro_service"])]:
        module = ModuleType(name)
        module.router = APIRouter()
        for name2 in services: setattr(module, name2, Mock())
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    runtime, scheduler = Mock(), Mock()
    order = []
    runtime.start.side_effect = lambda: order.append("auth_and_recovery")
    scheduler.start.side_effect = lambda: order.append("scheduler")
    monkeypatch.setattr("scripts.migrate_database.verify_database", Mock())
    monkeypatch.setattr("app.integrations.ctrader.client.CTraderClient", Mock())
    monkeypatch.setattr("app.providers.ctrader_broker_provider.CTraderBrokerProvider", Mock())
    monkeypatch.setattr("app.services.ctrader_runtime_service.CTraderRuntimeService", Mock(return_value=runtime))
    monkeypatch.setattr(main, "create_trading_scheduler", Mock(return_value=scheduler))
    try:
        with TestClient(main.app):
            assert order == ["auth_and_recovery", "scheduler"]
            from app.services.ctrader_runtime_service import CTraderRuntimeService
            assert CTraderRuntimeService.call_args.kwargs["observation_enabled"] is observation
            from scripts.migrate_database import verify_database
            assert verify_database.call_args.kwargs["require_strategy"] == (True if observation else None)
            with pytest.raises(RuntimeError, match="already running"):
                with TestClient(main.app): pass
        scheduler.shutdown.assert_called_once_with(wait=True)
        runtime.stop.assert_called_once()
    finally:
        sys.modules.pop("app.main", None)


def test_pending_broker_order_pauses_new_entries(setup):
    setup.start()
    setup.resume()
    setup.provider.get_pending_order_ids.return_value = ["99"]
    with pytest.raises(RuntimeError, match="pending orders"):
        setup.recover()
    assert not setup.order_allowed()
    assert "99" in setup.store.load()["critical_error"]


def test_resume_cannot_erase_new_safety_alert(setup):
    setup.start()
    original = setup.recover
    def recover_with_alert():
        original()
        setup.critical("new protection alert")
    setup.recover = recover_with_alert
    with pytest.raises(RuntimeError, match="new safety alert"):
        setup.resume()
    assert setup.store.load()["critical_error"] == "new protection alert"
    assert not setup.order_allowed()


def test_management_cannot_close_during_shutdown(setup):
    setup.start()
    setup.resume()
    setup.stopping.set()
    setup.manage_positions()
    setup.provider.position_management.manage.assert_called_with(allow_close=False)
    assert not setup.client.close_guard()


def test_stopped_reactor_never_restarted(client):
    client._started = False
    with patch("app.integrations.ctrader.client.reactor") as reactor:
        reactor.running = False
        reactor._startedBefore = True
        with pytest.raises(RuntimeError, match="cannot be restarted"):
            client.start()
        client._client.startService.assert_not_called()


def test_client_stop_cancels_reconnect_even_disconnected(client):
    client._client.isConnected = False
    with patch("app.integrations.ctrader.client.ClientService.stopService", return_value=None) as stop, patch(
            "app.integrations.ctrader.client.reactor.callFromThread", side_effect=lambda fn: fn()):
        client.stop()
    stop.assert_called_once_with(client._client)
    assert not client.authenticated and not client.demo_verified


def test_owner_loss_blocks_preflight(setup):
    setup.start()
    setup.resume()
    setup.store.assert_owner.side_effect = RuntimeError("ownership lost")
    with pytest.raises(RuntimeError, match="ownership lost"):
        setup.before_open(Mock(symbol="XAUUSD", request_id="new"))


def test_runtime_state_survives_new_store_instance(setup):
    setup.store.load()
    setup.store.save(False, "Unsafe SL/TP")
    restarted = stores.BrokerRuntimeStore(123)
    assert restarted.load() == {"paused": False, "critical_error": "Unsafe SL/TP"}


def test_database_owner_prevents_second_connection(setup):
    setup.store.acquire.side_effect = RuntimeError("already owns")
    with pytest.raises(RuntimeError, match="already owns"):
        setup.start()
    setup.client.start.assert_not_called()


def test_event_failure_latches_runtime(setup):
    from app.services.ctrader_execution_event_service import CTraderExecutionEventService
    setup.start()
    setup.resume()
    handler = CTraderExecutionEventService(123, Mock())
    handler.error_callback = setup.critical
    handler._process = Mock(side_effect=RuntimeError("DB failed"))
    handler.process(Mock())
    assert not setup.order_allowed()
    assert "persistence failed" in setup.store.load()["critical_error"]


def test_runtime_ownership_release_invalidates_on_error():
    store = stores.BrokerRuntimeStore(123)
    connection = Mock(invalidated=False)
    connection.execute.side_effect = RuntimeError("database lost")
    store.connection = connection
    with pytest.raises(RuntimeError): store.release()
    connection.invalidate.assert_called_once()
    connection.close.assert_called_once()
    assert store.connection is None


def test_recovery_cancels_before_broker_send(setup):
    from app.services.ctrader_recovery_service import CTraderRecoveryService
    recovery = CTraderRecoveryService(setup.provider, setup.persistence)
    setup.stopping.set()
    with pytest.raises(RuntimeError, match="shutdown"):
        recovery._send(M.ProtoOAReconcileReq(), M.ProtoOAReconcileRes)
    setup.client.send.assert_not_called()


def test_cycle_collects_decision_while_paused_without_execution(monkeypatch, tmp_path):
    # Reuse the existing fully isolated analysis fixture through its test factory.
    from tests.test_trading_scheduler import cycle as cycle_fixture
    service = cycle_fixture.__wrapped__(monkeypatch, tmp_path)
    runtime = Mock()
    from contextlib import nullcontext
    runtime.operation.side_effect = nullcontext
    runtime.block_reason.return_value = "trading_paused"
    service.runtime = runtime
    result = service.run("XAUUSD")
    assert result.execution_block_reason == "trading_paused"
    service.agent_service.make_decision.assert_called_once()
    service.broker_provider.open_position.assert_not_called()


def test_runtime_management_reports_logged_by_scheduler(setup, caplog):
    from app.services.trading_scheduler import run_scheduled_cycle
    import logging
    setup.start()
    cycle = Mock(runtime=setup)
    cycle.market_session_service.is_market_open.return_value = True
    manager = Mock()
    manager.manage_open_positions.return_value = [BrokerPositionManagementResult(
        broker_position_id="77", protection="verified", holding_policy="not_due")]
    with caplog.at_level(logging.INFO):
        run_scheduled_cycle(cycle, manager, "XAUUSD")
    assert "Broker management" in caplog.text
    cycle.run.assert_called_once_with("XAUUSD")


def test_resume_blocked_while_alert_waiting_for_lock_or_database(setup):
    setup.start()
    setup.pending_alert.set()
    with pytest.raises(RuntimeError, match="durable persistence"):
        setup.resume()
    assert not setup.order_allowed()


def test_main_auth_failure_never_starts_scheduler(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRADING_BROKER", "ctrader_demo")
    monkeypatch.setenv("RUNTIME_CONTROL_TOKEN", "control")
    for name, services in [("app.api.market_routes", ["analysis_service", "market_service"]),
                            ("app.api.macro_routes", ["macro_service"])]:
        module = ModuleType(name)
        module.router = APIRouter()
        for service in services: setattr(module, service, Mock())
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    runtime = Mock()
    runtime.start.side_effect = RuntimeError("authentication failed")
    factory = Mock()
    monkeypatch.setattr(main, "create_trading_scheduler", factory)
    monkeypatch.setattr("scripts.migrate_database.verify_database", Mock())
    monkeypatch.setattr("app.integrations.ctrader.client.CTraderClient", Mock())
    monkeypatch.setattr("app.providers.ctrader_broker_provider.CTraderBrokerProvider", Mock())
    monkeypatch.setattr("app.services.ctrader_runtime_service.CTraderRuntimeService", Mock(return_value=runtime))
    try:
        with pytest.raises(RuntimeError, match="authentication"):
            with TestClient(main.app): pass
        factory.assert_not_called()
        runtime.stop.assert_called_once()
        assert main._serving is False
    finally:
        sys.modules.pop("app.main", None)


def test_pending_operator_pause_cannot_be_overridden_by_resume(setup):
    setup.start()
    setup.resume()
    setup.pause_requested.set()
    assert not setup.order_allowed()
    with pytest.raises(RuntimeError, match="operator pause"):
        setup.resume()
    setup.pause()
    assert setup.store.load()["paused"] is True


def test_resume_rejected_after_shutdown(setup):
    setup.start()
    setup.stop()
    with pytest.raises(RuntimeError, match="stopping"):
        setup.resume()
    assert not setup.order_allowed()
