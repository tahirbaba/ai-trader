# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- Phase 3: `FeaturePack` — frozen Pydantic model holding all computed features for a single bar.
- Phase 3: `indicators.py` — pure-numpy EMA, SMA, RSI (Wilder's), MACD, ATR (Wilder's), Bollinger Bands, VWAP, swing highs/lows, BB%.
- Phase 3: `alignment.py` — `trend_direction`, `mtf_alignment`, `htf_aligned` for multi-timeframe trend logic.
- Phase 3: `derivatives_features.py` — `funding_zscore`, `oi_price_divergence`, `basis_zscore`.
- Phase 3: `orderflow_features.py` — `cvd_divergence`, `spread_percentile`.
- Phase 3: `regime.py` — `classify_regime` with five regimes in strict priority order (news_shock > high_vol_breakout > trend_up > trend_down > range).
- Phase 3: `confluence.py` — six-group independent `confluence_score` (0–6) and `confluence_score_both`.
- Phase 3: `builder.py` — `build_feature_pack()` deterministic assembler; same inputs always produce the same `features_hash`.
- Phase 3: `snapshot_writer.py` — `save_feature_pack()` bridges `FeaturePack` → `MarketSnapshot` ORM row (no auto-commit).
- Phase 3: 87 unit tests covering all new modules including golden-file determinism test with hardcoded SHA-256 hash.
- ADR-008: Feature engineering pipeline design decisions (seven sub-decisions).
- `numpy>=1.26` added to project dependencies.
- `docs/specs/phase3-features.md` — ten acceptance criteria.

- Phase 2B: `CrossAssetCollector` — DXY, SPX, Gold via yfinance; BTC dominance and ETH/BTC via CoinGecko free API; stale-mask for traditional-market assets outside trading hours.
- Phase 2B: `InputSanitizer` — standalone `sanitize_text`, `detect_injection`, `sanitize_news_item` functions for stripping HTML, collapsing whitespace, truncation, and injection-phrase detection.
- Phase 2B: `NewsCollector` — polls CryptoPanic/CoinDesk free RSS feeds via feedparser; SHA-256 in-memory deduplication; per-item injection flagging.
- Phase 2B: `ExchangeAnnouncementCollector` — fetches Bybit V5 announcement endpoint; `is_blackout(now)` returns True during `maintain_systems` windows or windows titled "maintenance".
- Phase 2B: `MacroCalendar` — hardcoded 2026 FOMC/CPI/NFP dates with optional ForexFactory free JSON override; `is_blackout(now, before_min=30, after_min=30)` and `upcoming(n)`.
- Phase 2B: `SentimentCollector` — Fear & Greed index from alternative.me; feature-flagged via `enabled` param; graceful degradation with quality flags.
- `BybitRestClient.get_announcements()` — new convenience method wrapping the V5 announcements endpoint.
- ADR-007: documents cross-asset/news/macro design decisions (eight sub-decisions).
- `scripts/soak.py` updated to instantiate and report Phase 2B collectors each poll round.
- `yfinance>=0.2` and `feedparser>=6.0` added to project dependencies.

- Phase 1: typed Pydantic settings (AppSettings, RiskSettings) loading .env + app.yaml + risk.yaml with cross-field validation and live-mode guard.
- Phase 1: SQLAlchemy 2.x ORM models for all 19 DSOR tables (BUILD_PLAN.md §3).
- Phase 1: Alembic initial migration (SQLite + Postgres support).
- Phase 1: Typed repository classes for all DSOR tables.
- Phase 1: SystemStateHelper (mode, kill switch, daily counters).
- Phase 1: Structured JSON logging with secret redaction filter.
- Phase 1: Clock drift check stored in system_state.
- Phase 1: Startup config audit (git SHA + file hash → config_audit table).
- ADR-005: DSOR schema and ORM design decisions.
- Phase 0: repo scaffold, tooling, feasibility analysis.
