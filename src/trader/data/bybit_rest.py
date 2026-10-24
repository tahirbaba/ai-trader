"""Phase 2A: Bybit V5 REST client (public endpoints only, no auth required)."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_BYBIT_BASE = "https://api.bybit.com"
_DEFAULT_TIMEOUT = 10
_DEFAULT_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds; doubles each retry


class BybitRestError(Exception):
    """Raised when all retries are exhausted or a non-retryable error occurs."""


class _RetryableError(Exception):
    """Internal: raised on 5xx to trigger retry logic."""


class BybitRestClient:
    """Thin synchronous wrapper around Bybit V5 public market-data endpoints."""

    def __init__(
        self,
        base_url: str = _BYBIT_BASE,
        timeout: int = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = requests.Session()
        self._session.headers["Accept"] = "application/json"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make a GET request with retries on 5xx / connection errors.

        Returns the parsed JSON body.
        Raises BybitRestError after *max_retries* failures.
        """
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                resp = self._session.get(url, params=params, timeout=self._timeout)
                if resp.status_code >= 500:
                    raise _RetryableError(f"Server error {resp.status_code} from {url}")
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
                if data.get("retCode", 0) != 0:
                    # Non-zero retCode is a permanent API error — do not retry
                    raise BybitRestError(
                        f"Bybit API error retCode={data.get('retCode')} "
                        f"msg={data.get('retMsg')} url={url}"
                    )
                return data
            except (requests.ConnectionError, requests.Timeout, _RetryableError) as exc:
                last_exc = exc
                wait = _BACKOFF_BASE * (2**attempt)
                logger.warning(
                    "Bybit REST retry",
                    extra={
                        "attempt": attempt + 1,
                        "max_retries": self._max_retries,
                        "wait_s": wait,
                        "error": str(exc),
                        "url": url,
                    },
                )
                if attempt < self._max_retries - 1:
                    time.sleep(wait)

        raise BybitRestError(f"Exhausted {self._max_retries} retries for {url}") from (
            last_exc if not isinstance(last_exc, _RetryableError) else None
        )

    # ------------------------------------------------------------------
    # Market-data convenience methods
    # ------------------------------------------------------------------

    def get_kline(
        self,
        symbol: str,
        interval: str,
        limit: int = 200,
    ) -> list[list[Any]]:
        """Return OHLCV candles.

        Each row: [ts_ms, open, high, low, close, volume, turnover]
        Bybit returns newest first; we reverse to oldest-first.
        """
        data = self.get(
            "/v5/market/kline",
            params={"category": "linear", "symbol": symbol, "interval": interval, "limit": limit},
        )
        rows: list[list[Any]] = data.get("result", {}).get("list", [])
        return list(reversed(rows))

    def get_orderbook(self, symbol: str, depth: int = 50) -> dict[str, Any]:
        """Return top-of-book dict with keys: bids, asks, ts, symbol."""
        data = self.get(
            "/v5/market/orderbook",
            params={"category": "linear", "symbol": symbol, "limit": depth},
        )
        result: dict[str, Any] = data.get("result", {})
        return {
            "bids": result.get("b", []),
            "asks": result.get("a", []),
            "ts": result.get("ts"),
            "symbol": result.get("s", symbol),
            "seq": result.get("seq"),
        }

    def get_tickers(self, symbol: str) -> dict[str, Any]:
        """Return ticker dict for a single symbol."""
        data = self.get(
            "/v5/market/tickers",
            params={"category": "linear", "symbol": symbol},
        )
        items: list[dict[str, Any]] = data.get("result", {}).get("list", [])
        if not items:
            raise BybitRestError(f"No ticker data returned for {symbol}")
        return items[0]

    def get_open_interest(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 200,
    ) -> list[list[Any]]:
        """Return OI rows [[ts_ms, openInterest], ...] oldest-first."""
        data = self.get(
            "/v5/market/open-interest",
            params={
                "category": "linear",
                "symbol": symbol,
                "intervalTime": interval,
                "limit": limit,
            },
        )
        rows: list[list[Any]] = data.get("result", {}).get("list", [])
        return list(reversed(rows))

    def get_long_short_ratio(
        self,
        symbol: str,
        period: str = "1h",
        limit: int = 200,
    ) -> list[list[Any]]:
        """Return L/S rows [[ts_ms, longRatio, shortRatio], ...] oldest-first."""
        data = self.get(
            "/v5/market/account-ratio",
            params={
                "category": "linear",
                "symbol": symbol,
                "period": period,
                "limit": limit,
            },
        )
        rows: list[dict[str, Any]] = data.get("result", {}).get("list", [])
        parsed: list[list[Any]] = [
            [r.get("timestamp"), r.get("buyRatio"), r.get("sellRatio")] for r in rows
        ]
        return list(reversed(parsed))

    def get_recent_trades(self, symbol: str, limit: int = 60) -> list[dict[str, Any]]:
        """Return recent public trades list, oldest-first."""
        data = self.get(
            "/v5/market/recent-trade",
            params={"category": "linear", "symbol": symbol, "limit": limit},
        )
        rows: list[dict[str, Any]] = data.get("result", {}).get("list", [])
        return list(reversed(rows))

    def get_server_time(self) -> int:
        """Return Bybit server time in milliseconds."""
        data = self.get("/v5/market/time")
        ts = data.get("result", {}).get("timeNano") or data.get("result", {}).get("timeSecond")
        if ts is None:
            # fallback: try top-level
            ts = data.get("time")
        return int(ts) if ts else 0

    def get_announcements(
        self,
        locale: str = "en-US",
        ann_type: str = "maintain_systems",
    ) -> list[dict[str, Any]]:
        """Return announcement list from Bybit V5 announcements endpoint."""
        resp = self.get(
            "/v5/announcements/index",
            {"locale": locale, "type": ann_type},
        )
        result: list[dict[str, Any]] = resp.get("result", {}).get("list", [])
        return result

    def close(self) -> None:
        """Release the underlying requests.Session."""
        self._session.close()

    def __enter__(self) -> BybitRestClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
