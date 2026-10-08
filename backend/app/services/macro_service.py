from datetime import datetime, timezone

from app.models.macro_models import MacroEvent, MacroEventRisk
from app.providers.macro_base import MacroProvider


class MacroService:
    HIGH_IMPACT_BLOCK_WINDOW_MINUTES = 30

    def __init__(self, provider: MacroProvider):
        self.provider = provider

    def get_event_risk(self) -> MacroEventRisk:
        now = datetime.now(timezone.utc)

        events = self.provider.get_upcoming_events()

        upcoming_events = [event for event in events if event.event_time > now]

        if not upcoming_events:
            return MacroEventRisk(
                event_risk="none",
                next_event=None,
                minutes_until_event=None,
                event_time=None,
                allow_new_trade=True,
            )

        next_event = min(
            upcoming_events,
            key=lambda event: event.event_time,
        )

        minutes_until_event = int((next_event.event_time - now).total_seconds() / 60)

        allow_new_trade = self._allow_new_trade(
            event=next_event,
            minutes_until_event=minutes_until_event,
        )

        return MacroEventRisk(
            event_risk=next_event.impact,
            next_event=next_event.name,
            minutes_until_event=minutes_until_event,
            event_time=next_event.event_time,
            allow_new_trade=allow_new_trade,
        )

    def _allow_new_trade(
        self,
        event: MacroEvent,
        minutes_until_event: int,
    ) -> bool:
        if (
            event.impact == "high"
            and minutes_until_event <= self.HIGH_IMPACT_BLOCK_WINDOW_MINUTES
        ):
            return False

        return True
