from datetime import datetime, timezone

from app.models.macro_models import MacroEvent


class MockMacroProvider:
    def get_upcoming_events(self) -> list[MacroEvent]:
        return [
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
