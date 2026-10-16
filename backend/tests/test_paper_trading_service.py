import pytest

from app.db_models import PaperTradeRecord
from app.models.trade_models import TradeProposal
from app.services.paper_trading_service import PaperTradingService

pytestmark = pytest.mark.usefixtures("isolated_sessions")


def proposal(direction="long"):
    return TradeProposal(
        symbol="XAUUSD",
        direction=direction,
        trade_style="swing",
        confidence=75,
        thesis="Test setup",
    )


def test_open_persists_and_survives_service_restart(isolated_sessions):
    position = PaperTradingService().open_position(proposal(), 4180.0)

    with isolated_sessions() as db:
        record = db.get(PaperTradeRecord, position.id)
        assert record is not None
        assert record.trade_style == "swing"
        assert record.status == "open"
        assert record.entry_price == 4180.0

    restarted = PaperTradingService()
    loaded = restarted.get_position(position.id)
    assert loaded is not None
    # SQLite does not retain timezone metadata; PostgreSQL does.
    assert loaded.opened_at.replace(tzinfo=None) == position.opened_at.replace(
        tzinfo=None
    )
    assert loaded.model_dump(exclude={"opened_at"}) == position.model_dump(
        exclude={"opened_at"}
    )
    assert restarted.get_open_positions() == [loaded]
    assert restarted.get_closed_positions() == []
    assert restarted.get_position("missing") is None


@pytest.mark.parametrize(
    "direction,current_price,exit_price,unrealized,realized",
    [
        ("long", 4187.0, 4172.0, 7.0, -8.0),
        ("short", 4170.0, 4172.0, 10.0, 8.0),
        ("long", 4180.123456, 4180.654321, 0.12346, 0.65432),
        ("short", 4180.123456, 4180.654321, -0.12346, -0.65432),
    ],
)
def test_update_and_close_persist_only_realized_values(
    isolated_sessions, direction, current_price, exit_price, unrealized, realized
):
    service = PaperTradingService()
    position = service.open_position(proposal(direction), 4180.0)
    updated = service.update_position(position, current_price)
    assert updated is position
    assert updated.current_price == current_price
    assert updated.unrealized_pnl == unrealized
    loaded = service.get_position(position.id)
    assert loaded.current_price is None
    assert loaded.unrealized_pnl == 0.0

    closed = service.close_position(position, exit_price)
    assert closed.status == "closed"
    assert closed.exit_price == exit_price
    assert closed.closed_at is not None
    assert closed.realized_pnl == realized
    with isolated_sessions() as db:
        record = db.get(PaperTradeRecord, position.id)
        assert record.status == "closed"
        assert record.exit_price == exit_price
        assert record.realized_pnl == realized
        assert record.closed_at == closed.closed_at.replace(tzinfo=None)

    restarted = PaperTradingService()
    loaded = restarted.get_position(position.id)
    assert loaded.status == "closed"
    assert loaded.realized_pnl == realized
    assert restarted.get_closed_positions() == [loaded]
    assert restarted.get_open_positions() == []


def test_close_by_id_and_performance_survive_restart():
    service = PaperTradingService()
    winner = service.open_position(proposal("short"), 4180.0)
    loser = service.open_position(proposal(), 4180.0)
    still_open = service.open_position(proposal(), 4180.0)

    restarted = PaperTradingService()
    assert restarted.close_position_by_id("missing", 4172.0) is None
    closed = restarted.close_position_by_id(winner.id, 4172.0)
    assert closed.realized_pnl == 8.0
    existing = restarted.get_position(winner.id)
    assert restarted.close_position_by_id(winner.id, 4000.0) == existing
    restarted.close_position_by_id(loser.id, 4176.0)

    restarted = PaperTradingService()
    assert {p.id for p in restarted.get_closed_positions()} == {winner.id, loser.id}
    assert [p.id for p in restarted.get_open_positions()] == [still_open.id]
    assert restarted.get_performance_summary() == {
        "total_trades": 2,
        "wins": 1,
        "losses": 1,
        "breakeven_trades": 0,
        "win_rate": 50.0,
        "total_pnl": 4.0,
        "average_win": 8.0,
        "average_loss": -4.0,
        "profit_factor": 2.0,
        "largest_win": 8.0,
        "largest_loss": -4.0,
        "average_holding_minutes": 0.0,
        "long_trades": 1,
        "short_trades": 1,
        "long_pnl": -4.0,
        "short_pnl": 8.0,
    }


def test_empty_performance_summary():
    assert PaperTradingService().get_performance_summary() == {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "breakeven_trades": 0,
        "win_rate": 0.0,
        "total_pnl": 0,
        "average_win": 0.0,
        "average_loss": 0.0,
        "profit_factor": None,
        "largest_win": 0.0,
        "largest_loss": 0.0,
        "average_holding_minutes": 0.0,
        "long_trades": 0,
        "short_trades": 0,
        "long_pnl": 0,
        "short_pnl": 0,
    }
