"""explain (P10): replay a stored run's log. Nothing is guessed: the input is the ledger, not memory."""
from __future__ import annotations

from ..budget import budget
from ..events import bus
from ..ledger import ledger
from ..prompt_loader import render
from .common import chat, finish_kwargs


async def load_log(run_id: str) -> tuple[list[dict], list[dict], dict | None]:
    """(compact events, compact evidence, stored final) for a past run."""
    events = []
    for _, msg in await ledger.get_events(run_id):
        if msg.get("type") != "event":
            continue
        e = msg["data"]
        if e["status"] in ("started", "progress"):
            continue
        events.append({k: e.get(k) for k in ("seq", "node", "status", "message", "evidence_ids", "latency_ms", "model")})
    run = await ledger.get_run(run_id)
    final = (run or {}).get("final")
    evidence = [{"id": ev["id"], "tool": ev["tool"], "summary": ev.get("summary"), "degraded": ev.get("degraded"),
                 "source": ev.get("source")} for ev in (final or {}).get("evidence", [])]
    return events, evidence, final


def code_explanation(events: list[dict], evidence: list[dict]) -> str:
    lines = []
    for i, e in enumerate(events, start=1):
        cites = " ".join(f"[{x}]" for x in (e.get("evidence_ids") or [])[:4])
        lines.append(f"{i}. **{e['node']}** {e['status']}: {e.get('message') or ''} {cites}".rstrip())
    return "\n".join(lines) or "No log found for that run."


async def explain_run(state: dict, ref_run_id: str | None) -> dict:
    run_id = state["run_id"]
    target = ref_run_id or await ledger.latest_done_run(exclude=run_id)
    await bus.emit(run_id, "explain", "started", message=f"replaying {target or 'no run'}")
    if not target:
        await bus.emit(run_id, "explain", "degraded", message="no earlier run to explain")
        return {"explanation": {"ref_run_id": None, "explanation_markdown": "There is no earlier run to explain yet.",
                                "steps": []}}
    events, evidence, final = await load_log(target)
    text, status = "", "finished"
    try:
        async with budget("explain") as b:
            res = await chat(state, "explain", b, "explain",
                             [{"role": "user", "content": render("P10", log_json={"events": events, "evidence": evidence})}])
            if res.ok and res.text.strip():
                text = res.text.strip()
    except TimeoutError:
        pass
    if not text:
        text, status = code_explanation(events, evidence), "degraded"
    trigger = ((await ledger.get_run(target)) or {}).get("request", {}).get("alert_id")
    if trigger:                              # 11 §13: say what started the run (written by code, not the model)
        text = f"This run was started from monitor alert `{trigger}`.\n\n{text}"
    await bus.emit(run_id, "explain", status, **finish_kwargs(b, message=f"explained {target}"))
    return {"explanation": {"ref_run_id": target, "explanation_markdown": text, "steps": events,
                            "ref_final": final}}


async def explain(state: dict) -> dict:
    return await explain_run(state, state["request"].get("ref_run_id"))
