from datetime import timedelta
from unittest.mock import Mock

import pytest

from app.models.macro_models import MacroEvent
from app.services.macro_context_service import MacroContextService
from app.services.macro_service import MacroService
from app.services.evidence_priority_service import EvidencePriorityService
from tests.macro_fixtures import NOW


@pytest.mark.parametrize(
    "minutes,state,allowed",
    [
        (-30, "post_event", False),
        (-1, "post_event", False),
        (0, "event_imminent", False),
        (15, "event_imminent", False),
        (16, "pre_event", True),
        (60, "pre_event", True),
        (61, "high_impact_day", True),
    ],
)
def test_typed_event_context_preserves_timing_rules(minutes, state, allowed):
    event = MacroEvent(
        name="Central bank speech",
        country="US",
        impact="high",
        event_time=NOW + timedelta(minutes=minutes),
        category="speech",
        speaker="Chair",
        scheduled=False,
    )
    context = MacroContextService().build_context([event], NOW)
    assert context == {
        "state": state,
        "event_name": event.name,
        "event_category": "speech",
        "speaker": "Chair",
        "impact": "high",
        "scheduled": False,
        "minutes_to_event": float(minutes) if minutes >= 0 else None,
        "minutes_since_event": float(-minutes) if minutes < 0 else None,
        "allow_new_trade": allowed,
    }
    assert EvidencePriorityService().prioritize(context)["allow_new_trade"] is allowed


def test_event_older_than_post_event_window_produces_normal_context():
    event = MacroEvent(
        name="Central bank speech",
        country="US",
        impact="high",
        event_time=NOW - timedelta(minutes=31),
        category="speech",
        speaker="Chair",
        scheduled=False,
    )

    context = MacroContextService().build_context([event], NOW)

    assert context == {
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


@pytest.mark.parametrize("impact", [None, "low", "medium"])
def test_no_high_impact_events_produces_normal_context(impact):
    events = (
        []
        if impact is None
        else [
            MacroEvent(
                name="Event",
                country="US",
                impact=impact,
                event_time=NOW,
            )
        ]
    )
    assert MacroContextService().build_context(events, NOW) == {
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


def test_nearest_high_impact_event_and_optional_defaults():
    events = [
        MacroEvent(
            name=str(minutes),
            country="US",
            impact=impact,
            event_time=NOW + timedelta(minutes=minutes),
        )
        for minutes, impact in [(1, "low"), (60, "high"), (-5, "high")]
    ]
    context = MacroContextService().build_context(events, NOW)
    assert context["event_name"] == "-5"
    assert context["state"] == "post_event"
    assert context["event_category"] is None
    assert context["speaker"] is None
    assert context["scheduled"] is True


def test_macro_service_delegates_without_filtering_events():
    events = [
        MacroEvent(
            name="Past event",
            country="US",
            impact="high",
            event_time=NOW - timedelta(minutes=5),
        )
    ]
    provider = Mock()
    provider.get_upcoming_events.return_value = events
    assert MacroService(provider).get_events() is events
    provider.get_upcoming_events.assert_called_once_with()


def test_post_event_block_is_not_hidden_by_upcoming_event():
    events = [
        MacroEvent(
            name="Past event",
            country="US",
            impact="high",
            event_time=NOW - timedelta(minutes=20),
        ),
        MacroEvent(
            name="Upcoming event",
            country="US",
            impact="high",
            event_time=NOW + timedelta(minutes=16),
        ),
    ]

    context = MacroContextService().build_context(events, NOW)

    assert context["allow_new_trade"] is False
