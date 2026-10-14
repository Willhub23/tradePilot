import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler

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
        if not cycle.market_session_service.is_market_open():
            logger.info(
                "Scheduled trading cycle skipped: market closed (%s)",
                symbol,
            )
            return

        logger.info("Managing open positions for %s", symbol)
        closed_positions = position_manager.manage_open_positions()

        for position in closed_positions:
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
    interval = int(os.getenv("TRADING_INTERVAL_MINUTES", "15"))

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
