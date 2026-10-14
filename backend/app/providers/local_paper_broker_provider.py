from app.models.paper_trading_models import PaperPosition
from app.models.trade_models import TradeProposal
from app.services.paper_trading_service import PaperTradingService


class LocalPaperBrokerProvider:
    def __init__(
        self,
        paper_trading_service: PaperTradingService,
    ):
        self.paper_trading_service = paper_trading_service

    def get_open_positions(self) -> list[PaperPosition]:
        return self.paper_trading_service.get_open_positions()

    def open_position(
        self,
        proposal: TradeProposal,
        entry_price: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        max_holding_minutes: int | None = None,
    ) -> PaperPosition:
        return self.paper_trading_service.open_position(
            proposal=proposal,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_holding_minutes=max_holding_minutes,
        )

    def close_position(
        self,
        position: PaperPosition,
        exit_price: float,
        close_reason: str | None = None,
    ) -> PaperPosition:
        return self.paper_trading_service.close_position(
            position=position,
            exit_price=exit_price,
            close_reason=close_reason,
        )
