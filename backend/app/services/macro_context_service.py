from app.models.macro_models import MacroEvent


class MacroContextService:
    def build_context(
        self,
        events: list[MacroEvent],
        now,
    ) -> dict:
        if not events:
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

        high_impact_events = [event for event in events if event.impact == "high"]

        if not high_impact_events:
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

        nearest = min(
            high_impact_events,
            key=lambda event: abs((event.event_time - now).total_seconds()),
        )

        minutes = (nearest.event_time - now).total_seconds() / 60

        if 0 <= minutes <= 15:
            state = "event_imminent"
            allow_new_trade = False

        elif 15 < minutes <= 60:
            state = "pre_event"
            allow_new_trade = True

        elif -30 <= minutes < 0:
            state = "post_event"
            allow_new_trade = False

        else:
            state = "high_impact_day"
            allow_new_trade = True

        return {
            "state": state,
            "event_name": nearest.name,
            "event_category": getattr(nearest, "category", None),
            "speaker": getattr(nearest, "speaker", None),
            "impact": nearest.impact,
            "minutes_to_event": (round(minutes, 1) if minutes >= 0 else None),
            "minutes_since_event": (round(abs(minutes), 1) if minutes < 0 else None),
            "scheduled": getattr(nearest, "scheduled", True),
            "allow_new_trade": allow_new_trade,
        }
