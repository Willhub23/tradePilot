"""Read-only server: journal plus opt-in broker account access; no scheduler or inference."""
from fastapi import FastAPI
from app.api.journal_routes import create_journal_router
from app.api.account_routes import router as account_router
from app.services.account_dashboard_lifecycle import account_lifespan

app = FastAPI(title='TradePilot read-only Decision Journal and Account', lifespan=account_lifespan)
app.include_router(create_journal_router())
app.include_router(account_router)


@app.get('/api/health')
def health():
    return {'status': 'ok', 'service': 'tradepilot-journal'}
