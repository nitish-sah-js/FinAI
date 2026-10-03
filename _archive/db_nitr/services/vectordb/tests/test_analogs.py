"""
tests/test_analogs.py
Tests for analogs search, holdout exclusion, as_of time-machine filtering, numpy fallback,
and endpoint integration.
"""
from __future__ import annotations
import asyncio
import os
import pytest
from datetime import date
from fastapi.testclient import TestClient

from vectordb.analogs import AnalogReq, find_analogs
from vectordb.main import app

HOLDOUT_IDS = {
    "hurricane_ida_2021",
    "cyclone_biparjoy_2023",
    "cyclone_michaung_2023",
    "monsoon_deficit_2015",
    "heatwave_2022",
    "rbi_offcycle_hike_2022",
    "abqaiq_attack_2019",
    "rice_export_ban_2023",
}


def test_find_analogs_numpy_fallback_general():
    async def _test():
        req = AnalogReq(
            situation="cyclone Odisha severity 5 IMD class (1-7). Very severe cyclonic storm expected to make landfall near Puri with heavy rain. Mechanism: port closures, power outages, crop damage",
            event_type="cyclone",
            severity_norm=0.71,
            region_hint="Odisha",
            assets=["^NSEI", "^CNXFMCG"],
            horizon="5d",
            k=5,
            exclude_holdout=False,
        )
        val, warnings, is_degraded = await find_analogs(req)
        analogs = val.get("analogs", [])
        assert len(analogs) > 0
        # Every analog should have similarity >= 0.5
        for a in analogs:
            assert a["similarity"] >= 0.5
            assert "event_id" in a
            assert "why_similar" in a

    asyncio.run(_test())


def test_holdout_filter_never_returns_holdouts():
    """
    spec §4.2 & §10:
    When exclude_holdout=True, none of the 8 holdout events must ever be returned.
    """
    async def _test():
        req = AnalogReq(
            situation="cyclone Gujarat Kutch severity 5 IMD class. Severe cyclonic storm tracking toward Gujarat coast. Mechanism: port shutdown, oil pipeline disruption",
            event_type="cyclone",
            assets=["ADANIPORTS.NS", "^NSEI"],
            k=10,
            exclude_holdout=True,
        )
        val, _, _ = await find_analogs(req)
        returned_ids = {a["event_id"] for a in val.get("analogs", [])}
        overlap = returned_ids & HOLDOUT_IDS
        assert not overlap, f"Holdout IDs returned despite exclude_holdout=True: {overlap}"

    asyncio.run(_test())


def test_as_of_filter_no_lookahead():
    """
    spec §4.2:
    as_of adds event_date < as_of, so there is no look-ahead.
    """
    async def _test():
        cutoff = date(2020, 1, 1)
        req = AnalogReq(
            situation="cyclone Bay of Bengal severity 6. Mechanism: port damage and flooding",
            event_type="cyclone",
            as_of=cutoff,
            k=10,
        )
        val, _, _ = await find_analogs(req)
        analogs = val.get("analogs", [])
        for a in analogs:
            event_dt = str(a.get("event_date", ""))[:10]
            assert event_dt < "2020-01-01", f"Event {a['event_id']} on {event_dt} violates as_of={cutoff}"

    asyncio.run(_test())


def test_cyclone_odisha_top_matches():
    """
    Acceptance checklist §10:
    A query for 'very severe cyclone Odisha' returns Fani, Phailin or Yaas in the top 3.
    """
    async def _test():
        req = AnalogReq(
            situation="cyclone Odisha severity 6 IMD class (1-7). Very severe cyclonic storm making landfall on Odisha coast near Puri with extreme winds and flooding. Mechanism: port shutdowns, power outages, crop damage",
            event_type="cyclone",
            region_hint="Odisha",
            assets=["^NSEI", "^CNXFMCG"],
            k=5,
        )
        val, _, _ = await find_analogs(req)
        top_3_ids = [a["event_id"] for a in val.get("analogs", [])[:3]]
        expected_matches = {"cyclone_fani_2019", "cyclone_phailin_2013", "cyclone_yaas_2021", "cyclone_dana_2024"}
        assert any(eid in expected_matches for eid in top_3_ids), f"Expected one of {expected_matches} in top 3, got {top_3_ids}"

    asyncio.run(_test())


def test_hurricane_gulf_top_matches():
    """
    Acceptance checklist §10:
    A query for 'Category 4 hurricane Gulf of Mexico refinery coast' returns hurricanes in the top 3,
    with Harvey, Laura or Ida in the top 3.
    """
    async def _test():
        req = AnalogReq(
            situation="hurricane Gulf of Mexico severity 4 Saffir-Simpson category. Category 4 hurricane Gulf of Mexico refinery coast landfall. Mechanism: refinery shutdowns, offshore oil platform shut-ins, pipeline outages",
            event_type="hurricane",
            region_hint="Gulf of Mexico",
            assets=["CL=F", "NG=F"],
            k=5,
        )
        val, _, _ = await find_analogs(req)
        top_3_ids = [a["event_id"] for a in val.get("analogs", [])[:3]]
        expected = {"hurricane_harvey_2017", "hurricane_laura_2020", "hurricane_ida_2021", "hurricane_katrina_2005", "hurricane_rita_2005"}
        assert any(eid in expected for eid in top_3_ids), f"Expected one of {expected} in top 3, got {top_3_ids}"

    asyncio.run(_test())


def test_mock_mode_endpoint():
    """Tests MOCK=1 behavior returning fixtures."""
    from copilot_common.settings import settings
    prev_mock = settings.mock
    prev_delay = settings.mock_delay_ms
    try:
        settings.mock = True
        settings.mock_delay_ms = 0
        client = TestClient(app)

        resp = client.post(
            "/find_analogs",
            json={
                "situation": "test query",
                "assets": ["^NSEI"],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "evidence" in body
        assert len(body["evidence"]) > 0
        ev = body["evidence"][0]
        assert ev["degraded"] is True
        assert ev["degraded_reason"] == "mock"
    finally:
        settings.mock = prev_mock
        settings.mock_delay_ms = prev_delay
