"""Hedge-review wiring: evidence-based scenario + out-of-sample hedge back-check reach the red team and the answer."""
import asyncio

from copilot_common.models import FinalAnswer, QueryRequest
from orchestrator import graph as G
from orchestrator.nodes.quant_agent import analog_events, shock_evidence
from orchestrator.nodes.red_team import code_red_team, hedge_check_reasons
from orchestrator.nodes.synthesizer import scenario_range_lines, validation_lines
from orchestrator.nodes.validator import validate

ANALOGS = {"id": "ev_analogs_001", "tool": "analogs", "value": {"analogs": [
    {"event_id": "a", "event_date": "2019-05-03"}, {"event_id": "b", "event_date": "2020-05-20"}, {"event_id": "c"}]}}


def _hv(n, good, med=3.58, rng=(1.28, 8.0)):
    return {"id": "ev_hedge_validation_001", "tool": "hedge_validation", "degraded": False, "confidence": 0.5,
            "value": {"n": n, "n_improved": good, "median_dd_reduction_pp": med, "range_dd_reduction_pp": list(rng)}}


def test_shock_evidence_and_analog_events():
    ev = [ANALOGS, {"id": "ev_news_001", "tool": "news", "value": {"items": []}},
          {"id": "ev_macro_001", "tool": "macro", "value": {"brent_chg_5d": 0.05}, "summary": "LLM text is not sent"}]
    se = shock_evidence(ev)
    assert [e["tool"] for e in se] == ["analogs", "macro"] and "summary" not in se[1]
    assert analog_events(ev) == [{"event_id": "a", "event_date": "2019-05-03"}, {"event_id": "b", "event_date": "2020-05-20"}]
    assert analog_events([]) == []


def test_red_team_flags_weak_or_failed_hedge_checks():
    assert hedge_check_reasons([_hv(6, 6)]) == []
    assert "only back-checked on 3" in hedge_check_reasons([_hv(3, 3)])[0]
    assert "did not reduce drawdown" in hedge_check_reasons([_hv(6, 2)])[0]
    rep = code_red_team({"evidence": [_hv(6, 3)]})
    assert rep.reasons[0].endswith("[ev_hedge_validation_001]")


def test_answer_lines_are_cited_and_pass_the_numbers_ledger():
    sc = {"id": "ev_scenario_002", "tool": "scenario", "degraded": False, "confidence": 0.4,
          "value": {"pnl_inr": -4683.62, "cases": {"p10": {"pnl_inr": -15605.79}, "median": {"pnl_inr": -4683.62},
                                                   "p90": {"pnl_inr": 5393.76}}}}
    hv = _hv(6, 6)
    lines = scenario_range_lines([sc]) + validation_lines(hv)
    assert "₹-4,684" in lines[0] and "₹-15,606" in lines[0] and "[ev_scenario_002]" in lines[0]
    assert "6 of them" in lines[1] and "+3.6 pp" in lines[1] and "sanity check" not in lines[1]
    assert "sanity check" in validation_lines(_hv(3, 3))[0]
    rep, _ = validate("\n".join(lines), [sc, hv])
    assert rep.action == "pass", rep


def test_mock_hedge_run_includes_back_check():
    final = asyncio.run(G.run_graph(QueryRequest(query="Cyclone near Odisha: how should I hedge my portfolio this week?")))
    fa = FinalAnswer.model_validate(final)
    tools = [e.tool for e in fa.evidence]
    assert "hedge_validation" in tools
    assert sum(t == "scenario" for t in tools) >= 1
    assert fa.validator.action == "pass"
