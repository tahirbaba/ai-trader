"""Phase 2A: Unit tests for session tagging and timezone display helpers."""

from __future__ import annotations

import datetime

from trader.data.session import format_for_display, tag_session, to_pkt, to_uae


def _utc(hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 1, 15, hour, minute, 0)


class TestTagSession:
    def test_asia_midnight(self):
        assert tag_session(_utc(0, 0)) == "asia"

    def test_asia_4am(self):
        assert tag_session(_utc(4)) == "asia"

    def test_asia_end(self):
        # 06:59 UTC still asia
        assert tag_session(_utc(6, 59)) == "asia"

    def test_asia_london_overlap_start(self):
        assert tag_session(_utc(7, 0)) == "asia_london_overlap"

    def test_asia_london_overlap_mid(self):
        assert tag_session(_utc(7, 30)) == "asia_london_overlap"

    def test_asia_london_overlap_end(self):
        # 07:59 still overlap
        assert tag_session(_utc(7, 59)) == "asia_london_overlap"

    def test_london_only(self):
        # 08:00 – 11:59 is london only
        assert tag_session(_utc(8, 0)) == "london"
        assert tag_session(_utc(10)) == "london"
        assert tag_session(_utc(11, 59)) == "london"

    def test_london_ny_overlap_start(self):
        assert tag_session(_utc(12, 0)) == "london_ny_overlap"

    def test_london_ny_overlap_mid(self):
        assert tag_session(_utc(13)) == "london_ny_overlap"

    def test_london_ny_overlap_end(self):
        # 15:59 still overlap
        assert tag_session(_utc(15, 59)) == "london_ny_overlap"

    def test_ny_only(self):
        # 16:00 – 20:59 is NY only
        assert tag_session(_utc(16, 0)) == "ny"
        assert tag_session(_utc(18)) == "ny"
        assert tag_session(_utc(20, 59)) == "ny"

    def test_off_after_ny(self):
        assert tag_session(_utc(21)) == "off"
        assert tag_session(_utc(22)) == "off"
        assert tag_session(_utc(23, 59)) == "off"

    def test_aware_datetime_utc(self):
        ts = datetime.datetime(2026, 1, 15, 4, 0, 0, tzinfo=datetime.UTC)
        assert tag_session(ts) == "asia"

    def test_aware_datetime_non_utc_converted(self):
        # 7:30 UTC+5 = 02:30 UTC → asia
        tz_pkt = datetime.timezone(datetime.timedelta(hours=5))
        ts = datetime.datetime(2026, 1, 15, 7, 30, 0, tzinfo=tz_pkt)
        # 7:30 PKT = 2:30 UTC → asia
        assert tag_session(ts) == "asia"


class TestToPkt:
    def test_noon_utc_is_5pm_pkt(self):
        ts_utc = datetime.datetime(2026, 1, 15, 12, 0, 0)
        pkt = to_pkt(ts_utc)
        assert pkt.hour == 17
        assert pkt.minute == 0

    def test_midnight_utc_is_5am_pkt(self):
        ts_utc = datetime.datetime(2026, 1, 15, 0, 0, 0)
        pkt = to_pkt(ts_utc)
        assert pkt.hour == 5

    def test_aware_input(self):
        ts = datetime.datetime(2026, 1, 15, 12, 0, tzinfo=datetime.UTC)
        pkt = to_pkt(ts)
        assert pkt.hour == 17

    def test_offset_is_plus5(self):
        ts_utc = datetime.datetime(2026, 6, 1, 10, 30, 0)
        pkt = to_pkt(ts_utc)
        assert pkt.utcoffset() == datetime.timedelta(hours=5)


class TestToUae:
    def test_noon_utc_is_4pm_uae(self):
        ts_utc = datetime.datetime(2026, 1, 15, 12, 0, 0)
        uae = to_uae(ts_utc)
        assert uae.hour == 16
        assert uae.minute == 0

    def test_midnight_utc_is_4am_uae(self):
        ts_utc = datetime.datetime(2026, 1, 15, 0, 0, 0)
        uae = to_uae(ts_utc)
        assert uae.hour == 4

    def test_offset_is_plus4(self):
        ts_utc = datetime.datetime(2026, 6, 1, 10, 30, 0)
        uae = to_uae(ts_utc)
        assert uae.utcoffset() == datetime.timedelta(hours=4)

    def test_aware_input(self):
        ts = datetime.datetime(2026, 1, 15, 12, 0, tzinfo=datetime.UTC)
        uae = to_uae(ts)
        assert uae.hour == 16


class TestFormatForDisplay:
    def test_format_contains_all_parts(self):
        ts = datetime.datetime(2026, 1, 1, 12, 0, 0)
        result = format_for_display(ts)
        assert "UTC" in result
        assert "PKT" in result
        assert "UAE" in result

    def test_specific_times(self):
        ts = datetime.datetime(2026, 1, 1, 12, 0, 0)
        result = format_for_display(ts)
        # 12:00 UTC → 17:00 PKT → 16:00 UAE
        assert "12:00 UTC" in result
        assert "17:00 PKT" in result
        assert "16:00 UAE" in result

    def test_date_included(self):
        ts = datetime.datetime(2026, 3, 15, 8, 30, 0)
        result = format_for_display(ts)
        assert "2026-03-15" in result

    def test_format_separator(self):
        ts = datetime.datetime(2026, 1, 1, 0, 0, 0)
        result = format_for_display(ts)
        assert "|" in result
