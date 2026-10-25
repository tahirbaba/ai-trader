"""Market regime classifier (Phase 3)."""

from __future__ import annotations

from typing import Literal

REGIMES = Literal["trend_up", "trend_down", "range", "high_vol_breakout", "news_shock"]


def classify_regime(
    close: float,
    ema_20: float | None,
    ema_50: float | None,
    ema_200: float | None,
    atr_14: float | None,
    atr_history: list[float],
    htf_aligned_up: bool,
    htf_aligned_down: bool,
    has_news_shock: bool = False,
) -> str:
    """Classify the current market regime.

    Rules applied in strict priority order:
    1. news_shock        — has_news_shock is True
    2. high_vol_breakout — ATR > 1.5 × mean(atr_history[-20:]) and price
                           breaks above/below the range midpoint
    3. trend_up          — close > ema_200 AND ema_20 > ema_50 AND htf_aligned_up
    4. trend_down        — close < ema_200 AND ema_20 < ema_50 AND htf_aligned_down
    5. range             — otherwise
    """
    # 1. News shock (highest priority)
    if has_news_shock:
        return "news_shock"

    # 2. High-volatility breakout
    if atr_14 is not None and len(atr_history) > 0:
        recent = atr_history[-20:] if len(atr_history) >= 20 else atr_history
        mean_atr = sum(recent) / len(recent)
        if mean_atr > 0 and atr_14 > 1.5 * mean_atr:
            return "high_vol_breakout"

    # 3. Trend up
    if (
        ema_200 is not None
        and ema_20 is not None
        and ema_50 is not None
        and close > ema_200
        and ema_20 > ema_50
        and htf_aligned_up
    ):
        return "trend_up"

    # 4. Trend down
    if (
        ema_200 is not None
        and ema_20 is not None
        and ema_50 is not None
        and close < ema_200
        and ema_20 < ema_50
        and htf_aligned_down
    ):
        return "trend_down"

    # 5. Default
    return "range"
