import os

from dotenv import load_dotenv
from fastapi import APIRouter

from app.models.market_models import MarketQuote
from app.providers.twelve_data_provider import TwelveDataProvider
from app.services.market_service import MarketService

load_dotenv()

router = APIRouter(prefix="/api/market", tags=["Market"])

api_key = os.getenv("TWELVE_DATA_API_KEY")

if not api_key:
    raise RuntimeError("TWELVE_DATA_API_KEY is not configured")

market_service = MarketService(
    provider=TwelveDataProvider(api_key)
)

@router.get("/{symbol}", response_model=MarketQuote)
def get_market_price(symbol: str):
    return market_service.get_price(symbol.upper())