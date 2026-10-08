from datetime import datetime, timezone

from app.models.macro_models import MacroEvent, MacroEventRisk


class MacroService:
    HIGH_IMPACT_BLOCK_WINDOW_MINUTES = 30

    def __init__(self):
        self.events = [
            # hardcoded test events
        ]

    def get_event_risk(self) -> MacroEventRisk:
        now = datetime.now(timezone.utc)

        upcoming_events = [event for event in self.events if event.event_time > now]

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

    def __init__(self):
        self.events = [
            MacroEvent(
                name="US CPI",
                country="US",
                impact="high",
                event_time=datetime(
                    2026,
                    10,
                    8,
                    12,
                    30,
                    tzinfo=timezone.utc,
                ),
            ),
            MacroEvent(
                name="Federal Reserve Speech",
                country="US",
                impact="high",
                event_time=datetime(
                    2026,
                    10,
                    8,
                    18,
                    0,
                    tzinfo=timezone.utc,
                ),
            ),
            MacroEvent(
                name="US Initial Jobless Claims",
                country="US",
                impact="medium",
                event_time=datetime(
                    2026,
                    10,
                    9,
                    12,
                    30,
                    tzinfo=timezone.utc,
                ),
            ),
        ]

    def get_event_risk(self) -> MacroEventRisk:
        now = datetime.now(timezone.utc)

        upcoming_events = [event for event in self.events if event.event_time > now]

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

        time_difference = next_event.event_time - now

        minutes_until_event = int(time_difference.total_seconds() / 60)

        allow_new_trade = True

        if next_event.impact == "high" and minutes_until_event <= 30:
            allow_new_trade = False

        return MacroEventRisk(
            event_risk=next_event.impact,
            next_event=next_event.name,
            minutes_until_event=minutes_until_event,
            event_time=next_event.event_time,
            allow_new_trade=allow_new_trade,
        )
