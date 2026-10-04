"""The DEMO-ODISHA storm is synthetic: it exists only with DEMO_MODE=1 and is always flagged."""
import json

from services.ingestion.sources import storms as storm_src

DEMO = {"name": "DEMO-ODISHA", "basin": "NIO", "category": "VSCS", "lat": 18.9, "lon": 87.2, "max_wind_kt": 75,
        "track_toward": ["OD-Puri"], "valid_from": "2026-10-01T00:00:00Z", "synthetic": True}
REAL = {"name": "DANA", "basin": "NIO", "category": "SCS", "lat": 19.5, "lon": 88.0, "max_wind_kt": 55,
        "track_toward": [], "valid_from": "2026-10-01T00:00:00Z"}


def _write(tmp_path):
    (tmp_path / "storms.json").write_text(json.dumps([DEMO, REAL]), encoding="utf-8")


def test_synthetic_storm_hidden_without_demo_mode(env, tmp_path):
    _write(tmp_path)
    env(DATA_DIR=tmp_path, DEMO_MODE="0")
    assert [s["name"] for s in storm_src.manual_storms()] == ["DANA"]


def test_synthetic_storm_flagged_in_demo_mode(env, tmp_path):
    _write(tmp_path)
    env(DATA_DIR=tmp_path, DEMO_MODE="1")
    storms = storm_src.manual_storms()
    assert {s["name"] for s in storms} == {"DEMO-ODISHA", "DANA"}
    hit = storm_src.attach_storm({"region_id": "OD-Puri", "lat": 19.8, "lon": 85.8}, [DEMO])
    assert hit and hit["synthetic"] is True
    real = storm_src.attach_storm({"region_id": "OD-Puri", "lat": 19.8, "lon": 85.8}, [REAL])
    assert real and "synthetic" not in real


def test_repo_storm_file_marks_demo_storm_synthetic():
    from copilot_common.settings import project_root
    for name in ("storms.json", "storms.json.example"):
        p = project_root() / "data" / name
        if p.exists():
            for s in json.loads(p.read_text(encoding="utf-8")):
                if str(s.get("name", "")).upper().startswith("DEMO"):
                    assert s.get("synthetic") is True, name
