from datetime import datetime
from typing import Literal

from pydantic import ConfigDict

from pydantic import BaseModel

from app.models.market_models import MultiTimeframeAnalysis
from app.models.macro_models import MacroEventRisk
from app.models.trade_models import TradeProposal
from app.models.paper_trading_models import PaperPosition


class AgentDecision(BaseModel):
    action: Literal["long", "short", "hold"]

    trade_style: Literal[
        "intraday",
        "swing",
        "none",
    ]

    confidence: float

    thesis: str

    invalidation_reason: str | None = None

    target_reason: str | None = None

    expected_horizon: str | None = None


class AgentMarketContext(BaseModel):
    symbol: str
    market_analysis: MultiTimeframeAnalysis
    macro_risk: MacroEventRisk


class PersistedAgentDecision(BaseModel):
    decision_id: str
    decision: AgentDecision


class AgentDecisionHistory(AgentDecision):
    model_config = ConfigDict(from_attributes=True)

    id: str
    timestamp: datetime
    symbol: str
    market_context: dict
    macro_context: dict


class AgentResult(BaseModel):
    decision_id: str
    decision: AgentDecision
    proposal: TradeProposal | None
    approved: bool
    position: PaperPosition | None
    execution_block_reason: str | None = None
