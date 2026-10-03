from __future__ import annotations
from datetime import date, datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(v) -> datetime:
    if isinstance(v, datetime):
        d = v
    else:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def parse_as_of(v) -> datetime | None:
    """None -> None. A bare date means END of that day (rows with date <= as_of are kept)."""
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return parse_dt(v)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, 23, 59, 59, tzinfo=timezone.utc)
    s = str(v)
    if len(s) == 10:
        return datetime.fromisoformat(s).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    return parse_dt(s)
