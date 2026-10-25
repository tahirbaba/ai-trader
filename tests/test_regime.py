"""Tests for market regime classifier (Phase 3)."""

from __future__ import annotations

from trader.features.regime import classify_regime


def _flat_atr_history(val: float = 1.0, n: int = 20) -> list[float]:
    return [val] * n


# ---------------------------------------------------------------------------
# Basic regime classifications
# ---------------------------------------------------------------------------


def test_classify_trend_up() -> None:
    """Close > EMA200, EMA20 > EMA50, htf_aligned_up → 'trend_up'."""
    result = classify_regime(
        close=250.0,
        ema_20=210.0,
        ema_50=200.0,
        ema_200=180.0,
        atr_14=2.0,
        atr_history=_flat_atr_history(2.0),
        htf_aligned_up=True,
        htf_aligned_down=False,
        has_news_shock=False,
    )
    assert result == "trend_up"


def test_classify_trend_down() -> None:
    """Close < EMA200, EMA20 < EMA50, htf_aligned_down → 'trend_down'."""
    result = classify_regime(
        close=150.0,
        ema_20=170.0,
        ema_50=180.0,
        ema_200=200.0,
        atr_14=2.0,
        atr_history=_flat_atr_history(2.0),
        htf_aligned_up=False,
        htf_aligned_down=True,
        has_news_shock=False,
    )
    assert result == "trend_down"


def test_classify_range() -> None:
    """No strong trend or breakout → 'range'."""
    result = classify_regime(
        close=200.0,
        ema_20=200.0,
        ema_50=200.0,
        ema_200=200.0,
        atr_14=2.0,
        atr_history=_flat_atr_history(2.0),
        htf_aligned_up=False,
        htf_aligned_down=False,
        has_news_shock=False,
    )
    assert result == "range"


def test_classify_news_shock() -> None:
    """has_news_shock=True → 'news_shock' regardless of other conditions."""
    result = classify_regime(
        close=250.0,
        ema_20=210.0,
        ema_50=200.0,
        ema_200=180.0,
        atr_14=2.0,
        atr_history=_flat_atr_history(2.0),
        htf_aligned_up=True,
        htf_aligned_down=False,
        has_news_shock=True,
    )
    assert result == "news_shock"


def test_classify_high_vol_breakout() -> None:
    """ATR >> history mean → 'high_vol_breakout'."""
    # ATR is 5x the history mean → well above 1.5x threshold
    history = [1.0] * 20
    result = classify_regime(
        close=250.0,
        ema_20=210.0,
        ema_50=200.0,
        ema_200=180.0,
        atr_14=5.0,  # 5x the mean of 1.0
        atr_history=history,
        htf_aligned_up=True,
        htf_aligned_down=False,
        has_news_shock=False,
    )
    assert result == "high_vol_breakout"


def test_news_shock_overrides_breakout() -> None:
    """news_shock has higher priority than high_vol_breakout."""
    result = classify_regime(
        close=250.0,
        ema_20=210.0,
        ema_50=200.0,
        ema_200=180.0,
        atr_14=10.0,
        atr_history=_flat_atr_history(1.0),
        htf_aligned_up=True,
        htf_aligned_down=False,
        has_news_shock=True,
    )
    assert result == "news_shock"


def test_classify_none_ema_values() -> None:
    """None EMAs → no trend_up/trend_down → 'range'."""
    result = classify_regime(
        close=200.0,
        ema_20=None,
        ema_50=None,
        ema_200=None,
        atr_14=2.0,
        atr_history=_flat_atr_history(2.0),
        htf_aligned_up=False,
        htf_aligned_down=False,
        has_news_shock=False,
    )
    assert result == "range"
