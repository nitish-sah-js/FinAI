"""Download NASA MODIS MOD13Q1 v061 (16-day, 250 m NDVI/EVI) granules with earthaccess.

Credentials come only from the repo-root .env (EARTHDATA_USERNAME + EARTHDATA_PASSWORD, or EARTHDATA_TOKEN). They are
never printed, logged or written anywhere else.

Usage (repo root):
    python scripts/download_mod13q1.py --dry-run                         # count + size only, India, 2026-01-01..today
    python scripts/download_mod13q1.py --start 2026-01-01 --end 2026-10-04 --bbox 74 17 82 22
    python scripts/download_mod13q1.py --out data/mod13q1 --yes          # no confirmation prompt above 200 granules

Exit codes: 0 ok · 2 bad arguments · 3 authentication failed · 4 no granules matched · 5 some downloads failed ·
6 cancelled at the confirmation prompt.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MOD13Q1_FIRST_DAY = date(2000, 2, 18)          # first MOD13Q1 composite (Terra MODIS)
CONFIRM_ABOVE = 200
RETRIES = 3
BACKOFF_S = (2.0, 5.0, 10.0)
SECRET_VARS = ("EARTHDATA_PASSWORD", "EARTHDATA_TOKEN")

EXIT_OK, EXIT_ARGS, EXIT_AUTH, EXIT_NO_GRANULES, EXIT_DOWNLOAD, EXIT_CANCELLED = 0, 2, 3, 4, 5, 6

log = logging.getLogger("download_mod13q1")

AUTH_HELP = """Earthdata authentication failed. Check:
  1. .env exists in the repo root and contains EARTHDATA_USERNAME and EARTHDATA_PASSWORD (or EARTHDATA_TOKEN),
     one per line, with no quotes and no trailing spaces.
  2. Log in at https://urs.earthdata.nasa.gov > My Profile > Applications > Authorized Apps and confirm that
     "LP DAAC Data Pool" and "Earthdata Search" are approved ("Approve More Applications" if they are missing).
  3. A 401 during download usually means a missing app authorization or an expired token,
     not necessarily a wrong password."""


# ---------------------------------------------------------------- safety
def sanitize(text: object) -> str:
    """Remove every credential value (and password=/token= pairs) from text before it is logged."""
    s = str(text)
    for var in SECRET_VARS:
        val = os.environ.get(var)
        if val:
            s = s.replace(val, "***")
    return re.sub(r"(?i)(password|token|authorization)(['\"]?\s*[:=]\s*['\"]?)[^\s'\",&]+", r"\1\2***", s)


# ---------------------------------------------------------------- arguments
def parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date {text!r}: use YYYY-MM-DD") from None


def validate_dates(start: date, end: date, today: date | None = None) -> None:
    """Raise ValueError with a clear message when the period is unusable."""
    today = today or date.today()
    if end < start:
        raise ValueError(f"--end {end} is before --start {start}")
    if end < MOD13Q1_FIRST_DAY:
        raise ValueError(f"MOD13Q1 starts on {MOD13Q1_FIRST_DAY}; --end {end} is earlier")
    if start > today:
        raise ValueError(f"--start {start} is in the future (today is {today})")


def validate_bbox(west: float, south: float, east: float, north: float) -> None:
    """Raise ValueError unless the box is a valid lon/lat rectangle (no antimeridian crossing)."""
    for name, v in (("west", west), ("east", east)):
        if not -180.0 <= v <= 180.0:
            raise ValueError(f"{name} longitude {v} is outside -180..180")
    for name, v in (("south", south), ("north", north)):
        if not -90.0 <= v <= 90.0:
            raise ValueError(f"{name} latitude {v} is outside -90..90")
    if west >= east:
        raise ValueError(f"west ({west}) must be less than east ({east})")
    if south >= north:
        raise ValueError(f"south ({south}) must be less than north ({north})")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Download MODIS MOD13Q1 v061 NDVI granules from NASA Earthdata.",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--start", type=parse_date, default=date(2026, 1, 1), help="first day, YYYY-MM-DD")
    p.add_argument("--end", type=parse_date, default=date.today(), help="last day, YYYY-MM-DD")
    p.add_argument("--bbox", type=float, nargs=4, metavar=("WEST", "SOUTH", "EAST", "NORTH"),
                   default=[68.0, 6.0, 98.0, 36.0], help="bounding box in degrees (default: India)")
    p.add_argument("--out", type=Path, default=Path("data/mod13q1"), help="output directory")
    p.add_argument("--dry-run", action="store_true", help="search and report only, download nothing")
    p.add_argument("--yes", action="store_true", help=f"no confirmation prompt above {CONFIRM_ABOVE} granules")
    return p


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_dates(args.start, args.end)
        validate_bbox(*args.bbox)
    except ValueError as e:
        parser.error(str(e))                     # prints usage + message, exits 2
    return args


# ---------------------------------------------------------------- steps
def load_env() -> None:
    """Load the repo-root .env into the environment. Values are never printed."""
    from dotenv import load_dotenv
    env_path = ROOT / ".env"
    if not env_path.is_file():
        log.warning("no .env found at %s", env_path)
    load_dotenv(env_path)


def authenticate() -> Any:
    """earthaccess login from EARTHDATA_USERNAME/PASSWORD or EARTHDATA_TOKEN. Exits 3 with instructions on failure."""
    import earthaccess
    has_user = bool(os.environ.get("EARTHDATA_USERNAME")) and bool(os.environ.get("EARTHDATA_PASSWORD"))
    if not (has_user or os.environ.get("EARTHDATA_TOKEN")):
        log.error("EARTHDATA_USERNAME/EARTHDATA_PASSWORD (or EARTHDATA_TOKEN) are not set")
        print(AUTH_HELP, file=sys.stderr)
        sys.exit(EXIT_AUTH)
    try:
        auth = earthaccess.login(strategy="environment")
    except Exception as e:  # noqa: BLE001  earthaccess raises several types; message is sanitised
        log.error("login failed: %s", sanitize(e))
        auth = None
    if auth is None or not getattr(auth, "authenticated", False):
        print(AUTH_HELP, file=sys.stderr)
        sys.exit(EXIT_AUTH)
    log.info("authenticated with NASA Earthdata")
    return auth


def search(start: date, end: date, bbox: list[float]) -> list[Any]:
    import earthaccess
    log.info("searching MOD13Q1 v061 %s..%s bbox %s", start, end, bbox)
    return list(earthaccess.search_data(short_name="MOD13Q1", version="061",
                                        temporal=(start.isoformat(), end.isoformat()),
                                        bounding_box=tuple(bbox)))


def granule_filename(granule: Any) -> str | None:
    """File name of the granule's main HDF file (from its first HTTPS data link)."""
    links = [u for u in granule.data_links() if u.lower().endswith(".hdf")] or granule.data_links()
    return links[0].rsplit("/", 1)[-1] if links else None


_UNIT_MB = {"B": 1 / 1024 ** 2, "KB": 1 / 1024, "MB": 1.0, "GB": 1024.0, "TB": 1024.0 ** 2}


def granule_size_mb(granule: Any) -> float | None:
    """Size in MB from the granule's CMR metadata. earthaccess exposes `size` as a float attribute (0.19) or a
    method (older releases); the UMM ArchiveAndDistributionInformation entries are the fallback."""
    size = getattr(granule, "size", None)
    try:
        val = size() if callable(size) else size
        if val:
            return float(val)
    except Exception:  # noqa: BLE001
        pass
    try:
        info = granule["umm"]["DataGranule"]["ArchiveAndDistributionInformation"]
        return sum(float(i["Size"]) * _UNIT_MB[i.get("SizeUnit", "MB").upper()] for i in info) or None
    except (KeyError, TypeError, ValueError):
        return None


def total_size_mb(granules: list[Any]) -> float | None:
    """Sum of granule sizes (MB), or None when any granule's metadata has no size."""
    sizes = [granule_size_mb(g) for g in granules]
    return None if not sizes or any(s is None for s in sizes) else sum(sizes)


def confirm(n: int) -> bool:
    if not sys.stdin.isatty():
        log.error("%d granules and no terminal to confirm: re-run with --yes", n)
        return False
    return input(f"Download {n} granules? [y/N] ").strip().lower() in ("y", "yes")


def download(granules: list[Any], out_dir: Path) -> tuple[list[Path], list[Path], list[str]]:
    """Download what is not already on disk, retrying failures. Returns (downloaded, skipped, failed names)."""
    import earthaccess
    out_dir.mkdir(parents=True, exist_ok=True)
    skipped, todo = [], []
    for g in granules:
        name = granule_filename(g)
        path = out_dir / name if name else None
        if path is not None and path.is_file() and path.stat().st_size > 0:
            skipped.append(path)
        else:
            todo.append((name, g))
    log.info("%d already present, %d to download into %s", len(skipped), len(todo), out_dir)
    downloaded: list[Path] = []
    for attempt in range(1, RETRIES + 1):
        if not todo:
            break
        try:
            earthaccess.download([g for _, g in todo], local_path=out_dir)
        except Exception as e:  # noqa: BLE001
            log.warning("download attempt %d failed: %s", attempt, sanitize(e))
        still = []
        for name, g in todo:
            path = out_dir / name if name else None
            if path is not None and path.is_file() and path.stat().st_size > 0:
                downloaded.append(path)
            else:
                still.append((name, g))
        todo = still
        if todo and attempt < RETRIES:
            wait = BACKOFF_S[attempt - 1]
            log.warning("%d files missing after attempt %d; retrying in %.0f s", len(todo), attempt, wait)
            time.sleep(wait)
    failed = [name or "<granule without data link>" for name, _ in todo]
    if failed:
        log.error("%d files failed after %d attempts (a 401 usually means a missing app authorization)",
                  len(failed), RETRIES)
    return downloaded, skipped, failed


# ---------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    out_dir = args.out if args.out.is_absolute() else ROOT / args.out
    load_env()
    authenticate()
    granules = search(args.start, args.end, args.bbox)
    if not granules:
        log.error("no MOD13Q1 v061 granules matched. Check --start/--end (16-day composites, available from %s, "
                  "a few days' latency), and --bbox (west south east north, degrees).", MOD13Q1_FIRST_DAY)
        return EXIT_NO_GRANULES
    size = total_size_mb(granules)
    log.info("%d granules matched; estimated total size %s", len(granules),
             f"{size / 1024:.2f} GB" if size is not None else "unknown (not in metadata)")
    if args.dry_run:
        log.info("dry run: nothing downloaded")
        return EXIT_OK
    if len(granules) > CONFIRM_ABOVE and not args.yes and not confirm(len(granules)):
        log.info("cancelled")
        return EXIT_CANCELLED
    downloaded, skipped, failed = download(granules, out_dir)
    print(f"downloaded {len(downloaded)} files, skipped {len(skipped)} already present, failed {len(failed)}")
    for p in downloaded:
        print(f"  {p}")
    for name in failed:
        print(f"  FAILED {name}")
    return EXIT_DOWNLOAD if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
