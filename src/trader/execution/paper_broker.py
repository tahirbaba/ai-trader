"""Phase 7: PaperBroker — realistic in-memory paper trading simulation."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from trader.execution.adapter import ExchangeAdapter
from trader.execution.exceptions import DuplicateOrderError


class PaperBroker(ExchangeAdapter):
    """Realistic paper trading simulation.

    - Fees: configurable maker/taker rates.
    - Fill model: limit orders fill at price (or better) on mark price touch;
      market orders fill at mark_price * (1 + slippage) for buys,
      * (1 - slippage) for sells.
    - SL/TP triggers: checked on every mark_price update (call update_mark_price).
    - Funding: applied every 8h based on funding_rate passed to update_funding.
    - State: in-memory; injectable clock for deterministic tests.
    """

    def __init__(
        self,
        initial_balance: float = 10_000.0,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.00055,
        slippage_pct: float = 0.0005,
        clock_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._initial_balance = initial_balance
        self._maker_fee = maker_fee
        self._taker_fee = taker_fee
        self._slippage_pct = slippage_pct
        self._clock_fn: Callable[[], datetime] = clock_fn or (lambda: datetime.now(UTC))

        # Balance tracking
        self._cash: float = initial_balance

        # Per-symbol mark prices
        self._mark_prices: dict[str, float] = {}

        # Open positions: symbol -> position dict
        self._positions: dict[str, dict[str, Any]] = {}

        # Pending limit orders: order_id -> order dict
        self._pending_orders: dict[str, dict[str, Any]] = {}

        # Used client_order_ids for idempotency check
        self._used_client_ids: set[str] = set()

        # All orders (for get_open_orders and cancel)
        self._orders: dict[str, dict[str, Any]] = {}

        # Funding accumulated per symbol
        self._funding_paid: dict[str, float] = {}

        # Fill history
        self._fills: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # ExchangeAdapter interface
    # ------------------------------------------------------------------

    def get_balance(self) -> dict[str, float]:
        """Return equity and available balance.

        equity = free_cash + margin_in_positions + unrealised_pnl
        available = free_cash (margin is already deducted from cash at fill time)
        """
        unrealised = 0.0
        margin_used = 0.0
        for sym, pos in self._positions.items():
            mark = self._mark_prices.get(sym, pos["entry_price"])
            if pos["side"] == "buy":
                unrealised += (mark - pos["entry_price"]) * pos["qty"]
            else:
                unrealised += (pos["entry_price"] - mark) * pos["qty"]
            margin_used += pos["margin"]
        # cash already had margin deducted at fill time; add it back for equity
        equity = self._cash + margin_used + unrealised
        available = self._cash  # margin already deducted from cash
        return {"equity_usd": equity, "available_usd": max(available, 0.0)}

    def get_positions(self) -> list[dict[str, Any]]:
        """Return all open positions."""
        result = []
        for sym, pos in self._positions.items():
            mark = self._mark_prices.get(sym, pos["entry_price"])
            if pos["side"] == "buy":
                upnl = (mark - pos["entry_price"]) * pos["qty"]
            else:
                upnl = (pos["entry_price"] - mark) * pos["qty"]
            result.append(
                {
                    "symbol": sym,
                    "side": pos["side"],
                    "qty": pos["qty"],
                    "entry_price": pos["entry_price"],
                    "unrealised_pnl": upnl,
                    "sl_price": pos.get("sl_price"),
                }
            )
        return result

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Return pending limit orders for a symbol."""
        return [
            o
            for o in self._pending_orders.values()
            if o["symbol"] == symbol and o["status"] == "open"
        ]

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str,
        price: float | None = None,
        reduce_only: bool = False,
        client_order_id: str | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        ttl_ts: datetime | None = None,
    ) -> dict[str, Any]:
        """Place a market or limit order.

        Parameters
        ----------
        ttl_ts:
            Optional expiry time for limit orders. Not part of the abstract
            interface but accepted by PaperBroker for test convenience.
        """
        # Idempotency check
        if client_order_id is not None and client_order_id in self._used_client_ids:
            raise DuplicateOrderError(f"client_order_id already used: {client_order_id}")

        order_id = str(uuid.uuid4())
        if client_order_id is None:
            client_order_id = order_id

        self._used_client_ids.add(client_order_id)

        mark = self._mark_prices.get(symbol, price or 0.0)

        if order_type == "market":
            # Fill immediately with slippage
            fill_price = self._apply_slippage(mark, side)
            fee = fill_price * qty * self._taker_fee
            fill = {
                "order_id": order_id,
                "client_order_id": client_order_id,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": fill_price,
                "fee": fee,
                "ts": self._clock_fn(),
            }
            self._fills.append(fill)

            order = {
                "order_id": order_id,
                "client_order_id": client_order_id,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "order_type": order_type,
                "price": price,
                "status": "filled",
                "filled_qty": qty,
                "avg_price": fill_price,
                "reduce_only": reduce_only,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "ttl_ts": ttl_ts,
            }
            self._orders[order_id] = order

            # Update positions
            self._apply_fill(symbol, side, qty, fill_price, fee, reduce_only)

            # Attach SL/TP to position if provided
            if not reduce_only and symbol in self._positions:
                pos = self._positions[symbol]
                if stop_loss is not None:
                    pos["sl_price"] = stop_loss
                if take_profit is not None:
                    pos["tp_price"] = take_profit

            return {
                "order_id": order_id,
                "client_order_id": client_order_id,
                "status": "filled",
                "filled_qty": qty,
                "avg_price": fill_price,
            }

        else:  # limit order
            if price is None:
                raise ValueError("price is required for limit orders")

            order = {
                "order_id": order_id,
                "client_order_id": client_order_id,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "order_type": order_type,
                "price": price,
                "status": "open",
                "filled_qty": 0.0,
                "avg_price": 0.0,
                "reduce_only": reduce_only,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "ttl_ts": ttl_ts,
            }
            self._orders[order_id] = order
            self._pending_orders[order_id] = order

            return {
                "order_id": order_id,
                "client_order_id": client_order_id,
                "status": "open",
                "filled_qty": 0.0,
                "avg_price": 0.0,
            }

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel a pending limit order."""
        if order_id in self._pending_orders:
            self._pending_orders[order_id]["status"] = "cancelled"
            del self._pending_orders[order_id]
            if order_id in self._orders:
                self._orders[order_id]["status"] = "cancelled"
            return True
        return False

    def set_leverage(self, symbol: str, leverage: int) -> None:
        """No-op for paper broker."""

    def set_margin_mode(self, symbol: str, mode: str) -> None:
        """No-op for paper broker."""

    def set_position_mode(self, mode: str) -> None:
        """No-op for paper broker."""

    def get_market_meta(self, symbol: str) -> dict[str, Any]:
        """Return default market metadata."""
        return {
            "lot_size": 1.0,
            "min_qty": 1.0,
            "tick_size": 0.01,
            "maker_fee": self._maker_fee,
            "taker_fee": self._taker_fee,
            "max_leverage": 10,
        }

    def get_server_time(self) -> int:
        """Return current epoch ms from injected clock."""
        dt = self._clock_fn()
        return int(dt.timestamp() * 1000)

    def ping(self) -> bool:
        """Always true for paper broker."""
        return True

    def amend_stop_loss(self, symbol: str, new_sl: float) -> bool:
        """Amend the stop-loss on an open position in paper trading.

        Never widens: if the new SL would widen the existing SL, returns False.
        Verification is implicit since state is in-memory.
        """
        if symbol not in self._positions:
            return False
        if new_sl <= 0:
            return False
        pos = self._positions[symbol]
        current_sl = pos.get("sl_price")
        if current_sl is None:
            pos["sl_price"] = new_sl
            return True
        side = pos["side"].lower()
        is_long = side in {"buy", "long"}
        if is_long and new_sl <= current_sl:
            return False  # would widen (long SL should only move up)
        if not is_long and new_sl >= current_sl:
            return False  # would widen (short SL should only move down)
        pos["sl_price"] = new_sl
        return True

    # ------------------------------------------------------------------
    # Paper-specific methods
    # ------------------------------------------------------------------

    def update_mark_price(self, symbol: str, price: float) -> list[dict[str, Any]]:
        """Feed a new mark price. Triggers SL/TP/TTL checks.

        Returns list of fill events generated.
        """
        self._mark_prices[symbol] = price
        generated_fills: list[dict[str, Any]] = []

        # Update MAE/MFE for open positions
        if symbol in self._positions:
            pos = self._positions[symbol]
            entry = pos["entry_price"]
            if pos["side"] == "buy":
                adverse = entry - price  # loss direction
                favorable = price - entry
            else:
                adverse = price - entry
                favorable = entry - price

            if adverse > pos.get("max_adverse_excursion", 0.0):
                pos["max_adverse_excursion"] = adverse
            if favorable > pos.get("max_favorable_excursion", 0.0):
                pos["max_favorable_excursion"] = favorable

            # Check SL trigger
            sl = pos.get("sl_price")
            if sl is not None:
                triggered = False
                if pos["side"] == "buy" and price <= sl or pos["side"] == "sell" and price >= sl:
                    triggered = True

                if triggered:
                    fill = self._close_position(symbol, sl, "sl")
                    if fill:
                        generated_fills.append(fill)

            # Check TP trigger (only if position still open)
            if symbol in self._positions:
                tp = pos.get("tp_price")
                if tp is not None:
                    triggered = False
                    if (
                        pos["side"] == "buy"
                        and price >= tp
                        or pos["side"] == "sell"
                        and price <= tp
                    ):
                        triggered = True

                    if triggered:
                        fill = self._close_position(symbol, tp, "tp")
                        if fill:
                            generated_fills.append(fill)

        # Check TTL expiry on pending limit orders for this symbol
        now = self._clock_fn()
        expired = [
            oid
            for oid, o in list(self._pending_orders.items())
            if o["symbol"] == symbol and o.get("ttl_ts") is not None and now > o["ttl_ts"]
        ]
        for oid in expired:
            self._pending_orders[oid]["status"] = "expired"
            self._orders[oid]["status"] = "expired"
            del self._pending_orders[oid]

        # Check limit order fills
        to_fill = []
        for oid, order in list(self._pending_orders.items()):
            if order["symbol"] != symbol:
                continue
            lp = order["price"]
            side = order["side"]
            # buy limit fills when mark_price <= limit price
            # sell limit fills when mark_price >= limit price
            if side == "buy" and price <= lp or side == "sell" and price >= lp:
                to_fill.append(oid)

        for oid in to_fill:
            _pending = self._pending_orders.get(oid)
            if _pending is None:
                continue
            fill_order: dict[str, Any] = _pending
            fill_price = fill_order["price"]
            qty = fill_order["qty"]
            fee = fill_price * qty * self._maker_fee
            fill = {
                "order_id": oid,
                "client_order_id": fill_order["client_order_id"],
                "symbol": symbol,
                "side": fill_order["side"],
                "qty": qty,
                "price": fill_price,
                "fee": fee,
                "ts": self._clock_fn(),
            }
            self._fills.append(fill)
            fill_order["status"] = "filled"
            fill_order["filled_qty"] = qty
            fill_order["avg_price"] = fill_price
            del self._pending_orders[oid]

            self._apply_fill(
                symbol,
                fill_order["side"],
                qty,
                fill_price,
                fee,
                fill_order.get("reduce_only", False),
            )

            # Attach SL/TP to position if provided on the limit order
            if not fill_order.get("reduce_only") and symbol in self._positions:
                pos = self._positions[symbol]
                if fill_order.get("stop_loss") is not None:
                    pos["sl_price"] = fill_order["stop_loss"]
                if fill_order.get("take_profit") is not None:
                    pos["tp_price"] = fill_order["take_profit"]

            generated_fills.append(fill)

        return generated_fills

    def update_funding(self, symbol: str, funding_rate: float) -> float:
        """Apply funding payment. Returns funding amount (positive = paid by long)."""
        pos = self._positions.get(symbol)
        if pos is None:
            return 0.0

        mark: float = self._mark_prices.get(symbol, float(pos["entry_price"]))
        notional: float = mark * float(pos["qty"])

        funding_amount: float
        if pos["side"] == "buy":
            funding_amount = notional * funding_rate
        else:
            funding_amount = -notional * funding_rate

        # Deduct from cash (long pays when rate > 0)
        self._cash -= funding_amount
        self._funding_paid[symbol] = self._funding_paid.get(symbol, 0.0) + funding_amount
        pos["funding_paid"] = float(pos.get("funding_paid", 0.0)) + funding_amount

        return funding_amount

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_slippage(self, mark: float, side: str) -> float:
        """Apply slippage to fill price."""
        if side == "buy":
            return mark * (1.0 + self._slippage_pct)
        else:
            return mark * (1.0 - self._slippage_pct)

    def _apply_fill(
        self,
        symbol: str,
        side: str,
        qty: float,
        fill_price: float,
        fee: float,
        reduce_only: bool,
    ) -> None:
        """Update position state after a fill."""
        if reduce_only:
            # Close existing position
            pos = self._positions.get(symbol)
            if pos is not None:
                entry = pos["entry_price"]
                pos_qty = pos["qty"]
                pos_side = pos["side"]

                if pos_side == "buy":
                    pnl = (fill_price - entry) * min(qty, pos_qty)
                else:
                    pnl = (entry - fill_price) * min(qty, pos_qty)

                pnl -= fee
                self._cash += pnl + pos["margin"] * (min(qty, pos_qty) / pos_qty)

                remaining = pos_qty - qty
                if remaining <= 1e-9:
                    del self._positions[symbol]
                else:
                    pos["qty"] = remaining
                    pos["margin"] = pos["margin"] * (remaining / pos_qty)
            return

        existing = self._positions.get(symbol)
        if existing is None:
            # Open new position
            margin = fill_price * qty / 10.0  # default 10x leverage for paper
            self._cash -= margin + fee
            self._positions[symbol] = {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "entry_price": fill_price,
                "margin": margin,
                "sl_price": None,
                "tp_price": None,
                "open_ts": self._clock_fn(),
                "max_adverse_excursion": 0.0,
                "max_favorable_excursion": 0.0,
                "funding_paid": 0.0,
            }
        else:
            # Add to existing position (same side)
            old_qty = existing["qty"]
            new_qty = old_qty + qty
            existing["entry_price"] = (
                existing["entry_price"] * old_qty + fill_price * qty
            ) / new_qty
            existing["qty"] = new_qty
            margin = fill_price * qty / 10.0
            existing["margin"] += margin
            self._cash -= margin + fee

    def _close_position(self, symbol: str, exit_price: float, reason: str) -> dict[str, Any] | None:
        """Close position at exit_price and return fill dict."""
        pos = self._positions.get(symbol)
        if pos is None:
            return None

        qty = pos["qty"]
        entry = pos["entry_price"]
        side = pos["side"]
        fee = exit_price * qty * self._taker_fee

        pnl = (exit_price - entry) * qty if side == "buy" else (entry - exit_price) * qty
        pnl -= fee
        self._cash += pnl + pos["margin"]

        fill: dict[str, Any] = {
            "order_id": str(uuid.uuid4()),
            "client_order_id": f"auto-{reason}-{symbol}",
            "symbol": symbol,
            "side": "sell" if side == "buy" else "buy",
            "qty": qty,
            "price": exit_price,
            "fee": fee,
            "reason": reason,
            "ts": self._clock_fn(),
        }
        self._fills.append(fill)
        del self._positions[symbol]

        return fill
