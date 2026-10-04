"""Router table, validator, staleness, shocks parsing, prompts, keyword intent."""
import pytest

from copilot_common.models import Intent
from orchestrator.nodes.parse_intent import keyword_intent
from orchestrator.nodes.validator import validate
from orchestrator.prompt_loader import render
from orchestrator.router import parse_shocks, select_agents
from orchestrator.staleness import staleness_factor

PF = {"holdings": [{"ticker": "COALINDIA.NS", "qty": 1, "sector": "Energy"}, {"ticker": "HDFCBANK.NS", "qty": 1, "sector": "Banks"}]}


def I(**kw):
    return Intent(**{"intent": "event_impact", **kw})


@pytest.mark.parametrize("intent, expected", [
    (I(event_type="hurricane", region="Gulf"), {"weather_agent", "analog_agent", "exposure_agent", "sentiment_agent"}),
    (I(event_type="cyclone", region="Odisha"), {"weather_agent", "analog_agent", "exposure_agent", "sentiment_agent", "agri_agent"}),
    (I(event_type="oil"), {"weather_agent", "analog_agent", "exposure_agent", "sentiment_agent", "macro_agent"}),
    (Intent(intent="portfolio_risk"), {"exposure_agent", "sentiment_agent", "macro_agent", "weather_agent"}),
    (Intent(intent="hedge_request"), {"exposure_agent", "macro_agent", "analog_agent"}),
    (Intent(intent="market_summary"), {"sentiment_agent", "macro_agent"}),
    (Intent(intent="market_summary", needs_tools=["weather"]), {"sentiment_agent", "macro_agent", "weather_agent"}),
    (Intent(intent="explain"), set()),
])
def test_router_table(intent, expected):
    assert set(select_agents(intent, PF)) == expected


def test_needs_tools_adds_but_never_removes():
    got = select_agents(Intent(intent="hedge_request", needs_tools=["agri"]), PF)
    assert {"exposure_agent", "macro_agent", "analog_agent", "agri_agent"} == set(got)


def test_staleness_factor():
    assert staleness_factor("weather", 0) == 1.0
    assert staleness_factor("weather", 21_600) == pytest.approx(0.5)
    assert staleness_factor("weather", 10 ** 7) == 0.3            # floor
    assert staleness_factor("analogs", 10 ** 7) == 1.0            # no half-life
    assert staleness_factor("news", None) == 1.0


def EV(id_, tool, value, conf=0.8):
    return {"id": id_, "tool": tool, "value": value, "confidence": conf}


def test_validator_pass():
    rep, md = validate("Brent rose 4.2% [ev_macro_001].", [EV("ev_macro_001", "macro", {"brent_chg_5d": 0.042})])
    assert rep.action == "pass" and rep.numbers_found == 1 and rep.numbers_matched == 1
    assert md == "Brent rose 4.2% [ev_macro_001]."


def test_validator_flags_ambiguous_uncited_and_unsupported():
    evs = [EV("ev_macro_001", "macro", {"brent_chg_5d": 0.042, "usd_inr": 88.4}),
           EV("ev_fx_001", "macro", {"usd_inr": 88.4})]
    rep, md = validate("Brent rose 4.2% [ev_macro_001]. The rupee is at 88.4 today. It may fall about 7%.", evs)
    assert rep.action == "flagged"
    assert "uncited:88.4" in rep.unmatched and "7%" in rep.unmatched      # 88.4 is in two items → ambiguous
    assert "⚠️88.4" in md and "⚠️7%" in md


def test_validator_resolves_ambiguity_by_sentence_context():
    evs = [EV("ev_risk_001", "risk", {"var_inr": 48200, "cvar_inr": 63900}),
           EV("ev_hedge_001", "hedge", {"pre_var_inr": 48200, "post_var_inr": 29100})]
    rep, md = validate("VaR is ₹48,200 and expected shortfall ₹63,900.", evs)   # 48,200 is in both items
    assert rep.action == "pass" and rep.numbers_matched == 2
    assert md == "VaR is ₹48,200 and expected shortfall ₹63,900 [ev_risk_001]."


def test_validator_accepts_signal_confidence():
    evs = [EV("ev_analogs_001", "analogs", {"analogs": []}, conf=0.6)]
    sig = [{"agent": "analog_agent", "confidence": 0.23, "evidence_ids": ["ev_analogs_001"]}]
    rep, _ = validate("Analog confidence is only 0.23 [ev_analogs_001].", evs, signals=sig)
    assert rep.action == "pass"


def test_validator_auto_cites_unambiguous_figures():
    evs = [EV("ev_risk_001", "risk", {"var_inr": 48200}), EV("ev_hedge_001", "hedge", {"post_var_inr": 29100})]
    rep, md = validate("Hedging lowers VaR from ₹48,200 to ₹29,100.", evs)
    assert rep.action == "pass" and rep.numbers_matched == 2
    assert md == "Hedging lowers VaR from ₹48,200 [ev_risk_001] to ₹29,100 [ev_hedge_001]."
    assert rep.auto_cited == ["₹48,200→ev_risk_001", "₹29,100→ev_hedge_001"]


def test_validator_strips_more_than_two_fakes():
    evs = [EV("ev_risk_001", "risk", {"var_inr": 48200})]
    draft = ("### Bottom line\nVaR is ₹48,200 [ev_risk_001].\n"
             "Crude could jump 13% [ev_risk_001]. Gold may add 9.5% [ev_risk_001]. Banks lose 2.7% [ev_risk_001].")
    rep, md = validate(draft, evs)
    assert rep.action == "stripped" and len(rep.unmatched) == 3
    assert "₹48,200" in md and "13%" not in md and "unsupported figures removed" in md


def test_validator_ignores_years_dates_horizon_list_markers_and_query_numbers():
    evs = [EV("ev_analogs_001", "analogs", {"analogs": [{"similarity": 0.87}] * 4})]
    draft = ("1. Fani (2019) on 2019-05-03 is the closest match with similarity 0.87 [ev_analogs_001].\n"
             "2. Over 5 days, 4 analogs matched [ev_analogs_001]. You asked about crude +10%.")
    rep, _ = validate(draft, evs, horizon_days=5, query="what if crude +10%")
    assert rep.action == "pass", rep


def test_validator_handles_crore_lakh_bps():
    evs = [EV("ev_x_001", "risk", {"var_inr": 25_000_000, "cost": 150_000, "repo_change_bps": 40})]
    rep, _ = validate("VaR ₹2.5 cr, cost ₹1.5 lakh, repo +40 bps [ev_x_001].", evs)
    assert rep.action == "pass" and rep.numbers_found == 3


def test_parse_shocks():
    assert parse_shocks("what if crude +10% and repo up 25bps") == {"crude": 10.0, "repo_bps": 25.0}
    assert parse_shocks("if the rupee falls 2% and nifty -3%") == {"usd_inr": -2.0, "nifty": -3.0}
    assert parse_shocks("cyclone heading to odisha") == {}


def test_prompts_render_and_keep_json_braces():
    p = render("P1", query="Will ITC fall?")
    assert "Will ITC fall?" in p and '{"intent":' in p
    p4 = render("P4", evidence_json=[{"id": "ev_weather_001"}], exposed_holdings=[])
    assert p4.startswith("You are a component") and '[{"id":"ev_weather_001"}]' in p4
    with pytest.raises(KeyError):
        render("P8", query="x")


@pytest.mark.parametrize("q, intent, et, horizon", [
    ("Hurricane in the Gulf heading to Louisiana — impact on my energy stocks this week?", "event_impact", "hurricane", 5),
    ("Agar monsoon kamzor raha toh FMCG ka kya hoga next month?", "event_impact", "monsoon", 20),
    ("What's my 1-day VaR?", "portfolio_risk", None, 1),
    ("Hedge my Reliance and ONGC position against an oil crash", "hedge_request", "oil", 5),
    ("Explain your last recommendation", "explain", None, 5),
    ("Summarise the market today", "market_summary", None, 1),
])
def test_keyword_intent_fallback(q, intent, et, horizon):
    got = keyword_intent(q)
    assert (got.intent, got.event_type, got.horizon_days) == (intent, et, horizon)


def test_keyword_intent_maps_tickers():
    assert set(keyword_intent("Hedge my Reliance and ONGC position").tickers) == {"RELIANCE.NS", "ONGC.NS"}


# ---- fixes found by the prompt evals (05) ----
def test_intent_llm_schema_bounds_lists_for_the_grammar():
    from orchestrator.nodes.parse_intent import IntentLLM
    props = IntentLLM.model_json_schema()["properties"]
    assert props["tickers"]["maxItems"] == 5 and props["needs_tools"]["maxItems"] == 8
    assert set(props["needs_tools"]["items"]["enum"]) >= {"risk", "agri", "macro"}


def test_normalise_drops_null_tickers_and_caps():
    from orchestrator.nodes.parse_intent import normalise
    got = normalise(Intent(intent="portfolio_risk", tickers=["null", "itc.ns", "ITC.NS", "None", "^NSEI"]), "x")
    assert got.tickers == ["ITC.NS", "^NSEI"] and type(got) is Intent


def test_clean_reasons_brackets_bare_ids_and_drops_invented():
    from orchestrator.nodes.red_team import clean_reasons
    ids = {"ev_analogs_001", "ev_weather_001"}
    got = clean_reasons(["weak analog: confidence 0.6 in ev_analogs_001", "ev_weather_001",
                         "No evidence for ITC [ev_missing_001].", "Stale weather [ev_weather_001]."], ids)
    assert got == ["weak analog: confidence 0.6 in [ev_analogs_001]", "No evidence for ITC.", "Stale weather [ev_weather_001]."]


def test_dedupe_repeated_bullets():
    from orchestrator.nodes.synthesizer import dedupe_lines
    assert dedupe_lines("### A\n- x\n- y\n- x\n- x\n\n### B\n- z") == "### A\n- x\n- y\n\n### B\n- z"


def test_validator_accepts_numbers_inside_instrument_names():
    evs = [EV("ev_hedge_001", "hedge", {"proposals": [{"instrument": "NIFTY OCT 24500 PE buy", "quantity": 2}]})]
    rep, _ = validate("Buy 2 lots NIFTY OCT 24500 PE [ev_hedge_001].", evs)
    assert rep.action == "pass" and rep.numbers_found == 2


def test_validator_ignores_compound_labels_like_10_year():
    evs = [EV("ev_macro_001", "macro", {"us10y": 4.12})]
    rep, _ = validate("The US 10-year yield is 4.12% [ev_macro_001], near its 52-week high.", evs)
    assert rep.action == "pass" and rep.numbers_found == 1


def test_validator_rejects_fixture_evidence_outside_mock():
    from orchestrator.nodes.validator import validate
    ev = [{"id": "ev_risk_001", "tool": "risk", "value": {"var_inr": 48200}, "confidence": 0.8, "fixture": True}]
    draft = "5-day VaR is ₹48,200 [ev_risk_001]."
    ok, _ = validate(draft, ev, allow_fixture=True)
    assert ok.action == "pass" and not ok.rejected_evidence
    rep, _ = validate(draft, ev, allow_fixture=False)
    assert rep.rejected_evidence == ["ev_risk_001"] and rep.action != "pass" and rep.numbers_matched == 0


def test_unavailable_result_has_no_numbers():
    from orchestrator.tools_client import unavailable_result
    tr = unavailable_result("risk", "quant", "the quant service could not be reached")
    ev = tr.evidence[0]
    assert ev.value == {"status": "unavailable", "reason": "the quant service could not be reached"}
    assert ev.confidence == 0.0 and ev.degraded_reason == "unavailable" and not ev.fixture
    assert ev.summary == "No risk data: the quant service could not be reached"


def test_agri_signal_is_low_weight_with_dated_caveat():
    from orchestrator.nodes.synthesizer import AGRI_CAVEAT, code_answer, synth_inputs
    state = {"request": {"query": "monsoon deficit in Vidarbha"}, "portfolio": {"holdings": []}, "evidence": [],
             "signals": [{"agent": "agri_agent", "signal": "bearish", "summary": "Yavatmal stressed.", "evidence_ids": []},
                         {"agent": "weather_agent", "signal": "bearish", "summary": "Rain 40% below normal.", "evidence_ids": []}]}
    sig = {s["agent"]: s for s in synth_inputs(state)["signals_json"]}
    assert sig["agri_agent"]["weight"] == "low" and "2026-10-04" in sig["agri_agent"]["caveat"]
    md = code_answer(state)
    bottom = md.split("### Impact")[0]
    assert "Yavatmal stressed" not in bottom and "Rain 40% below normal" in bottom     # agri never leads
    assert AGRI_CAVEAT in md
