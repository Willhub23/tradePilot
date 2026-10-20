"""Opt-in persistent read-only connection. No autonomous runtime or scheduler."""
import os
import logging
from contextlib import asynccontextmanager
from starlette.concurrency import run_in_threadpool


def build_account_service():
    from app.integrations.ctrader.client import CTraderClient
    from app.services.broker_runtime_store import BrokerRuntimeStore
    from app.services.account_dashboard_service import AccountDashboardService, ReadOnlyEvents
    client=CTraderClient(execution_event_handler=ReadOnlyEvents(),read_only=True)
    return AccountDashboardService(client,BrokerRuntimeStore(client.account_id))


@asynccontextmanager
async def account_lifespan(app):
    enabled=os.getenv('ACCOUNT_DASHBOARD_ENABLED','false').lower()
    if enabled not in ('true','false'): raise ValueError('ACCOUNT_DASHBOARD_ENABLED must be true or false')
    app.state.account_dashboard=None
    app.state.account_error=None
    instance=None
    try:
        if enabled=='true':
            try:
                instance=build_account_service()
                await run_in_threadpool(instance.start)
                app.state.account_dashboard=instance
            except Exception as exc:
                logging.getLogger(__name__).warning('Read-only account connection unavailable (%s)',type(exc).__name__)
                app.state.account_error='Read-only DEMO connection unavailable; verify credentials and exclusive runtime ownership'
        yield
    finally:
        if instance is not None and instance.started:
            await run_in_threadpool(instance.stop)
        app.state.account_dashboard=None
