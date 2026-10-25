# ADR-010: Execution Layer Design

**Date**: 2026-10-25
**Status**: Accepted
**Phase**: 7 — Execution

## Context

The system needs to place orders on a real (or simulated) exchange based on
`RiskVerdict` from the RiskGate. The execution layer must:
1. Never allow the LLM to touch the exchange
2. Perform an independent Layer 2 cap re-check before every order
3. Simulate realistically for development/backtesting without live exchange access
4. Be guarded by a hard file-system gate (`config/live_approval.yaml`) before live trading

## Decisions

### 1. Abstract Adapter Interface

All exchange interactions go through `ExchangeAdapter` (ABC). This decouples
`OrderManager` from any specific exchange or simulation, enabling dependency
injection in tests and easy swap from paper to live.

### 2. PaperBroker: In-Memory Simulation

`PaperBroker` is purely in-memory with no I/O. DB writes happen only in
`OrderManager`. This keeps the simulation fast, deterministic, and testable
with injectable clocks.

Mark price must be fed externally (`update_mark_price`). This mirrors the
real system where market data comes from a separate data pipeline.

### 3. LiveAdapter: Disabled by Default

`LiveAdapter` raises `LiveNotApprovedError` unless `config/live_approval.yaml`
exists. This file is never created automatically by any code path. It requires
explicit human action, matching the BUILD_PLAN.md Phase 11 gate requirement.

ccxt is imported lazily (`import ccxt` inside `__init__`) so tests run without
ccxt installed.

### 4. Layer 2 Cap Check in OrderManager

`OrderManager.submit()` calls `layer2_cap_check()` before calling the broker.
This is an independent re-check that does not trust the `RiskVerdict`. It uses
only the settings object and exchange metadata (lot_size, taker_fee).
`CapBreachError` aborts the order before any exchange call.

### 5. SL Placement Failure Handling

If `SlPlacementError` is raised after an entry order succeeds:
1. Cancel the entry order (best-effort)
2. Market-close any position (best-effort)
3. Record a `HALT` risk event to DSOR
4. Re-raise `SlPlacementError` to the caller

This ensures we never hold an unprotected position after an SL placement failure.

### 6. client_order_id Generation

`client_order_id = f"{cycle_id[:8]}-{uuid4().hex[:8]}"` provides traceability
(cycle prefix) while being collision-resistant (UUID suffix). The broker
enforces idempotency: reusing a `client_order_id` raises `DuplicateOrderError`.

### 7. DSOR Writes in OrderManager Only

`PaperBroker` never writes to the database. All `Order` and `Fill` rows are
written by `OrderManager` after a successful broker call. This keeps the broker
a pure simulation engine and the DB writes auditable in one place.

## Alternatives Considered

- **Single adapter class with a mode flag**: rejected because it makes the
  guarded live path harder to reason about and test.
- **DB writes in PaperBroker**: rejected because it couples the simulation to
  the database schema and makes tests heavier.
- **Sync SL placement only on LiveAdapter**: rejected because the fallback
  (separate stop_market order) is the only reliable path for many exchange APIs.

## Consequences

- All exchange access is through a single abstraction; future adapters (testnet,
  other exchanges) only need to implement `ExchangeAdapter`.
- The `config/live_approval.yaml` file gate is the single hard barrier before
  live trading; it cannot be bypassed in code.
- DSOR `Order` and `Fill` rows are always written in `OrderManager.submit()`,
  making the DB the canonical source of truth for what was placed.
