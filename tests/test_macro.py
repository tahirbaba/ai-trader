"""Phase 2B: Tests for MacroCalendar."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

from trader.data.collector import CollectorResult

# A known seeded FOMC date: 2026-01-29 at 19:00 UTC (typical FOMC release)
_FOMC_DT = datetime.datetime(2026, 1, 29, 19, 0, 0)


class TestMacroCalendar:
    def test_is_blackout_30min_before_event(self):
        """29 minutes before FOMC → blackout=True."""
        from trader.data.macro import MacroCalendar

        cal = MacroCalendar()
        now = _FOMC_DT - datetime.timedelta(minutes=29)
        blackout, name = cal.is_blackout(now)
        assert blackout is True
        assert name != ""

    def test_not_blackout_31min_before(self):
        """31 minutes before FOMC → blackout=False."""
        from trader.data.macro import MacroCalendar

        cal = MacroCalendar()
        now = _FOMC_DT - datetime.timedelta(minutes=31)
        blackout, _ = cal.is_blackout(now)
        assert blackout is False

    def test_not_blackout_after_window(self):
        """31 minutes after FOMC → blackout=False."""
        from trader.data.macro import MacroCalendar

        cal = MacroCalendar()
        now = _FOMC_DT + datetime.timedelta(minutes=31)
        blackout, _ = cal.is_blackout(now)
        assert blackout is False

    def test_is_blackout_during_event(self):
        """Exactly at FOMC time → blackout=True."""
        from trader.data.macro import MacroCalendar

        cal = MacroCalendar()
        blackout, name = cal.is_blackout(_FOMC_DT)
        assert blackout is True
        assert name != ""

    def test_upcoming_sorted(self):
        """upcoming(n) returns events in chronological order; pin to future date."""
        from trader.data.macro import MacroCalendar

        cal = MacroCalendar()
        # Use the full 2026 event set and request more than available to avoid
        # time-sensitivity: just check the order property on whatever is returned.
        events = cal.upcoming(100)
        assert len(events) >= 1
        dts = [e["dt"] for e in events]
        assert dts == sorted(dts)

    def test_forexfactory_fetch_overrides(self):
        """Mock FF JSON → high-impact USD event appears in upcoming."""
        from trader.data.macro import MacroCalendar

        ff_data = [
            {
                "title": "FOMC Statement",
                "country": "USD",
                "date": "2026-11-05",
                "time": "18:00:00",
                "impact": "High",
                "forecast": "",
                "previous": "",
                "actual": "",
            }
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = ff_data
        mock_resp.raise_for_status.return_value = None

        with patch("trader.data.macro.requests.get", return_value=mock_resp):
            cal = MacroCalendar()
            cal.fetch_forexfactory()

        events = cal.upcoming(20)
        titles = [e["name"] for e in events]
        assert any("FOMC" in t for t in titles)

    def test_forexfactory_failure_falls_back(self):
        """requests.get raises → hardcoded events still available."""
        from trader.data.macro import MacroCalendar

        with patch("trader.data.macro.requests.get", side_effect=Exception("Network error")):
            cal = MacroCalendar()
            cal.fetch_forexfactory()

        events = cal.upcoming(5)
        assert len(events) > 0

    def test_collect_returns_collector_result(self):
        """collect() returns CollectorResult."""
        from trader.data.macro import MacroCalendar

        cal = MacroCalendar()
        result = cal.collect()
        assert isinstance(result, CollectorResult)
        assert "upcoming" in result.data
        assert "is_blackout_now" in result.data
        assert "next_event" in result.data

    def test_collect_next_event_is_soonest(self):
        """next_event is the chronologically first upcoming event."""
        from trader.data.macro import MacroCalendar

        cal = MacroCalendar()
        result = cal.collect()
        upcoming = result.data["upcoming"]
        next_ev = result.data["next_event"]
        if upcoming:
            assert next_ev is not None
            assert next_ev["dt"] == upcoming[0]["dt"]

    def test_collect_emits_macro_calendar_stale_when_no_coverage(self) -> None:
        """If no high-impact event covers the next 14 days, macro_calendar_stale is set."""
        import datetime
        from unittest.mock import patch

        from trader.data.macro import MacroCalendar

        # Simulate January 2027 — well beyond the 2026 hardcoded events.
        # At that point the calendar will have no upcoming high-impact events
        # within 14 days of "now".
        jan_2027 = datetime.datetime(2027, 1, 15, 12, 0, 0, tzinfo=datetime.UTC)
        with patch("trader.data.macro.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = jan_2027
            mock_dt.timedelta = datetime.timedelta
            mock_dt.UTC = datetime.UTC
            cal = MacroCalendar()
            result = cal.collect()

        assert "macro_calendar_stale" in result.quality_flags, (
            "Expected macro_calendar_stale flag when no high-impact events "
            "cover the next 14 days (Jan 2027 scenario)"
        )

    def test_has_coverage_true_within_horizon(self) -> None:
        """has_coverage returns True when a high-impact event is within horizon_days."""
        import datetime
        from unittest.mock import patch

        from trader.data.macro import MacroCalendar

        # 2026-01-15: FOMC on 2026-01-29 is 14 days away
        jan_15_2026 = datetime.datetime(2026, 1, 15, 12, 0, 0, tzinfo=datetime.UTC)
        with patch("trader.data.macro.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = jan_15_2026
            mock_dt.timedelta = datetime.timedelta
            mock_dt.UTC = datetime.UTC
            cal = MacroCalendar()
            covered = cal.has_coverage(horizon_days=14)

        assert covered is True

    def test_has_coverage_false_for_jan_2027(self) -> None:
        """has_coverage returns False when no high-impact event exists within 14 days."""
        import datetime
        from unittest.mock import patch

        from trader.data.macro import MacroCalendar

        jan_2027 = datetime.datetime(2027, 1, 15, 12, 0, 0, tzinfo=datetime.UTC)
        with patch("trader.data.macro.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = jan_2027
            mock_dt.timedelta = datetime.timedelta
            mock_dt.UTC = datetime.UTC
            cal = MacroCalendar()
            covered = cal.has_coverage(horizon_days=14)

        assert covered is False
