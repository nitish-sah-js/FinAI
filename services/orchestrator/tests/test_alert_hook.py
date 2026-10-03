"""11 §13: a run started from a monitor alert deep link stores alert_id, and "explain yourself" says so."""
import asyncio

from copilot_common.models import QueryRequest
from orchestrator import graph as G
from orchestrator.ledger import ledger


def test_alert_id_is_stored_and_explained():
    async def go():
        first = await G.run_graph(QueryRequest(query="How does the Odisha cyclone affect my portfolio over 5 days?",
                                               alert_id="al_20261003_3fa21c"))
        run = await ledger.get_run(first["run_id"])
        ex = await G.run_graph(QueryRequest(query="Explain your last recommendation", ref_run_id=first["run_id"]))
        return run, ex
    run, ex = asyncio.run(go())
    assert run["request"]["alert_id"] == "al_20261003_3fa21c"
    assert "monitor alert `al_20261003_3fa21c`" in ex["answer_markdown"]
