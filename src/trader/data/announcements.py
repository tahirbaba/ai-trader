"""Phase 2B: Exchange announcement collector and blackout detector."""

from __future__ import annotations

import datetime
import logging
from typing import Any

from trader.data.bybit_rest import BybitRestClient, BybitRestError
from trader.data.collector import BaseCollector, CollectorResult

logger = logging.getLogger(__name__)

# Types for maintenance windows: (start, end, type, title)
_Window = tuple[datetime.datetime, datetime.datetime | None, str, str]


def _utcnow() -> datetime.datetime:
    """Return current UTC time (injectable for testing)."""
    return datetime.datetime.now(datetime.UTC)


def _ms_to_dt(ms: int | None) -> datetime.datetime | None:
    """Convert Bybit epoch milliseconds (UTC) to a naive UTC datetime."""
    if ms is None:
        return None
    return datetime.datetime.fromtimestamp(ms / 1000.0, datetime.UTC)


class ExchangeAnnouncementCollector(BaseCollector):
    """Poll Bybit V5 announcement endpoint and detect maintenance blackouts.

    Parameters
    ----------
    rest_client:
        A ``BybitRestClient`` instance used to call the announcements endpoint.
    """

    def __init__(self, rest_client: BybitRestClient) -> None:
        # We don't call super().__init__ because BaseCollector requires `symbol`
        # which isn't applicable here; we store rest_client directly.
        self._rest = rest_client
        self._windows: list[_Window] = []

    # BaseCollector ABC requires symbol — set a sentinel
    @property
    def symbol(self) -> str:  # type: ignore[override]
        return "EXCHANGE"

    def fetch(self) -> None:
        """Fetch announcements and refresh the in-memory window list."""
        try:
            items = self._rest.get_announcements(
                locale="en-US",
                ann_type="new_crypto,maintain_systems,delistings",
            )
            self._windows = []
            for item in items:
                title: str = item.get("title", "")
                ann_type: str = item.get("type", "")
                start_ms: int | None = item.get("startTime")
                end_ms: int | None = item.get("endTime")

                start_dt = _ms_to_dt(start_ms)
                end_dt = _ms_to_dt(end_ms)

                if start_dt is not None:
                    self._windows.append((start_dt, end_dt, ann_type, title))
        except BybitRestError as exc:
            logger.warning("Failed to fetch announcements: %s", exc)
            # Leave existing windows as-is; don't raise

    def is_blackout(self, now: datetime.datetime) -> tuple[bool, str]:
        """Check whether *now* falls inside a maintenance window.

        Returns
        -------
        (True, reason)  if inside a ``maintain_systems`` window or a window
                        whose title contains "maintenance".
        (False, "")     otherwise.
        """
        # Normalise to aware UTC so naive test inputs still compare correctly.
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.UTC)
        for start, end, ann_type, title in self._windows:
            is_maintenance = (
                ann_type.lower() == "maintain_systems" or "maintenance" in title.lower()
            )
            if not is_maintenance:
                continue
            after_start = now >= start
            before_end = end is None or now < end
            if after_start and before_end:
                reason = title if title else ann_type
                return True, f"Maintenance window: {reason} starts {start}"
        return False, ""

    def collect(self) -> CollectorResult:
        """Fetch announcements and return result with blackout status."""
        self.fetch()
        now = _utcnow()
        blackout, reason = self.is_blackout(now)

        windows_data: list[dict[str, Any]] = [
            {
                "start": start.isoformat(),
                "end": end.isoformat() if end else None,
                "type": ann_type,
                "title": title,
            }
            for start, end, ann_type, title in self._windows
        ]

        return CollectorResult(
            symbol="EXCHANGE",
            ts=now,
            data={"windows": windows_data, "is_blackout_now": blackout, "reason": reason},
            quality_flags=[],
        )

    def backfill(self, limit: int) -> list[CollectorResult]:
        """Not supported; returns empty list."""
        return []
