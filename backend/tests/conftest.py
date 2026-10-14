import os

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# Test collection must never configure the production PostgreSQL database.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.db_models import AgentDecisionRecord, PaperTradeRecord
from app.services import agent_decision_persistence_service, paper_trading_service


@pytest.fixture
def isolated_sessions(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    # Only the temporary SQLite database receives test tables.
    AgentDecisionRecord.__table__.create(engine)
    PaperTradeRecord.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(paper_trading_service, "SessionLocal", sessions)
    monkeypatch.setattr(agent_decision_persistence_service, "SessionLocal", sessions)
    yield sessions
    engine.dispose()
