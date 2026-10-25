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
                if p.get("contracts", 0) == 0:
                    continue
                result.append(
                    {
                        "symbol": p["symbol"],
                        "side": p["side"],
                        "qty": float(p.get("contracts", 0)),
                        "entry_price": float(p.get("entryPrice") or 0),
                        "unrealised_pnl": float(p.get("unrealizedPnl") or 0),
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

            # SL placed separately — verify it actually exists as a conditional order.
            try:
                open_orders = self._exchange.fetch_open_orders(symbol)
            except Exception:
                open_orders = []
            sl_types = {"stop", "stop_market", "stop_limit"}
            sl_verified = any(
                o.get("type") in sl_types and o.get("reduceOnly", False) for o in open_orders
            )
            if not sl_verified:
                raise SlPlacementError(
                    f"SL verification failed: no stop/reduce_only order found for "
                    f"entry {exchange_order_id}",
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


__all__ = [
    "LiveAdapter",
    "LiveNotApprovedError",
    "SlPlacementError",
    "DuplicateOrderError",
    "AdapterError",
]
