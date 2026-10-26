"""fix/pre-phase8: Ensure all datetime usage is timezone-aware (UTC).

These tests fail if naive datetimes creep back in.
"""

from __future__ import annotations

import datetime
import types

# ---------------------------------------------------------------------------
# Helper: walk a module's attributes and collect datetime instances
# ---------------------------------------------------------------------------


def _collect_naive_datetimes(mod: types.ModuleType) -> list[str]:
    """Return names of module-level datetime attributes that are naive."""
    naive: list[str] = []
    for name in dir(mod):
        val = getattr(mod, name, None)
        if isinstance(val, datetime.datetime) and val.tzinfo is None:
            naive.append(f"{mod.__name__}.{name}")
    return naive


# ---------------------------------------------------------------------------
# Test: key producer functions return aware datetimes
# ---------------------------------------------------------------------------


class TestAwareDatetimes:
    def test_dsor_utcnow_helper_returns_aware(self) -> None:
        from trader.dsor.models import _utcnow

        ts = _utcnow()
        assert ts.tzinfo is not None, "dsor._utcnow() must return an aware datetime"
        assert ts.tzinfo is datetime.UTC or ts.utcoffset() == datetime.timedelta(0)

    def test_announcements_utcnow_helper_returns_aware(self) -> None:
        from trader.data.announcements import _utcnow

        ts = _utcnow()
        assert ts.tzinfo is not None, "announcements._utcnow() must return an aware datetime"

    def test_cross_asset_utcnow_helper_returns_aware(self) -> None:
        from trader.data.cross_asset import _utcnow

        ts = _utcnow()
        assert ts.tzinfo is not None, "cross_asset._utcnow() must return an aware datetime"

    def test_macro_upcoming_returns_aware_or_normalised_datetimes(self) -> None:
        """upcoming() must not crash with offset-naive comparison error."""
        from trader.data.macro import MacroCalendar

        cal = MacroCalendar()
        events = cal.upcoming(5)
        # If this runs without TypeError it means naive comparison was handled.
        assert isinstance(events, list)

    def test_macro_is_blackout_accepts_naive_input_without_crash(self) -> None:
        """is_blackout() must accept a naive datetime (tests may pass naive values)."""
        from trader.data.macro import MacroCalendar

        cal = MacroCalendar()
        # Deliberately naive — must not raise TypeError
        naive_now = datetime.datetime(2026, 1, 29, 19, 0, 0)
        result, _ = cal.is_blackout(naive_now)
        assert isinstance(result, bool)

    def test_announcements_is_blackout_accepts_naive_input(self) -> None:
        """is_blackout() must accept a naive datetime without crashing."""
        from unittest.mock import MagicMock

        from trader.data.announcements import ExchangeAnnouncementCollector

        client = MagicMock()
        client.get_announcements.return_value = []
        collector = ExchangeAnnouncementCollector(rest_client=client)
        collector.fetch()

        naive_now = datetime.datetime(2026, 10, 21, 15, 0, 0)
        result, _ = collector.is_blackout(naive_now)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Regression: DSOR model _utcnow produces aware datetimes
# ---------------------------------------------------------------------------


class TestNaiveDatetimeRegression:
    def test_dsor_model_default_ts_is_aware(self) -> None:
        """_utcnow() used as column default must produce an aware datetime."""
        from trader.dsor.models import _utcnow

        ts = _utcnow()
        assert ts.tzinfo is not None, "_utcnow() must produce an aware datetime"
        assert ts.utcoffset() == datetime.timedelta(0), "_utcnow() must be UTC"

    def test_naive_datetime_comparison_raises_in_standard_python(self) -> None:
        """Sanity check: mixing naive and aware datetimes raises TypeError.

        This documents the problem we're preventing. If this test ever fails
        it means Python changed its datetime comparison semantics.
        """
        import pytest

        aware = datetime.datetime.now(datetime.UTC)
        naive = datetime.datetime(2026, 1, 1, 0, 0, 0)
        with pytest.raises(TypeError):
            _ = naive >= aware
