from app.models.paper_trading_models import PaperPosition
from app.models.trade_models import TradeProposal


class CTraderBrokerProvider:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        access_token: str,
        account_id: str,
        environment: str = "demo",
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.account_id = account_id
        self.environment = environment

        if environment not in {"demo", "live"}:
            raise ValueError("environment must be 'demo' or 'live'")

    def get_open_positions(self) -> list[PaperPosition]:
        raise NotImplementedError("cTrader position retrieval is not implemented yet")

    def open_position(
        self,
        proposal: TradeProposal,
        entry_price: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        max_holding_minutes: int | None = None,
    ) -> PaperPosition:
        raise NotImplementedError("cTrader order execution is not implemented yet")

    def close_position(
        self,
        position: PaperPosition,
        exit_price: float,
        close_reason: str | None = None,
    ) -> PaperPosition:
        raise NotImplementedError("cTrader position closing is not implemented yet")
