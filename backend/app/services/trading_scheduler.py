import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler

from app.models.broker_models import BrokerPositionManagementResult

from app.services.position_manager_service import PositionManagerService
from app.services.trading_cycle_service import TradingCycleService

logger = logging.getLogger(__name__)
SCHEDULED_SYMBOLS = ("XAUUSD",)


def run_scheduled_cycle(
    cycle: TradingCycleService,
    position_manager: PositionManagerService,
    symbol: str,
) -> None:
    try:
        runtime = getattr(cycle, "runtime", None)
        # For DEMO, keep positions observable even outside entry sessions.
        if getattr(runtime, "started", False) is True:
            for report in position_manager.manage_open_positions():
                logger.info("Broker management: %s", report.model_dump_json())
        if not cycle.market_session_service.is_market_open():
            logger.info(
                "Scheduled trading cycle skipped: market closed (%s)",
                symbol,
            )
            return

        logger.info("Managing open positions for %s", symbol)
        closed_positions = [] if getattr(runtime, "started", False) is True else position_manager.manage_open_positions()

        for position in closed_positions:
            if isinstance(position, BrokerPositionManagementResult):
                logger.info("Broker management: %s", position.model_dump_json())
                continue
            logger.info(
                "Closed position %s for %s: %s",
                position.id,
                position.symbol,
                position.close_reason,
            )

        logger.info("Scheduled trading cycle started for %s", symbol)

        cycle.run(symbol)

        logger.info("Scheduled trading cycle completed for %s", symbol)

    except Exception:
        logger.exception("Scheduled trading cycle failed for %s", symbol)


def create_trading_scheduler(
    cycle: TradingCycleService,
    position_manager: PositionManagerService,
) -> BackgroundScheduler:
    interval = int(os.getenv("TRADING_INTERVAL_MINUTES", "5"))

    if interval < 1:
        raise ValueError("TRADING_INTERVAL_MINUTES must be a positive integer")

    scheduler = BackgroundScheduler(timezone="UTC")

    for symbol in SCHEDULED_SYMBOLS:
        scheduler.add_job(
            run_scheduled_cycle,
            trigger="interval",
            minutes=interval,
            args=(cycle, position_manager, symbol),
            id=f"trading-cycle-{symbol}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    return scheduler
