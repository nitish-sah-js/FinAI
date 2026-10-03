"""Agri region ids must be districts the agri model covers (services/agri); unmodelled regions → None, never a wrong district."""
import asyncio

import pytest

from copilot_common.models import QueryRequest
from orchestrator import graph as G
from orchestrator.regions import resolve

MODEL_REGIONS = {"MH-Yavatmal", "MH-Latur", "MP-Indore", "MP-Ujjain", "GJ-Rajkot", "PB-Ludhiana", "KA-Kalaburagi",
                 "RJ-Jodhpur"}


@pytest.mark.parametrize("text, weather, agri", [
    ("Cyclone near Odisha", "OD-Puri", None), ("rain in Chennai", "TN-Chennai", None),
    ("Kolkata floods", "WB-Kolkata", None), ("drought in Rajasthan", "RJ-Jaipur", "RJ-Jodhpur"),
    ("Latur soybean crop", "MH-Latur", "MH-Latur"), ("Yavatmal cotton", "MH-Yavatmal", "MH-Yavatmal"),
    ("monsoon deficit", "IN-All", "MH-Yavatmal"), ("Punjab wheat", "PB-Ludhiana", "PB-Ludhiana"),
])
def test_resolve(text, weather, agri):
    assert resolve(None, text) == (weather, agri)


def test_every_agri_id_is_modelled():
    from orchestrator.regions import DEFAULT_AGRI, REGION_MAP
    assert {a for _, _, a in REGION_MAP if a} | {DEFAULT_AGRI} <= MODEL_REGIONS


def test_unmodelled_region_agri_agent_says_so_without_calling_the_tool():
    final = asyncio.run(G.run_graph(QueryRequest(query="Cyclone near Odisha: crop damage to paddy, what about my portfolio?")))
    sig = next((s for s in final.get("signals", []) if s["agent"] == "agri_agent"), None)
    assert all(e["tool"] != "agri" for e in final["evidence"])
    if sig:                                               # agri_agent was routed (cyclone) → honest n/a
        assert sig["signal"] == "n/a" and "No crop-stress model" in sig["summary"]
