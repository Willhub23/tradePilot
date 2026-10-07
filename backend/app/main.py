from fastapi import FastAPI
from app.api.market_routes import router as market_router

app = FastAPI(title="TradePilot API")

app.include_router(market_router)

@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "service": "tradepilot-api"
    }