"""Phase 2A: Order-flow collector — CVD, book imbalance, taker buy/sell volume."""

from __future__ import annotations

import datetime
import logging
from typing import Any

from trader.data.bybit_rest import BybitRestClient
from trader.data.collector import BaseCollector, CollectorResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helper functions (also used by tests)
# ---------------------------------------------------------------------------


def cvd_from_trades(trades: list[dict[str, Any]]) -> float:
    """Compute Cumulative Volume Delta from a list of trade dicts.

    Each trade must have keys: ``side`` ("Buy" or "Sell") and ``size`` (numeric).
    Buy volume is positive; sell volume is negative.
    """
    cvd = 0.0
    for t in trades:
        size = float(t.get("size", 0))
        side = str(t.get("side", "")).lower()
        if side == "buy":
            cvd += size
        elif side == "sell":
            cvd -= size
    return cvd


def book_imbalance(
    bids: list[list[Any]],
    asks: list[list[Any]],
    depth: int = 10,
) -> float:
    """Compute order-book imbalance over the top *depth* levels.

    imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
    Returns 0.0 when both sides are empty.
    """
    bid_vol = sum(float(row[1]) for row in bids[:depth])
    ask_vol = sum(float(row[1]) for row in asks[:depth])
    total = bid_vol + ask_vol
    if total == 0.0:
        return 0.0
    return (bid_vol - ask_vol) / total


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class OrderFlowCollector(BaseCollector):
    """Collects taker buy/sell volume, CVD, and book imbalance."""

    def __init__(
        self,
        symbol: str,
        rest_client: BybitRestClient,
        trade_limit: int = 60,
        book_depth: int = 10,
    ) -> None:
        super().__init__(symbol, rest_client)
        self._trade_limit = trade_limit
        self._book_depth = book_depth

    def collect(self) -> CollectorResult:
        now = datetime.datetime.now(datetime.UTC)
        flags: list[str] = []
        data: dict[str, Any] = {}

        # -- Recent trades ------------------------------------------------
        try:
            trades = self._rest.get_recent_trades(self.symbol, limit=self._trade_limit)
            buy_vol = sum(float(t.get("size", 0)) for t in trades if t.get("side") == "Buy")
            sell_vol = sum(float(t.get("size", 0)) for t in trades if t.get("side") == "Sell")
            cvd = cvd_from_trades(trades)
            data["taker_buy_volume"] = buy_vol
            data["taker_sell_volume"] = sell_vol
            data["cvd"] = cvd
            data["trade_count"] = len(trades)
        except Exception as exc:
            logger.warning(
                "OrderFlowCollector trades fetch failed",
                extra={"symbol": self.symbol, "error": str(exc)},
            )
            flags.append("orderflow_trades_error")

        # -- Order book imbalance ----------------------------------------
        try:
            book = self._rest.get_orderbook(self.symbol, depth=self._book_depth)
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            imb = book_imbalance(bids, asks, depth=self._book_depth)
            data["book_imbalance"] = imb
            data["book_bid_vol"] = sum(float(r[1]) for r in bids[: self._book_depth])
            data["book_ask_vol"] = sum(float(r[1]) for r in asks[: self._book_depth])
        except Exception as exc:
            logger.warning(
                "OrderFlowCollector book fetch failed",
                extra={"symbol": self.symbol, "error": str(exc)},
            )
            flags.append("orderflow_book_error")

        return CollectorResult(symbol=self.symbol, ts=now, data=data, quality_flags=flags)

    def backfill(self, limit: int = 1) -> list[CollectorResult]:
        """Recent-trade endpoint has no pagination; returns current snapshot only."""
        return [self.collect()]
