"""
tests/test_templates.py
Tests exact string templates for historical events and news per spec §3.3.
"""
from __future__ import annotations
import pytest
from vectordb.embed import event_text, news_text


def test_event_text_exact_format():
    """
    Template per §3.3:
    "{event_type} {region} severity {severity_value} {severity_unit}. {description} Mechanism: {mechanism}"
    """
    sample_event = {
        "event_type": "cyclone",
        "region": "Odisha",
        "severity_value": 5,
        "severity_unit": "IMD class (1-7)",
        "description": "Very severe cyclonic storm tracking toward Odisha coast, landfall expected in 48h, heavy rain over ports and agri districts.",
        "mechanism": "port shutdowns, crop damage, power outages",
    }
    expected = (
        "cyclone Odisha severity 5 IMD class (1-7). "
        "Very severe cyclonic storm tracking toward Odisha coast, landfall expected in 48h, heavy rain over ports and agri districts. "
        "Mechanism: port shutdowns, crop damage, power outages"
    )
    result = event_text(sample_event)
    assert result == expected


def test_news_text_exact_format():
    """
    Template per §3.2 / §8:
    "{title}. {summary}"
    """
    sample_news = {
        "title": "Severe Cyclone Alert Issued for Odisha Coast",
        "summary": "IMD warns of strong winds up to 150 km/h with heavy rain likely in coastal districts.",
    }
    expected = "Severe Cyclone Alert Issued for Odisha Coast. IMD warns of strong winds up to 150 km/h with heavy rain likely in coastal districts."
    result = news_text(sample_news)
    assert result == expected
