"""data/sector_sensitivity.json: a value labelled 'measured' must be backed by a stable estimate with the same sign."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_measured_values_are_backed_by_stable_estimates():
    sens = json.loads((ROOT / "data" / "sector_sensitivity.json").read_text(encoding="utf-8"))
    prov, meas = sens.get("_provenance", {}), sens.get("_measured", {})
    n_measured = 0
    for sector, cols in prov.items():
        for factor, label in cols.items():
            if label != "measured":
                assert label.startswith("judgment")
                continue
            n_measured += 1
            m = meas[sector][factor]
            assert m["stable"] and abs(m["t"]) >= 2
            assert (m["half1"] > 0) == (m["half2"] > 0) == (m["beta_ols"] > 0)
            sign = sens["_signs"][factor].get(sector, sens["_signs"][factor]["default"])
            assert (sign > 0) == (m["beta_shrunk"] > 0), f"{sector}/{factor}: stored sign disagrees with the estimate"
            expect = round(min(1.0, abs(m["beta_shrunk"]) / sens["_scale"][factor]), 2)
            assert sens[sector][factor] == expect
    assert "JUDGMENT" in sens["_meta"]                      # anything not measured is still called judgment
    assert n_measured == sum(v == "measured" for c in prov.values() for v in c.values())
