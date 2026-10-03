"""join: the fan-in after the parallel agents."""
from __future__ import annotations

from ..events import bus


async def join(state: dict) -> dict:
    run_id = state["run_id"]
    fanout_ms = bus.since_mark_ms(run_id, "fanout_start") or 0
    evidence = state.get("evidence", [])
    ids = list(dict.fromkeys(e["id"] for e in evidence))
    degraded = sorted({s["agent"] for s in state.get("signals", []) if s.get("degraded")})
    msg = f"{len(state.get('signals', []))} agents · {len(ids)} evidence items · fan-out {fanout_ms} ms"
    if degraded:
        msg += f" · degraded: {', '.join(degraded)}"
    await bus.emit(run_id, "join", "finished", latency_ms=fanout_ms, evidence_ids=ids, message=msg)
    return {"latency": {"fanout": fanout_ms}}
