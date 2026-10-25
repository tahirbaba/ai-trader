# ADR-009: Claude Brain + TradeCycleOrchestrator

**Status:** Accepted
**Date:** 2026-10-25

## Context

Phase 6 introduces the LLM reasoning layer. We need a safe, testable design where:
- The LLM is sandboxed away from all I/O (network, exchange, DB)
- LLM output is treated as untrusted input (always Pydantic-validated)
- External text (news, alerts) cannot become LLM instructions
- Budget enforcement happens before any API call

## Decision

1. **LlmClient** is a thin wrapper — it tracks cost, enforces budget, logs every call to `llm_calls` DSOR table, and raises typed exceptions.

2. **Sanitizer** wraps all external text in `<label>...</label>` data blocks before it enters any prompt. System prompts explicitly mark these as DATA.

3. **Analyst agents** are synchronous methods wrapped in `asyncio.to_thread` for parallelism. Each has an independent timeout; failure returns a neutral report (never propagates).

4. **TradeCycleOrchestrator** is pure Python with no LLM calls. It coordinates agents in a fixed order with explicit stop conditions. An asyncio.Lock per symbol prevents concurrent cycles.

5. **BrainDecision parse flow**: extract JSON from LLM response (strip markdown fences) → `BrainDecision.model_validate_json()` → on any failure, return `BrainDecision(action='NO_TRADE')`.

## Consequences

- Tests run fully offline (mock anthropic SDK)
- Any LLM misbehaviour degrades to NO_TRADE, never to an uncontrolled trade
- Budget enforced deterministically before any API call is made
- Prompt injection from news/alerts cannot change agent behaviour (text is data, not instructions)
