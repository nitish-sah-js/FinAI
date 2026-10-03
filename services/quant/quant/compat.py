"""Adapter: quant was written against a minimal stand-in of copilot_common whose `mock_or` and `degraded` have different
signatures from the real package (which sentiment and the orchestrator already use). Everything else in quant imports the
real package directly; only these two go through here, so the service logic stays untouched."""
from __future__ import annotations

from typing import Awaitable, Callable

from copilot_common.ids import counter_for
from copilot_common.models import Evidence, ToolResult
from copilot_common.service_base import create_service_app  # noqa: F401  (re-exported for main.py)
from copilot_common.service_base import degraded as _degraded
from copilot_common.service_base import mock_or as _mock_or


def mock_or(endpoint_name: str, real_fn: Callable[..., Awaitable[ToolResult]], service: str = "quant"):
    """Stand-in style: returns a wrapper; MOCK=1 → copilot_common/fixtures/quant/<endpoint>.json (degraded_reason=mock)."""
    async def wrapper(*args, **kwargs):
        return await _mock_or(service, endpoint_name, lambda: real_fn(*args, **kwargs))
    return wrapper


def degraded(run_id: str | None, tool: str, reason: str, value: dict | None = None, source: str = "n/a",
             summary: str | None = None) -> Evidence:
    ev = _degraded(tool, counter_for(run_id).next(tool) if run_id else f"ev_{tool}_001", reason, value=value,
                   source=source, run_id=run_id)
    if summary:
        ev.summary = summary
    return ev
