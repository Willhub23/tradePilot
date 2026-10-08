from datetime import datetime

from pydantic import BaseModel


class MacroEvent(BaseModel):
    name: str
    country: str
    impact: str
    event_time: datetime


class MacroEventRisk(BaseModel):
    event_risk: str
    next_event: str | None
    minutes_until_event: int | None
    event_time: datetime | None
    allow_new_trade: bool
