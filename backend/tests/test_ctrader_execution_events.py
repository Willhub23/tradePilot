from concurrent.futures import ThreadPoolExecutor
from itertools import permutations
from threading import get_ident
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.integrations.ctrader.client import CTraderClient
from app.models.execution_models import BrokerExecutionRequest, BrokerExecutionResult
from app.services import broker_execution_persistence_service as persistence_module
from app.services.ctrader_execution_event_service import CTraderExecutionEventService
from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent, ProtoOAOrderErrorEvent
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOAExecutionType as Type


@pytest.fixture
def persistence(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'executions.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(persistence_module, "SessionLocal", sessionmaker(bind=engine))
    service = persistence_module.BrokerExecutionPersistenceService()
    service.save_request(BrokerExecutionRequest(
        request_id="request-1", agent_decision_id=None, symbol="XAUUSD",
        direction="long", volume_lots=0.01, reference_price=4180,
        stop_loss=4165, take_profit=4200, max_holding_minutes=2880,
        trade_style="swing", confidence=75, thesis="Test",
    ))
    yield service
    engine.dispose()


@pytest.fixture
def handler(persistence):
    return CTraderExecutionEventService(account_id=123, persistence=persistence)


def event(kind, request_id="request-1", order_id=42, executed=None, price=None):
    # Offline protobuf instances exercise real HasField behavior, no SDK client.
    message = ProtoOAExecutionEvent(ctidTraderAccountId=123, executionType=kind)
    if request_id is not None:
        message.order.clientOrderId = request_id
    if order_id is not None:
        message.order.orderId = order_id
    if executed is not None:
        message.order.tradeData.volume = 100
        message.order.executedVolume = executed
    if price is not None:
        message.order.executionPrice = price
    return message


def record(persistence):
    return persistence.find_execution(request_id="request-1")


@pytest.mark.parametrize("kind,status", [
    (Type.ORDER_ACCEPTED, "submitted"), (Type.ORDER_FILLED, "filled"),
    (Type.ORDER_PARTIAL_FILL, "pending"), (Type.ORDER_REJECTED, "rejected"),
    (Type.ORDER_CANCELLED, "unknown"), (Type.ORDER_EXPIRED, "unknown"),
])
def test_status_mapping(handler, persistence, kind, status):
    handler.process(event(kind))
    assert record(persistence).status == status
    assert record(persistence).broker_order_id == "42"
    assert bool(persistence.get_unresolved()) == (status not in {"filled", "rejected"})


def test_cumulative_partial_fills_duplicate_and_delayed(handler, persistence):
    for quantity in [30, 30, 60, 20]:
        handler.process(event(Type.ORDER_PARTIAL_FILL, executed=quantity, price=4181))
    assert record(persistence).filled_volume_lots == pytest.approx(0.006)
    assert record(persistence).status == "pending"
    handler.process(event(Type.ORDER_FILLED, executed=100, price=4182))
    assert record(persistence).filled_volume_lots == pytest.approx(0.01)
    assert record(persistence).fill_price == 4182
    assert persistence.get_unresolved() == []


@pytest.mark.parametrize("kinds", list(permutations([
    Type.ORDER_ACCEPTED, Type.ORDER_PARTIAL_FILL, Type.ORDER_FILLED,
])))
def test_out_of_order_events(handler, persistence, kinds):
    for kind in kinds:
        handler.process(event(kind, executed=100 if kind == Type.ORDER_FILLED else 30,
                              price=4182 if kind == Type.ORDER_FILLED else 4180))
    assert record(persistence).status == "filled"
    assert record(persistence).filled_volume_lots == pytest.approx(0.01)
    assert record(persistence).fill_price == 4182


def test_late_synchronous_result_cannot_downgrade_fill(handler, persistence):
    handler.process(event(Type.ORDER_FILLED, executed=100, price=4182))
    for status in ["submitted", "unknown", "pending", "rejected"]:
        persistence.update_result(BrokerExecutionResult(request_id="request-1", status=status))
    assert record(persistence).status == "filled"
    assert record(persistence).fill_price == 4182
    assert record(persistence).broker_order_id == "42"


def test_rejected_is_not_downgraded_by_acceptance(handler, persistence):
    rejected = event(Type.ORDER_REJECTED)
    rejected.errorCode = "NO_MONEY"
    handler.process(rejected)
    handler.process(event(Type.ORDER_ACCEPTED))
    assert record(persistence).status == "rejected"
    assert record(persistence).error_code == "NO_MONEY"


def test_fill_evidence_overrides_prior_rejection(handler, persistence):
    handler.process(event(Type.ORDER_REJECTED))
    handler.process(event(Type.ORDER_FILLED, executed=100))
    assert record(persistence).status == "filled"


def test_rejected_remainder_does_not_hide_partial_exposure(handler, persistence):
    handler.process(event(Type.ORDER_PARTIAL_FILL, executed=40))
    handler.process(event(Type.ORDER_REJECTED))
    assert record(persistence).status == "pending"
    assert record(persistence).filled_volume_lots == pytest.approx(0.004)
    assert len(persistence.get_unresolved()) == 1


def test_lookup_by_order_and_missing_fields_preserve_values(handler, persistence):
    initial = event(Type.ORDER_PARTIAL_FILL, executed=40, price=4181)
    initial.position.positionId = 99
    initial.errorCode = "PRIOR_INFO"
    handler.process(initial)
    handler.process(event(Type.ORDER_PARTIAL_FILL, request_id=None))
    saved = persistence.find_execution(broker_order_id="42")
    assert saved.request_id == "request-1"
    assert saved.broker_position_id == "99"
    assert saved.filled_volume_lots == pytest.approx(0.004)
    assert saved.fill_price == 4181
    assert saved.error_code == "PRIOR_INFO"


@pytest.mark.parametrize("request_id,order_id", [("unknown", 42), (None, 999), (None, None)])
def test_unknown_identifiers_do_not_update_other_execution(handler, persistence, caplog, request_id, order_id):
    handler.process(event(Type.ORDER_ACCEPTED))
    handler.process(event(Type.ORDER_FILLED, request_id=request_id, order_id=order_id))
    assert record(persistence).status == "submitted"
    assert "Unmatched" in caplog.text


def test_conflicting_stored_identifiers_are_not_overwritten(handler, persistence, caplog):
    initial = event(Type.ORDER_ACCEPTED)
    initial.position.positionId = 99
    handler.process(initial)
    handler.process(event(Type.ORDER_FILLED, order_id=999))
    conflict = event(Type.ORDER_FILLED)
    conflict.position.positionId = 100
    handler.process(conflict)
    assert record(persistence).status == "submitted"
    assert record(persistence).broker_order_id == "42"
    assert record(persistence).broker_position_id == "99"
    assert "Conflicting" in caplog.text


def test_order_error_is_not_proof_of_rejection(handler, persistence):
    handler.process(event(Type.ORDER_ACCEPTED))
    handler.process(ProtoOAOrderErrorEvent(
        ctidTraderAccountId=123, orderId=42, errorCode="TIMEOUT", description="Uncertain outcome",
    ))
    assert record(persistence).status == "submitted"
    assert record(persistence).error_message == "Uncertain outcome"
    handler.process(event(Type.ORDER_FILLED))
    assert record(persistence).error_code == "TIMEOUT"


def test_wrong_account_ignored(handler, persistence, caplog):
    message = event(Type.ORDER_FILLED)
    message.ctidTraderAccountId = 456
    handler.process(message)
    assert record(persistence).status == "prepared"
    assert "account" in caplog.text


def test_missing_cumulative_volume_does_not_sum_deals(handler, persistence):
    message = event(Type.ORDER_PARTIAL_FILL)
    message.deal.filledVolume = 30
    message.deal.executionPrice = 4181
    handler.process(message)
    handler.process(message)
    assert record(persistence).status == "pending"
    assert record(persistence).filled_volume_lots is None
    assert record(persistence).fill_price is None


def test_database_commit_failure_rolls_back_and_logs(handler, persistence, monkeypatch, caplog):
    factory = persistence_module.SessionLocal
    session_class = factory.class_
    with monkeypatch.context() as scoped:
        scoped.setattr(session_class, "commit", Mock(side_effect=RuntimeError("database unavailable")))
        handler.process(event(Type.ORDER_FILLED))
    assert record(persistence).status == "prepared"
    assert "database unavailable" in caplog.text
    handler.process(event(Type.ORDER_FILLED))
    assert record(persistence).status == "filled"


def test_lookup_failure_logged(persistence, caplog):
    broken = Mock()
    broken.find_execution.side_effect = RuntimeError("lookup unavailable")
    CTraderExecutionEventService(123, broken).process(event(Type.ORDER_ACCEPTED))
    broken.update_result.assert_not_called()
    assert "lookup unavailable" in caplog.text


def test_callback_dispatches_persistence_off_callback_thread(handler, persistence):
    client = CTraderClient.__new__(CTraderClient)
    client._execution_event_handler = handler
    callback_thread = get_ident()
    workers = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        def dispatch(fn, message, **kwargs):
            def run():
                workers.append(get_ident())
                fn(message, **kwargs)
            pool.submit(run)
            return Mock()
        with patch("app.integrations.ctrader.client.Protobuf.extract", return_value=event(Type.ORDER_FILLED)), patch(
            "app.integrations.ctrader.client.threads.deferToThread", side_effect=dispatch,
        ):
            client._on_message_received(None, Mock())
    assert workers and workers[0] != callback_thread
    assert record(persistence).status == "filled"


def test_final_event_missing_fill_fields_preserves_partial_observation(handler, persistence):
    handler.process(event(Type.ORDER_PARTIAL_FILL, executed=40, price=4181))
    handler.process(event(Type.ORDER_FILLED))
    assert record(persistence).status == "filled"
    assert record(persistence).filled_volume_lots == pytest.approx(0.004)
    assert record(persistence).fill_price == 4181


def test_duplicate_fill_is_idempotent(handler, persistence):
    message = event(Type.ORDER_FILLED, executed=100, price=4182)
    message.position.positionId = 99
    handler.process(message)
    handler.process(message)
    assert record(persistence).filled_volume_lots == pytest.approx(0.01)
    assert record(persistence).fill_price == 4182
    assert record(persistence).broker_position_id == "99"


def test_concurrent_sync_result_and_async_fill(handler, persistence):
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(handler.process, event(Type.ORDER_FILLED, executed=100, price=4182)),
                   pool.submit(persistence.update_result, BrokerExecutionResult(
                       request_id="request-1", status="submitted", broker_order_id="42"))]
        for future in futures:
            future.result(timeout=5)
    assert record(persistence).status == "filled"
    assert record(persistence).filled_volume_lots == pytest.approx(0.01)
    assert record(persistence).fill_price == 4182


def test_final_cumulative_event_enriches_sync_single_deal_result(handler, persistence):
    persistence.update_result(BrokerExecutionResult(
        request_id="request-1", status="filled", filled_volume_lots=0.003, fill_price=4183,
    ))
    handler.process(event(Type.ORDER_FILLED, executed=100, price=4182))
    assert record(persistence).filled_volume_lots == pytest.approx(0.01)
    assert record(persistence).fill_price == 4182


def test_identifiers_from_deal_correlate_only_known_order(handler, persistence):
    handler.process(event(Type.ORDER_ACCEPTED))
    message = ProtoOAExecutionEvent(ctidTraderAccountId=123, executionType=Type.ORDER_FILLED)
    message.deal.orderId = 42
    message.deal.positionId = 99
    handler.process(message)
    assert record(persistence).status == "filled"
    assert record(persistence).broker_position_id == "99"


def test_conflicting_identifiers_between_event_parts(handler, persistence, caplog):
    message = event(Type.ORDER_FILLED)
    message.deal.orderId = 999
    handler.process(message)
    assert record(persistence).status == "prepared"
    assert "Conflicting" in caplog.text


def test_request_and_order_pointing_to_different_records(handler, persistence, caplog):
    request = dict(record(persistence).request_data, request_id="request-2")
    persistence.save_request(BrokerExecutionRequest(**request))
    persistence.update_result(BrokerExecutionResult(request_id="request-2", status="submitted", broker_order_id="42"))
    handler.process(event(Type.ORDER_FILLED))
    assert record(persistence).status == "prepared"
    assert persistence.find_execution(request_id="request-2").status == "submitted"
    assert "Conflicting" in caplog.text


def test_lookup_missing_identifier(persistence):
    with pytest.raises(ValueError):
        persistence.find_execution()
    assert persistence.find_execution(request_id="missing") is None
    assert persistence.find_execution(broker_order_id="missing") is None


def test_ambiguous_order_id_is_not_associated(handler, persistence, caplog):
    request = dict(record(persistence).request_data, request_id="request-2")
    persistence.save_request(BrokerExecutionRequest(**request))
    for request_id in ["request-1", "request-2"]:
        persistence.update_result(BrokerExecutionResult(request_id=request_id, status="submitted", broker_order_id="42"))
    handler.process(event(Type.ORDER_FILLED, request_id=None))
    assert record(persistence).status == "submitted"
    assert "reconciliation required" in caplog.text


def test_callback_order_error_is_dispatched():
    client = CTraderClient.__new__(CTraderClient)
    client._execution_event_handler = Mock()
    error = ProtoOAOrderErrorEvent(ctidTraderAccountId=123, orderId=42, errorCode="ERROR")
    with patch("app.integrations.ctrader.client.Protobuf.extract", return_value=error), patch(
        "app.integrations.ctrader.client.threads.deferToThread"
    ) as dispatch:
        client._on_message_received(None, Mock())
    dispatch.assert_called_once()
    assert dispatch.call_args.args == (client._execution_event_handler.process, error)
    assert dispatch.call_args.kwargs["received_at"].tzinfo is not None
    assert dispatch.call_args.kwargs["received_monotonic_ns"] > 0


def test_worker_failure_errback_logs_without_clearing_authentication(caplog):
    client = CTraderClient.__new__(CTraderClient)
    client._authenticated = Mock()
    client._on_execution_tracking_error(RuntimeError("worker failed"))
    assert "worker failed" in caplog.text
    client._authenticated.clear.assert_not_called()


def test_delayed_partial_without_fill_evidence_cannot_downgrade_rejection(handler, persistence):
    handler.process(event(Type.ORDER_REJECTED))
    handler.process(event(Type.ORDER_PARTIAL_FILL))
    assert record(persistence).status == "rejected"


def test_partial_fill_evidence_after_rejection_remains_unresolved(handler, persistence):
    handler.process(event(Type.ORDER_REJECTED))
    handler.process(event(Type.ORDER_PARTIAL_FILL, executed=40))
    assert record(persistence).status == "pending"
    assert record(persistence).filled_volume_lots == pytest.approx(0.004)


def test_unsupported_event_type_does_not_change_status(handler, persistence):
    handler.process(event(Type.SWAP))
    assert record(persistence).status == "prepared"
