---
id: L-0001
type: lesson
status: active
tags: [funding_negative, short, squeeze]
regime: [range, trend_up]
symbols: []
condition: "Short signal while funding is strongly negative"
rule:
  all:
    - {field: decision.side, op: eq, value: short}
    - {field: features.funding_z, op: lt, value: -2.0}
effect: confidence_penalty
effect_value: 0.20
action: "Reduce confidence by 0.20 when shorting with strongly negative funding"
evidence_trades: []
win_stats: {n: 0, wins: 0}
created: "2026-10-25"
last_validated: null
expires: "2027-04-25"
---

# No Short with Strongly Negative Funding

## Condition

A short entry is proposed while the funding z-score is strongly negative (< -2.0).

## Why This Matters

When funding is strongly negative, shorts are already paying longs. This means:

1. **Crowded short side:** Many traders are already short, creating latent demand for a
   short squeeze.
2. **Funding payment burden:** Being short costs you funding at every settlement. This
   erodes profitability even if the directional bet is correct.
3. **Squeeze risk:** Any catalyst that triggers short covering can cause a rapid, violent
   upward move — stopping out shorts before the downtrend resumes (if it does).

## Effect

Apply a confidence penalty of 0.20 to the LLM-generated confidence score. This reduces
the effective confidence, making it harder to clear the `min_confidence` threshold in
risk.yaml (default: 0.70).

## When to Override

This lesson can be overridden by the decision agent only with explicit justification:
- Very strong bearish structure breakdown (e.g., major support lost on high volume)
- Funding expected to flip (e.g., post-squeeze reset anticipated)
- Multiple confirming signals (HTF trend down, bearish regime, negative OI divergence)

If overriding, the `lessons_considered` field in the decision must explicitly list this
lesson and the reason for the override.

## Evidence

No confirmed evidence trades yet (lesson seeded at system initialization). Will be updated
as trades are reviewed.

See also: `ksor/playbook/002_funding_oi.md` for the general funding/OI playbook rules.
