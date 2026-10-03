import pandas as pd
from datetime import date
from services.ingestion.features.weather_features import *
from services.ingestion.features.macro_features import pct_change_n, bps_change
from services.ingestion import store
from services.ingestion.timeutil import parse_as_of


def test_rain_anomaly():
    assert rain_anomaly_pct(284.0, 100.0) == 184.0
    assert rain_anomaly_pct(5.0, 0.0) == 500.0


def test_heat_index():
    assert heat_index_c(20, 50) == 19.4
    assert 48 <= heat_index_c(35, 70) <= 54


def test_soil_z():
    assert abs(soil_moisture_z(0.41, 0.30, 0.05) - 2.2) < 1e-9


def test_saffir():
    assert [saffir_simpson(x) for x in (50, 64, 83, 96, 113, 137)] == [0, 1, 2, 3, 4, 5]


def test_confidence():
    assert weather_confidence(True, 5, False) == 0.75
    assert weather_confidence(True, 5, True) == 0.6
    assert weather_confidence(False, 10, False) == 0.1


def test_alerts():
    assert weather_alerts(184.0, 88.0, 31.4, 33.1, {"basin": "NIO"}, 10) == ["cyclone", "heavy_rain"]
    assert "rain_deficit" in weather_alerts(-50, 1, 30, 30, None, 7)
    assert weather_alerts(0, 1, 30, 30, None, 12) == []
    assert "heatwave" in weather_alerts(0, 1, 46, 30, None, 5)


def test_window_clim():
    s = pd.Series(2.0, index=pd.date_range("2001-01-01", "2003-12-31"))
    assert window_clim_mm(s, date(2020, 6, 1), 5, years=range(2001, 2004)) == 10.0


def test_macro():
    s = pd.Series([100, 101, 102, 103, 104, 105.0])
    assert abs(pct_change_n(s, 5) - 0.05) < 1e-9
    assert pct_change_n(s, 10) is None
    assert bps_change(5.25, 5.50) == -25


def _df(a, b):
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1},
                        index=pd.date_range(a, b))


def test_as_of_never_leaks(tmp_path, env):
    env(DATA_DIR=tmp_path)
    store.save_prices("ITC.NS", "1d", _df("2021-08-20", "2021-09-05"))
    out = store.load_prices("ITC.NS", "1d", parse_as_of("2021-08-25"))
    assert out.index.max() == pd.Timestamp("2021-08-25")
    items = [{"published_at": "2021-08-25T10:00:00Z"}, {"published_at": "2021-08-26T00:00:01Z"}]
    assert len(store.filter_as_of(items, parse_as_of("2021-08-25"))) == 1


def test_incremental_append(tmp_path, env):
    env(DATA_DIR=tmp_path)
    store.save_prices("X", "1d", _df("2021-01-01", "2021-01-05"))
    store.save_prices("X", "1d", _df("2021-01-04", "2021-01-08"))
    assert len(store.load_prices("X", "1d")) == 8


def test_price_window_follows_as_of():
    from services.ingestion.handlers import price_window
    s, e = price_window("1y", None, None, parse_as_of("2021-08-25"))
    assert e == pd.Timestamp("2021-08-25") and s == pd.Timestamp("2020-08-24")
    s, e = price_window("1y", "2021-01-01", "2021-12-31", parse_as_of("2021-08-25"))
    assert s == pd.Timestamp("2021-01-01") and e == pd.Timestamp("2021-08-25")   # as_of caps end
    assert price_window("1y", None, None, None) == (None, None)                  # live: plain period


def test_prices_fresh_requires_window_coverage(tmp_path, env):
    env(DATA_DIR=tmp_path)
    store.save_prices("X", "1d", _df("2025-01-01", "2025-12-31"))
    assert not store.prices_fresh("X", "1d", 43200, 366, pd.Timestamp("2021-01-01"), pd.Timestamp("2021-08-25"))
    assert store.prices_fresh("X", "1d", 43200, 366, pd.Timestamp("2025-02-01"), pd.Timestamp("2025-06-30"))


def test_gdelt_cooldown_fails_fast(monkeypatch):
    import asyncio, time, pytest
    from services.ingestion.sources import gdelt
    monkeypatch.setattr(gdelt, "_cool_until", time.monotonic() + 30)
    t = time.monotonic()
    with pytest.raises(gdelt.GdeltRateLimited):
        asyncio.run(gdelt.search("cyclone", {}))
    assert time.monotonic() - t < 1   # no network call, no waiting
