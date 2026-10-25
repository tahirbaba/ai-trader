# Phase 3 — Feature Engineering Spec

## Goal
Transform raw collector data into a compact, deterministic `FeaturePack` with a code-computed
`confluence_score`. Same input always produces same output (golden-file verified).

## Acceptance Criteria

1. **AC-1 Determinism**: `build_feature_pack(...)` called twice with identical arguments returns
   two `FeaturePack` objects where `fp1.features_hash == fp2.features_hash`. No `datetime.now()`,
   no randomness, no mutable defaults that accumulate state.

2. **AC-2 Frozen model**: `FeaturePack` is a `frozen=True` Pydantic model. Any attempt to assign
   a field after construction raises a `ValidationError` or `TypeError`.

3. **AC-3 Pure-numpy indicators**: All indicator functions (`ema`, `sma`, `rsi`, `macd`, `atr`,
   `bollinger`, `vwap`, `swing_highs`, `swing_lows`, `bb_pct`) are implemented in
   `indicators.py` using only numpy and the Python standard library. No pandas, no pandas-ta.

4. **AC-4 Graceful insufficient-data**: Every indicator function returns `None` (no exception)
   when the input list is shorter than the required period. The builder propagates `None`
   cleanly into the `FeaturePack` fields.

5. **AC-5 Confluence score range**: `confluence_score` returns an integer in `[0, 6]`.
   Each of the six independence groups (`trend`, `momentum`, `derivatives`, `orderflow`,
   `level`, `regime`) contributes at most 1 point. `high_vol_breakout` counts for both long
   and short regime groups simultaneously.

6. **AC-6 Regime priority**: `classify_regime` applies rules in strict priority order:
   `news_shock` > `high_vol_breakout` > `trend_up` > `trend_down` > `range`.

7. **AC-7 Snapshot persistence**: `save_feature_pack(session, fp)` converts a `FeaturePack`
   into a `MarketSnapshot` ORM row and adds it to the SQLAlchemy session without committing.
   The stored `features_hash` and `regime` columns match the `FeaturePack` fields exactly.

8. **AC-8 Golden-file hash**: The `test_build_feature_pack_deterministic` test hard-codes
   a known SHA-256 `features_hash` value computed from a fixed input. Any change to the
   numeric output of the feature pipeline causes the test to fail, ensuring accidental
   regressions are caught.

9. **AC-9 Data quality propagation**: Quality flags passed to `build_feature_pack` via
   `quality_flags` appear unchanged in `FeaturePack.data_quality_flags`. No flags are
   silently dropped.

10. **AC-10 Multi-timeframe alignment**: `mtf_alignment` accepts candle series for up to
    five timeframes (`5m`, `15m`, `1h`, `4h`, `1d`) and returns a direction string for each.
    Missing timeframes default to `"neutral"`. `htf_aligned` returns `True` only when all
    HTF keys share the same non-neutral direction.

## Module layout

```
src/trader/features/
    __init__.py          — re-exports FeaturePack
    indicators.py        — pure-numpy technical indicators
    alignment.py         — trend direction and MTF alignment
    derivatives_features.py  — funding z-score, OI divergence, basis z-score
    orderflow_features.py    — CVD divergence, spread percentile
    regime.py            — market regime classifier
    confluence.py        — confluence score aggregator
    feature_pack.py      — FeaturePack Pydantic model
    builder.py           — build_feature_pack() assembler
    snapshot_writer.py   — save_feature_pack() ORM bridge
```

## Non-goals
- No live data fetching in features/ (collectors handle that).
- No LLM involvement in feature computation.
- No pandas or external TA libraries.
