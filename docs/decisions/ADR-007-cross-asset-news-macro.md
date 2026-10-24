# ADR-007: Cross-Asset Context, News Sanitisation, and Macro Calendar

**Date:** 2026-10-24
**Status:** Accepted
**Phase:** 2B

## Context

Phase 2B extends the data-collection pipeline with five new collectors that provide
macro and sentiment context to the downstream decision pipeline.  Key design questions:

1. Which external data sources should we use for traditional-market prices?
2. How do we prevent news text from becoming an injection vector into the LLM prompt?
3. How should we signal "do not trade" windows from exchange maintenance or macro events?

## Decisions

### D1 — yfinance for DXY, SPX, Gold

`yfinance` wraps Yahoo Finance's unofficial API and requires no key.  Alternatives
(Alpha Vantage, Quandl) require registration.  Risk: Yahoo can throttle or change
their undocumented API.  Mitigation: each fetch is wrapped in try/except; a failed
asset sets its value to `None` and adds a quality flag; the rest of the result is
still returned.

### D2 — CoinGecko free `/global` for BTC Dominance and ETH/BTC ratio

CoinGecko's free `/api/v3/global` endpoint returns `market_cap_percentage` for all
assets, requiring no API key.  ETH/BTC ratio is approximated as `eth_pct / btc_pct`,
which is a percentage-of-dominance ratio rather than a spot price ratio.  This is
sufficient for directional context; if exact spot ratio is needed in a later phase,
it can be fetched from Bybit directly.

### D3 — Stale flag via UTC trading-hours window

Traditional markets (DXY, SPX, Gold) are marked `stale=True` when the UTC clock falls
outside Mon–Fri 13:30–20:00.  The flag is purely informational; it propagates in
`CollectorResult.data["stale_mask"]` and does not suppress the data value itself.
Crypto assets (BTC_DOM, ETH_BTC) are always `stale=False` because crypto trades 24/7.

### D4 — Input sanitisation as standalone functions

`sanitize_text`, `detect_injection`, and `sanitize_news_item` are module-level
functions in `news.py`, not methods of `NewsCollector`.  This makes them importable
without instantiating a collector, useful for sanitising webhook payloads and other
untrusted text throughout the pipeline.

Detection uses a hardcoded tuple of nine injection phrases (substring, case-insensitive)
rather than an ML model, keeping the implementation dependency-free and deterministic.
News items with `injection_flag=True` are still returned (they are not dropped); the
caller can decide whether to pass them to the LLM or discard them.

### D5 — In-memory SHA-256 deduplication for news

News items are deduplicated within a single `NewsCollector` instance using
`SHA-256(source|link|published_iso)`.  This avoids database writes at the collection
layer.  Persistence of seen hashes across restarts is deferred; if the process
restarts, a few duplicate items may appear in one poll cycle, which is acceptable.

### D6 — Blackout detection via two separate collectors

Exchange maintenance (Bybit announcements) and macro events (FOMC/CPI/NFP) are
separate concerns with different data sources and different authority.  Keeping them in
two classes (`ExchangeAnnouncementCollector`, `MacroCalendar`) preserves SRP and lets
each be tested and updated independently.  The `TradeCycleOrchestrator` (future phase)
will consult both `is_blackout()` methods before generating a trade decision.

### D7 — Hardcoded 2026 dates with optional ForexFactory override

FOMC dates for 2026 are known in advance and hardcoded.  ForexFactory's free weekly
JSON (`nfs.faireconomy.media`) is fetched on demand to supplement with the current
week's events.  If ForexFactory is unreachable, the hardcoded dates remain in effect.
This hybrid avoids a hard dependency on a third-party API for the primary function.

### D8 — Feature flag on SentimentCollector

The Fear & Greed index is useful context but its API may be flaky.  A constructor
`enabled=False` flag allows the orchestrator to disable it via config without removing
the import.  Disabled state returns `quality_flags=["disabled"]` with empty data,
which is safe for downstream consumers.

## Consequences

- All five collectors follow the same `collect() → CollectorResult` interface.
- No API keys are introduced; all sources are free and public.
- Injection detection is substring-based, not semantic; sophisticated jailbreaks that
  avoid the listed phrases will not be caught here.  The LLM prompt envelope (future
  phase) must provide a second layer of defence.
- The `ExchangeAnnouncementCollector` does not inherit `BaseCollector.__init__` because
  `BaseCollector` requires a `symbol` argument that is not applicable; it implements the
  same `collect()` / `backfill()` interface but with a sentinel `symbol="EXCHANGE"`.
