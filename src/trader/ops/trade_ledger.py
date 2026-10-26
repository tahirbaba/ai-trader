"""Phase 9: TradeLedger — writes ``trades`` rows from broker position transitions.

The position-monitor job calls ``sync`` every tick with the broker's open
positions. A new position opens a Trade row; a vanished position closes it
using the exit recorded via ``record_exit`` (SL/TP fill or force-close), or the
last mark price if no exit was recorded.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from trader.dsor.models import Trade

logger = logging.getLogger(__name__)


@dataclass
class _Open:
    trade_id: int
    side: str  # "buy" | "sell"
    entry: float
    qty: float
    sl: float | None
    decision_id: int | None


@dataclass(frozen=True)
class ClosedTrade:
    trade_id: int
    symbol: str
    exit_price: float
    pnl_usd: float
    r_multiple: float | None
    reason: str


class TradeLedger:
    def __init__(
        self,
        session_factory: Callable[[], Any],
        taker_fee: float = 0.00055,
        clock: Callable[[], datetime.datetime] | None = None,
    ) -> None:
        self._sf = session_factory
        self._fee = taker_fee
        self._clock = clock or (lambda: datetime.datetime.now(datetime.UTC))
        self._open: dict[str, _Open] = {}
        self._exits: dict[str, tuple[float, str]] = {}
        self._pending_decision: dict[str, int | None] = {}

    def open_symbols(self) -> set[str]:
        return set(self._open)

    def open_since(self, symbol: str) -> datetime.datetime | None:
        o = self._open.get(symbol)
        if o is None:
            return None
        session = self._sf()
        try:
            row = session.get(Trade, o.trade_id)
            if row is None:
                return None
            ts: datetime.datetime = row.open_ts
            return ts if ts.tzinfo else ts.replace(tzinfo=datetime.UTC)
        finally:
            session.close()

    def expect_entry(self, symbol: str, decision_id: int | None) -> None:
        self._pending_decision[symbol] = decision_id

    def record_exit(self, symbol: str, price: float, reason: str) -> None:
        if price > 0:
            self._exits[symbol] = (price, reason)

    def sync(self, positions: list[dict[str, Any]], marks: dict[str, float]) -> list[ClosedTrade]:
        current = {str(p["symbol"]): p for p in positions}
        closed: list[ClosedTrade] = []
        for symbol, pos in current.items():
            if symbol not in self._open:
                self._open_trade(symbol, pos)
        for symbol in list(self._open):
            if symbol not in current:
                c = self._close_trade(symbol, marks.get(symbol))
                if c is not None:
                    closed.append(c)
        return closed

    def _open_trade(self, symbol: str, pos: dict[str, Any]) -> None:
        side = str(pos["side"]).lower()
        side = "buy" if side in {"buy", "long"} else "sell"
        entry = float(pos["entry_price"])
        qty = float(pos["qty"])
        sl = pos.get("sl_price")
        decision_id = self._pending_decision.pop(symbol, None)
        session = self._sf()
        try:
            row = Trade(
                decision_id=decision_id,
                open_ts=self._clock(),
                symbol=symbol,
                side=side,
                entry=entry,
                qty=qty,
                fees=entry * qty * self._fee,
            )
            session.add(row)
            session.commit()
            self._open[symbol] = _Open(
                row.id, side, entry, qty, float(sl) if sl else None, decision_id
            )
        finally:
            session.close()

    def _close_trade(self, symbol: str, mark: float | None) -> ClosedTrade | None:
        o = self._open.pop(symbol)
        exit_price, reason = self._exits.pop(symbol, (mark or 0.0, "unknown"))
        if exit_price <= 0:
            logger.error("trade %s closed with no exit price; recording entry", o.trade_id)
            exit_price = o.entry
        gross = (
            (exit_price - o.entry) * o.qty if o.side == "buy" else (o.entry - exit_price) * o.qty
        )
        fees = (o.entry + exit_price) * o.qty * self._fee
        pnl = gross - fees
        risk = abs(o.entry - o.sl) * o.qty if o.sl else 0.0
        r_mult = pnl / risk if risk > 0 else None
        session = self._sf()
        try:
            row = session.get(Trade, o.trade_id)
            if row is None:
                return None
            row.close_ts = self._clock()
            row.exit = exit_price
            row.pnl_usd = pnl
            row.pnl_pct = (gross / (o.entry * o.qty) * 100) if o.entry * o.qty else None
            row.r_multiple = r_mult
            row.fees = fees
            row.exit_reason = reason
            session.commit()
        finally:
            session.close()
        return ClosedTrade(o.trade_id, symbol, exit_price, pnl, r_mult, reason)
