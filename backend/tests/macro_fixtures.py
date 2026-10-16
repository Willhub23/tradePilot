from datetime import datetime, timedelta, timezone

from app.models.macro_models import MacroEvent
from app.services.evidence_priority_service import EvidencePriorityService
from app.services.macro_context_service import MacroContextService

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def macro_events(now=NOW):
    return [MacroEvent(
        name="US Initial Jobless Claims", country="US", impact="high",
        event_time=now + timedelta(minutes=120), category="employment",
    )]


def agent_macro_fields():
    context = MacroContextService().build_context(macro_events(), NOW)
    return {
        "macro_context": context,
        "evidence_priority": EvidencePriorityService().prioritize(context),
    }
