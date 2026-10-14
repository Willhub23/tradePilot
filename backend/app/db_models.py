from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, JSON, String, Text, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentDecisionRecord(Base):
    __tablename__ = "agent_decisions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    symbol: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    trade_style: Mapped[str] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float)
    thesis: Mapped[str] = mapped_column(Text)
    invalidation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_horizon: Mapped[str | None] = mapped_column(String, nullable=True)
    market_context: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"))
    macro_context: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"))


class PaperTradeRecord(Base):
    __tablename__ = "paper_trades"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
    )

    agent_decision_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agent_decisions.id"), nullable=True
    )

    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_holding_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    symbol: Mapped[str] = mapped_column(String)
    direction: Mapped[str] = mapped_column(String)
    trade_style: Mapped[str] = mapped_column(String)

    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(String)

    confidence: Mapped[float] = mapped_column(Float)
    thesis: Mapped[str] = mapped_column(Text)

    realized_pnl: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
