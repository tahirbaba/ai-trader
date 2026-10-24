"""Phase 2A: Unit tests for derivatives helpers (basis, L/S ratio parsing)."""

from __future__ import annotations

import pytest

from trader.data.derivatives import compute_basis, parse_long_short_ratio


class TestComputeBasis:
    def test_positive_basis(self):
        assert compute_basis(0.6050, 0.6040) == pytest.approx(0.0010)

    def test_negative_basis(self):
        assert compute_basis(0.6035, 0.6040) == pytest.approx(-0.0005)

    def test_zero_basis(self):
        assert compute_basis(0.6040, 0.6040) == pytest.approx(0.0)

    def test_large_values(self):
        assert compute_basis(30500.0, 30490.0) == pytest.approx(10.0)

    def test_fractional(self):
        assert compute_basis(1.23456, 1.23400) == pytest.approx(0.00056, abs=1e-8)


class TestParseLongShortRatio:
    def test_standard_row(self):
        row = ["1700000000000", "0.55", "0.45"]
        result = parse_long_short_ratio(row)
        assert result["ts_ms"] == 1700000000000
        assert result["long_ratio"] == pytest.approx(0.55)
        assert result["short_ratio"] == pytest.approx(0.45)

    def test_none_values_are_none(self):
        row = [None, None, None]
        result = parse_long_short_ratio(row)
        assert result["ts_ms"] is None
        assert result["long_ratio"] is None
        assert result["short_ratio"] is None

    def test_string_ts_converted_to_int(self):
        row = ["1700000001234", "0.6", "0.4"]
        result = parse_long_short_ratio(row)
        assert isinstance(result["ts_ms"], int)
        assert result["ts_ms"] == 1700000001234

    def test_ratios_sum_approximately_one(self):
        row = ["1700000000000", "0.5234", "0.4766"]
        result = parse_long_short_ratio(row)
        total = result["long_ratio"] + result["short_ratio"]
        assert total == pytest.approx(1.0, abs=1e-4)

    def test_numeric_inputs(self):
        row = [1700000000000, 0.7, 0.3]
        result = parse_long_short_ratio(row)
        assert result["long_ratio"] == pytest.approx(0.7)
        assert result["short_ratio"] == pytest.approx(0.3)
