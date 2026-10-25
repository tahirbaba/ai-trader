"""Order-flow feature functions (Phase 3)."""

from __future__ import annotations


def cvd_divergence(
    cvd_history: list[float],
    price_history: list[float],
    window: int = 3,
) -> bool:
    """Return True if CVD and price direction disagree over the last *window* points.

    Divergence cases:
    - Bearish: price up, CVD down
    - Bullish: price down, CVD up

    Returns False if insufficient data.
    """
    if len(cvd_history) < window or len(price_history) < window:
        return False

    cvd_tail = cvd_history[-window:]
    price_tail = price_history[-window:]

    cvd_up = cvd_tail[-1] > cvd_tail[0]
    cvd_down = cvd_tail[-1] < cvd_tail[0]
    price_up = price_tail[-1] > price_tail[0]
    price_down = price_tail[-1] < price_tail[0]

    return (price_up and cvd_down) or (price_down and cvd_up)


def spread_percentile(spread_history: list[float]) -> float | None:
    """Percentile (0..100) of the latest spread value vs history.

    Returns None if fewer than 2 values are available.
    Uses linear interpolation method (same as numpy percentileofscore).
    """
    if len(spread_history) < 2:
        return None

    latest = spread_history[-1]
    history = spread_history

    n = len(history)
    count_below = sum(1 for x in history if x < latest)

    # Percentile: fraction of values strictly below latest (0-100 scale)
    # This gives 0 for the minimum and 100*(n-1)/n for the maximum.
    # At exact max all others are below → percentile = (n-1)/n * 100
    percentile = count_below / n * 100.0
    return percentile
