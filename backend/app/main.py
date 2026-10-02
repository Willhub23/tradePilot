from fastapi import FastAPI

app = FastAPI(title="TradePilot API")


@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "service": "tradepilot-api"
    }