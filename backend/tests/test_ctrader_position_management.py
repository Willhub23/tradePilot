from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from ctrader_open_api.messages import OpenApiMessages_pb2 as M
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAPositionStatus as PS, ProtoOAExecutionType as E,
)

from app.models.broker_models import BrokerPosition, BrokerCloseResult
from app.models.execution_models import BrokerExecutionResult, BrokerExecutionRequest
from app.providers.ctrader_broker_provider import CTraderBrokerProvider
from app.services.position_manager_service import PositionManagerService
from app.services.ctrader_execution_event_service import CTraderExecutionEventService
from tests.test_ctrader_execution_events import persistence, event, Type


@pytest.fixture
def setup(persistence):
    persistence.update_result(BrokerExecutionResult(request_id="request-1", status="filled",
        broker_order_id="42", broker_position_id="77", filled_volume_lots=.01, fill_price=4182))
    position = BrokerPosition(broker_position_id="77", symbol="XAUUSD", direction="long",
        volume_lots=.01, entry_price=4182, stop_loss=4167, take_profit=4202,
        opened_at=datetime.now(timezone.utc) - timedelta(hours=49), status="open")
    client = Mock(account_id=123, environment="demo", authenticated=True, access_token="fake")
    provider = CTraderBrokerProvider(client, persistence)
    provider.get_symbol_metadata = Mock(return_value=SimpleNamespace(lotSize=10000, digits=2))
    provider.get_open_positions = Mock(return_value=[position])
    state = SimpleNamespace(provider=provider, client=client, position=position, persistence=persistence,
                            kind=E.ORDER_ACCEPTED, closed=False, fail=False, calls=[])
    def send(request):
        state.calls.append(request)
        if isinstance(request, M.ProtoOAGetAccountListByAccessTokenReq):
            response = M.ProtoOAGetAccountListByAccessTokenRes()
            response.ctidTraderAccount.add(ctidTraderAccountId=123, isLive=False)
            return response
        if isinstance(request, M.ProtoOAClosePositionReq):
            if state.fail:
                raise TimeoutError("uncertain")
            response = M.ProtoOAExecutionEvent(ctidTraderAccountId=123, executionType=state.kind)
            response.order.orderId = 99
            response.order.closingOrder = True
            response.order.positionId = 77
            response.position.positionId = 77
            response.position.positionStatus = PS.POSITION_STATUS_CLOSED if state.closed else PS.POSITION_STATUS_OPEN
            if state.closed:
                provider.get_open_positions.return_value = []
            return response
        raise AssertionError(type(request))
    client.send.side_effect = send
    return state


def closes(state):
    return [r for r in state.calls if isinstance(r, M.ProtoOAClosePositionReq)]


def test_close_request_volume_and_confirmed_outcome(setup):
    setup.kind, setup.closed = E.ORDER_FILLED, True
    result = setup.provider.close_position(setup.position)
    assert result.status == "closed"
    request, = closes(setup)
    assert (request.ctidTraderAccountId, request.positionId, request.volume) == (123, 77, 100)
    assert result.broker_order_id == "99"
    assert setup.persistence.find_execution(request_id="request-1").status == "filled"


@pytest.mark.parametrize("kind,expected", [(E.ORDER_ACCEPTED, "submitted"),
    (E.ORDER_FILLED, "submitted"), (E.ORDER_PARTIAL_FILL, "submitted"),
    (E.ORDER_REJECTED, "rejected")])
def test_response_without_confirmed_closure(setup, kind, expected):
    setup.kind = kind
    assert setup.provider.close_position(setup.position).status == expected
    assert setup.provider.close_position(setup.position).status == expected
    assert len(closes(setup)) == 1


def test_timeout_restart_and_later_reconciliation(setup):
    setup.fail = True
    assert setup.provider.close_position(setup.position).status == "unknown"
    restarted = CTraderBrokerProvider(setup.client, type(setup.persistence)())
    restarted.get_open_positions = setup.provider.get_open_positions
    restarted.get_symbol_metadata = setup.provider.get_symbol_metadata
    assert restarted.close_position(setup.position).status == "unknown"
    assert len(closes(setup)) == 1
    restarted.get_open_positions.return_value = []
    reports = restarted.manage_positions()
    assert reports[0].close_result.status == "closed"
    assert len(closes(setup)) == 1


def test_concurrent_close_single_submission(setup):
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: setup.provider.close_position(setup.position), range(2)))
    assert len(closes(setup)) == 1
    assert all(r.status in ("prepared", "submitted") for r in results)


@pytest.mark.parametrize("age,due", [(47, False), (49, True)])
def test_holding_time_and_no_price_polling(setup, age, due):
    setup.position.opened_at = datetime.now(timezone.utc) - timedelta(hours=age)
    market = Mock()
    reports = PositionManagerService(setup.provider, market).manage_open_positions()
    assert reports[0].protection == "verified"
    assert reports[0].holding_policy == ("expired" if due else "not_due")
    assert len(closes(setup)) == int(due)
    market.get_price.assert_not_called()


def test_restart_preserves_broker_opening_time(setup):
    restarted = CTraderBrokerProvider(setup.client, type(setup.persistence)())
    restarted.get_open_positions = setup.provider.get_open_positions
    restarted.get_symbol_metadata = setup.provider.get_symbol_metadata
    assert restarted.manage_positions()[0].holding_policy == "expired"
    assert len(closes(setup)) == 1


@pytest.mark.parametrize("field,value", [("stop_loss", None), ("take_profit", None),
    ("stop_loss", 4185), ("take_profit", 4200), ("stop_loss", float("nan"))])
def test_unsafe_protection_never_auto_closes(setup, field, value, caplog):
    setattr(setup.position, field, value)
    report = setup.provider.manage_positions()[0]
    assert report.protection == "unsafe"
    assert report.message
    assert not closes(setup)
    assert any(r.levelname == "CRITICAL" for r in caplog.records)


def test_actual_fill_relative_distances(setup):
    request = BrokerExecutionRequest.model_validate(setup.persistence.find_execution(request_id="request-1").request_data)
    status, message = setup.provider.position_management.verify_fill(request, "77")
    assert (status, message) == ("verified", None)
    assert request.reference_price == 4180 and setup.position.entry_price == 4182


def test_short_protection_distances(setup):
    request = BrokerExecutionRequest.model_validate(setup.persistence.find_execution(request_id="request-1").request_data)
    request.direction = "short"
    request.stop_loss, request.take_profit = 4195, 4160
    setup.position.direction = "short"
    setup.position.stop_loss, setup.position.take_profit = 4197, 4162
    assert setup.provider.position_management.verify_fill(request, "77")[0] == "verified"


def test_missing_execution_metadata(setup):
    setup.position.broker_position_id = "999"
    report = setup.provider.manage_positions()[0]
    assert report.holding_policy == "unresolved"
    assert report.protection == "unconfirmed"
    assert not closes(setup)


def test_already_closed_by_sl_tp(setup):
    setup.provider.get_open_positions.return_value = []
    assert setup.provider.close_position(setup.position).status == "closed"
    assert setup.provider.manage_positions() == []
    assert not closes(setup)


def test_snapshot_failure_surface_unsafe(setup):
    setup.provider.get_open_positions.side_effect = TimeoutError("snapshot failed")
    report = setup.provider.manage_positions()[0]
    assert report.protection == "unconfirmed"
    assert report.holding_policy == "unresolved"
    with pytest.raises(TimeoutError):
        setup.provider.close_position(setup.position)
    assert not closes(setup)


def test_post_send_snapshot_failure_not_closed(setup):
    setup.provider.get_open_positions.side_effect = [[setup.position], TimeoutError("lost")]
    assert setup.provider.close_position(setup.position).status == "submitted"
    assert len(closes(setup)) == 1


@pytest.mark.parametrize("volume", [0, -.01, .02, .005, float("nan"), .01000001])
def test_invalid_or_stale_volume(setup, volume):
    stale = setup.position.model_copy(update={"volume_lots": volume})
    with pytest.raises(ValueError):
        setup.provider.close_position(stale)
    assert not closes(setup)


@pytest.mark.parametrize("field,value", [("environment", "live"), ("authenticated", False)])
def test_connection_guard(setup, field, value):
    setattr(setup.client, field, value)
    with pytest.raises(RuntimeError):
        setup.provider.close_position(setup.position)
    assert not closes(setup)


@pytest.mark.parametrize("live", [True, None])
def test_actual_account_demo_guard(setup, live):
    response = M.ProtoOAGetAccountListByAccessTokenRes()
    account = response.ctidTraderAccount.add(ctidTraderAccountId=123)
    if live is not None: account.isLive = live
    setup.client.send.side_effect = lambda _: response
    with pytest.raises(RuntimeError):
        setup.provider.close_position(setup.position)
    assert setup.persistence.get_close(123, "77") is None


def test_durable_claim_failure_prevents_send(setup, monkeypatch):
    monkeypatch.setattr(setup.persistence, "claim_close", Mock(side_effect=RuntimeError("db down")))
    with pytest.raises(RuntimeError):
        setup.provider.close_position(setup.position)
    assert not closes(setup)


def test_result_persistence_failure_leaves_claim(setup, monkeypatch):
    original = setup.persistence.update_close
    monkeypatch.setattr(setup.persistence, "update_close", Mock(side_effect=RuntimeError("db down")))
    with pytest.raises(RuntimeError):
        setup.provider.close_position(setup.position)
    monkeypatch.setattr(setup.persistence, "update_close", original)
    assert setup.provider.close_position(setup.position).status == "prepared"
    assert len(closes(setup)) == 1


def test_async_closed_event_does_not_modify_opening_execution(setup):
    setup.provider.close_position(setup.position)
    handler = CTraderExecutionEventService(123, setup.persistence)
    response = M.ProtoOAExecutionEvent(ctidTraderAccountId=123, executionType=E.ORDER_FILLED)
    response.order.closingOrder = True
    response.order.orderId = 99
    response.order.positionId = 77
    response.position.positionId = 77
    response.position.positionStatus = PS.POSITION_STATUS_CLOSED
    handler.process(response)
    assert setup.persistence.get_close(123, "77").status == "closed"
    opening = setup.persistence.find_execution(request_id="request-1")
    assert opening.broker_order_id == "42" and opening.status == "filled"
    setup.persistence.update_close(123, BrokerCloseResult(broker_position_id="77", status="submitted"))
    assert setup.persistence.get_close(123, "77").status == "closed"


def test_fill_event_invokes_protection_verifier(setup):
    handler = CTraderExecutionEventService(123, setup.persistence)
    handler.protection_verifier = Mock()
    handler.process(event(Type.ORDER_FILLED, executed=100))
    handler.protection_verifier.assert_called_once_with("request-1")


def test_multiple_positions_block_management(setup):
    setup.provider.get_open_positions.return_value = [setup.position, setup.position.model_copy(update={"broker_position_id": "78"})]
    assert setup.provider.manage_positions()[0].holding_policy == "unresolved"
    with pytest.raises(ValueError):
        setup.provider.close_position(setup.position)
    assert not closes(setup)


def test_paper_management_preserved():
    from app.models.paper_trading_models import PaperPosition
    from app.providers.local_paper_broker_provider import LocalPaperBrokerProvider
    position = PaperPosition(id="paper", symbol="XAUUSD", direction="long", entry_price=4180,
        opened_at=datetime.now(timezone.utc), status="open", confidence=75, thesis="test",
        stop_loss=4165, take_profit=4200)
    paper, market = Mock(), Mock()
    paper.get_open_positions.return_value = [position]
    paper.close_position.return_value = position.model_copy(update={"status": "closed"})
    market.get_price.return_value.price = 4160
    result = PositionManagerService(LocalPaperBrokerProvider(paper), market).manage_open_positions()
    assert result[0].status == "closed"
    paper.close_position.assert_called_once_with(position=position, exit_price=4160, close_reason="stop_loss")


def test_unknown_absent_position_not_claimed_closed(setup):
    setup.provider.get_open_positions.return_value = []
    unknown = setup.position.model_copy(update={"broker_position_id": "999"})
    with pytest.raises(ValueError, match="metadata"):
        setup.provider.close_position(unknown)
    assert not closes(setup)


def test_partial_close_remains_blocked(setup):
    setup.kind = E.ORDER_PARTIAL_FILL
    setup.provider.close_position(setup.position)
    setup.position.volume_lots = .004
    report = setup.provider.manage_positions()[0]
    assert report.close_result.status == "submitted"
    assert len(closes(setup)) == 1


def test_generic_error_is_uncertain(setup):
    original = setup.client.send.side_effect
    def send(request):
        if isinstance(request, M.ProtoOAClosePositionReq):
            setup.calls.append(request)
            return M.ProtoOAOrderErrorEvent(ctidTraderAccountId=123, positionId=77, errorCode="TIMEOUT")
        return original(request)
    setup.client.send.side_effect = send
    assert setup.provider.close_position(setup.position).status == "unknown"
    assert len(closes(setup)) == 1


@pytest.mark.parametrize("mode", ["account", "position", "opening"])
def test_wrong_close_response_not_trusted(setup, mode):
    original = setup.client.send.side_effect
    def send(request):
        response = original(request)
        if isinstance(request, M.ProtoOAClosePositionReq):
            if mode == "account": response.ctidTraderAccountId = 999
            if mode == "position": response.position.positionId = 999
            if mode == "opening": response.order.closingOrder = False
        return response
    setup.client.send.side_effect = send
    assert setup.provider.close_position(setup.position).status == "unknown"
    assert setup.persistence.get_close(123, "77").broker_order_id is None


def test_close_event_wrong_account_ignored(setup):
    setup.provider.close_position(setup.position)
    response = M.ProtoOAExecutionEvent(ctidTraderAccountId=999, executionType=E.ORDER_FILLED)
    response.position.positionId = 77
    response.position.positionStatus = PS.POSITION_STATUS_CLOSED
    CTraderExecutionEventService(123, setup.persistence).process(response)
    assert setup.persistence.get_close(123, "77").status == "submitted"


def test_management_worker_guard(setup, monkeypatch):
    monkeypatch.setattr("app.services.ctrader_position_management_service.isInIOThread", lambda: True)
    with pytest.raises(RuntimeError, match="reactor"):
        setup.provider.close_position(setup.position)
    assert not setup.calls


def test_sync_fill_reports_missing_protection(setup):
    setup.position.stop_loss = None
    request = BrokerExecutionRequest.model_validate(setup.persistence.find_execution(request_id="request-1").request_data)
    setup.provider.build_market_order_request = Mock(return_value=M.ProtoOANewOrderReq())
    original = setup.client.send.side_effect
    def send(message):
        if isinstance(message, M.ProtoOANewOrderReq):
            response = M.ProtoOAExecutionEvent(ctidTraderAccountId=123, executionType=E.ORDER_FILLED)
            response.order.orderId = 42
            response.position.positionId = 77
            return response
        return original(message)
    setup.client.send.side_effect = send
    result = setup.provider.open_position(request)
    assert result.status == "filled"
    assert result.protection_status == "unsafe" and result.protection_message
    assert not closes(setup)


def test_client_registers_worker_verifier(setup):
    from app.integrations.ctrader.client import CTraderClient
    client = CTraderClient.__new__(CTraderClient)
    client._execution_event_handler = CTraderExecutionEventService(123, setup.persistence)
    verifier = Mock()
    client.set_position_protection_verifier(verifier)
    client._execution_event_handler.process(event(Type.ORDER_FILLED, executed=100))
    verifier.assert_called_once_with("request-1")


@pytest.mark.parametrize("mode", ["account", "missing_status", "duplicate", "missing_volume"])
def test_invalid_raw_snapshot_cannot_confirm_closure(setup, mode):
    from tests.test_ctrader_broker_provider import make_position
    response = M.ProtoOAReconcileRes(ctidTraderAccountId=123)
    response.position.add().CopyFrom(make_position())
    if mode == "account": response.ctidTraderAccountId = 999
    if mode == "missing_status": response.position[0].ClearField("positionStatus")
    if mode == "duplicate": response.position.add().CopyFrom(response.position[0])
    if mode == "missing_volume": response.position[0].tradeData.ClearField("volume")
    setup.client.send.side_effect = lambda req: response if isinstance(req, M.ProtoOAReconcileReq) else SimpleNamespace(symbol=[SimpleNamespace(symbolId=41, symbolName="XAUUSD")])
    # Exercise the actual mapping implementation, not the manager fixture stub.
    with pytest.raises(ValueError):
        CTraderBrokerProvider.get_open_positions(setup.provider)
