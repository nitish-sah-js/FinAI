import pandas as pd
from services.ingestion.sources.macro import build_macro
from services.ingestion.timeutil import parse_as_of


def test_build_macro_as_of():
    idx = pd.bdate_range("2026-09-01", "2026-10-02")
    s = lambda base: pd.Series([base * (1 + 0.001 * i) for i in range(len(idx))], index=idx)
    policy = [{"date": "2025-06-06", "repo_rate": 5.5}, {"date": "2025-12-05", "repo_rate": 5.25}]
    cpi = pd.DataFrame({"date": pd.to_datetime(["2026-07-31", "2026-08-31"]), "cpi_yoy": [3.2, 3.1]})
    v, dates, warns, miss = build_macro(s(88), s(70), s(4), policy, cpi)
    assert v["repo_rate"] == 5.25 and v["repo_change_bps"] == -25 and miss == 0
    v2, *_ = build_macro(s(88), s(70), s(4), policy, cpi, parse_as_of("2026-09-10"))
    assert dates["usd_inr"] == "2026-10-02"
    assert v2["cpi_yoy"] == 3.1
    v3, d3, *_ = build_macro(s(88), s(70), s(4), policy, cpi, parse_as_of("2025-01-01"))
    assert v3["repo_rate"] is None and v3["usd_inr"] is None
