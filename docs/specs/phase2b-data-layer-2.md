# Phase 2B — Data Layer 2: Spec

## Purpose

Extend the data-collection pipeline with cross-asset context, news ingestion, exchange
announcements, macro event calendar, and market sentiment.  All new collectors return
`CollectorResult` and integrate into `build_snapshot` downstream.

## Acceptance Criteria

1. **AC-1 Cross-asset**: `CrossAssetCollector.collect()` returns a `CollectorResult` whose
   `data` contains keys `DXY`, `SPX`, `GOLD`, `BTC_DOM`, `ETH_BTC`, and `stale_mask`.
   Each value in `stale_mask` is `True` when the asset's exchange is outside trading hours.

2. **AC-2 Input sanitizer**: `sanitize_text` strips HTML tags, collapses whitespace, and
   truncates to `max_length` appending `"..."`.  `detect_injection` returns `True` for any
   of the nine forbidden phrases (case-insensitive substring match).

3. **AC-3 News deduplication**: `NewsCollector` assigns a SHA-256 hash of
   `source|link|published_iso` to each item and drops duplicates within the same instance
   lifetime.

4. **AC-4 News injection flag**: Any news item whose title or body contains an injection
   phrase receives `injection_flag=True`; the overall `data["injection_count"]` reflects the
   count.

5. **AC-5 Announcements blackout**: `ExchangeAnnouncementCollector.is_blackout(now)` returns
   `(True, reason)` when `now` falls inside a `maintain_systems` window and `(False, "")` at
   all other times.  A failed REST fetch leaves existing windows intact and does not raise.

6. **AC-6 Macro blackout**: `MacroCalendar.is_blackout(now, before_min=30, after_min=30)`
   returns `(True, event_name)` within the ±30-minute window around any seeded or
   ForexFactory-fetched high-impact event.

7. **AC-7 Sentiment feature flag**: `SentimentCollector(enabled=False).collect()` returns
   `quality_flags=["disabled"]` and no `data` keys.  On API failure the result carries
   `quality_flags=["sentiment_error"]` instead of raising.

## Out of scope

- Authenticated endpoints (no API keys)
- Persistent storage of news items (in-memory dedup only)
- Live mode (TRADING_MODE is always paper)
