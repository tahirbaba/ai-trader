"""Phase 9: Shadow outcomes for Risk-Gate-blocked decisions.

A blocked decision that still carried entry/SL/TP is replayed against the
candles that followed it: whichever of SL or TP is touched first decides the
hypothetical R. Same-candle touch of both counts as SL (conservative).
"""

from __future__ import annotations

from typing import Any


def shadow_outcome(
    side: str,
    entry: float,
    stop: float,
    tp: float,
    candles_after: list[dict[str, Any]],
) -> tuple[str, float] | None:
    """Return (outcome, r_multiple) or None if neither level was reached yet."""
    risk = abs(entry - stop)
    if risk <= 0 or entry <= 0 or stop <= 0 or tp <= 0:
        return None
    reward_r = abs(tp - entry) / risk
    is_long = side.lower() in {"long", "buy"}
    for c in candles_after:
        high, low = float(c["high"]), float(c["low"])
        hit_sl = low <= stop if is_long else high >= stop
        hit_tp = high >= tp if is_long else low <= tp
        if hit_sl:
            return ("loss", -1.0)
        if hit_tp:
            return ("win", reward_r)
    return None
