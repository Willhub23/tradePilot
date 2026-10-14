from typing import Literal

from pydantic import BaseModel


class TradeProposal(BaseModel):
    agent_decision_id: str | None = None
    symbol: str
    direction: Literal["long", "short"]
    trade_style: Literal["intraday", "swing"]
    confidence: float
    thesis: str
