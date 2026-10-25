"""
HTTP API for the compliance agent.

  GET  /healthz   liveness probe, no auth
  POST /analyze   {"transaction_id": "..."} -> verdict, signals, investigation, alert

/analyze requires an X-API-Key header matching one of the comma-separated keys in
the API_KEYS environment variable. If API_KEYS is empty or unset, every request is
rejected (fail closed). Run locally with:

    uvicorn api:app --env-file .env
"""

import math
import os
import secrets
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from agent import TransactionNotFound, build_graph, default_mcp
from ai_gateway import RateLimitExceeded


class AnalyzeRequest(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=64)


class AnalyzeResponse(BaseModel):
    is_anomalous: bool
    reasons: list[str]
    signals: dict
    # The next three are null for transactions that were not flagged (Claude is never called).
    investigation: Optional[str] = None
    alert_message: Optional[str] = None
    gateway_status: Optional[str] = None  # ok | cache_hit | no_api_key | throttled


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    valid = [key.strip() for key in os.environ.get("API_KEYS", "").split(",") if key.strip()]
    if x_api_key and any(secrets.compare_digest(x_api_key.encode(), key.encode()) for key in valid):
        return
    raise HTTPException(status_code=401, detail="invalid or missing API key", headers={"WWW-Authenticate": "ApiKey"})


def create_app(graph=None) -> FastAPI:
    """
    graph: a compiled agent graph. Defaults to the real one. THROTTLE_FALLBACK=false makes a
    throttled Claude call return 429 + Retry-After instead of the deterministic fallback alert.
    """
    if graph is None:
        fallback = os.environ.get("THROTTLE_FALLBACK", "true").lower() != "false"
        graph = build_graph(default_mcp(), fallback_on_throttle=fallback)

    app = FastAPI(title="Banking Compliance Agent")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.post("/analyze", response_model=AnalyzeResponse, dependencies=[Depends(require_api_key)])
    def analyze(body: AnalyzeRequest) -> AnalyzeResponse:
        try:
            result = graph.invoke({"transaction_id": body.transaction_id})
        except TransactionNotFound:
            raise HTTPException(status_code=404, detail=f"transaction {body.transaction_id} not found")
        except RateLimitExceeded as exc:
            raise HTTPException(
                status_code=429, detail=str(exc), headers={"Retry-After": str(max(1, math.ceil(exc.retry_after)))}
            )
        return AnalyzeResponse(
            is_anomalous=result["is_anomalous"],
            reasons=result["reasons"],
            signals=result["signals"],
            investigation=result.get("investigation"),
            alert_message=result.get("alert_message"),
            gateway_status=result.get("gateway_status"),
        )

    return app


app = create_app()
