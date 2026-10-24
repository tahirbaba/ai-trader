"""Phase 2B: Cross-asset context collector.

Collects DXY, SPX, Gold (via yfinance) and BTC dominance, ETH/BTC ratio
(via CoinGecko free API).  Marks traditional-market assets as stale when
outside their trading hours (Mon–Fri 13:30–20:00 UTC).
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

import requests
import yfinance as yf

from trader.data.collector import CollectorResult

logger = logging.getLogger(__name__)

# Traditional market trading window (UTC)
_MARKET_OPEN_H = 13
_MARKET_OPEN_M = 30
_MARKET_CLOSE_H = 20
_MARKET_CLOSE_M = 0

# yfinance ticker symbols
_YF_TICKERS: dict[str, str] = {
    "DXY": "DX-Y.NYB",
    "SPX": "^GSPC",
    "GOLD": "GC=F",
}

_COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
_REQUEST_TIMEOUT = 10


def _utcnow() -> datetime.datetime:
    """Return current UTC time (injectable for testing)."""
    return datetime.datetime.utcnow()


def _is_traditional_market_open(now: datetime.datetime) -> bool:
    """Return True if now falls within Mon–Fri 13:30–20:00 UTC."""
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    open_time = now.replace(hour=_MARKET_OPEN_H, minute=_MARKET_OPEN_M, second=0, microsecond=0)
    close_time = now.replace(
        hour=_MARKET_CLOSE_H, minute=_MARKET_CLOSE_M, second=0, microsecond=0
    )
    return open_time <= now < close_time


def _fetch_yf_price(ticker_sym: str) -> float | None:
    """Fetch the latest price for a yfinance ticker; return None on error."""
    try:
        ticker = yf.Ticker(ticker_sym)
        price: float | None = ticker.fast_info.last_price
        if price is not None:
            return float(price)
        # Fallback to history
        hist = ticker.history(period="1d", interval="1h")
        if not hist.empty:
            return float(hist.iloc[-1]["Close"])
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance fetch failed for %s: %s", ticker_sym, exc)
        return None


def _fetch_coingecko() -> dict[str, float | None]:
    """Return {'BTC_DOM': float|None, 'ETH_BTC': float|None}."""
    try:
        resp = requests.get(_COINGECKO_GLOBAL, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json()
        percentages: dict[str, float] = payload.get("data", {}).get(
            "market_cap_percentage", {}
        )
        btc_dom = percentages.get("btc")
        eth_pct = percentages.get("eth")
        # ETH/BTC ratio approximated from market-cap percentages
        eth_btc: float | None = None
        if btc_dom and eth_pct and btc_dom > 0:
            eth_btc = eth_pct / btc_dom
        return {
            "BTC_DOM": float(btc_dom) if btc_dom is not None else None,
            "ETH_BTC": eth_btc,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("CoinGecko fetch failed: %s", exc)
        return {"BTC_DOM": None, "ETH_BTC": None}


class CrossAssetCollector:
    """Collect cross-asset context data.

    Parameters
    ----------
    rest_client:
        Unused; kept for interface parity with other collectors.
    """

    def __init__(self, rest_client: Any = None) -> None:
        self._rest = rest_client  # unused

    def collect(self) -> CollectorResult:
        """Return latest cross-asset data."""
        now = _utcnow()
        market_open = _is_traditional_market_open(now)

        data: dict[str, Any] = {}
        quality_flags: list[str] = []

        # Fetch traditional assets via yfinance
        for key, ticker_sym in _YF_TICKERS.items():
            price = _fetch_yf_price(ticker_sym)
            data[key] = price
            if price is None:
                quality_flags.append(f"{key}_fetch_error")

        # Fetch crypto dominance via CoinGecko
        cg = _fetch_coingecko()
        data["BTC_DOM"] = cg["BTC_DOM"]
        data["ETH_BTC"] = cg["ETH_BTC"]
        if cg["BTC_DOM"] is None:
            quality_flags.append("BTC_DOM_fetch_error")
        if cg["ETH_BTC"] is None:
            quality_flags.append("ETH_BTC_fetch_error")

        # Build stale mask
        stale_mask: dict[str, bool] = {
            "DXY": not market_open,
            "SPX": not market_open,
            "GOLD": not market_open,
            "BTC_DOM": False,  # crypto 24/7
            "ETH_BTC": False,
        }
        data["stale_mask"] = stale_mask

        return CollectorResult(
            symbol="CROSS_ASSET",
            ts=now,
            data=data,
            quality_flags=quality_flags,
        )

    def backfill(self, limit: int) -> list[CollectorResult]:
        """Not supported for cross-asset; returns empty list."""
        return []
