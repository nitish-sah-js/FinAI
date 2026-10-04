"""Argument validation of scripts/download_mod13q1.py. No network, no credentials."""
from __future__ import annotations

import argparse
import importlib.util
from datetime import date
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "download_mod13q1", Path(__file__).resolve().parents[1] / "scripts" / "download_mod13q1.py")
dl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dl)


def test_parse_date():
    assert dl.parse_date("2026-01-01") == date(2026, 1, 1)
    with pytest.raises(argparse.ArgumentTypeError, match="YYYY-MM-DD"):
        dl.parse_date("01/01/2026")


@pytest.mark.parametrize("start, end, msg", [
    (date(2026, 5, 1), date(2026, 4, 1), "before --start"),
    (date(1999, 1, 1), date(2000, 1, 1), "MOD13Q1 starts"),
    (date(2030, 1, 1), date(2030, 2, 1), "in the future"),
])
def test_bad_dates(start, end, msg):
    with pytest.raises(ValueError, match=msg):
        dl.validate_dates(start, end, today=date(2026, 10, 4))


def test_good_dates():
    dl.validate_dates(date(2026, 1, 1), date(2026, 10, 4), today=date(2026, 10, 4))


@pytest.mark.parametrize("bbox, msg", [
    ((82, 17, 74, 22), "west .* less than east"),
    ((74, 22, 82, 17), "south .* less than north"),
    ((-181, 0, 10, 10), "outside -180..180"),
    ((0, -91, 10, 10), "outside -90..90"),
])
def test_bad_bbox(bbox, msg):
    with pytest.raises(ValueError, match=msg):
        dl.validate_bbox(*bbox)


def test_defaults_and_cli_errors_exit_2():
    a = dl.parse_args([])
    assert a.start == date(2026, 1, 1) and a.bbox == [68.0, 6.0, 98.0, 36.0] and not a.dry_run
    for argv in (["--bbox", "82", "17", "74", "22"], ["--start", "2026-13-01"], ["--start", "2026-05-01", "--end", "2026-04-01"]):
        with pytest.raises(SystemExit) as e:
            dl.parse_args(argv)
        assert e.value.code == 2


def test_sanitize_hides_secrets(monkeypatch):
    monkeypatch.setenv("EARTHDATA_PASSWORD", "s3cret-Value!")
    out = dl.sanitize("401 for user x with s3cret-Value! and token=abc123")
    assert "s3cret-Value!" not in out and "abc123" not in out


def test_granule_size_from_attribute_method_or_umm():
    class Attr(dict):
        size = 12.5

    class Method(dict):
        def size(self):
            return 3.0

    umm = {"umm": {"DataGranule": {"ArchiveAndDistributionInformation": [{"Size": 2, "SizeUnit": "GB"}]}}}
    assert dl.granule_size_mb(Attr()) == 12.5 and dl.granule_size_mb(Method()) == 3.0
    assert dl.granule_size_mb(dict(umm)) == 2048.0
    assert dl.total_size_mb([Attr(), Method()]) == 15.5 and dl.total_size_mb([Attr(), {}]) is None
