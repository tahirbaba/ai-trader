"""Phase 2B: Macro event calendar and blackout detector.

Hard-codes 2026–2027 high-impact event dates; optionally supplements with
ForexFactory free JSON feed (no API key required).
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

import requests

from trader.data.collector import CollectorResult

logger = logging.getLogger(__name__)

_FOREXFACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_REQUEST_TIMEOUT = 10

# ---------------------------------------------------------------------------
# Hardcoded 2026 FOMC dates (approximate) at 19:00 UTC (statement release)
# ---------------------------------------------------------------------------
_FOMC_DATES_2026: list[datetime.datetime] = [
    datetime.datetime(2026, 1, 29, 19, 0, 0),
    datetime.datetime(2026, 3, 19, 19, 0, 0),
    datetime.datetime(2026, 5, 7, 19, 0, 0),
    datetime.datetime(2026, 6, 18, 19, 0, 0),
    datetime.datetime(2026, 7, 30, 19, 0, 0),
    datetime.datetime(2026, 9, 17, 19, 0, 0),
    datetime.datetime(2026, 11, 5, 19, 0, 0),
    datetime.datetime(2026, 12, 10, 19, 0, 0),
]

# CPI release: 15th of each month 2026 at 13:30 UTC (approx)
_CPI_DATES_2026: list[datetime.datetime] = [
    datetime.datetime(2026, m, 15, 13, 30, 0) for m in range(1, 13)
]


# NFP: first Friday of each month at 13:30 UTC
def _first_friday(year: int, month: int) -> datetime.datetime:
    for day in range(1, 8):
        dt = datetime.datetime(year, month, day, 13, 30, 0)
        if dt.weekday() == 4:  # Friday
            return dt
    raise ValueError(f"No Friday found in first 7 days of {year}-{month}")


_NFP_DATES_2026: list[datetime.datetime] = [_first_friday(2026, m) for m in range(1, 13)]


def _build_hardcoded_events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for dt in _FOMC_DATES_2026:
        events.append({"name": "FOMC", "dt": dt, "country": "USD", "impact": "High"})
    for dt in _CPI_DATES_2026:
        events.append({"name": "CPI (US)", "dt": dt, "country": "USD", "impact": "High"})
    for dt in _NFP_DATES_2026:
        events.append({"name": "NFP (US)", "dt": dt, "country": "USD", "impact": "High"})
    return events


class MacroCalendar:
    """Macro event calendar with blackout detection.

    Parameters
    ----------
    rest_client:
        Unused; kept for interface parity.
    """

    def __init__(self, rest_client: Any = None) -> None:
        self._rest = rest_client
        self._events: list[dict[str, Any]] = _build_hardcoded_events()

    def fetch_forexfactory(self) -> None:
        """Fetch this week's calendar from ForexFactory and add high-impact events."""
        try:
            resp = requests.get(_FOREXFACTORY_URL, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            items: list[dict[str, Any]] = resp.json()

            _HIGH_IMPACT_COUNTRIES = {"USD", "EUR", "GBP"}
            new_events: list[dict[str, Any]] = []

            for item in items:
                if item.get("impact") != "High":
                    continue
                if item.get("country") not in _HIGH_IMPACT_COUNTRIES:
                    continue
                date_str: str = item.get("date", "")
                time_str: str = item.get("time", "00:00:00")
                try:
                    dt = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    try:
                        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                    except ValueError:
                        continue

                new_events.append(
                    {
                        "name": item.get("title", "Unknown"),
                        "dt": dt,
                        "country": item.get("country", ""),
                        "impact": "High",
                    }
                )

            if new_events:
                # Merge: keep hardcoded + add any FF events not already present
                existing_dts = {e["dt"] for e in self._events}
                for ev in new_events:
                    if ev["dt"] not in existing_dts:
                        self._events.append(ev)

        except Exception as exc:  # noqa: BLE001
            logger.warning("ForexFactory fetch failed: %s", exc)
            # Fall back to hardcoded events — no state change

    def is_blackout(
        self,
        now: datetime.datetime,
        before_min: int = 30,
        after_min: int = 30,
    ) -> tuple[bool, str]:
        """Check whether *now* falls within the blackout window of any event.

        Returns
        -------
        (True, event_name)  if within [event_time - before_min, event_time + after_min].
        (False, "")         otherwise.
        """
        # Normalise to aware UTC so naive test inputs compare correctly.
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.UTC)
        window_before = datetime.timedelta(minutes=before_min)
        window_after = datetime.timedelta(minutes=after_min)

        for event in self._events:
            event_dt: datetime.datetime = event["dt"]
            # Normalise event_dt if stored as naive (hardcoded calendar entries).
            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(tzinfo=datetime.UTC)
            if (event_dt - window_before) <= now <= (event_dt + window_after):
                return True, event["name"]
        return False, ""

    def upcoming(self, n: int = 5) -> list[dict[str, Any]]:
        """Return the next *n* events sorted by datetime."""
        now = datetime.datetime.now(datetime.UTC)

        def _aware(dt: datetime.datetime) -> datetime.datetime:
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=datetime.UTC)

        future = sorted(
            [e for e in self._events if _aware(e["dt"]) >= now],
            key=lambda e: e["dt"],
        )
        return future[:n]

    def has_coverage(self, horizon_days: int = 14) -> bool:
        """Return True if at least one high-impact event exists within *horizon_days*.

        Used as a staleness check: if the event source has no coverage over the
        next 14 days the calendar is considered stale and trading is blocked.
        """
        now = datetime.datetime.now(datetime.UTC)
        cutoff = now + datetime.timedelta(days=horizon_days)

        def _aware(dt: datetime.datetime) -> datetime.datetime:
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=datetime.UTC)

        return any(
            now <= _aware(e["dt"]) <= cutoff and e.get("impact", "").lower() == "high"
            for e in self._events
        )

    def collect(self) -> CollectorResult:
        """Return macro calendar result."""
        now = datetime.datetime.now(datetime.UTC)
        blackout, reason = self.is_blackout(now)
        upcoming_events = self.upcoming(10)

        # Fail-closed: if no high-impact event covers the next 14 days, flag as stale.
        quality_flags: list[str] = []
        if not self.has_coverage(horizon_days=14):
            quality_flags.append("macro_calendar_stale")

        # Serialise datetimes for JSON-safe output
        def _serialise(ev: dict[str, Any]) -> dict[str, Any]:
            return {**ev, "dt": ev["dt"].isoformat()}

        upcoming_serialised = [_serialise(e) for e in upcoming_events]
        next_event = upcoming_serialised[0] if upcoming_serialised else None

        return CollectorResult(
            symbol="MACRO",
            ts=now,
            data={
                "upcoming": upcoming_serialised,
                "is_blackout_now": blackout,
                "next_event": next_event,
            },
            quality_flags=quality_flags,
        )

    def backfill(self, limit: int) -> list[CollectorResult]:
        """Not supported; returns empty list."""
        return []
