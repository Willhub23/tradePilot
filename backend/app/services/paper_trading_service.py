from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.models.paper_trading_models import PaperPosition
from app.models.trade_models import TradeProposal
from app.database import SessionLocal
from app.db_models import PaperTradeRecord


class PaperTradingService:
    def _record_to_position(self, record: PaperTradeRecord) -> PaperPosition:
        return PaperPosition(
            id=record.id,
            symbol=record.symbol,
            direction=record.direction,
            entry_price=record.entry_price,
            opened_at=record.opened_at,
            status=record.status,
            confidence=record.confidence,
            thesis=record.thesis,
            closed_at=record.closed_at,
            exit_price=record.exit_price,
            realized_pnl=record.realized_pnl,
            current_price=None,
            unrealized_pnl=0.0,
            stop_loss=record.stop_loss,
            take_profit=record.take_profit,
            max_holding_minutes=record.max_holding_minutes,
            close_reason=record.close_reason,
        )

    def open_position(
        self,
        proposal: TradeProposal,
        entry_price: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        max_holding_minutes: int | None = None,
    ) -> PaperPosition:
        position = PaperPosition(
            id=str(uuid4()),
            symbol=proposal.symbol,
            direction=proposal.direction,
            entry_price=entry_price,
            opened_at=datetime.now(timezone.utc),
            status="open",
            confidence=proposal.confidence,
            thesis=proposal.thesis,
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_holding_minutes=max_holding_minutes,
            close_reason=None,
        )

        with SessionLocal() as db:
            record = PaperTradeRecord(
                id=position.id,
                agent_decision_id=proposal.agent_decision_id,
                symbol=position.symbol,
                direction=position.direction,
                trade_style=proposal.trade_style,
                entry_price=position.entry_price,
                exit_price=None,
                opened_at=position.opened_at,
                closed_at=None,
                status=position.status,
                confidence=position.confidence,
                thesis=position.thesis,
                realized_pnl=None,
                stop_loss=position.stop_loss,
                take_profit=position.take_profit,
                max_holding_minutes=position.max_holding_minutes,
                close_reason=None,
            )

            db.add(record)
            db.commit()

        return position

    def update_position(
        self,
        position: PaperPosition,
        current_price: float,
    ) -> PaperPosition:
        position.current_price = current_price

        if position.direction == "long":
            position.unrealized_pnl = round(current_price - position.entry_price, 5)
        else:
            position.unrealized_pnl = round(position.entry_price - current_price, 5)

        return position

    def close_position(
        self,
        position: PaperPosition,
        exit_price: float,
        close_reason: str | None = None,
    ) -> PaperPosition:
        position.exit_price = exit_price
        position.closed_at = datetime.now(timezone.utc)
        position.status = "closed"
        position.close_reason = close_reason

        if position.direction == "long":
            position.realized_pnl = round(
                exit_price - position.entry_price,
                5,
            )
        else:
            position.realized_pnl = round(
                position.entry_price - exit_price,
                5,
            )

        with SessionLocal() as db:
            record = db.get(
                PaperTradeRecord,
                position.id,
            )

            if record is not None:
                record.exit_price = position.exit_price
                record.closed_at = position.closed_at
                record.status = position.status
                record.realized_pnl = position.realized_pnl
                record.close_reason = position.close_reason

                db.commit()

        return position

    def get_open_positions(self) -> list[PaperPosition]:
        with SessionLocal() as db:
            records = db.scalars(
                select(PaperTradeRecord).where(PaperTradeRecord.status == "open")
            )
            return [self._record_to_position(record) for record in records]

    def get_position(
        self,
        position_id: str,
    ) -> PaperPosition | None:
        with SessionLocal() as db:
            record = db.get(PaperTradeRecord, position_id)
            return self._record_to_position(record) if record is not None else None

    def close_position_by_id(
        self, position_id: str, exit_price: float, close_reason: str | None = None
    ) -> PaperPosition | None:
        position = self.get_position(position_id)

        if position is None:
            return None

        if position.status == "closed":
            return position

        return self.close_position(
            position=position, exit_price=exit_price, close_reason=close_reason
        )

    def get_closed_positions(self) -> list[PaperPosition]:
        with SessionLocal() as db:
            records = db.scalars(
                select(PaperTradeRecord).where(PaperTradeRecord.status == "closed")
            )
            return [self._record_to_position(record) for record in records]

    def get_performance_summary(self) -> dict:
        closed = self.get_closed_positions()

        total_trades = len(closed)

        wins = [p for p in closed if p.realized_pnl is not None and p.realized_pnl > 0]

        losses = [
            p for p in closed if p.realized_pnl is not None and p.realized_pnl < 0
        ]

        breakeven = [p for p in closed if p.realized_pnl == 0]

        total_pnl = round(
            sum(p.realized_pnl or 0 for p in closed),
            5,
        )

        win_rate = (
            round((len(wins) / total_trades) * 100, 2) if total_trades > 0 else 0.0
        )

        average_win = (
            round(
                sum(p.realized_pnl for p in wins if p.realized_pnl is not None)
                / len(wins),
                5,
            )
            if wins
            else 0.0
        )

        average_loss = (
            round(
                sum(p.realized_pnl for p in losses if p.realized_pnl is not None)
                / len(losses),
                5,
            )
            if losses
            else 0.0
        )

        gross_profit = sum(p.realized_pnl for p in wins if p.realized_pnl is not None)

        gross_loss = abs(
            sum(p.realized_pnl for p in losses if p.realized_pnl is not None)
        )

        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None

        largest_win = (
            round(
                max(p.realized_pnl for p in wins if p.realized_pnl is not None),
                5,
            )
            if wins
            else 0.0
        )

        largest_loss = (
            round(
                min(p.realized_pnl for p in losses if p.realized_pnl is not None),
                5,
            )
            if losses
            else 0.0
        )

        holding_times = [
            (p.closed_at - p.opened_at).total_seconds() / 60
            for p in closed
            if p.closed_at is not None
        ]

        average_holding_minutes = (
            round(sum(holding_times) / len(holding_times), 2) if holding_times else 0.0
        )

        long_trades = [p for p in closed if p.direction == "long"]
        short_trades = [p for p in closed if p.direction == "short"]

        long_pnl = round(
            sum(p.realized_pnl or 0 for p in long_trades),
            5,
        )

        short_pnl = round(
            sum(p.realized_pnl or 0 for p in short_trades),
            5,
        )

        return {
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "breakeven_trades": len(breakeven),
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "average_win": average_win,
            "average_loss": average_loss,
            "profit_factor": profit_factor,
            "largest_win": largest_win,
            "largest_loss": largest_loss,
            "average_holding_minutes": average_holding_minutes,
            "long_trades": len(long_trades),
            "short_trades": len(short_trades),
            "long_pnl": long_pnl,
            "short_pnl": short_pnl,
        }
