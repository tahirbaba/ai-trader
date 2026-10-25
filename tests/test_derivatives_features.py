"""Tests for derivatives feature functions (Phase 3)."""

from __future__ import annotations

import math

import pytest

from trader.features.derivatives_features import (
    basis_zscore,
    funding_zscore,
    oi_price_divergence,
)

# ---------------------------------------------------------------------------
# funding_zscore
# ---------------------------------------------------------------------------


def test_funding_zscore_zero_variance() -> None:
    """All same values → z-score = 0 (or None if too few)."""
    history = [0.01] * 20
    result = funding_zscore(history)
    # std=0 case: z-score is 0 (last == mean)
    assert result is not None
    assert result == 0.0 or math.isnan(result) or result == pytest.approx(0.0, abs=1e-9)


def test_funding_zscore_high() -> None:
    """Last value >> mean → z-score > 2."""
    history = [0.01] * 19 + [0.5]
    result = funding_zscore(history)
    assert result is not None
    assert result > 2.0


def test_funding_zscore_low() -> None:
    """Last value << mean → z-score < -2."""
    history = [0.5] * 19 + [0.01]
    result = funding_zscore(history)
    assert result is not None
    assert result < -2.0


def test_funding_zscore_insufficient() -> None:
    """< 2 values → None."""
    assert funding_zscore([]) is None
    assert funding_zscore([0.01]) is None


def test_funding_zscore_two_values() -> None:
    """2 values → returns a float."""
    result = funding_zscore([0.01, 0.02])
    assert result is not None
    assert isinstance(result, float)


# ---------------------------------------------------------------------------
# oi_price_divergence
# ---------------------------------------------------------------------------


def test_oi_price_divergence_bullish() -> None:
    """OI down, price up → True (divergence)."""
    oi = [100.0, 95.0, 90.0]
    price = [1000.0, 1010.0, 1020.0]
    assert oi_price_divergence(oi, price, window=3) is True


def test_oi_price_divergence_bearish() -> None:
    """OI up, price down → True (divergence)."""
    oi = [100.0, 105.0, 110.0]
    price = [1000.0, 990.0, 980.0]
    assert oi_price_divergence(oi, price, window=3) is True


def test_oi_price_divergence_no_divergence() -> None:
    """Both up → False."""
    oi = [100.0, 105.0, 110.0]
    price = [1000.0, 1010.0, 1020.0]
    assert oi_price_divergence(oi, price, window=3) is False


def test_oi_price_divergence_both_down() -> None:
    """Both down → False (no divergence)."""
    oi = [110.0, 105.0, 100.0]
    price = [1020.0, 1010.0, 1000.0]
    assert oi_price_divergence(oi, price, window=3) is False


def test_oi_price_divergence_insufficient() -> None:
    """Not enough data → False (no exception)."""
    assert oi_price_divergence([100.0], [1000.0], window=3) is False


# ---------------------------------------------------------------------------
# basis_zscore
# ---------------------------------------------------------------------------


def test_basis_zscore_calculation() -> None:
    """Verify math: z = (last - mean) / std."""
    history = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = basis_zscore(history)
    assert result is not None
    # mean = 3.0, std (population) = sqrt(2) ≈ 1.4142
    # z = (5-3)/sqrt(2) ≈ 1.4142
    mean = sum(history) / len(history)
    variance = sum((x - mean) ** 2 for x in history) / len(history)
    std = variance**0.5
    expected = (history[-1] - mean) / std
    assert abs(result - expected) < 1e-9


def test_basis_zscore_insufficient() -> None:
    assert basis_zscore([]) is None
    assert basis_zscore([1.0]) is None
