from app.services.position_manager_service import PositionManagerService
from fastapi import APIRouter, HTTPException

from app.models.trade_models import TradeProposal
from app.services.paper_trading_service import PaperTradingService
from app.services.market_service import MarketService


def create_paper_router(
    paper_trading_service: PaperTradingService,
    market_service: MarketService,
    position_manager_service: PositionManagerService,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/paper",
        tags=["Paper Trading"],
    )

    @router.get("/positions")
    def get_positions():
        positions = paper_trading_service.get_open_positions()

        for position in positions:
            quote = market_service.get_price(position.symbol)

            paper_trading_service.update_position(
                position=position,
                current_price=quote.price,
            )

        return positions

    @router.post("/positions/{position_id}/close")
    def close_position(position_id: str):
        position = paper_trading_service.get_position(position_id)

        if position is None:
            raise HTTPException(
                status_code=404,
                detail="Paper position not found",
            )

        quote = market_service.get_price(position.symbol)

        closed_position = paper_trading_service.close_position_by_id(
            position_id=position_id,
            exit_price=quote.price,
        )

        return closed_position

    @router.post("/test-open")
    def test_open_position():
        proposal = TradeProposal(
            symbol="XAUUSD",
            direction="long",
            trade_style="swing",
            confidence=100,
            thesis="Manual paper trading test",
        )

        quote = market_service.get_price("XAUUSD")

        return paper_trading_service.open_position(
            proposal=proposal,
            entry_price=quote.price,
        )

    @router.post("/test-open-with-exits")
    def test_open_with_exits():
        entry_price = 4200.0
        atr = 10.0

        proposal = TradeProposal(
            agent_decision_id=None,
            symbol="XAUUSD",
            direction="long",
            trade_style="swing",
            confidence=100.0,
            thesis="Manual lifecycle test",
        )

        stop_loss = entry_price - (1.5 * atr)
        take_profit = entry_price + (2.0 * atr)

        position = paper_trading_service.open_position(
            proposal=proposal,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_holding_minutes=2880,
        )

        return position

    @router.post("/test-manage")
    def test_manage_positions():
        return position_manager_service.manage_open_positions()

    @router.get("/history")
    def get_history():
        return paper_trading_service.get_closed_positions()

    @router.get("/performance")
    def get_performance():
        return paper_trading_service.get_performance_summary()

    return router
