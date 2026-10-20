from contextlib import asynccontextmanager
import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

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


_serving = False


def configure_runtime_logging():
    logger = logging.getLogger("app")
    logger.setLevel(logging.INFO)
    path = Path(os.getenv("TRADING_LOG_FILE", "data/runtime.log")).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not any(getattr(h, "baseFilename", None) == str(path) for h in logger.handlers):
        handler = RotatingFileHandler(path, maxBytes=10_000_000, backupCount=5)
        handler.setFormatter(logging.Formatter("%(asctime)sZ %(levelname)s %(name)s %(message)s"))
        import time
        handler.formatter.converter = time.gmtime
        logger.addHandler(handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _serving
    if _serving:
        raise RuntimeError("A scheduler is already running in this process")
    _serving = True
    scheduler = None
    runtime = None
    try:
        mode = os.getenv("TRADING_BROKER", "paper")
        if mode not in ("paper", "ctrader_demo"):
            raise ValueError("TRADING_BROKER must be paper or ctrader_demo")
        observing = trading_cycle_service.strategy_selection.observation_enabled
        if observing and mode != "ctrader_demo":
            raise ValueError("Quick Trade observation requires ctrader_demo")
        if mode == "ctrader_demo":
            if not os.getenv("RUNTIME_CONTROL_TOKEN"):
                raise ValueError("RUNTIME_CONTROL_TOKEN is required")
            configure_runtime_logging()
            from app.integrations.ctrader.client import CTraderClient
            from app.providers.ctrader_broker_provider import CTraderBrokerProvider
            from app.services.broker_execution_persistence_service import BrokerExecutionPersistenceService
            from app.services.ctrader_runtime_service import CTraderRuntimeService
            from scripts.migrate_database import verify_database
            from app.database import engine
            await run_in_threadpool(verify_database, engine, require_strategy=True if observing else None)
            client = CTraderClient()
            persistence = BrokerExecutionPersistenceService()
            provider = CTraderBrokerProvider(client, persistence)
            runtime = CTraderRuntimeService(client, provider, persistence, observation_enabled=observing)
            trading_cycle_service.broker_provider = provider
            trading_cycle_service.execution_persistence = persistence
            trading_cycle_service.runtime = runtime
            position_manager_service.broker_provider = provider
            app.state.ctrader_runtime = runtime
            await run_in_threadpool(runtime.start)
        scheduler = create_trading_scheduler(trading_cycle_service, position_manager_service)
        scheduler.start()
        app.state.trading_scheduler = scheduler
        yield
    finally:
        try:
            if runtime is not None:
                runtime.stopping.set()
                runtime.ready.clear()
            if scheduler is not None and scheduler.running:
                await run_in_threadpool(scheduler.shutdown, wait=True)
        finally:
            try:
                if runtime is not None:
                    await run_in_threadpool(runtime.stop)
            finally:
                trading_cycle_service.broker_provider = broker_provider
                trading_cycle_service.execution_persistence = None
                trading_cycle_service.runtime = None
                position_manager_service.broker_provider = broker_provider
                app.state.ctrader_runtime = None
                _serving = False


app = FastAPI(title="TradePilot API", lifespan=lifespan)

app.include_router(market_router)
app.include_router(macro_router)

agent_router = create_agent_router(trading_cycle_service)

app.include_router(agent_router)

paper_router = create_paper_router(
    paper_trading_service=paper_trading_service,
    market_service=market_service,
    position_manager_service=PositionManagerService(broker_provider, market_service),
)

app.include_router(paper_router)


@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "tradepilot-api"}

from app.api.runtime_routes import router as runtime_router
app.include_router(runtime_router)

from app.api.journal_routes import create_journal_router
app.include_router(create_journal_router())
