"""Phase 9: Live market feed — Bybit public REST -> FeaturePack dict for the orchestrator.

Uses only public market-data endpoints (no keys). Any failure produces a dict
with ``data_quality_failed=True`` so the orchestrator stops at the data-quality
step with NO_TRADE and no LLM call.
"""

from __future__ import annotations

import datetime
import logging
import time
from collections import deque
from typing import Any, Protocol

from trader.data.bybit_rest import BybitRestClient
from trader.data.derivatives import DerivativesCollector
from trader.data.market import _INTERVAL_MAP, _parse_candle
from trader.data.orderflow import OrderFlowCollector
from trader.features.builder import build_feature_pack

logger = logging.getLogger(__name__)

PRIMARY_TF = "15m"
_TFS = ["5m", "15m", "1h", "4h", "1d"]
_HISTORY = 200


def to_exchange_symbol(symbol: str) -> str:
    """'XRP/USDT:USDT' -> 'XRPUSDT' (Bybit linear perpetual id)."""
    base_quote = symbol.split(":", 1)[0]
    return base_quote.replace("/", "").upper()


class FeatureSource(Protocol):
    def build(self, symbol: str) -> dict[str, Any]: ...

    def mark_price(self, symbol: str) -> float | None: ...

    def candles(self, symbol: str, timeframe: str, limit: int) -> list[dict[str, Any]]: ...


class BybitFeatureSource:
    def __init__(self, rest: BybitRestClient | None = None) -> None:
        self._rest = rest or BybitRestClient()
        # Rolling history for z-scores, per symbol
        self._funding: dict[str, deque[float]] = {}
        self._oi: dict[str, deque[float]] = {}
        self._basis: dict[str, deque[float]] = {}
        self._cvd: dict[str, deque[float]] = {}
        self._spread: dict[str, deque[float]] = {}

    def candles(self, symbol: str, timeframe: str, limit: int = _HISTORY) -> list[dict[str, Any]]:
        rows = self._rest.get_kline(to_exchange_symbol(symbol), _INTERVAL_MAP[timeframe], limit)
        return [dict(_parse_candle(r)) for r in rows]

    def server_time_ms(self) -> int:
        return self._rest.get_server_time()

    def mark_price(self, symbol: str) -> float | None:
        try:
            t = self._rest.get_tickers(to_exchange_symbol(symbol))
            raw = t.get("markPrice") or t.get("lastPrice")
            return float(raw) if raw else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("mark_price failed for %s: %s", symbol, type(exc).__name__)
            return None

    def build(self, symbol: str) -> dict[str, Any]:
        started = time.monotonic()
        ex_sym = to_exchange_symbol(symbol)
        try:
            candles_by_tf: dict[str, list[dict[str, object]]] = {
                tf: [dict(c) for c in self.candles(symbol, tf, _HISTORY)] for tf in _TFS
            }
            deriv = DerivativesCollector(ex_sym, self._rest).collect()
            flow = OrderFlowCollector(ex_sym, self._rest).collect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("feature build failed for %s: %s", symbol, type(exc).__name__)
            return {"data_quality_failed": True, "data_quality_flags": ["feed_error"]}

        d = deriv.data
        f = flow.data
        spread_pct: float | None = None
        bid, ask = d.get("bid1"), d.get("ask1")
        if bid and ask and float(bid) > 0:
            spread_pct = (float(ask) - float(bid)) / ((float(ask) + float(bid)) / 2) * 100
        orderflow = {**f, "spread_pct": spread_pct}

        hist = self._update_history(symbol, d, orderflow)
        fp = build_feature_pack(
            symbol=symbol,
            primary_tf=PRIMARY_TF,
            candles_by_tf=candles_by_tf,
            derivatives=d,
            orderflow=orderflow,
            book=None,
            quality_flags=[*deriv.quality_flags, *flow.quality_flags],
            **hist,
        )
        out: dict[str, Any] = fp.model_dump(mode="json")
        primary = candles_by_tf.get(PRIMARY_TF) or []
        live = d.get("mark_price") or d.get("last_price") or fp.close
        last_ts_ms = float(primary[-1]["ts_ms"]) if primary else 0.0  # type: ignore[arg-type]
        # Age of the last candle *close* is not staleness (a 15m bar opens every
        # 15m); staleness is how old our fetch is.
        out.update(
            {
                "confluence_score": max(fp.confluence_long, fp.confluence_short),
                "live_price": float(live) if live else 0.0,
                "snapshot_price": float(fp.close),
                "data_age_seconds": time.monotonic() - started,
                "last_candle_ts_ms": last_ts_ms,
                "data_quality_flags": [*deriv.quality_flags, *flow.quality_flags],
                "next_funding_ts": _ms_to_iso(d.get("next_funding_time_ms")),
                "derivatives": d,
                "orderflow": orderflow,
            }
        )
        return out

    def _update_history(
        self, symbol: str, d: dict[str, Any], f: dict[str, Any]
    ) -> dict[str, list[float]]:
        series = {
            "funding_history": (self._funding, d.get("funding_rate")),
            "oi_history": (self._oi, d.get("open_interest")),
            "basis_history": (self._basis, d.get("basis")),
            "cvd_history": (self._cvd, f.get("cvd")),
            "spread_history": (self._spread, f.get("spread_pct")),
        }
        out: dict[str, list[float]] = {}
        for name, (store, value) in series.items():
            q = store.setdefault(symbol, deque(maxlen=500))
            if value is not None:
                q.append(float(value))
            out[name] = list(q)
        return out


def _ms_to_iso(ms: Any) -> str | None:
    if not ms:
        return None
    return datetime.datetime.fromtimestamp(int(ms) / 1000, tz=datetime.UTC).isoformat()
