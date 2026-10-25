"""Derivatives-specific feature functions (Phase 3)."""

from __future__ import annotations

import math


def _zscore(history: list[float]) -> float | None:
    """Compute the z-score of the last value vs the full history.

    Returns None if len(history) < 2.
    Returns 0.0 if std == 0 (all values equal).
    """
    if len(history) < 2:
        return None
    n = len(history)
    mean = sum(history) / n
    variance = sum((x - mean) ** 2 for x in history) / n
    if variance == 0.0:
        return 0.0
    std = math.sqrt(variance)
    return (history[-1] - mean) / std


def funding_zscore(funding_history: list[float]) -> float | None:
    """Z-score of the latest funding rate vs history.

    Returns None if fewer than 2 values are available.
    """
    return _zscore(funding_history)


def oi_price_divergence(
    oi_history: list[float],
    price_history: list[float],
    window: int = 3,
) -> bool:
    """Return True if OI and price direction disagree over the last *window* points.

    Divergence cases:
    - Bearish: price up, OI down
    - Bullish: price down, OI up

    Returns False if insufficient data.
    """
    if len(oi_history) < window or len(price_history) < window:
        return False

    oi_tail = oi_history[-window:]
    price_tail = price_history[-window:]

    oi_up = oi_tail[-1] > oi_tail[0]
    oi_down = oi_tail[-1] < oi_tail[0]
    price_up = price_tail[-1] > price_tail[0]
    price_down = price_tail[-1] < price_tail[0]

    # Divergence: one goes up, the other goes down
    return (price_up and oi_down) or (price_down and oi_up)


def basis_zscore(basis_history: list[float]) -> float | None:
    """Z-score of the latest basis value vs history.

    Returns None if fewer than 2 values are available.
    """
    return _zscore(basis_history)
