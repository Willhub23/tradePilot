from typing import Protocol

from app.models.paper_trading_models import PaperPosition
from app.models.trade_models import TradeProposal


class BrokerProvider(Protocol):
    def get_open_positions(self) -> list[PaperPosition]: ...

    def open_position(
        self,
        proposal: TradeProposal,
        entry_price: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        max_holding_minutes: int | None = None,
    ) -> PaperPosition: ...

    def close_position(
        self,
        position: PaperPosition,
        exit_price: float,
        close_reason: str | None = None,
    ) -> PaperPosition: ...
