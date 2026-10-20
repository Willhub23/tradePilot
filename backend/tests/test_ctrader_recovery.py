from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from ctrader_open_api.messages import OpenApiMessages_pb2 as M
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOAOrderStatus as S
from app.providers.ctrader_broker_provider import CTraderBrokerProvider
from app.services.ctrader_recovery_service import CTraderRecoveryService
from app.models.execution_models import BrokerExecutionResult
from tests.test_ctrader_execution_events import persistence, handler, event, record, Type


def details(status=S.ORDER_STATUS_FILLED, volume=100, client_id="request-1"):
    result = M.ProtoOAOrderDetailsRes(ctidTraderAccountId=123)
    result.order.orderId = 42
    if client_id is not None:
        result.order.clientOrderId = client_id
    result.order.positionId = 77
    result.order.orderStatus = status
    result.order.tradeData.volume = 100
    if volume is not None:
        result.order.executedVolume = volume
    return result


@pytest.fixture
def recovery(persistence):
    client = Mock(account_id=123, environment="demo", authenticated=True)
    snapshot = M.ProtoOAReconcileRes(ctidTraderAccountId=123)
    history = M.ProtoOAOrderListRes(ctidTraderAccountId=123, hasMore=False)
    history.order.add().CopyFrom(details().order)
    client.send.side_effect = lambda request: {
        M.ProtoOAReconcileReq: snapshot,
        M.ProtoOAOrderListReq: history,
        M.ProtoOAOrderDetailsReq: details(),
    }[type(request)]
    return CTraderRecoveryService(CTraderBrokerProvider(client), persistence)


@pytest.mark.parametrize("status,volume,expected", [
    (S.ORDER_STATUS_FILLED, 100, "filled"),
    (S.ORDER_STATUS_ACCEPTED, 0, "submitted"),
    (S.ORDER_STATUS_ACCEPTED, 40, "pending"),
    (S.ORDER_STATUS_REJECTED, 0, "rejected"),
    (S.ORDER_STATUS_CANCELLED, 0, "cancelled"),
    (S.ORDER_STATUS_EXPIRED, 0, "cancelled"),
    (S.ORDER_STATUS_CANCELLED, 40, "pending"),
    (S.ORDER_STATUS_CANCELLED, None, "submitted"),
])
def test_known_order_outcomes(recovery, persistence, status, volume, expected):
    persistence.update_result(BrokerExecutionResult(request_id="request-1", status="submitted", broker_order_id="42"))
    recovery.client.send.side_effect = lambda r: (details(status, volume, None)
        if isinstance(r, M.ProtoOAOrderDetailsReq) else M.ProtoOAReconcileRes(ctidTraderAccountId=123))
    recovery.recover()
    assert record(persistence).status == expected
    if volume and expected != "submitted":
        assert record(persistence).filled_volume_lots == pytest.approx(volume / 10000)
    assert all(isinstance(c.args[0], (M.ProtoOAReconcileReq, M.ProtoOAOrderDetailsReq))
               for c in recovery.client.send.call_args_list)


def test_restart_request_only_and_closed_position(recovery, persistence):
    # Fresh service, empty open positions, exact historical order (position may be closed).
    restarted = CTraderRecoveryService(CTraderBrokerProvider(recovery.client), type(persistence)())
    assert restarted.recover() == []
    saved = record(persistence)
    assert (saved.status, saved.broker_order_id, saved.broker_position_id) == ("filled", "42", "77")
    timestamp = saved.updated_at
    assert restarted.recover() == []
    assert record(persistence).updated_at == timestamp


@pytest.mark.parametrize("mode", ["unknown", "incomplete", "missing_flag", "network", "wrong_account", "wrong_id", "conflicting_client", "db"])
def test_insufficient_evidence_keeps_blocked(recovery, persistence, monkeypatch, mode):
    original = recovery.client.send.side_effect
    def respond(request):
        if mode == "network":
            raise TimeoutError("interrupted")
        response = original(request)
        if isinstance(response, M.ProtoOAOrderListRes):
            if mode == "unknown": response.ClearField("order")
            if mode == "incomplete": response.hasMore = True
            if mode == "missing_flag": response.ClearField("hasMore")
        if mode == "wrong_account": response.ctidTraderAccountId = 999
        if isinstance(response, M.ProtoOAOrderDetailsRes):
            if mode == "wrong_id": response.order.orderId = 999
            if mode == "conflicting_client": response.order.clientOrderId = "another"
        return response
    recovery.client.send.side_effect = respond
    if mode == "db":
        monkeypatch.setattr(persistence, "update_result", Mock(side_effect=RuntimeError("database down")))
    assert len(recovery.recover()) == 1
    assert record(persistence).status == "prepared"


def test_concurrent_event_and_recovery(recovery, persistence, handler):
    with ThreadPoolExecutor(2) as pool:
        tasks = [pool.submit(recovery.recover), pool.submit(handler.process, event(Type.ORDER_FILLED, executed=100))]
        for task in tasks: task.result()
    assert record(persistence).status == "filled"
    assert record(persistence).filled_volume_lots == .01


def test_duplicate_pending_snapshot_no_write(recovery, persistence):
    persistence.update_result(BrokerExecutionResult(request_id="request-1", status="submitted", broker_order_id="42"))
    recovery.client.send.side_effect = lambda r: (details(S.ORDER_STATUS_ACCEPTED, 40)
        if isinstance(r, M.ProtoOAOrderDetailsReq) else M.ProtoOAReconcileRes(ctidTraderAccountId=123))
    recovery.recover()
    timestamp = record(persistence).updated_at
    recovery.recover()
    assert record(persistence).updated_at == timestamp


@pytest.mark.parametrize("environment,authenticated", [("live", True), ("demo", False)])
def test_recovery_guard(recovery, environment, authenticated):
    recovery.client.environment = environment
    recovery.client.authenticated = authenticated
    with pytest.raises((ValueError, RuntimeError)):
        recovery.recover()
    recovery.client.send.assert_not_called()


def test_reactor_guard(recovery, monkeypatch):
    monkeypatch.setattr("app.services.ctrader_recovery_service.isInIOThread", lambda: True)
    with pytest.raises(RuntimeError, match="reactor"):
        recovery.recover()
    recovery.client.send.assert_not_called()


def test_deals_supply_cumulative_volume_and_average(handler, persistence):
    response = details(volume=None)
    for identifier, quantity, price in [(1, 30, 4100), (2, 70, 4200)]:
        response.deal.add(dealId=identifier, orderId=42, positionId=77,
                          filledVolume=quantity, executionPrice=price)
    handler.process_order_snapshot("request-1", response.order, response.deal)
    assert record(persistence).filled_volume_lots == .01
    assert record(persistence).fill_price == 4170


@pytest.mark.parametrize("mode", ["quantity", "order", "position", "duplicate"])
def test_contradictory_deals_rejected(handler, persistence, mode):
    response = details()
    deal = response.deal.add(dealId=1, orderId=42, positionId=77, filledVolume=100)
    if mode == "quantity": deal.filledVolume = 30
    if mode == "order": deal.orderId = 99
    if mode == "position": deal.positionId = 99
    if mode == "duplicate": response.deal.add(dealId=1, orderId=42, filledVolume=20)
    with pytest.raises(ValueError):
        handler.process_order_snapshot("request-1", response.order, response.deal)
    assert record(persistence).status == "prepared"


def test_unknown_known_order_never_rejected(recovery, persistence):
    persistence.update_result(BrokerExecutionResult(request_id="request-1", status="unknown", broker_order_id="42"))
    recovery.client.send.side_effect = lambda r: (M.ProtoOAErrorRes(errorCode="ORDER_NOT_FOUND")
        if isinstance(r, M.ProtoOAOrderDetailsReq) else M.ProtoOAReconcileRes(ctidTraderAccountId=123))
    assert len(recovery.recover()) == 1
    assert record(persistence).status == "unknown"


def test_ambiguous_client_identifier(recovery, persistence):
    original = recovery.client.send.side_effect
    def respond(request):
        response = original(request)
        if isinstance(response, M.ProtoOAOrderListRes):
            response.order.add(orderId=43, clientOrderId="request-1")
        return response
    recovery.client.send.side_effect = respond
    recovery.recover()
    assert record(persistence).broker_order_id is None
    assert record(persistence).status == "prepared"


@pytest.mark.parametrize("terminal", ["filled", "cancelled", "rejected"])
def test_delayed_acceptance_preserves_terminal(handler, persistence, terminal):
    persistence.update_result(BrokerExecutionResult(request_id="request-1", status=terminal, broker_order_id="42"))
    handler.process_order_snapshot("request-1", details(S.ORDER_STATUS_ACCEPTED, 0).order, [])
    assert record(persistence).status == terminal


def test_stale_zero_cancel_cannot_erase_partial(handler, persistence):
    handler.process(event(Type.ORDER_PARTIAL_FILL, executed=40))
    handler.process_order_snapshot("request-1", details(S.ORDER_STATUS_CANCELLED, 0).order, [])
    assert record(persistence).status == "pending"
    assert record(persistence).filled_volume_lots == .004


def test_cancel_does_not_downgrade_fill(handler, persistence):
    handler.process(event(Type.ORDER_FILLED, executed=100))
    handler.process_order_snapshot("request-1", details(S.ORDER_STATUS_CANCELLED, 0).order, [])
    assert record(persistence).status == "filled"
    assert record(persistence).filled_volume_lots == .01


def test_existing_ids_preserved_on_conflicting_snapshot(handler, persistence):
    persistence.update_result(BrokerExecutionResult(request_id="request-1", status="submitted", broker_order_id="42", broker_position_id="99"))
    with pytest.raises(ValueError, match="broker_position_id"):
        handler.process_order_snapshot("request-1", details().order, [])
    assert record(persistence).broker_position_id == "99"
    assert record(persistence).status == "submitted"
