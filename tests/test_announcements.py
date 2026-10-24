"""Phase 2B: Tests for ExchangeAnnouncementCollector."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

from trader.data.collector import CollectorResult


def _make_rest_client(windows: list[dict] | None = None) -> MagicMock:
    client = MagicMock()
    if windows is None:
        # Default: one maintenance window 14:00–16:00 on 2026-10-21
        windows = [
            {
                "title": "System Maintenance",
                "type": "maintain_systems",
                "startTime": int(
                    datetime.datetime(2026, 10, 21, 14, 0, 0).timestamp() * 1000
                ),
                "endTime": int(
                    datetime.datetime(2026, 10, 21, 16, 0, 0).timestamp() * 1000
                ),
            }
        ]
    client.get_announcements.return_value = windows
    return client


class TestExchangeAnnouncementCollector:
    def test_is_blackout_during_maintenance(self):
        """Now is inside window → True."""
        from trader.data.announcements import ExchangeAnnouncementCollector

        client = _make_rest_client()
        collector = ExchangeAnnouncementCollector(rest_client=client)
        collector.fetch()

        now = datetime.datetime(2026, 10, 21, 15, 0, 0)  # inside 14:00–16:00
        blackout, reason = collector.is_blackout(now)
        assert blackout is True
        assert reason != ""

    def test_not_blackout_outside_window(self):
        """Now is outside window → False."""
        from trader.data.announcements import ExchangeAnnouncementCollector

        client = _make_rest_client()
        collector = ExchangeAnnouncementCollector(rest_client=client)
        collector.fetch()

        now = datetime.datetime(2026, 10, 21, 17, 0, 0)  # after 16:00
        blackout, reason = collector.is_blackout(now)
        assert blackout is False
        assert reason == ""

    def test_fetch_failure_does_not_raise(self):
        """REST raises BybitRestError → fetch() catches, windows empty, no exception."""
        from trader.data.announcements import ExchangeAnnouncementCollector
        from trader.data.bybit_rest import BybitRestError

        client = MagicMock()
        client.get_announcements.side_effect = BybitRestError("API down")

        collector = ExchangeAnnouncementCollector(rest_client=client)
        # Should not raise
        collector.fetch()

        now = datetime.datetime(2026, 10, 21, 15, 0, 0)
        blackout, _ = collector.is_blackout(now)
        assert blackout is False

    def test_collect_returns_result(self):
        """collect() returns CollectorResult."""
        from trader.data.announcements import ExchangeAnnouncementCollector

        client = _make_rest_client()
        collector = ExchangeAnnouncementCollector(rest_client=client)
        result = collector.collect()

        assert isinstance(result, CollectorResult)
        assert "windows" in result.data
        assert "is_blackout_now" in result.data

    def test_collect_reflects_blackout_status(self):
        """collect() is_blackout_now=True when called during a window."""
        from trader.data.announcements import ExchangeAnnouncementCollector

        client = _make_rest_client()
        collector = ExchangeAnnouncementCollector(rest_client=client)

        # Patch utcnow to be inside window
        with patch(
            "trader.data.announcements._utcnow",
            return_value=datetime.datetime(2026, 10, 21, 15, 0, 0),
        ):
            result = collector.collect()

        assert result.data["is_blackout_now"] is True

    def test_maintenance_by_title(self):
        """Window typed differently but title contains 'maintenance' → blackout."""
        from trader.data.announcements import ExchangeAnnouncementCollector

        windows = [
            {
                "title": "Scheduled Maintenance Window",
                "type": "other",
                "startTime": int(
                    datetime.datetime(2026, 10, 21, 14, 0, 0).timestamp() * 1000
                ),
                "endTime": int(
                    datetime.datetime(2026, 10, 21, 16, 0, 0).timestamp() * 1000
                ),
            }
        ]
        client = _make_rest_client(windows)
        collector = ExchangeAnnouncementCollector(rest_client=client)
        collector.fetch()

        now = datetime.datetime(2026, 10, 21, 15, 0, 0)
        blackout, _ = collector.is_blackout(now)
        assert blackout is True
