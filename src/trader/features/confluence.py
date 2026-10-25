"""Confluence score aggregator (Phase 3).

Six independence groups each contribute at most 1 point toward the specified
side.  The total is an integer in [0, 6].
"""

from __future__ import annotations

from typing import Literal


def confluence_score(
    side: Literal["long", "short"],
    htf_aligned: bool,
    htf_direction: str,
    ema_cross: str,
    rsi: float | None,
    macd_hist: float | None,
    funding_z: float | None,
    oi_divergence: bool,
    cvd_divergence_bear: bool,
    cvd_divergence_bull: bool,
    book_imbalance: float | None,
    regime: str,
    dist_to_support_atr: float | None,
    dist_to_resistance_atr: float | None,
) -> int:
    """Return the number of groups (0–6) that vote for *side*.

    Groups
    ------
    trend       : HTF aligned + EMA cross both agree with side
    momentum    : RSI + MACD histogram agree with side
    derivatives : funding_z signals the given side
    orderflow   : CVD divergence absent + book imbalance favours side
    level       : nearest S/R within 1 ATR
    regime      : current regime matches side (high_vol_breakout → both)
    """
    score = 0

    if side == "long":
        # trend
        if htf_aligned and htf_direction == "up" and ema_cross == "up":
            score += 1
        # momentum
        if rsi is not None and rsi > 50 and macd_hist is not None and macd_hist > 0:
            score += 1
        # derivatives — shorts paying (funding_z < -1.5) → bullish
        if funding_z is not None and funding_z < -1.5:
            score += 1
        # orderflow — no bearish CVD divergence AND positive book imbalance
        if not cvd_divergence_bear and book_imbalance is not None and book_imbalance > 0.1:
            score += 1
        # level — near support
        if dist_to_support_atr is not None and dist_to_support_atr < 1.0:
            score += 1
        # regime
        if regime in ("trend_up", "high_vol_breakout"):
            score += 1

    else:  # short
        # trend
        if htf_aligned and htf_direction == "down" and ema_cross == "down":
            score += 1
        # momentum
        if rsi is not None and rsi < 50 and macd_hist is not None and macd_hist < 0:
            score += 1
        # derivatives — longs paying (funding_z > 1.5) → bearish
        if funding_z is not None and funding_z > 1.5:
            score += 1
        # orderflow — no bullish CVD divergence AND negative book imbalance
        if not cvd_divergence_bull and book_imbalance is not None and book_imbalance < -0.1:
            score += 1
        # level — near resistance
        if dist_to_resistance_atr is not None and dist_to_resistance_atr < 1.0:
            score += 1
        # regime
        if regime in ("trend_down", "high_vol_breakout"):
            score += 1

    return score


def confluence_score_both(
    htf_aligned: bool,
    htf_direction: str,
    ema_cross: str,
    rsi: float | None,
    macd_hist: float | None,
    funding_z: float | None,
    oi_divergence: bool,
    cvd_divergence_bear: bool,
    cvd_divergence_bull: bool,
    book_imbalance: float | None,
    regime: str,
    dist_to_support_atr: float | None,
    dist_to_resistance_atr: float | None,
) -> tuple[int, int]:
    """Compute (score_long, score_short) in one call."""
    return (
        confluence_score(
            side="long",
            htf_aligned=htf_aligned,
            htf_direction=htf_direction,
            ema_cross=ema_cross,
            rsi=rsi,
            macd_hist=macd_hist,
            funding_z=funding_z,
            oi_divergence=oi_divergence,
            cvd_divergence_bear=cvd_divergence_bear,
            cvd_divergence_bull=cvd_divergence_bull,
            book_imbalance=book_imbalance,
            regime=regime,
            dist_to_support_atr=dist_to_support_atr,
            dist_to_resistance_atr=dist_to_resistance_atr,
        ),
        confluence_score(
            side="short",
            htf_aligned=htf_aligned,
            htf_direction=htf_direction,
            ema_cross=ema_cross,
            rsi=rsi,
            macd_hist=macd_hist,
            funding_z=funding_z,
            oi_divergence=oi_divergence,
            cvd_divergence_bear=cvd_divergence_bear,
            cvd_divergence_bull=cvd_divergence_bull,
            book_imbalance=book_imbalance,
            regime=regime,
            dist_to_support_atr=dist_to_support_atr,
            dist_to_resistance_atr=dist_to_resistance_atr,
        ),
    )
