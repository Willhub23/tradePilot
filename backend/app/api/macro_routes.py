from fastapi import APIRouter

from app.models.macro_models import MacroEventRisk
from app.services.macro_service import MacroService
from app.providers.mock_macro_provider import MockMacroProvider

router = APIRouter(
    prefix="/api/macro",
    tags=["Macro"],
)

macro_service = MacroService(provider=MockMacroProvider())


@router.get(
    "/risk",
    response_model=MacroEventRisk,
)
def get_macro_risk():
    return macro_service.get_event_risk()
