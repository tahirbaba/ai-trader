"""Phase 2A: Derivatives collector — funding rate, OI, L/S ratio, basis."""

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


def compute_basis(mark_price: float, index_price: float) -> float:
    """Return basis = markPrice − indexPrice."""
    return mark_price - index_price


def parse_long_short_ratio(row: list[Any]) -> dict[str, Any]:
    """Parse a single L/S ratio row [ts_ms, longRatio, shortRatio] → dict."""
    ts_ms, long_ratio, short_ratio = row[0], row[1], row[2]
    return {
        "ts_ms": int(ts_ms) if ts_ms is not None else None,
        "long_ratio": float(long_ratio) if long_ratio is not None else None,
        "short_ratio": float(short_ratio) if short_ratio is not None else None,
    }


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class DerivativesCollector(BaseCollector):
    """Collects perpetual-specific data: funding, OI, L/S ratio, basis."""

    def __init__(self, symbol: str, rest_client: BybitRestClient) -> None:
        super().__init__(symbol, rest_client)

    def collect(self) -> CollectorResult:
        now = datetime.datetime.now(datetime.UTC)
        flags: list[str] = []
        data: dict[str, Any] = {}

        # -- Ticker (funding rate, OI, mark/index prices) ------------------
        try:
            ticker = self._rest.get_tickers(self.symbol)
            funding_rate_raw = ticker.get("fundingRate")
            mark_price_raw = ticker.get("markPrice")
            index_price_raw = ticker.get("indexPrice")
            oi_raw = ticker.get("openInterest")
            next_funding_raw = ticker.get("nextFundingTime")

            data["funding_rate"] = float(funding_rate_raw) if funding_rate_raw else None
            data["next_funding_time_ms"] = int(next_funding_raw) if next_funding_raw else None
            data["open_interest"] = float(oi_raw) if oi_raw else None
            data["mark_price"] = float(mark_price_raw) if mark_price_raw else None
            data["index_price"] = float(index_price_raw) if index_price_raw else None

            if data["mark_price"] is not None and data["index_price"] is not None:
                data["basis"] = compute_basis(data["mark_price"], data["index_price"])
            else:
                data["basis"] = None

            data["last_price"] = float(ticker["lastPrice"]) if ticker.get("lastPrice") else None
            data["bid1"] = float(ticker["bid1Price"]) if ticker.get("bid1Price") else None
            data["ask1"] = float(ticker["ask1Price"]) if ticker.get("ask1Price") else None
        except Exception as exc:
            logger.warning(
                "DerivativesCollector ticker fetch failed",
                extra={"symbol": self.symbol, "error": str(exc)},
            )
            flags.append("derivatives_ticker_error")

        # -- Long/Short ratio (latest row) ---------------------------------
        try:
            ls_rows = self._rest.get_long_short_ratio(self.symbol, period="1h", limit=1)
            if ls_rows:
                ls = parse_long_short_ratio(ls_rows[-1])
                data["long_ratio"] = ls["long_ratio"]
                data["short_ratio"] = ls["short_ratio"]
            else:
                flags.append("no_ls_ratio")
        except Exception as exc:
            logger.warning(
                "DerivativesCollector L/S ratio fetch failed",
                extra={"symbol": self.symbol, "error": str(exc)},
            )
            flags.append("ls_ratio_error")

        return CollectorResult(symbol=self.symbol, ts=now, data=data, quality_flags=flags)

    def backfill(self, limit: int = 200) -> list[CollectorResult]:
        """Return historical OI and L/S ratio rows."""
        results: list[CollectorResult] = []

        # OI history
        try:
            oi_rows = self._rest.get_open_interest(self.symbol, interval="1h", limit=limit)
            for row in oi_rows:
                ts_ms, oi_val = row[0], row[1]
                ts = datetime.datetime.fromtimestamp(int(ts_ms) / 1000, datetime.UTC)
                results.append(
                    CollectorResult(
                        symbol=self.symbol,
                        ts=ts,
                        data={"open_interest": float(oi_val)},
                    )
                )
        except Exception as exc:
            logger.warning(
                "DerivativesCollector OI backfill failed",
                extra={"symbol": self.symbol, "error": str(exc)},
            )

        return results
