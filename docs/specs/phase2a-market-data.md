# Phase 2A: Market Data Layer — Specification & Acceptance Criteria

**Status**: Implemented  
**Phase**: 2A (BUILD_PLAN tasks 1, 2, 3, 10, 11)  
**Branch**: phase/2a-market-data

---

## Scope

Market-data ingestion for XRPUSDT and DOGEUSDT on Bybit USDT perpetual (public endpoints only, no API key required).

---

## Deliverables

| Module | Description |
|--------|-------------|
| `src/trader/data/collector.py` | `BaseCollector` ABC + `CollectorResult` dataclass |
| `src/trader/data/bybit_rest.py` | Sync REST client with retry/backoff |
| `src/trader/data/bybit_ws.py` | Async WebSocket client in daemon thread |
| `src/trader/data/market.py` | `OHLCVCollector`, `BookCollector` |
| `src/trader/data/orderflow.py` | `OrderFlowCollector`, `cvd_from_trades`, `book_imbalance` |
| `src/trader/data/derivatives.py` | `DerivativesCollector`, `compute_basis`, `parse_long_short_ratio` |
| `src/trader/data/session.py` | `tag_session`, `to_pkt`, `to_uae`, `format_for_display` |
| `src/trader/data/quality.py` | `DataQualityMonitor` |
| `src/trader/data/snapshot_builder.py` | `build_snapshot` → `MarketSnapshot` |
| `scripts/soak.py` | 1-hour live soak test |

---

## API Endpoints Used (Bybit V5 Public)

| Endpoint | Purpose |
|----------|---------|
| `GET /v5/market/kline` | OHLCV candles (5m, 15m, 1h, 4h, 1d) |
| `GET /v5/market/orderbook` | Top-of-book, depth-N |
| `GET /v5/market/tickers` | Mark price, index price, funding rate, OI |
| `GET /v5/market/open-interest` | Historical open interest |
| `GET /v5/market/account-ratio` | Long/short ratio |
| `GET /v5/market/recent-trade` | Taker trades for CVD |
| WebSocket `wss://stream.bybit.com/v5/public/linear` | Real-time kline, orderbook, tickers, trades |

---

## Acceptance Criteria

### AC-1: OHLCV
- [x] Intervals: 5m, 15m, 1h, 4h, 1d collected via REST
- [x] Rows parsed to `{ts_ms, open, high, low, close, volume, turnover}`
- [x] `backfill()` returns up to N candles, oldest-first
- [x] Missing timeframe adds quality flag `kline_error_<tf>`

### AC-2: Order Book
- [x] `best_bid`, `best_ask`, `spread`, `spread_pct`, `mid_price` computed
- [x] Full top-N bid/ask ladder returned
- [x] `book_error` flag added on failure

### AC-3: Order Flow
- [x] `taker_buy_volume`, `taker_sell_volume` from recent trades
- [x] `cvd_from_trades()`: Buy side positive, Sell side negative
- [x] `book_imbalance()`: `(bid_vol - ask_vol) / (bid_vol + ask_vol)`, range [-1, 1]

### AC-4: Derivatives
- [x] `funding_rate`, `next_funding_time_ms` from tickers
- [x] `open_interest` from tickers
- [x] `basis = mark_price - index_price`
- [x] `long_ratio`, `short_ratio` from account-ratio endpoint

### AC-5: Session Tagging
- [x] Asia: 00:00–07:00 UTC
- [x] Asia/London overlap: 07:00–08:00 UTC
- [x] London only: 08:00–12:00 UTC
- [x] London/NY overlap: 12:00–16:00 UTC
- [x] NY only: 16:00–21:00 UTC
- [x] Off: 21:00–24:00 UTC
- [x] `to_pkt()`: UTC+5
- [x] `to_uae()`: UTC+4
- [x] `format_for_display()`: "2026-01-01 12:00 UTC | 17:00 PKT | 16:00 UAE"

### AC-6: Data Quality
- [x] `stale_data` when `age > max_data_age_seconds` or `latest_ts is None`
- [x] `clock_drift` when `|drift_ms| > max_clock_drift_ms`
- [x] `outlier_price` when latest price deviates > 5σ from recent history (≥3 samples needed)
- [x] `gap_detected` when any price in history is `None` or `0.0` (≥2 samples needed)

### AC-7: WebSocket
- [x] Topics: `kline.5`, `kline.15`, `kline.60`, `kline.240`, `kline.D`, `orderbook.50`, `tickers`, `publicTrade` per symbol
- [x] Daemon thread with own asyncio event loop
- [x] Exponential backoff reconnect: 1s → 2s → 4s … capped at 60s with ±20% jitter
- [x] Manual ping/pong every 20s
- [x] `start()`, `stop()`, `is_connected()` interface

### AC-8: REST Client
- [x] `timeout=10s`, `max_retries=3`
- [x] Retry on 5xx / `ConnectionError` / `Timeout` with 1s/2s/4s backoff
- [x] Raises `BybitRestError` after exhausting retries
- [x] Raises immediately on non-zero `retCode`

### AC-9: Snapshot Builder
- [x] All collector outputs assembled into `MarketSnapshot` DSOR row
- [x] `data_quality_flags` serialised as JSON list
- [x] All JSON columns non-null (empty dict `{}` when no data)

---

## Out of Scope (Phase 2A)

- News, macro, cross-asset, sentiment collectors
- TradingView webhook ingestion
- Database persistence (handled by DSOR layer in later phase)
- Authenticated endpoints

---

## Test Coverage

| Test file | What it covers |
|-----------|---------------|
| `tests/test_bybit_rest.py` | HTTP retry logic, response parsing, error handling |
| `tests/test_orderflow.py` | CVD and imbalance math with known inputs |
| `tests/test_derivatives.py` | Basis calculation, L/S ratio parsing |
| `tests/test_session.py` | All session windows + PKT/UAE conversions |
| `tests/test_quality.py` | All four quality flags |
| `tests/test_snapshot_builder.py` | Integration: all collectors → MarketSnapshot |
