"""Phase 2A: Session tagging and timezone display helpers.

Session windows (UTC):
    Asia          00:00 – 08:00
    London        07:00 – 16:00
    NY            12:00 – 21:00
    Overlaps      Asia/London  07:00 – 08:00
                  London/NY    12:00 – 16:00
    Off           21:00 – 24:00 and other gaps

PKT = UTC+5 (Pakistan Standard Time)
UAE = UTC+4 (Gulf Standard Time)
"""

from __future__ import annotations

import datetime


def tag_session(ts: datetime.datetime) -> str:
    """Return the trading session name for the given UTC timestamp.

    Returns one of:
        "asia", "london", "ny",
        "asia_london_overlap", "london_ny_overlap", "off"
    """
    # Normalise to UTC
    if ts.tzinfo is not None:
        ts = ts.astimezone(datetime.UTC).replace(tzinfo=None)

    h = ts.hour + ts.minute / 60.0

    # Overlaps checked first (more specific)
    if 7.0 <= h < 8.0:
        return "asia_london_overlap"
    if 12.0 <= h < 16.0:
        return "london_ny_overlap"

    # Single sessions
    if 0.0 <= h < 7.0:
        return "asia"
    if 8.0 <= h < 12.0:
        return "london"
    if 16.0 <= h < 21.0:
        return "ny"

    # 21:00 – 24:00
    return "off"


_PKT_OFFSET = datetime.timezone(datetime.timedelta(hours=5), name="PKT")
_UAE_OFFSET = datetime.timezone(datetime.timedelta(hours=4), name="UAE")


def to_pkt(ts: datetime.datetime) -> datetime.datetime:
    """Convert a UTC (or timezone-aware) datetime to PKT (UTC+5)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.UTC)
    return ts.astimezone(_PKT_OFFSET)


def to_uae(ts: datetime.datetime) -> datetime.datetime:
    """Convert a UTC (or timezone-aware) datetime to UAE/GST (UTC+4)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.UTC)
    return ts.astimezone(_UAE_OFFSET)


def format_for_display(ts: datetime.datetime) -> str:
    """Return a human-readable timestamp in UTC, PKT, and UAE.

    Example: "2026-01-01 12:00 UTC | 17:00 PKT | 16:00 UAE"
    """
    ts_utc = ts.replace(tzinfo=datetime.UTC) if ts.tzinfo is None else ts.astimezone(datetime.UTC)

    pkt = to_pkt(ts_utc)
    uae = to_uae(ts_utc)

    utc_str = ts_utc.strftime("%Y-%m-%d %H:%M UTC")
    pkt_str = pkt.strftime("%H:%M PKT")
    uae_str = uae.strftime("%H:%M UAE")

    return f"{utc_str} | {pkt_str} | {uae_str}"
