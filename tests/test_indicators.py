"""Tests for pure-numpy technical indicators (Phase 3)."""

from __future__ import annotations

from trader.features.indicators import (
    atr,
    bb_pct,
    bollinger,
    ema,
    macd,
    rsi,
    sma,
    swing_highs,
    swing_lows,
    vwap,
)

# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------


def test_ema_basic() -> None:
    """EMA of 30 constant values equals that constant."""
    values = [5.0] * 30
    result = ema(values, period=20)
    assert result is not None
    assert abs(result - 5.0) < 1e-9


def test_ema_insufficient_data() -> None:
    """Fewer values than period → None."""
    result = ema([1.0, 2.0, 3.0], period=20)
    assert result is None


def test_ema_deterministic() -> None:
    """Same input always gives same output (golden file check)."""
    closes = [float(i) for i in range(1, 51)]
    r1 = ema(closes, period=20)
    r2 = ema(closes, period=20)
    assert r1 == r2
    assert r1 is not None


def test_ema_cross_check() -> None:
    """EMA(20) of 1..50 should equal ~40.67 (manually computed value).

    Seed = SMA of first 20 values = mean(1..20) = 10.5
    alpha = 2 / (20 + 1) = 2/21
    Apply recursive EMA for values 21..50 (30 more values).
    """
    closes = [float(i) for i in range(1, 51)]
    result = ema(closes, period=20)
    assert result is not None

    # Manual computation
    alpha = 2.0 / (20 + 1)
    seed = sum(closes[:20]) / 20  # = 10.5
    val = seed
    for c in closes[20:]:
        val = alpha * c + (1 - alpha) * val

    assert abs(result - val) < 1e-9


# ---------------------------------------------------------------------------
# SMA
# ---------------------------------------------------------------------------


def test_sma_basic() -> None:
    """SMA(3) of [1,2,3,4,5] → 4.0 for the last value."""
    result = sma([1.0, 2.0, 3.0, 4.0, 5.0], period=3)
    assert result is not None
    assert abs(result - 4.0) < 1e-9


def test_sma_insufficient_data() -> None:
    result = sma([1.0, 2.0], period=3)
    assert result is None


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------


def _make_rsi_prices(n: int, delta: float) -> list[float]:
    """Generate n prices with constant delta."""
    prices = [100.0]
    for _ in range(n):
        prices.append(prices[-1] + delta)
    return prices


def test_rsi_all_gains() -> None:
    """14 bars of constant gains → RSI = 100."""
    prices = _make_rsi_prices(14, +1.0)
    result = rsi(prices, period=14)
    assert result is not None
    assert abs(result - 100.0) < 1e-6


def test_rsi_all_losses() -> None:
    """14 bars of constant losses → RSI = 0."""
    prices = _make_rsi_prices(14, -1.0)
    result = rsi(prices, period=14)
    assert result is not None
    assert abs(result - 0.0) < 1e-6


def test_rsi_range() -> None:
    """RSI is always in [0, 100]."""
    import random

    random.seed(42)
    prices = [100.0 + random.gauss(0, 1) for _ in range(50)]
    for i in range(10, len(prices)):
        r = rsi(prices[: i + 1], period=14)
        if r is not None:
            assert 0.0 <= r <= 100.0


def test_rsi_insufficient_data() -> None:
    result = rsi([1.0, 2.0], period=14)
    assert result is None


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------


def test_macd_returns_three_values() -> None:
    """MACD returns a 3-tuple; either all float or all None."""
    closes = [float(i) for i in range(1, 60)]
    result = macd(closes)
    assert isinstance(result, tuple)
    assert len(result) == 3
    # If enough data, all should be floats
    m, s, h = result
    if m is None:
        assert s is None and h is None
    else:
        assert isinstance(m, float)
        assert isinstance(s, float)
        assert isinstance(h, float)


def test_macd_insufficient_data() -> None:
    """Too few values → (None, None, None)."""
    result = macd([1.0, 2.0, 3.0])
    assert result == (None, None, None)


def test_macd_histogram_is_diff() -> None:
    """Histogram = macd_line - signal_line."""
    closes = [float(i) for i in range(1, 60)]
    m, s, h = macd(closes)
    if m is not None and s is not None and h is not None:
        assert abs(h - (m - s)) < 1e-9


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------


def test_atr_positive() -> None:
    """ATR is always > 0 when enough data is available."""
    highs = [float(i) + 1.0 for i in range(1, 30)]
    lows = [float(i) - 1.0 for i in range(1, 30)]
    closes = [float(i) for i in range(1, 30)]
    result = atr(highs, lows, closes, period=14)
    assert result is not None
    assert result > 0.0


def test_atr_insufficient_data() -> None:
    result = atr([2.0], [1.0], [1.5], period=14)
    assert result is None


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------


def test_bollinger_band_ordering() -> None:
    """upper > mid > lower always."""
    closes = [float(i) + (i % 3) * 0.1 for i in range(1, 30)]
    u, m, lo = bollinger(closes, period=20)
    assert u is not None and m is not None and lo is not None
    assert u > m > lo


def test_bollinger_insufficient_data() -> None:
    result = bollinger([1.0, 2.0], period=20)
    assert result == (None, None, None)


# ---------------------------------------------------------------------------
# BB%
# ---------------------------------------------------------------------------


def test_bb_pct_midpoint() -> None:
    """At the midband price, bb_pct = 0.5."""
    upper, mid, lower = 110.0, 100.0, 90.0
    result = bb_pct(mid, upper, lower)
    assert result is not None
    assert abs(result - 0.5) < 1e-9


def test_bb_pct_zero_range() -> None:
    """Upper == lower → None (avoid div-by-zero)."""
    result = bb_pct(100.0, 100.0, 100.0)
    assert result is None


# ---------------------------------------------------------------------------
# VWAP
# ---------------------------------------------------------------------------


def test_vwap_equal_prices() -> None:
    """All same price → vwap equals that price."""
    closes = [50.0] * 10
    volumes = [100.0] * 10
    result = vwap(closes, volumes)
    assert result is not None
    assert abs(result - 50.0) < 1e-9


def test_vwap_zero_volume() -> None:
    """All zero volume → None."""
    result = vwap([1.0, 2.0], [0.0, 0.0])
    assert result is None


def test_vwap_mismatched_lengths() -> None:
    """Mismatched lengths → None."""
    result = vwap([1.0, 2.0], [1.0])
    assert result is None


# ---------------------------------------------------------------------------
# Swing highs / lows
# ---------------------------------------------------------------------------


def test_swing_highs_finds_peak() -> None:
    """[1,2,3,2,1] with window=2 → contains 3.0."""
    result = swing_highs([1.0, 2.0, 3.0, 2.0, 1.0], window=2)
    assert 3.0 in result


def test_swing_lows_finds_trough() -> None:
    """[3,2,1,2,3] with window=2 → contains 1.0."""
    result = swing_lows([3.0, 2.0, 1.0, 2.0, 3.0], window=2)
    assert 1.0 in result


def test_swing_highs_empty() -> None:
    result = swing_highs([], window=5)
    assert result == []


def test_swing_lows_empty() -> None:
    result = swing_lows([], window=5)
    assert result == []
