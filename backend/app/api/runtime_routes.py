import hmac
import os
from fastapi import APIRouter, Header, HTTPException, Request

router = APIRouter(prefix="/api/runtime", tags=["Runtime"])


def runtime(request, authorization):
    token = os.getenv("RUNTIME_CONTROL_TOKEN", "")
    if not token or not hmac.compare_digest(authorization or "", f"Bearer {token}"):
        raise HTTPException(401, "Runtime control authentication required")
    service = getattr(request.app.state, "ctrader_runtime", None)
    if service is None:
        raise HTTPException(409, "cTrader runtime is not active")
    return service


@router.get("/status")
def status(request: Request, authorization: str | None = Header(default=None)):
    return runtime(request, authorization).status()


@router.post("/pause")
def pause(request: Request, authorization: str | None = Header(default=None)):
    return runtime(request, authorization).pause()


@router.post("/resume")
def resume(request: Request, authorization: str | None = Header(default=None)):
    try:
        return runtime(request, authorization).resume()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
