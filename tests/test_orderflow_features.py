"""Tests for order-flow feature functions (Phase 3)."""

from __future__ import annotations

from trader.features.orderflow_features import cvd_divergence, spread_percentile

# ---------------------------------------------------------------------------
# cvd_divergence
# ---------------------------------------------------------------------------


def test_cvd_divergence_bearish() -> None:
    """CVD down, price up → True (bearish divergence)."""
    cvd = [1000.0, 900.0, 800.0]
    price = [100.0, 105.0, 110.0]
    assert cvd_divergence(cvd, price, window=3) is True


def test_cvd_divergence_bullish() -> None:
    """CVD up, price down → True (bullish divergence)."""
    cvd = [800.0, 900.0, 1000.0]
    price = [110.0, 105.0, 100.0]
    assert cvd_divergence(cvd, price, window=3) is True


def test_cvd_no_divergence() -> None:
    """CVD up, price up → False."""
    cvd = [800.0, 900.0, 1000.0]
    price = [100.0, 105.0, 110.0]
    assert cvd_divergence(cvd, price, window=3) is False


def test_cvd_both_down() -> None:
    """CVD down, price down → False (no divergence)."""
    cvd = [1000.0, 900.0, 800.0]
    price = [110.0, 105.0, 100.0]
    assert cvd_divergence(cvd, price, window=3) is False


def test_cvd_insufficient_data() -> None:
    """Not enough data → False."""
    assert cvd_divergence([1000.0], [100.0], window=3) is False


# ---------------------------------------------------------------------------
# spread_percentile
# ---------------------------------------------------------------------------


def test_spread_percentile_max() -> None:
    """Latest spread = max of history → high percentile (>= 75)."""
    history = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = spread_percentile(history)
    assert result is not None
    assert result >= 75.0


def test_spread_percentile_min() -> None:
    """Latest spread = min of history → low percentile (<= 25)."""
    history = [5.0, 4.0, 3.0, 2.0, 1.0]
    result = spread_percentile(history)
    assert result is not None
    assert result <= 25.0


def test_spread_percentile_middle() -> None:
    """Latest spread is median of history → ~50."""
    history = [1.0, 2.0, 3.0, 4.0, 5.0, 3.0]
    result = spread_percentile(history)
    assert result is not None
    assert 30.0 <= result <= 70.0


def test_spread_percentile_insufficient() -> None:
    """< 2 values → None."""
    assert spread_percentile([]) is None
    assert spread_percentile([1.0]) is None
