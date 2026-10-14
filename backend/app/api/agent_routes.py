from fastapi import APIRouter, HTTPException, Query

from app.providers.twelve_data_provider import MarketDataRateLimitError
from app.models.agent_models import AgentDecisionHistory, AgentResult
from app.services.trading_cycle_service import TradingCycleService


def create_agent_router(trading_cycle_service: TradingCycleService) -> APIRouter:
    router = APIRouter(prefix="/api/agent", tags=["Agent"])

    @router.get("/decision/{symbol}", response_model=AgentResult)
    def get_agent_decision(symbol: str):
        try:
            return trading_cycle_service.run(symbol)

        except MarketDataRateLimitError:
            raise HTTPException(
                status_code=503, detail="Market data provider rate limit reached"
            )

    @router.get("/history", response_model=list[AgentDecisionHistory])
    def get_history(limit: int = Query(default=50, ge=1)):
        return trading_cycle_service.agent_service.persistence.get_recent_decisions(
            limit
        )

    @router.get("/history/{decision_id}", response_model=AgentDecisionHistory)
    def get_history_decision(decision_id: str):
        decision = trading_cycle_service.agent_service.persistence.get_decision(
            decision_id
        )
        if decision is None:
            raise HTTPException(status_code=404, detail="Agent decision not found")
        return decision

    return router
