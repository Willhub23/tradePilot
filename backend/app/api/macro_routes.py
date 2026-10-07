from fastapi import APIRouter

from app.models.macro_models import MacroEventRisk
from app.services.macro_service import MacroService

router = APIRouter(
    prefix="/api/macro",
    tags=["Macro"],
)

macro_service = MacroService()


@router.get(
    "/risk",
    response_model=MacroEventRisk,
)
def get_macro_risk():
    return macro_service.get_event_risk()
