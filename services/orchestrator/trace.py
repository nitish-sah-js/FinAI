"""Per-run usage trace (ledger table `trace`): which node called which service or model, on which laptop, how
long it took and how it went. Read by GET /runs/{run_id}/trace and scripts/verify_usage.py.

status for a service call: ok | degraded | empty (answered, no data) | unavailable | fixture.
status for an LLM call:    ok (the role's own model) | fallback (another model answered) | cached | mock | failed.
Writing a trace row never breaks a run.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from copilot_common.settings import get_settings

from .ledger import ledger

log = logging.getLogger("orchestrator.trace")
SERVICE_URL = {"quant": "QUANT_URL", "sentiment": "SENTIMENT_URL", "agri": "AGRI_URL", "vectordb": "VECTOR_URL",
               "ingestion": "INGEST_URL", "monitor": "MONITOR_URL"}
SERVICE_LAPTOP = {"quant": "L2", "sentiment": "L2", "agri": "L2", "vectordb": "L2", "ingestion": "L3", "monitor": "L3"}


def service_host(service: str) -> str:
    """'L2 (192.168.1.12:8101)' so the trace shows the laptop AND where the call really went."""
    url = getattr(get_settings(), SERVICE_URL.get(service, ""), "") or ""
    return f"{SERVICE_LAPTOP.get(service, '?')} ({url.split('//')[-1].rstrip('/')})" if url else SERVICE_LAPTOP.get(service, "?")


def tool_status(tr) -> str:
    evs = tr.evidence
    if any(getattr(e, "fixture", False) for e in evs):
        return "fixture"
    if not evs:                                   # the service answered but had nothing (e.g. no headlines to score)
        return "empty"
    if any(isinstance(e.value, dict) and e.value.get("status") == "unavailable" for e in evs):
        return "unavailable"
    return "degraded" if any(e.degraded for e in evs) else "ok"


async def record(run_id: str | None, node: str | None, service: str, host: str, model: str | None,
                 latency_ms: int | None, status: str, detail: str = "") -> None:
    if not run_id:
        return
    try:
        await ledger.add_trace({"run_id": run_id, "ts": datetime.now(timezone.utc).isoformat(), "node": node or "?",
                                "service": service, "host": host, "model": model, "latency_ms": latency_ms,
                                "status": status, "detail": detail[:300]})
    except Exception as e:  # noqa: BLE001
        log.warning("trace write failed: %s", e)
