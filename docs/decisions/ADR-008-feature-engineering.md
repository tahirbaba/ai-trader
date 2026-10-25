# ADR-008: Feature Engineering Pipeline Design

**Status**: Accepted
**Date**: 2026-10-24
**Phase**: 3

## Context

Phase 3 introduces the feature engineering layer that transforms raw collector data
(OHLCV candles, order book snapshots, derivatives feeds, and alternative data) into a
compact, deterministic `FeaturePack`.  Several design decisions were made.

## Decisions

### 1. Pure-numpy indicators, no pandas / pandas-ta

**Decision**: All technical indicator functions (`ema`, `sma`, `rsi`, `macd`, `atr`,
`bollinger`, `vwap`, `swing_highs`, `swing_lows`, `bb_pct`) are implemented in
`indicators.py` using only numpy and the Python standard library.

**Rationale**:
- Eliminates a heavy dependency (pandas-ta) for a small set of indicators.
- Makes the computation graph fully auditable and testable without mocking.
- Removes the risk of upstream library changes altering indicator values.
- Pure Python + numpy is sufficient for the indicator set required by Phase 3.

**Rejected alternative**: `pandas-ta` — convenient but opaque; its EMA/RSI defaults
differ from Wilder's method in subtle ways; adds pandas as a transitive dependency.

### 2. Frozen Pydantic model (`FeaturePack`)

**Decision**: `FeaturePack` is a `frozen=True` Pydantic v2 `BaseModel`.

**Rationale**:
- Immutability prevents accidental in-place mutation between pipeline stages.
- Serialisation via `model_dump()` is deterministic (sorted keys in JSON hash).
- Pydantic validation enforces types at construction time.

**Trade-off**: `model_copy(update=...)` is required to attach the hash after all numeric
fields are computed.

### 3. SHA-256 `features_hash` for determinism verification

**Decision**: A SHA-256 digest of all numeric fields (sorted JSON) is stored in
`FeaturePack.features_hash` and in the `MarketSnapshot.features_hash` column.

**Rationale**:
- Provides a one-line regression test: any unintended change to indicator math
  causes the golden hash to mismatch.
- Can be used downstream (LLM prompt deduplication, re-processing guards).
- Does not include string/bool fields to avoid false mismatches from label changes.

### 4. Six-group independent confluence scoring

**Decision**: `confluence_score` is split into six logically independent groups:
`trend`, `momentum`, `derivatives`, `orderflow`, `level`, `regime`.  Each
contributes at most 1 point.

**Rationale**:
- Prevents over-weighting from correlated signals (e.g. multiple momentum proxies).
- Integer output (0–6) is easy to threshold in downstream decision logic.
- `high_vol_breakout` deliberately counts for both long and short regime groups
  because elevated volatility can be traded in either direction.

**Rejected alternative**: weighted sum — more tunable but opaque and subject to
overfitting on small historical samples.

### 5. Builder receives historical context as plain lists

**Decision**: `build_feature_pack` accepts `funding_history`, `oi_history`, etc. as
plain `list[float]` parameters rather than querying the DSOR itself.

**Rationale**:
- Keeps the feature engineering layer stateless and side-effect free.
- Enables deterministic unit testing without a database.
- The caller (future `TradeCycleOrchestrator`) is responsible for fetching and
  caching the rolling history windows.

### 6. Timestamp derived from last candle, not `datetime.now()`

**Decision**: `FeaturePack.ts` is set from `candles[-1]["ts_ms"]` rather than wall
clock.

**Rationale**:
- Determinism requirement: calling `build_feature_pack` twice with the same candle
  data must produce the same hash.
- `datetime.now()` would invalidate the hash on every call.

### 7. `snapshot_writer.py` — no auto-commit

**Decision**: `save_feature_pack(session, fp)` adds the ORM row to the session but
never calls `session.commit()`.

**Rationale**: Consistent with the repository pattern used throughout Phase 1 DSOR.
Callers batch multiple inserts and commit once for atomicity and performance.

## Consequences

- The golden-file test (`test_build_feature_pack_golden_hash`) must be updated
  whenever intentional changes are made to indicator math or field composition.
- numpy is now a first-class runtime dependency (`numpy>=1.26`).
- The features module does not import from `data/` (collectors); coupling is
  one-way: builder consumes dicts, not collector objects.
