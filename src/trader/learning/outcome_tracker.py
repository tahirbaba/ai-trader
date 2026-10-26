"""Compute trade outcomes: R-multiple, MAE/MFE, holding_seconds, regime, session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

MISTAKE_TYPES = frozenset(
    {
        "bad_thesis",
        "bad_timing",
        "ignored_higher_timeframe",
        "ignored_news_or_calendar",
        "ignored_existing_lesson",
        "funding_oi_squeeze_missed",
        "stop_too_tight",
        "stop_too_wide",
        "overtrading",
        "revenge_trade",
        "execution_slippage",
        "data_issue",
        "prompt_injection_suspected",
        "random_variance",
    }
)

OUTCOME_CLASSES = frozenset(
    {
        "good_decision_good_outcome",
        "good_decision_bad_outcome",
        "bad_decision_good_outcome",
        "bad_decision_bad_outcome",
    }
)


@dataclass
class TradeOutcome:
    trade_id: int
    symbol: str
    side: str
    r_multiple: float
    holding_seconds: float
    regime: str | None
    session: str | None
    pnl_usd: float
    mae: float | None
    mfe: float | None


def _parse_ts(ts: Any) -> datetime:
    """Accept a datetime or ISO string and return a timezone-aware datetime."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=UTC)
        return ts
    # Assume ISO string
    dt = datetime.fromisoformat(str(ts))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def compute_outcome(trade_row: dict[str, Any]) -> TradeOutcome:
    """Compute a TradeOutcome from a flat trade dict.

    Expected keys: id, symbol, side, entry_price, exit_price, qty, sl_price,
                   open_ts, close_ts, regime, session.
    Optional keys: mae, mfe.
    """
    trade_id: int = int(trade_row["id"])
    symbol: str = str(trade_row["symbol"])
    side: str = str(trade_row["side"]).lower()

    entry_price: float = float(trade_row["entry_price"])
    exit_price: float = float(trade_row["exit_price"])
    qty: float = float(trade_row["qty"])
    sl_price_raw = trade_row.get("sl_price")

    # R-multiple calculation
    if sl_price_raw is None or sl_price_raw == 0:
        r_multiple: float = 0.0
    else:
        sl_price = float(sl_price_raw)
        if side == "long":
            risk = entry_price - sl_price
            r_multiple = 0.0 if risk == 0 else (exit_price - entry_price) / risk
        else:  # short
            risk = sl_price - entry_price
            r_multiple = 0.0 if risk == 0 else (entry_price - exit_price) / risk

    # Holding seconds
    open_ts = _parse_ts(trade_row["open_ts"])
    close_ts = _parse_ts(trade_row["close_ts"])
    holding_seconds: float = (close_ts - open_ts).total_seconds()

    # PnL
    multiplier = 1.0 if side == "long" else -1.0
    pnl_usd: float = (exit_price - entry_price) * qty * multiplier

    regime: str | None = trade_row.get("regime") or None
    session: str | None = trade_row.get("session") or None
    mae: float | None = _to_float_or_none(trade_row.get("mae"))
    mfe: float | None = _to_float_or_none(trade_row.get("mfe"))

    return TradeOutcome(
        trade_id=trade_id,
        symbol=symbol,
        side=side,
        r_multiple=r_multiple,
        holding_seconds=holding_seconds,
        regime=regime,
        session=session,
        pnl_usd=pnl_usd,
        mae=mae,
        mfe=mfe,
    )


def _to_float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
