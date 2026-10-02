from fastapi import APIRouter
from app.services.market_service import MarketService

router = APIRouter(prefix="/api/market", tags=["Market"])

market_service = MarketService()


@router.get("/{symbol}")
def get_market_price(symbol: str):
    return market_service.get_price(symbol.upper())