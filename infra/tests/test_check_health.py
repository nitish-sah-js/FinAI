"""check_health.py: health parsing, port-conflict detection, model checks, exit codes."""
import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_health", ROOT / "infra" / "check_health.py")
ch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ch)


def client(routes: dict):
    def handler(req: httpx.Request):
        key = f"{req.url.host}:{req.url.port}{req.url.path}"
        if key not in routes:
            raise httpx.ConnectError("refused", request=req)
        status, body = routes[key]
        return httpx.Response(status, json=body)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def test_service_ok_and_degraded_deps():
    routes = {"h:8101/health": (200, {"service": "quant", "status": "ok", "mock": False, "models": [], "deps": {}}),
              "h:8102/health": (200, {"service": "sentiment", "status": "degraded", "mock": True,
                                      "models": ["finbert"], "deps": {"gpu": "missing"}})}

    async def go():
        async with client(routes) as c:
            return (await ch.check_service(c, "quant", "http://h:8101"),
                    await ch.check_service(c, "sentiment", "http://h:8102"),
                    await ch.check_service(c, "agri", "http://h:8103"))
    q, s, a = run(go())
    assert q["status"] == "ok" and q["latency_ms"] is not None
    assert s["status"] == "degraded" and s["detail"] == "gpu:missing" and s["mock"] is True
    assert a["status"] == "down" and a["detail"] == "ConnectError"


def test_port_taken_by_another_app_is_down():
    routes = {"h:8000/health": (200, {"status": "ok", "service": "scraper"})}

    async def go():
        async with client(routes) as c:
            return await ch.check_service(c, "orchestrator", "http://h:8000")
    row = run(go())
    assert row["status"] == "down" and "scraper" in row["detail"]


def test_ollama_missing_models_is_degraded():
    routes = {"h:11434/api/tags": (200, {"models": [{"name": "qwen3:4b-instruct"}, {"name": "phi4-mini:latest"}]})}

    async def go():
        async with client(routes) as c:
            return (await ch.check_ollama(c, "ollama@L1", "http://h:11434", ["qwen3:4b-instruct"]),
                    await ch.check_ollama(c, "ollama@L3", "http://h:11434", ["qwen3:1.7b", "phi4-mini"]))
    l1, l3 = run(go())
    assert l1["status"] == "ok"
    assert l3["status"] == "degraded" and l3["missing"] == ["qwen3:1.7b"]       # phi4-mini matches phi4-mini:latest


def test_cli_json_and_exit_codes(tmp_path):
    """Every target on a closed port → down; exit 1 by default, exit 0 when nothing down is required."""
    env = tmp_path / "dead.env"
    lines = [f"{k}=http://127.0.0.1:{p}" for k, p in
             [("ORCH_URL", 1), ("QUANT_URL", 2), ("SENTIMENT_URL", 3), ("AGRI_URL", 4), ("VECTOR_URL", 5),
              ("INGEST_URL", 6), ("MONITOR_URL", 7), ("OLLAMA_L1", 9), ("OLLAMA_L2", 9), ("OLLAMA_L3", 9)]]
    env.write_text("\n".join(lines + ["WEAVIATE_HOST=192.0.2.1"]) + "\n")
    cmd = [sys.executable, str(ROOT / "infra" / "check_health.py"), "--env", str(env), "--json", "--timeout", "0.5"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    out = json.loads(p.stdout)
    assert p.returncode == 1 and out["ok"] is False
    assert len(out["targets"]) == 11 and all(t["status"] == "down" for t in out["targets"])
    p2 = subprocess.run(cmd + ["--require", "nothing"], capture_output=True, text=True, timeout=60)
    assert p2.returncode == 0 and json.loads(p2.stdout)["down_required"] == []
