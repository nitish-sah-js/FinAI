"""§3.3 / §3.2 text templates (ported) and the embedder wiring."""
from __future__ import annotations

from vectordb.embed import MODEL_NAME, Embedder, event_text, model_state, news_text, resolve_device


def test_event_text_exact_format():
    e = {"event_type": "cyclone", "region": "Odisha", "severity_value": 5, "severity_unit": "IMD class (1-7)",
         "description": "Very severe cyclonic storm tracking toward Odisha coast, landfall expected in 48h, heavy rain over ports and agri districts.",
         "mechanism": "port shutdowns, crop damage, power outages"}
    assert event_text(e) == (
        "cyclone Odisha severity 5 IMD class (1-7). Very severe cyclonic storm tracking toward Odisha coast, "
        "landfall expected in 48h, heavy rain over ports and agri districts. Mechanism: port shutdowns, crop damage, "
        "power outages")
    assert event_text({**e, "severity_value": 5.0}).startswith("cyclone Odisha severity 5 IMD")


def test_news_text_exact_format():
    n = {"title": "Severe Cyclone Alert Issued for Odisha Coast",
         "summary": "IMD warns of strong winds up to 150 km/h with heavy rain likely in coastal districts."}
    assert news_text(n) == ("Severe Cyclone Alert Issued for Odisha Coast. IMD warns of strong winds up to 150 km/h "
                            "with heavy rain likely in coastal districts.")


def test_real_model_loads_not_hashing(embedder):
    """Regression: the stand-in read settings.embed_device inside a try, so the real settings made the model fail
    silently and the hashing embedder took over."""
    assert model_state()["status"] == "ok"
    assert embedder.name == MODEL_NAME and not embedder.is_fallback
    v = embedder.encode(["cyclone Odisha", "hurricane Gulf"])
    assert v.shape == (2, 384)
    assert abs(float((v[0] ** 2).sum()) - 1.0) < 1e-4


def test_device_resolution():
    assert resolve_device(None) == "cpu"
    assert resolve_device("cpu") == "cpu"
    assert resolve_device("auto") in ("cpu", "cuda")
    assert resolve_device("cuda") in ("cpu", "cuda")
