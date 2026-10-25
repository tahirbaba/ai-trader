# Phase 7: Execution Layer

## Overview

Phase 7 implements the Execution layer — the only part of the system that touches exchange APIs.
The LLM never calls the exchange; the path is Decision → LessonFilter → RiskGate → **Executor**.

Key components:
- `ExchangeAdapter` (ABC) — abstract interface all brokers implement
- `PaperBroker` — realistic in-memory paper trading simulation
- `LiveAdapter` — ccxt-based Bybit adapter (disabled without `config/live_approval.yaml`)
- `OrderManager` — coordinates RiskGate verdict → broker → DSOR write
- Exceptions: `AdapterError`, `LiveNotApprovedError`, `SlPlacementError`, `DuplicateOrderError`

## Architecture

```
RiskGate.evaluate()
    │ RiskVerdict (approved=True, adjusted_size=N)
    ▼
OrderManager.submit()
    │ 1. layer2_cap_check() — independent re-verify
    │ 2. generate client_order_id
    │ 3. broker.place_order(SL, TP attached)
    │ 4. on SlPlacementError: cancel entry → close → record HALT → raise
    │ 5. write Order + Fill rows to DSOR
    ▼
ExchangeAdapter (PaperBroker | LiveAdapter)
```

## PaperBroker

Realistic simulation on live price data fed via `update_mark_price()`:

- **Fees**: configurable maker/taker rates (default 0.02% / 0.055%)
- **Fill model**: limit orders fill at price on mark price touch; market orders fill at
  `mark_price * (1 ± slippage_pct)`
- **SL/TP triggers**: checked on every `update_mark_price()` call
  - Long SL: `mark_price <= sl_price`; Long TP: `mark_price >= tp_price`
  - Short SL: `mark_price >= sl_price`; Short TP: `mark_price <= tp_price`
- **TTL expiry**: limit orders with `ttl_ts` set expire when `now() > ttl_ts`
- **Funding**: applied via `update_funding(symbol, rate)`
- **MAE/MFE**: tracked on every mark price update
- **Idempotency**: `DuplicateOrderError` on reused `client_order_id`
- **State**: purely in-memory; injectable clock for deterministic tests

## LiveAdapter

ccxt-based Bybit adapter:
- **Disabled** unless `config/live_approval.yaml` exists → raises `LiveNotApprovedError`
- ccxt imported lazily (tests don't require it installed)
- Attaches SL/TP via `params={"stopLoss": ..., "takeProfit": ...}` on `createOrder`
- If exchange rejects SL attachment: places separate `stop_market` order
- If that also fails → cancel entry + raise `SlPlacementError`

## OrderManager

- Calls `layer2_cap_check()` (Layer 2 independent re-verify) before any broker call
- Generates `client_order_id = f"{cycle_id[:8]}-{uuid4().hex[:8]}"`
- On `SlPlacementError`: cancel entry, market-close position, write `HALT` risk event, re-raise
- Writes `Order` and `Fill` rows to DSOR after successful placement
- `cancel_expired_orders()`: TTL-based cleanup
- `reconcile()`: broker vs DSOR position comparison; mismatches logged as risk events
- `force_close()`: emergency market close (kill switch, max_holding_minutes)

## Acceptance Criteria

| # | Criterion | Test |
|---|-----------|------|
| 1 | Market order fills immediately at slippage price | `test_paper_broker` |
| 2 | Limit order fills only when price touches | `test_paper_broker` |
| 3 | SL/TP trigger long and short | `test_paper_broker` |
| 4 | TTL expiry removes limit orders | `test_paper_broker` |
| 5 | DuplicateOrderError on reused client_order_id | `test_paper_broker` |
| 6 | MAE/MFE tracked on mark price updates | `test_paper_broker` |
| 7 | Funding correctly deducted from long | `test_paper_broker` |
| 8 | LiveNotApprovedError without approval file | `test_live_adapter` |
| 9 | All 10 LiveAdapter interface methods callable with mocked ccxt | `test_live_adapter` |
| 10 | SlPlacementError from live SL order fails | `test_live_adapter` |
| 11 | CapBreachError raised before broker call | `test_order_manager` |
| 12 | SlPlacementError records HALT risk event | `test_order_manager` |
| 13 | force_close closes open position | `test_order_manager` |
| 14 | reconcile detects broker/DSOR mismatch | `test_order_manager` |
| 15 | AdapterError returns failed OrderResult | `test_execution_chaos` |
| 16 | Partial fill reflected in OrderResult | `test_execution_chaos` |
