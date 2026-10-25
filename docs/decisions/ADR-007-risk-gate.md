# ADR-007: Risk Gate Design

**Status:** Accepted
**Date:** 2026-10-25
**Phase:** 5

## Context

The autonomous AI trader needs a deterministic, auditable safety layer that sits between
the LLM brain's Decision output and the order Executor. This layer must:

1. Enforce all hard risk limits from `config/risk.yaml`
2. Never be influenced by LLM output beyond accepting the Decision struct
3. Log every check result to DSOR
4. Be independently verifiable (Layer 2 check in Executor)

## Decisions

### D1: RiskGate is pure Python, no I/O during evaluate()
All state needed for evaluation (account state, market state) is passed in as arguments.
No database reads during evaluation. This makes the gate fast, testable, and auditable.

### D2: Fail-fast check order
Checks are ordered from cheapest/most certain to most expensive. System-level checks
(kill switch, mode) come first, then market/account checks, then sizing math.
This minimizes wasted computation on invalid inputs.

### D3: Layer 2 independence
`layer2_check.py` is a completely independent code path in the Executor. It imports only
from `sizing.py` — never from `gate.py`. This ensures that a bug in the RiskGate cannot
silently bypass the cap enforcement.

### D4: SizingError and CapBreachError are separate exception types
`SizingError` is raised by `sizing.py` when a position cannot be sized within limits.
`CapBreachError` is raised by `layer2_check.py` for Layer 2 violations. They are defined
in their respective modules to preserve independence.

### D5: TRADING_MODE=live requires live_approval.yaml
The gate rejects any order if `TRADING_MODE=live` but `config/live_approval.yaml` does not
exist. This creates a physical barrier against accidental live trading.

### D6: LLM never reaches exchange
The Decision struct is validated by Pydantic. If parsing fails, the gate returns NO_TRADE.
The path is strictly: Decision → LessonFilter → RiskGate → Executor.

### D7: Risk limits are owner-only
`config/risk.yaml` is never modified by code. The RiskGate reads it at startup via
`Settings`. Any change to limits requires a human git commit.

## Consequences

- All risk checks are testable in isolation with no database or exchange mocks
- Every rejection is logged to DSOR `risk_checks` and `risk_events` tables
- Layer 2 provides defence-in-depth: two independent code paths must both agree before any order is sent
- Adding a new risk check requires: (1) adding to gate.py, (2) adding a test, (3) updating this ADR

## Rejected Alternatives

- **Database-backed gate**: rejected because it adds I/O latency and failure modes on the critical trading path
- **Single merged check layer**: rejected because it creates a single point of failure for cap enforcement
- **Config in code**: rejected because it would require code changes (and PR review) for limit adjustments
