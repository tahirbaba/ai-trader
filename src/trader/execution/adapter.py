"""Phase 7: Abstract ExchangeAdapter interface for all exchange adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ExchangeAdapter(ABC):
    """Abstract interface for all exchange adapters (paper, testnet, live)."""

    @abstractmethod
    def get_balance(self) -> dict[str, Any]:  # {"equity_usd": float, "available_usd": float}
        ...

    @abstractmethod
    def get_positions(self) -> list[dict[str, Any]]:
        # [{"symbol", "side", "qty", "entry_price", "unrealised_pnl"}]
        ...

    @abstractmethod
    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]: ...

    @abstractmethod
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
        # {"order_id", "client_order_id", "status", "filled_qty", "avg_price"}
        ...

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> bool: ...

    @abstractmethod
    def set_leverage(self, symbol: str, leverage: int) -> None: ...

    @abstractmethod
    def set_margin_mode(self, symbol: str, mode: str) -> None:  # "isolated" | "cross"
        ...

    @abstractmethod
    def set_position_mode(self, mode: str) -> None:  # "one_way" | "hedge"
        ...

    @abstractmethod
    def get_market_meta(self, symbol: str) -> dict[str, Any]:
        # {"lot_size", "min_qty", "tick_size", "maker_fee", "taker_fee", "max_leverage"}
        ...

    @abstractmethod
    def get_server_time(self) -> int:  # epoch ms
        ...

    @abstractmethod
    def ping(self) -> bool: ...
