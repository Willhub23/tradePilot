from datetime import datetime, timedelta, timezone

from app.models.macro_models import MacroEvent
from app.services.macro_service import MacroService


class FakeMacroProvider:
    def __init__(self, events):
        self.events = events

    def get_upcoming_events(self):
        return self.events


def test_high_impact_event_inside_block_window_blocks_trade():
    event = MacroEvent(
        name="US CPI",
        country="US",
        impact="high",
        event_time=datetime.now(timezone.utc) + timedelta(minutes=20),
    )

    provider = FakeMacroProvider([event])
    service = MacroService(provider=provider)

    result = service.get_event_risk()

    assert result.allow_new_trade is False
    assert result.event_risk == "high"


def test_high_impact_event_outside_block_window_allows_trade():
    event = MacroEvent(
        name="US CPI",
        country="US",
        impact="high",
        event_time=datetime.now(timezone.utc) + timedelta(hours=3),
    )

    provider = FakeMacroProvider([event])
    service = MacroService(provider=provider)

    result = service.get_event_risk()

    assert result.allow_new_trade is True
    assert result.event_risk == "high"
