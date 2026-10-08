from typing import Protocol

from app.models.macro_models import MacroEvent


class MacroProvider(Protocol):
    def get_upcoming_events(self) -> list[MacroEvent]: ...
