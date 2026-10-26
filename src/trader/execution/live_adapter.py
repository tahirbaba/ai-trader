"""Phase 7: LiveAdapter — ccxt-based Bybit adapter.

DISABLED unless config/live_approval.yaml exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from trader.config.settings import Settings
from trader.execution.adapter import ExchangeAdapter
from trader.execution.exceptions import (
    AdapterError,
    DuplicateOrderError,
    LiveNotApprovedError,
    SlPlacementError,
)

if TYPE_CHECKING:
    import ccxt as ccxt_module  # pragma: no cover

_LIVE_APPROVAL = Path("config/live_approval.yaml")


class LiveAdapter(ExchangeAdapter):
    """Live exchange adapter using ccxt.

    DISABLED unless config/live_approval.yaml exists.
    Raises LiveNotApprovedError on construction if file absent.
    All methods respect ccxt rate limiting.
    """

    def __init__(
        self,
        settings: Settings,
        approval_path: Path | None = None,
    ) -> None:
        approval = approval_path if approval_path is not None else _LIVE_APPROVAL
        if not approval.exists():
            raise LiveNotApprovedError(
                "config/live_approval.yaml missing — live trading not approved"
            )

        if getattr(settings, "trading_mode", "paper") != "live":
            raise LiveNotApprovedError(
                "trading_mode is not 'live' — LiveAdapter requires explicit live mode"
            )

        # Import ccxt lazily so tests don't require it installed
        import ccxt  # noqa: PLC0415

        self._exchange = cast(
            "ccxt_module.bybit",
            ccxt.bybit(
                {
                    "apiKey": getattr(settings, "exchange_api_key", ""),
                    "secret": getattr(settings, "exchange_api_secret", ""),
                    "options": {"defaultType": "linear"},
                    "enableRateLimit": True,
                    "timeout": 30000,
                }
            ),
        )
        self._exchange.load_markets()
        self._settings = settings

    def get_balance(self) -> dict[str, float]:
        """Fetch balance from exchange."""
        try:
            bal = self._exchange.fetch_balance()
            usdt = bal.get("USDT", {})
            return {
                "equity_usd": float(usdt.get("total", 0.0)),
                "available_usd": float(usdt.get("free", 0.0)),
            }
        except Exception as exc:
            raise AdapterError(f"get_balance failed: {exc}") from exc

    def get_positions(self) -> list[dict[str, Any]]:
        """Fetch open positions from exchange."""
        try:
            positions = self._exchange.fetch_positions()
            result = []
            for p in positions:
                if not p.get("contracts"):  # covers both 0 and None
                    continue
                result.append(
                    {
                        "symbol": p["symbol"],
                        "side": p["side"],
                        "qty": float(p.get("contracts", 0)),
                        "entry_price": float(p.get("entryPrice") or 0),
                        "unrealised_pnl": float(p.get("unrealizedPnl") or 0),
                        "sl_price": float(p["stopLoss"]) if p.get("stopLoss") else None,
                    }
                )
            return result
        except Exception as exc:
            raise AdapterError(f"get_positions failed: {exc}") from exc

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Fetch open orders for a symbol."""
        try:
            orders = self._exchange.fetch_open_orders(symbol)
            return [
                {
                    "order_id": o["id"],
                    "client_order_id": o.get("clientOrderId", ""),
                    "symbol": o["symbol"],
                    "side": o["side"],
                    "qty": float(o["amount"]),
                    "price": o.get("price"),
                    "status": o["status"],
                }
                for o in orders
            ]
        except Exception as exc:
            raise AdapterError(f"get_open_orders failed: {exc}") from exc

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
    ) -> dict[str, Any]:
        """Place an order on the exchange.

        Attempts to attach SL/TP via params. If that fails, places separate
        stop_market order. If that also fails → cancel entry + raise SlPlacementError.
        """
        params: dict[str, Any] = {}
        if reduce_only:
            params["reduceOnly"] = True
        if client_order_id:
            params["clientOrderId"] = client_order_id

        sl_tp_params: dict[str, Any] = {}
        if stop_loss is not None:
            sl_tp_params["stopLoss"] = stop_loss
        if take_profit is not None:
            sl_tp_params["takeProfit"] = take_profit

        # Try placing with attached SL/TP
        try:
            order = self._exchange.create_order(
                symbol,
                order_type,
                side,
                qty,
                price,
                {**params, **sl_tp_params},
            )
        except Exception as exc:
            raise AdapterError(f"place_order failed: {exc}") from exc

        exchange_order_id: str = order["id"]

        # Determine whether the entry order is already filled.
        entry_status = order.get("status", "open")
        entry_filled = float(order.get("filled", 0.0) or 0.0)
        entry_is_filled = entry_status in {"closed", "filled"} or entry_filled > 0

        # If SL was requested but not confirmed in response, place as separate stop order.
        # Covers both open (limit) and filled/closed (market) entry states.
        sl_confirmed = bool(order.get("stopLossPrice") or order.get("stopLoss"))
        if stop_loss is not None and not sl_confirmed:
            sl_side = "sell" if side == "buy" else "buy"
            try:
                self._exchange.create_order(
                    symbol,
                    "stop_market",
                    sl_side,
                    qty,
                    None,
                    {"stopPrice": stop_loss, "reduceOnly": True},
                )
            except Exception as sl_exc:
                close_failed = False
                if entry_is_filled:
                    # Entry already filled — cancel does nothing; place a market close.
                    close_side = "sell" if side == "buy" else "buy"
                    try:
                        self._exchange.create_order(
                            symbol,
                            "market",
                            close_side,
                            qty,
                            None,
                            {"reduceOnly": True},
                        )
                    except Exception:
                        close_failed = True
                else:
                    # Entry not yet filled — cancel it (best-effort).
                    import contextlib  # noqa: PLC0415

                    with contextlib.suppress(Exception):
                        self._exchange.cancel_order(exchange_order_id, symbol)
                raise SlPlacementError(
                    f"SL placement failed after entry {exchange_order_id}: {sl_exc}",
                    order_id=exchange_order_id,
                    close_failed=close_failed,
                ) from sl_exc

            # SL placed separately — verify it exists as a matching conditional order.
            # Check: type, reduceOnly, side, stopPrice (within 0.1%), and qty (within 0.1%).
            try:
                open_orders = self._exchange.fetch_open_orders(symbol)
            except Exception:
                open_orders = []
            sl_types = {"stop", "stop_market", "stop_limit"}
            sl_side = "sell" if side == "buy" else "buy"
            sl_verified = False
            for o in open_orders:
                if o.get("type") not in sl_types:
                    continue
                if not o.get("reduceOnly", False):
                    continue
                if o.get("side", "").lower() != sl_side:
                    continue
                order_stop = float(o.get("stopPrice") or o.get("price") or 0.0)
                if order_stop > 0 and abs(order_stop - stop_loss) / stop_loss > 0.001:
                    continue
                order_qty = float(o.get("amount") or o.get("qty") or 0.0)
                if order_qty > 0 and abs(order_qty - qty) / qty > 0.001:
                    continue
                sl_verified = True
                break
            if not sl_verified:
                raise SlPlacementError(
                    f"SL verification failed: no matching stop/reduceOnly order for "
                    f"entry {exchange_order_id} (sl={stop_loss}, qty={qty})",
                    order_id=exchange_order_id,
                )

        filled_qty = float(order.get("filled", 0.0) or 0.0)
        avg_price = float(order.get("average") or order.get("price") or price or 0.0)

        return {
            "order_id": exchange_order_id,
            "client_order_id": order.get("clientOrderId", client_order_id or ""),
            "status": order.get("status", "open"),
            "filled_qty": filled_qty,
            "avg_price": avg_price,
        }

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an order on the exchange."""
        try:
            self._exchange.cancel_order(order_id, symbol)
            return True
        except Exception as exc:
            raise AdapterError(f"cancel_order failed: {exc}") from exc

    def set_leverage(self, symbol: str, leverage: int) -> None:
        """Set leverage for a symbol."""
        try:
            self._exchange.set_leverage(leverage, symbol)
        except Exception as exc:
            raise AdapterError(f"set_leverage failed: {exc}") from exc

    def set_margin_mode(self, symbol: str, mode: str) -> None:
        """Set margin mode for a symbol."""
        try:
            self._exchange.set_margin_mode(mode, symbol)
        except Exception as exc:
            raise AdapterError(f"set_margin_mode failed: {exc}") from exc

    def set_position_mode(self, mode: str) -> None:
        """Set position mode."""
        try:
            ccxt_mode = mode == "hedge"
            self._exchange.set_position_mode(ccxt_mode)
        except Exception as exc:
            raise AdapterError(f"set_position_mode failed: {exc}") from exc

    def get_market_meta(self, symbol: str) -> dict[str, Any]:
        """Return market metadata for a symbol."""
        try:
            markets = self._exchange.markets
            if symbol not in markets:
                raise AdapterError(f"Symbol not found: {symbol}")
            m = markets[symbol]
            limits = m.get("limits", {})
            amount_limits = limits.get("amount", {})
            fees = m.get("fees", {})
            return {
                "lot_size": float(m.get("precision", {}).get("amount", 1.0) or 1.0),
                "min_qty": float(amount_limits.get("min", 1.0) or 1.0),
                "tick_size": float(m.get("precision", {}).get("price", 0.01) or 0.01),
                "maker_fee": float(fees.get("maker", 0.0002) or 0.0002),
                "taker_fee": float(fees.get("taker", 0.00055) or 0.00055),
                "max_leverage": float(m.get("limits", {}).get("leverage", {}).get("max", 10) or 10),
            }
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"get_market_meta failed: {exc}") from exc

    def get_server_time(self) -> int:
        """Return exchange server time in epoch ms."""
        try:
            return int(self._exchange.fetch_time())
        except Exception as exc:
            raise AdapterError(f"get_server_time failed: {exc}") from exc

    def ping(self) -> bool:
        """Check if exchange is reachable."""
        try:
            self._exchange.fetch_time()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # SL amendment helpers (fail-closed)
    # ------------------------------------------------------------------

    def _find_sl_order_to_cancel(
        self, is_long: bool, open_orders: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Return the stop order to cancel: strict reduceOnly==True on close side.

        Only matches reduceOnly is True (not None, not False) to avoid canceling
        entry-side or ambiguously-flagged orders.
        """
        stop_types = {"stop", "stop_market", "stop_limit"}
        expected_side = "sell" if is_long else "buy"
        for o in open_orders:
            if (
                o.get("type") in stop_types
                and o.get("reduceOnly") is True
                and o.get("side", "").lower() == expected_side
            ):
                return o
        return None

    def _conservative_existing_sl(
        self, is_long: bool, pos_stop_loss_raw: Any, open_orders: list[dict[str, Any]]
    ) -> float:
        """Conservative never-widen baseline.

        1. Primary: position's stopLoss field (Bybit v5).
        2. Secondary: any stop-type order on the close side, regardless of reduceOnly.
           (reduceOnly=None/True/False — we treat any close-side stop conservatively.)

        Returns 0.0 if no baseline can be determined.
        """
        # Primary: position's stopLoss field
        if pos_stop_loss_raw is not None:
            try:
                v = float(pos_stop_loss_raw)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass

        # Secondary: any stop order on the close side (conservative)
        stop_types = {"stop", "stop_market", "stop_limit"}
        expected_side = "sell" if is_long else "buy"
        for o in open_orders:
            if o.get("type") in stop_types and o.get("side", "").lower() == expected_side:
                raw_sp = o.get("stopPrice") or o.get("price") or 0
                try:
                    sp = float(raw_sp)
                    if sp > 0:
                        return sp
                except (TypeError, ValueError):
                    pass

        return 0.0

    def _verify_sl_placed(
        self,
        symbol: str,
        new_sl: float,
        is_long: bool,
        qty: float,
    ) -> bool:
        """Fail-closed SL verification — unknown = not verified.

        Primary: position's stopLoss field from fetch_positions (Bybit v5).
        Secondary: open conditional order that is reduceOnly==True, correct
        close-side, qty within 0.1%, trigger price within 0.1%.

        Returns True only when one source positively confirms the SL.
        """
        # Primary: position's stopLoss field
        try:
            positions = self._exchange.fetch_positions([symbol])
            for p in positions:
                if p.get("symbol") == symbol and p.get("contracts", 0):
                    raw = p.get("stopLoss")
                    if raw is not None:
                        try:
                            pos_sl = float(raw)
                            if pos_sl > 0 and abs(pos_sl - new_sl) / new_sl <= 0.001:
                                return True
                        except (TypeError, ValueError, ZeroDivisionError):
                            pass
                    break
        except Exception:
            pass

        # Secondary: open conditional order with strict criteria
        stop_types = {"stop", "stop_market", "stop_limit"}
        sl_side = "sell" if is_long else "buy"
        try:
            orders = self._exchange.fetch_open_orders(symbol)
            for o in orders:
                if o.get("type") not in stop_types:
                    continue
                if o.get("reduceOnly") is not True:  # strict: must be True
                    continue
                if o.get("side", "").lower() != sl_side:
                    continue
                try:
                    sp = float(o.get("stopPrice") or o.get("price") or 0)
                except (TypeError, ValueError):
                    continue
                if sp <= 0 or abs(sp - new_sl) / new_sl > 0.001:
                    continue
                try:
                    order_qty = float(o.get("amount") or o.get("qty") or 0)
                except (TypeError, ValueError):
                    continue
                if order_qty <= 0 or abs(order_qty - qty) / qty > 0.001:
                    continue
                return True
        except Exception:
            pass

        return False  # unknown = not verified

    def amend_stop_loss(self, symbol: str, new_sl: float) -> bool:
        """Amend SL on open position (reduceOnly). Fail-closed verification.

        Never widens:
          Primary baseline: position's stopLoss field from fetch_positions.
          Secondary baseline: open stop order with reduceOnly==True (strict).

        After placing the new SL (once), verifies via the same primary/secondary
        sources. Anything unknown = NOT verified → emergency market close
        (reduceOnly) + return False.
        """
        import logging  # noqa: PLC0415

        _log = logging.getLogger(__name__)

        if new_sl <= 0:
            return False

        # 1. Fetch position — primary source for side, qty, existing SL
        try:
            positions = self._exchange.fetch_positions([symbol])
        except Exception as exc:
            _log.warning(
                "amend_stop_loss: fetch_positions failed for %s: %s", symbol, type(exc).__name__
            )
            return False

        pos: dict[str, Any] | None = None
        for p in positions:
            if p.get("symbol") == symbol and p.get("contracts", 0):
                pos = p
                break
        if pos is None:
            return False

        side_raw = pos.get("side", "").lower()
        is_long = side_raw in {"long", "buy"}
        qty = float(pos.get("contracts", 0) or 0)
        if qty <= 0:
            return False

        # 2. Fetch open orders once — used for never-widen baseline AND for finding
        #    the stop order to cancel. Done BEFORE placement, not after.
        try:
            open_orders: list[dict[str, Any]] = self._exchange.fetch_open_orders(symbol)
        except Exception as exc:
            _log.warning(
                "amend_stop_loss: fetch_open_orders failed for %s: %s", symbol, type(exc).__name__
            )
            open_orders = []

        # 3a. Conservative never-widen baseline (position's stopLoss OR any close-side stop)
        existing_sl = self._conservative_existing_sl(is_long, pos.get("stopLoss"), open_orders)

        # Apply never-widen
        if existing_sl > 0:
            if is_long and new_sl <= existing_sl:
                return False
            if not is_long and new_sl >= existing_sl:
                return False

        # 3b. Find the stop order to cancel (strict: reduceOnly==True only)
        stop_to_cancel = self._find_sl_order_to_cancel(is_long, open_orders)

        # 4. Cancel existing stop (best-effort — do not abort if cancel fails)
        if stop_to_cancel is not None:
            try:
                self._exchange.cancel_order(stop_to_cancel["id"], symbol)
            except Exception as exc:
                _log.warning(
                    "amend_stop_loss: cancel existing stop failed for %s: %s",
                    symbol,
                    type(exc).__name__,
                )

        # 5. Place new stop (exactly once)
        sl_side = "sell" if is_long else "buy"
        try:
            self._exchange.create_order(
                symbol,
                "stop_market",
                sl_side,
                qty,
                None,
                {"stopPrice": new_sl, "reduceOnly": True},
            )
        except Exception as exc:
            _log.error(
                "amend_stop_loss: place new stop failed for %s: %s", symbol, type(exc).__name__
            )
            return False

        # 6. Verify (fail-closed: unknown = not verified)
        if self._verify_sl_placed(symbol, new_sl, is_long, qty):
            return True

        # 7. Verification failed → emergency market close (reduceOnly) + return False
        _log.critical(
            "amend_stop_loss: SL verification failed for %s after placement — "
            "executing emergency market close (reduceOnly)",
            symbol,
        )
        close_side = "sell" if is_long else "buy"
        try:
            self._exchange.create_order(
                symbol, "market", close_side, qty, None, {"reduceOnly": True}
            )
        except Exception as close_exc:
            _log.critical(
                "amend_stop_loss: emergency close ALSO failed for %s: %s",
                symbol,
                type(close_exc).__name__,
            )
        return False


__all__ = [
    "LiveAdapter",
    "LiveNotApprovedError",
    "SlPlacementError",
    "DuplicateOrderError",
    "AdapterError",
]
