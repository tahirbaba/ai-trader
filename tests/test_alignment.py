"""Tests for multi-timeframe alignment (Phase 3)."""

from __future__ import annotations

from trader.features.alignment import htf_aligned, mtf_alignment, trend_direction


def _rising(n: int = 60, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start + i * step for i in range(n)]


def _falling(n: int = 60, start: float = 200.0, step: float = 1.0) -> list[float]:
    return [start - i * step for i in range(n)]


def _flat(n: int = 60, val: float = 100.0) -> list[float]:
    return [val] * n


# ---------------------------------------------------------------------------
# trend_direction
# ---------------------------------------------------------------------------


def test_trend_up_when_ema20_above_ema50() -> None:
    """Strongly rising series → 'up'."""
    result = trend_direction(_rising(60), ema_fast=20, ema_slow=50)
    assert result == "up"


def test_trend_down_when_ema20_below_ema50() -> None:
    """Strongly falling series → 'down'."""
    result = trend_direction(_falling(60), ema_fast=20, ema_slow=50)
    assert result == "down"


def test_neutral_when_flat() -> None:
    """Constant price → 'neutral' (EMAs are equal)."""
    result = trend_direction(_flat(60), ema_fast=20, ema_slow=50)
    assert result == "neutral"


def test_trend_insufficient_data() -> None:
    """Too few candles → 'neutral' (no exception)."""
    result = trend_direction([100.0] * 5, ema_fast=20, ema_slow=50)
    assert result == "neutral"


# ---------------------------------------------------------------------------
# mtf_alignment
# ---------------------------------------------------------------------------


def test_mtf_alignment_returns_all_keys() -> None:
    """Returns dict with all 5 timeframe keys."""
    candles = {
        "5m": _rising(60),
        "15m": _rising(60),
        "1h": _rising(60),
        "4h": _rising(60),
        "1d": _rising(60),
    }
    result = mtf_alignment(candles)
    assert set(result.keys()) == {"5m", "15m", "1h", "4h", "1d"}


def test_mtf_alignment_all_rising() -> None:
    """All rising candle series → all 'up'."""
    candles = {tf: _rising(60) for tf in ["5m", "15m", "1h", "4h", "1d"]}
    result = mtf_alignment(candles)
    for tf in ["5m", "15m", "1h", "4h", "1d"]:
        assert result[tf] == "up", f"Expected 'up' for {tf}"


def test_mtf_alignment_missing_tf() -> None:
    """Missing timeframes default to 'neutral'."""
    candles = {"1h": _rising(60)}
    result = mtf_alignment(candles)
    assert result.get("5m", "neutral") == "neutral"


# ---------------------------------------------------------------------------
# htf_aligned
# ---------------------------------------------------------------------------


def test_htf_aligned_true_when_all_up() -> None:
    """All HTF keys 'up' → True."""
    alignment = {"5m": "up", "15m": "up", "1h": "up", "4h": "up", "1d": "up"}
    assert htf_aligned(alignment, htf_keys=["1h", "4h", "1d"]) is True


def test_htf_aligned_false_when_mixed() -> None:
    """Mixed HTF directions → False."""
    alignment = {"1h": "up", "4h": "down", "1d": "up"}
    assert htf_aligned(alignment, htf_keys=["1h", "4h", "1d"]) is False


def test_htf_aligned_false_when_neutral() -> None:
    """Neutral direction does not count as aligned."""
    alignment = {"1h": "neutral", "4h": "neutral", "1d": "neutral"}
    assert htf_aligned(alignment, htf_keys=["1h", "4h", "1d"]) is False


def test_htf_aligned_all_down() -> None:
    """All HTF 'down' is also aligned."""
    alignment = {"1h": "down", "4h": "down", "1d": "down"}
    assert htf_aligned(alignment, htf_keys=["1h", "4h", "1d"]) is True
