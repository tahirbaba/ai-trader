"""Phase 2A: OHLCV and order-book collectors."""

from __future__ import annotations

import datetime
import logging
from typing import Any

from trader.data.bybit_rest import BybitRestClient
from trader.data.collector import BaseCollector, CollectorResult

logger = logging.getLogger(__name__)

# Bybit interval strings
_INTERVAL_MAP: dict[str, str] = {
    "5m": "5",
    "15m": "15",
    "1h": "60",
    "4h": "240",
    "1d": "D",
}


def _parse_candle(row: list[Any]) -> dict[str, float | int]:
    """Convert a raw Bybit kline row to a typed dict."""
    ts_ms, open_, high, low, close, vol, turnover = row[:7]
    return {
        "ts_ms": int(ts_ms),
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(vol),
        "turnover": float(turnover),
    }


class OHLCVCollector(BaseCollector):
    """Collects OHLCV candles for a single symbol across multiple timeframes."""

    TIMEFRAMES: list[str] = ["5m", "15m", "1h", "4h", "1d"]

    def __init__(self, symbol: str, rest_client: BybitRestClient) -> None:
        super().__init__(symbol, rest_client)

    def collect(self) -> CollectorResult:
        """Return the latest candle for each configured timeframe."""
        now = datetime.datetime.now(datetime.UTC)
        data: dict[str, Any] = {}
        flags: list[str] = []

        for tf in self.TIMEFRAMES:
            interval = _INTERVAL_MAP[tf]
            try:
                rows = self._rest.get_kline(self.symbol, interval, limit=1)
                if rows:
                    data[tf] = _parse_candle(rows[-1])
                else:
                    flags.append(f"no_kline_{tf}")
            except Exception as exc:
                logger.warning(
                    "OHLCVCollector kline fetch failed",
                    extra={"symbol": self.symbol, "tf": tf, "error": str(exc)},
                )
                flags.append(f"kline_error_{tf}")

        return CollectorResult(symbol=self.symbol, ts=now, data=data, quality_flags=flags)

    def backfill(self, limit: int = 200) -> list[CollectorResult]:
        """Return up to *limit* historical candles per timeframe (oldest-first)."""
        results: list[CollectorResult] = []
        for tf in self.TIMEFRAMES:
            interval = _INTERVAL_MAP[tf]
            try:
                rows = self._rest.get_kline(self.symbol, interval, limit=limit)
                for row in rows:
                    candle = _parse_candle(row)
                    ts = datetime.datetime.fromtimestamp(candle["ts_ms"] / 1000, datetime.UTC)
                    results.append(
                        CollectorResult(
                            symbol=self.symbol,
                            ts=ts,
                            data={tf: candle},
                        )
                    )
            except Exception as exc:
                logger.warning(
                    "OHLCVCollector backfill failed",
                    extra={"symbol": self.symbol, "tf": tf, "error": str(exc)},
                )
        return results


class BookCollector(BaseCollector):
    """Collects top-of-book, top-N depth, and bid-ask spread."""

    def __init__(
        self,
        symbol: str,
        rest_client: BybitRestClient,
        depth: int = 50,
    ) -> None:
        super().__init__(symbol, rest_client)
        self._depth = depth

    def collect(self) -> CollectorResult:
        now = datetime.datetime.now(datetime.UTC)
        flags: list[str] = []

        try:
            book = self._rest.get_orderbook(self.symbol, depth=self._depth)
            bids: list[list[str]] = book.get("bids", [])
            asks: list[list[str]] = book.get("asks", [])

            best_bid = float(bids[0][0]) if bids else None
            best_ask = float(asks[0][0]) if asks else None
            spread = (
                (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None
            )
            mid = (
                ((best_bid + best_ask) / 2)
                if (best_bid is not None and best_ask is not None)
                else None
            )
            spread_pct = (
                (spread / mid * 100)
                if (spread is not None and mid is not None and mid != 0)
                else None
            )

            data: dict[str, Any] = {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": spread,
                "spread_pct": spread_pct,
                "mid_price": mid,
                "bids": [[float(p), float(s)] for p, s in bids[: self._depth]],
                "asks": [[float(p), float(s)] for p, s in asks[: self._depth]],
                "book_ts_ms": book.get("ts"),
                "depth": self._depth,
            }
        except Exception as exc:
            logger.warning(
                "BookCollector fetch failed",
                extra={"symbol": self.symbol, "error": str(exc)},
            )
            data = {}
            flags.append("book_error")

        return CollectorResult(symbol=self.symbol, ts=now, data=data, quality_flags=flags)

    def backfill(self, limit: int = 1) -> list[CollectorResult]:
        """Order book has no historical endpoint; returns current snapshot only."""
        return [self.collect()]
