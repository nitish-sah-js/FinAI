from pydantic import BaseModel, Field
from datetime import datetime
from typing import Any, Literal
import uuid

class Evidence(BaseModel):
    id: str
    run_id: str | None = None
    tool: str
    value: dict[str, Any]
    summary: str | None = None
    source: str
    source_url: str | None = None
    as_of: datetime
    timestamp: datetime
    freshness_s: int | None = None
    confidence: float | None = Field(None, ge=0, le=1)
    degraded: bool = False
    degraded_reason: str | None = None
    latency_ms: int | None = None
    model_version: str | None = None
    staleness_factor: float | None = None

class Alert(BaseModel):
    alert_id: str
    tier: int
    kind: str
    tickers: list[str]
    headline: str
    reason: str
    impact_score: float
    confidence: float
    evidence_ids: list[str]
    created_at: datetime
    cooldown_key: str
    deeplink: str
    acknowledged: bool = False

class Health(BaseModel):
    status: str
    deps: dict[str, str] = {}

class Settings(BaseModel):
    ingest_url: str = "http://localhost:8201"
    agri_url: str = "http://localhost:8103"
    orch_url: str = "http://localhost:8000"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    smtp_user: str | None = None
    smtp_app_password: str | None = None
    alert_email_to: str | None = None
    mock: int = 0

settings = Settings()

def new_alert_id():
    return f"al_{datetime.utcnow().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"

def create_service_app(name, deps_check=None):
    from fastapi import FastAPI
    app = FastAPI(title=name)
    @app.get("/health")
    def health():
        return Health(status="ok")
    return app

class copilot_llm:
    @staticmethod
    async def chat(role, messages, max_tokens=60):
        # Stub for LLM
        return "Stub headline from LLM"
