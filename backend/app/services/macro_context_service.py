from app.models.macro_models import MacroEvent


class MacroContextService:
    def build_context(
        self,
        events: list[MacroEvent],
        now,
    ) -> dict:
        high_impact_events = [event for event in events if event.impact == "high"]

        relevant_events = []

        for event in high_impact_events:
            minutes = (event.event_time - now).total_seconds() / 60

            if -30 <= minutes <= 1440:
                relevant_events.append(event)

        if not relevant_events:
            return self._normal_context()

        nearest = min(
            relevant_events,
            key=lambda event: abs((event.event_time - now).total_seconds()),
        )

        nearest_minutes = (nearest.event_time - now).total_seconds() / 60

        blocking_event = any(
            -30 <= (event.event_time - now).total_seconds() / 60 <= 15
            for event in relevant_events
        )

        if 0 <= nearest_minutes <= 15:
            state = "event_imminent"

        elif 15 < nearest_minutes <= 60:
            state = "pre_event"

        elif -30 <= nearest_minutes < 0:
            state = "post_event"

        else:
            state = "high_impact_day"

        return {
            "state": state,
            "event_name": nearest.name,
            "event_category": nearest.category,
            "speaker": nearest.speaker,
            "impact": nearest.impact,
            "minutes_to_event": (
                round(nearest_minutes, 1) if nearest_minutes >= 0 else None
            ),
            "minutes_since_event": (
                round(abs(nearest_minutes), 1) if nearest_minutes < 0 else None
            ),
            "scheduled": nearest.scheduled,
            "allow_new_trade": not blocking_event,
        }

    def _normal_context(self) -> dict:
        return {
            "state": "normal",
            "event_name": None,
            "event_category": None,
            "speaker": None,
            "impact": "low",
            "minutes_to_event": None,
            "minutes_since_event": None,
            "scheduled": False,
            "allow_new_trade": True,
        }
