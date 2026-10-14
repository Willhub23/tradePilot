from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from app.api.market_routes import (
    router as market_router,
    analysis_service,
    market_service,
)

from app.api.macro_routes import router as macro_router, macro_service
from app.services.position_manager_service import PositionManagerService
from app.services.paper_trading_service import PaperTradingService
from app.api.agent_routes import create_agent_router
from app.api.paper_routes import create_paper_router
from app.services.trading_cycle_service import TradingCycleService
from app.providers.local_paper_broker_provider import LocalPaperBrokerProvider
from app.services.trading_scheduler import create_trading_scheduler

paper_trading_service = PaperTradingService()

broker_provider = LocalPaperBrokerProvider(
    paper_trading_service=paper_trading_service,
)

trading_cycle_service = TradingCycleService(
    analysis_service=analysis_service,
    macro_service=macro_service,
    broker_provider=broker_provider,
)

position_manager_service = PositionManagerService(
    broker_provider=broker_provider,
    market_service=market_service,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create/start only in the serving process, never at module import.
    scheduler = create_trading_scheduler(
        trading_cycle_service,
        position_manager_service,
    )
    scheduler.start()
    app.state.trading_scheduler = scheduler
    try:
        yield
    finally:
        await run_in_threadpool(scheduler.shutdown, wait=True)


app = FastAPI(title="TradePilot API", lifespan=lifespan)

app.include_router(market_router)
app.include_router(macro_router)

agent_router = create_agent_router(trading_cycle_service)

app.include_router(agent_router)

paper_router = create_paper_router(
    paper_trading_service=paper_trading_service,
    market_service=market_service,
    position_manager_service=position_manager_service,
)

app.include_router(paper_router)


@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "tradepilot-api"}
