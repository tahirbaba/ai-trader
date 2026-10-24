"""Phase 2A: Unit tests for DataQualityMonitor."""

from __future__ import annotations

import datetime

from trader.data.quality import DataQualityMonitor


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _ago(seconds: float) -> datetime.datetime:
    return _now() - datetime.timedelta(seconds=seconds)


class TestStaleness:
    def test_fresh_data_no_flag(self):
        monitor = DataQualityMonitor()
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=_ago(10),
            max_data_age_seconds=60,
            price_history=[0.60, 0.61, 0.605],
        )
        assert "stale_data" not in flags

    def test_stale_data_flag(self):
        monitor = DataQualityMonitor()
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=_ago(120),
            max_data_age_seconds=60,
            price_history=[0.60, 0.61, 0.605],
        )
        assert "stale_data" in flags

    def test_none_ts_is_stale(self):
        monitor = DataQualityMonitor()
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=None,
            max_data_age_seconds=60,
            price_history=[0.60],
        )
        assert "stale_data" in flags

    def test_exactly_at_threshold_not_stale(self):
        monitor = DataQualityMonitor()
        # 50 seconds old, threshold 60 → should not be stale
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=_ago(50),
            max_data_age_seconds=60,
            price_history=[0.60, 0.61],
        )
        assert "stale_data" not in flags

    def test_just_over_threshold_is_stale(self):
        monitor = DataQualityMonitor()
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=_ago(61),
            max_data_age_seconds=60,
            price_history=[0.60, 0.61],
        )
        assert "stale_data" in flags


class TestClockDrift:
    def test_no_drift_no_flag(self):
        monitor = DataQualityMonitor()
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=_ago(5),
            max_data_age_seconds=60,
            price_history=[0.60, 0.61, 0.62],
            clock_drift_ms=100.0,
            max_clock_drift_ms=1000,
        )
        assert "clock_drift" not in flags

    def test_drift_over_threshold_flagged(self):
        monitor = DataQualityMonitor()
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=_ago(5),
            max_data_age_seconds=60,
            price_history=[0.60, 0.61, 0.62],
            clock_drift_ms=1500.0,
            max_clock_drift_ms=1000,
        )
        assert "clock_drift" in flags

    def test_negative_drift_also_flagged(self):
        monitor = DataQualityMonitor()
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=_ago(5),
            max_data_age_seconds=60,
            price_history=[0.60, 0.61, 0.62],
            clock_drift_ms=-2000.0,
            max_clock_drift_ms=1000,
        )
        assert "clock_drift" in flags

    def test_exactly_at_threshold_no_flag(self):
        monitor = DataQualityMonitor()
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=_ago(5),
            max_data_age_seconds=60,
            price_history=[0.60, 0.61, 0.62],
            clock_drift_ms=1000.0,
            max_clock_drift_ms=1000,
        )
        assert "clock_drift" not in flags


class TestOutlierDetection:
    def test_normal_price_no_outlier(self):
        monitor = DataQualityMonitor()
        # 100 normal prices, latest is within range
        history = [0.60] * 9 + [0.61]  # 10 prices, latest = 0.61
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=_ago(5),
            max_data_age_seconds=60,
            price_history=history,
        )
        assert "outlier_price" not in flags

    def test_extreme_outlier_flagged(self):
        monitor = DataQualityMonitor()
        # Mean ~0.60, small std; outlier = 10.0 → >> 5σ
        # Use slight variation so std > 0
        history = [0.600, 0.601, 0.599, 0.602, 0.598, 0.601, 0.600, 0.599, 10.0]
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=_ago(5),
            max_data_age_seconds=60,
            price_history=history,
        )
        assert "outlier_price" in flags

    def test_5_sigma_boundary(self):
        monitor = DataQualityMonitor()
        # history = [10, 10, 10, ..., 10] (9 values), std = 0
        # zero std → no outlier flag (no division)
        history = [10.0] * 9 + [10.0]
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=_ago(5),
            max_data_age_seconds=60,
            price_history=history,
        )
        assert "outlier_price" not in flags

    def test_not_enough_history_skips_outlier(self):
        monitor = DataQualityMonitor()
        # < 3 data points → skip outlier check
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=_ago(5),
            max_data_age_seconds=60,
            price_history=[0.60, 999.0],
        )
        assert "outlier_price" not in flags


class TestGapDetection:
    def test_gap_zero_value(self):
        monitor = DataQualityMonitor()
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=_ago(5),
            max_data_age_seconds=60,
            price_history=[0.60, 0.0, 0.62],
        )
        assert "gap_detected" in flags

    def test_gap_none_value(self):
        monitor = DataQualityMonitor()
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=_ago(5),
            max_data_age_seconds=60,
            price_history=[0.60, None, 0.62],  # type: ignore[list-item]
        )
        assert "gap_detected" in flags

    def test_no_gap_clean_data(self):
        monitor = DataQualityMonitor()
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=_ago(5),
            max_data_age_seconds=60,
            price_history=[0.60, 0.61, 0.62, 0.61],
        )
        assert "gap_detected" not in flags

    def test_short_history_skips_gap_check(self):
        monitor = DataQualityMonitor()
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=_ago(5),
            max_data_age_seconds=60,
            price_history=[0.60],  # only 1 item, need >= 2
        )
        assert "gap_detected" not in flags


class TestMultipleFlags:
    def test_all_flags_together(self):
        monitor = DataQualityMonitor()
        # Construct a history with: a gap (0.0), and an outlier at the end.
        # history[:-1] filtered = [0.600, 0.601, 0.599, 0.602, 0.598] (no None/0.0)
        # latest = 10.0, which is >> 5σ from ~0.60
        price_history = [0.600, 0.601, 0.0, 0.599, 0.602, 0.598, 10.0]
        flags = monitor.check(
            symbol="XRPUSDT",
            latest_ts=None,          # stale_data
            max_data_age_seconds=60,
            price_history=price_history,
            clock_drift_ms=5000.0,    # clock_drift
            max_clock_drift_ms=1000,
        )
        assert "stale_data" in flags
        assert "clock_drift" in flags
        assert "gap_detected" in flags
        assert "outlier_price" in flags
