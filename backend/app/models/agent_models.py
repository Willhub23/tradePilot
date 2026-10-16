from datetime import datetime
from typing import Literal

from pydantic import ConfigDict

from pydantic import BaseModel, Field

from app.models.market_models import MultiTimeframeAnalysis
from app.models.trade_models import TradeProposal
from app.models.paper_trading_models import PaperPosition


class AgentDecision(BaseModel):
    action: Literal["long", "short", "hold"]

    trade_style: Literal[
        "intraday",
        "swing",
        "none",
    ]

    confidence: float = Field(ge=0, le=100, allow_inf_nan=False)

    thesis: str

    invalidation_reason: str | None = None

    target_reason: str | None = None

    expected_horizon: str | None = None


class AgentMarketContext(BaseModel):
    symbol: str
    market_analysis: MultiTimeframeAnalysis

    macro_context: dict
    evidence_priority: dict


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
