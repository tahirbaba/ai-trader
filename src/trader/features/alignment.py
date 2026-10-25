"""Multi-timeframe trend alignment functions (Phase 3)."""

from __future__ import annotations

from trader.features.indicators import ema as _ema

_ALL_TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]


def trend_direction(
    closes: list[float],
    ema_fast: int = 20,
    ema_slow: int = 50,
) -> str:
    """Classify trend direction from a close price series.

    Returns:
        "up"      — EMA_fast > EMA_slow AND close > EMA_fast
        "down"    — EMA_fast < EMA_slow AND close < EMA_fast
        "neutral" — otherwise (insufficient data or mixed signals)
    """
    if not closes:
        return "neutral"

    fast_val = _ema(closes, ema_fast)
    slow_val = _ema(closes, ema_slow)

    if fast_val is None or slow_val is None:
        return "neutral"

    close = closes[-1]
    if fast_val > slow_val and close > fast_val:
        return "up"
    if fast_val < slow_val and close < fast_val:
        return "down"
    return "neutral"


def mtf_alignment(
    candles_by_tf: dict[str, list[float]],
) -> dict[str, str]:
    """Compute trend direction for each timeframe.

    Args:
        candles_by_tf: mapping from timeframe string to a list of close prices.
            Recognised keys: "5m", "15m", "1h", "4h", "1d".
            Missing keys default to "neutral".

    Returns:
        dict mapping each of the 5 standard timeframe keys to a direction string.
    """
    return {
        tf: trend_direction(candles_by_tf[tf]) if tf in candles_by_tf else "neutral"
        for tf in _ALL_TIMEFRAMES
    }


def htf_aligned(
    alignment: dict[str, str],
    htf_keys: list[str] | None = None,
) -> bool:
    """Return True if all htf_keys share the same non-neutral direction.

    Args:
        alignment: mapping from timeframe → direction string.
        htf_keys:  which keys to check; defaults to ["1h", "4h", "1d"].
    """
    if htf_keys is None:
        htf_keys = ["1h", "4h", "1d"]

    directions = {alignment.get(k, "neutral") for k in htf_keys}
    # All identical and not neutral
    if len(directions) == 1:
        (direction,) = directions
        return direction != "neutral"
    return False
