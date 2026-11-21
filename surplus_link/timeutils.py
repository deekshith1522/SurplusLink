"""Time helpers. All stored timestamps are UTC ISO-8601 text; display is IST."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Optional clock override (used by tests). None means real UTC time.
_clock = None


def set_clock(fn_or_none):
    """Override the clock with a zero-argument callable returning an aware datetime, or reset with None."""
    global _clock
    _clock = fn_or_none


def now_utc():
    if _clock is not None:
        return _clock()
    return datetime.now(timezone.utc)


def to_utc_iso(dt):
    """Convert an aware datetime to a UTC ISO-8601 string ending in +00:00."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("to_utc_iso requires a timezone-aware datetime")
    return dt.astimezone(timezone.utc).isoformat()


def parse_utc(text):
    """Parse an ISO-8601 string (accepts 'Z' or +00:00) into an aware UTC datetime.

    Naive input is treated as UTC.
    """
    s = text.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_ist(value):
    """Format a UTC string or datetime for display in IST, e.g. '01 Jan 2025, 05:30 IST'."""
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        dt = parse_utc(value)
    else:
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime("%d %b %Y, %H:%M IST")
