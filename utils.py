from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from urllib.parse import unquote

from config import DRAGON_ABBR


# -----------------------
# Safe deep getter
# -----------------------
def sg(d: Any, path: str, default=None):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


# -----------------------
# Time helpers
# -----------------------
def parse_rfc3339(ts: str) -> Optional[datetime]:
    """'2026-01-25T11:00:00Z' / '...000Z' / '...+00:00' -> aware datetime in UTC"""
    if not ts:
        return None
    s = ts.strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def iso_date_multiply_of_10(dt: Optional[datetime] = None) -> str:
    """Текущее время, округлённое вниз к 10 секундам — как у Andy."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    dt = dt.replace(microsecond=0)
    floored = dt.replace(second=(dt.second // 10) * 10)
    return floored.strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"


def minus_seconds_rfc3339(ts: str, seconds: int) -> str:
    base = ts.replace(".000Z", "").replace("Z", "")
    dt = datetime.fromisoformat(base).replace(tzinfo=timezone.utc)
    dt2 = dt - timedelta(seconds=seconds)
    return dt2.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def sanitize_starting_time(ts: str) -> str:
    ts = unquote(ts)
    try:
        head, tail = ts.split("T", 1)
        hh = tail[0:2]
        mm = tail[3:5]
        ss = tail[6:8]
        return f"{head}T{hh}:{mm}:{ss}.000Z"
    except Exception:
        return ts


def pretty_utc(ts: Optional[str]) -> str:
    if not ts:
        return "—"
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts


def pretty_local(ts: Optional[str]) -> str:
    if not ts:
        return "—"
    dt = parse_rfc3339(ts)
    if not dt:
        return ts
    local_tz = datetime.now().astimezone().tzinfo
    return dt.astimezone(local_tz).strftime("%d-%m-%Y %H:%M:%S")


# -----------------------
# Formatting helpers
# -----------------------
def fmt_dragons(team: dict) -> str:
    d = (team or {}).get("dragons")
    if not d:
        return "—"
    if isinstance(d, list):
        return " ".join(DRAGON_ABBR.get(str(x).lower(), str(x)) for x in d) if d else "—"
    if isinstance(d, dict):
        items = [f"{DRAGON_ABBR.get(k.lower(), k)}:{v}" for k, v in d.items()]
        return " ".join(items) if items else "—"
    return str(d)
