"""Pure-numpy technical indicator functions (Phase 3).

All functions:
- Accept plain Python lists (converted internally to numpy arrays).
- Return float | None — None when there is insufficient data.
- No pandas, no pandas-ta.
"""

from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# EMA — Exponential Moving Average
# ---------------------------------------------------------------------------


def ema(values: list[float], period: int) -> float | None:
    """Compute EMA of *values* with the given *period*.

    Seeded with the SMA of the first *period* values; then the standard
    recursive formula is applied for the remaining values.

    Returns the EMA of the last value, or None if len(values) < period.
    """
    if len(values) < period:
        return None
    arr = np.array(values, dtype=np.float64)
    alpha = 2.0 / (period + 1)
    # Seed with SMA of first `period` values
    val = float(np.mean(arr[:period]))
    for price in arr[period:]:
        val = alpha * float(price) + (1.0 - alpha) * val
    return val


# ---------------------------------------------------------------------------
# SMA — Simple Moving Average
# ---------------------------------------------------------------------------


def sma(values: list[float], period: int) -> float | None:
    """Compute SMA of the last *period* values.

    Returns None if len(values) < period.
    """
    if len(values) < period:
        return None
    arr = np.array(values[-period:], dtype=np.float64)
    return float(np.mean(arr))


# ---------------------------------------------------------------------------
# RSI — Relative Strength Index (Wilder's smoothing)
# ---------------------------------------------------------------------------


def rsi(values: list[float], period: int = 14) -> float | None:
    """Compute RSI using Wilder's smoothing (alpha = 1/period).

    Returns None if len(values) <= period (need at least period+1 values to
    compute period differences).
    Handles divide-by-zero: avg_loss == 0 → RSI = 100.
    """
    if len(values) <= period:
        return None
    arr = np.array(values, dtype=np.float64)
    deltas = np.diff(arr)

    # Initial averages over first `period` deltas
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

    # Wilder smoothing for the remaining deltas
    alpha = 1.0 / period
    for g, loss in zip(gains[period:], losses[period:], strict=False):
        avg_gain = alpha * float(g) + (1.0 - alpha) * avg_gain
        avg_loss = alpha * float(loss) + (1.0 - alpha) * avg_loss

    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------


def macd(
    values: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[float | None, float | None, float | None]:
    """Compute MACD line, signal line, and histogram.

    Returns (None, None, None) if there is insufficient data.
    Minimum required: slow + signal values (so the signal EMA can be seeded).
    """
    min_required = slow + signal
    if len(values) < min_required:
        return None, None, None

    # We need MACD values for each point from index `slow-1` onward.
    # Compute EMA fast and EMA slow at each step, then the signal EMA.
    arr = np.array(values, dtype=np.float64)
    alpha_fast = 2.0 / (fast + 1)
    alpha_slow = 2.0 / (slow + 1)

    # Seed both EMAs on the first `slow` values
    ema_fast_val = float(np.mean(arr[:slow]))
    ema_slow_val = float(np.mean(arr[:slow]))

    macd_values: list[float] = []
    for price in arr[slow:]:
        ema_fast_val = alpha_fast * float(price) + (1.0 - alpha_fast) * ema_fast_val
        ema_slow_val = alpha_slow * float(price) + (1.0 - alpha_slow) * ema_slow_val
        macd_values.append(ema_fast_val - ema_slow_val)

    if len(macd_values) < signal:
        return None, None, None

    # Signal line = EMA of macd_values
    alpha_signal = 2.0 / (signal + 1)
    sig_val = float(np.mean(macd_values[:signal]))
    for mv in macd_values[signal:]:
        sig_val = alpha_signal * mv + (1.0 - alpha_signal) * sig_val

    macd_line = macd_values[-1]
    signal_line = sig_val
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ---------------------------------------------------------------------------
# ATR — Average True Range (Wilder's method)
# ---------------------------------------------------------------------------


def atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> float | None:
    """Compute ATR using Wilder's smoothing.

    True Range = max(high-low, |high-prev_close|, |low-prev_close|).
    Returns None if insufficient data.
    """
    n = len(closes)
    if n < period + 1:
        return None
    if len(highs) != n or len(lows) != n:
        return None

    h = np.array(highs, dtype=np.float64)
    lo = np.array(lows, dtype=np.float64)
    c = np.array(closes, dtype=np.float64)

    # True range for bars 1..n-1 (need prev close)
    tr = np.maximum(
        h[1:] - lo[1:],
        np.maximum(np.abs(h[1:] - c[:-1]), np.abs(lo[1:] - c[:-1])),
    )

    # Need at least `period` TR values
    if len(tr) < period:
        return None

    # Seed with mean of first `period` TRs
    atr_val = float(np.mean(tr[:period]))
    alpha = 1.0 / period
    for t in tr[period:]:
        atr_val = alpha * float(t) + (1.0 - alpha) * atr_val
    return atr_val


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------


def bollinger(
    values: list[float],
    period: int = 20,
    std_dev: float = 2.0,
) -> tuple[float | None, float | None, float | None]:
    """Compute Bollinger Bands (upper, mid, lower).

    Returns (None, None, None) if insufficient data.
    """
    if len(values) < period:
        return None, None, None
    arr = np.array(values[-period:], dtype=np.float64)
    mid = float(np.mean(arr))
    std = float(np.std(arr, ddof=0))
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return upper, mid, lower


# ---------------------------------------------------------------------------
# BB% (Bollinger Band Percent)
# ---------------------------------------------------------------------------


def bb_pct(close: float, upper: float, lower: float) -> float | None:
    """Compute Bollinger Band % position: (close - lower) / (upper - lower).

    Returns None if upper == lower (zero range).
    """
    band_width = upper - lower
    if band_width == 0.0:
        return None
    return (close - lower) / band_width


# ---------------------------------------------------------------------------
# VWAP — Volume-Weighted Average Price
# ---------------------------------------------------------------------------


def vwap(closes: list[float], volumes: list[float]) -> float | None:
    """Compute simple VWAP over all provided bars.

    Returns None if lengths differ, list is empty, or total volume is zero.
    """
    if len(closes) != len(volumes) or len(closes) == 0:
        return None
    c = np.array(closes, dtype=np.float64)
    v = np.array(volumes, dtype=np.float64)
    total_volume = float(np.sum(v))
    if total_volume == 0.0:
        return None
    return float(np.sum(c * v) / total_volume)


# ---------------------------------------------------------------------------
# Swing Highs / Lows
# ---------------------------------------------------------------------------


def swing_highs(highs: list[float], window: int = 5) -> list[float]:
    """Return local maximum prices over *window* bars on each side.

    A bar at index i is a swing high if highs[i] > all bars in
    [i-window, i) and (i, i+window].
    """
    if len(highs) == 0:
        return []
    h = np.array(highs, dtype=np.float64)
    result: list[float] = []
    for i in range(window, len(h) - window):
        left = h[i - window : i]
        right = h[i + 1 : i + window + 1]
        if h[i] > np.max(left) and h[i] > np.max(right):
            result.append(float(h[i]))
    return result


def swing_lows(lows: list[float], window: int = 5) -> list[float]:
    """Return local minimum prices over *window* bars on each side."""
    if len(lows) == 0:
        return []
    lo = np.array(lows, dtype=np.float64)
    result: list[float] = []
    for i in range(window, len(lo) - window):
        left = lo[i - window : i]
        right = lo[i + 1 : i + window + 1]
        if lo[i] < np.min(left) and lo[i] < np.min(right):
            result.append(float(lo[i]))
    return result
