import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.execution_models import (
    BrokerExecutionRequest,
    BrokerExecutionResult,
)
from app.services import broker_execution_persistence_service as module


@pytest.fixture
def persistence(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Base.metadata.create_all(bind=engine)

    test_session = sessionmaker(bind=engine)
    monkeypatch.setattr(module, "SessionLocal", test_session)

    yield module.BrokerExecutionPersistenceService()

    engine.dispose()


@pytest.mark.parametrize(
    "status",
    ["prepared", "submitted", "pending", "unknown"],
)
def test_unresolved_execution_statuses(persistence, status):
    request = BrokerExecutionRequest(
        request_id=f"test-{status}",
        agent_decision_id=None,
        symbol="XAUUSD",
        direction="long",
        trade_style="swing",
        confidence=70,
        thesis="Testing unresolved status",
        volume_lots=0.01,
        reference_price=4180,
        stop_loss=4165,
        take_profit=4200,
        max_holding_minutes=2880,
    )

    persistence.save_request(request)

    if status != "prepared":
        persistence.update_result(
            BrokerExecutionResult(
                request_id=request.request_id,
                status=status,
            )
        )

    unresolved = persistence.get_unresolved()

    assert len(unresolved) == 1
    assert unresolved[0].status == status


def test_execution_persistence(persistence):
    request = BrokerExecutionRequest(
        request_id="test-execution-001",
        agent_decision_id=None,
        symbol="XAUUSD",
        direction="long",
        trade_style="swing",
        confidence=70,
        thesis="Testing execution persistence",
        volume_lots=0.01,
        reference_price=4180,
        stop_loss=4165,
        take_profit=4200,
        max_holding_minutes=2880,
    )

    persistence.save_request(request)

    unresolved = persistence.get_unresolved()

    assert len(unresolved) == 1
    assert unresolved[0].request_id == request.request_id
    assert unresolved[0].status == "prepared"
    assert unresolved[0].request_data["symbol"] == "XAUUSD"

    result = BrokerExecutionResult(
        request_id=request.request_id,
        status="filled",
        broker_order_id="12345",
        broker_position_id="98765",
        filled_volume_lots=0.01,
        fill_price=4180.50,
    )

    persistence.update_result(result)

    assert persistence.get_unresolved() == []
