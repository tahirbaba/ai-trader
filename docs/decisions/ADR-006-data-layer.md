# ADR-006: Phase 2A Data Layer Architecture

**Date**: 2026-10-24  
**Status**: Accepted  
**Deciders**: naveed569  
**Phase**: 2A (Market Data)

---

## Context

The AI trader requires a reliable, low-latency market-data ingestion layer for Bybit USDT perpetual futures. The data must be gathered without API keys (public endpoints only), stored in the DSOR `market_snapshots` table, and used downstream by feature engineering and LLM decision agents.

Key constraints:
- No live trading; `TRADING_MODE=paper` is the default.
- External calls must be resilient (timeout + retry).
- WebSocket state must not block the main trading-cycle thread.
- All collected data must pass through a quality gate before use.

---

## Decision 1: Synchronous REST + Async WebSocket in Separate Thread

**Chosen**: REST calls are synchronous (`requests`); WebSocket is asyncio inside a daemon thread.

**Rationale**:
- REST calls are infrequent (every 30s) — sync is simpler and avoids async-context spreading.
- WebSocket requires async I/O for efficient multiplexed streams; running it in a daemon thread isolates it from the main synchronous trading cycle.
- Background thread uses its own `asyncio.new_event_loop()` so it cannot interfere with any future async code in the main thread.

**Rejected alternatives**:
- Full asyncio everywhere: increases complexity, not needed for current polling frequency.
- Polling only (no WS): loses sub-second data for order-flow and tick data.

---

## Decision 2: `CollectorResult` Dataclass Over Raw Dicts

**Chosen**: Each collector returns a typed `CollectorResult(symbol, ts, data, quality_flags)`.

**Rationale**:
- Carries quality flags alongside data without a separate channel.
- Typed; mypy can check usage.
- `snapshot_builder.build_snapshot()` accepts named `CollectorResult` arguments and merges them cleanly.

---

## Decision 3: Exponential Backoff with Jitter for WebSocket Reconnect

**Chosen**: `backoff = min(backoff * 2, 60)` + uniform jitter of ±20%.

**Rationale**:
- Prevents thundering-herd if Bybit has an outage and multiple instances reconnect simultaneously.
- Cap at 60s prevents indefinitely long outages from delaying restarts too much.
- Minimum 1s prevents tight spin loops.

---

## Decision 4: `DataQualityMonitor` is Stateless

**Chosen**: `DataQualityMonitor.check()` is a pure function; caller provides price history.

**Rationale**:
- Easier to test (no state setup/teardown).
- Collectors are stateless per-cycle; history is maintained by the orchestrator or soak test.
- Flags are persisted in `MarketSnapshot.data_quality_flags` as a JSON list.

---

## Decision 5: Session Definition (UTC Windows)

**Chosen**:

| Session | Window (UTC) |
|---------|-------------|
| Asia | 00:00–07:00 |
| Asia/London overlap | 07:00–08:00 |
| London | 08:00–12:00 |
| London/NY overlap | 12:00–16:00 |
| NY | 16:00–21:00 |
| Off | 21:00–24:00 |

PKT = UTC+5, UAE = UTC+4.

**Rationale**: Based on standard FX/crypto session definitions widely used in the trading literature. PKT and UAE are the owner's relevant display timezones.

---

## Decision 6: `basis = markPrice − indexPrice`

**Chosen**: Simple arithmetic difference (not basis points or percentage).

**Rationale**: Raw basis in price units is most useful for funding-rate context; percentage basis can be derived downstream by feature engineering.

---

## Consequences

- REST client is not thread-safe; each thread that needs it should construct its own instance (lightweight).
- `websockets` library is added as a runtime dependency.
- The `scripts/soak.py` tool provides an operational confidence check but is not run in CI (requires live network).
- Future phases can swap the REST implementation for an async client without changing collector interfaces.
