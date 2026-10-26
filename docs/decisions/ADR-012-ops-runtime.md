# ADR-012: Ops Runtime — Scheduler, Control Plane, Dashboard

**Date**: 2026-10-27
**Status**: Proposed (Phase 9 PR)
**Phase**: 9 — Ops, Monitoring, Control

## Context

Phases 2–8 built collectors, features, the brain, the Risk Gate, execution and
learning as libraries. Nothing ran them on a schedule, nothing let the owner
stop them remotely, and nothing proved the process was alive.

## Decisions

1. **One composition root** (`trader.main`), paper only. It exits unless
   `TRADING_MODE=paper` and only constructs `PaperBroker`. Live is Phase 11.
2. **Plain asyncio scheduler** (`ops/scheduler.py`) instead of APScheduler: per-job
   timeout, error isolation, sync jobs in worker threads, daily `HH:MM` UTC jobs.
   No new dependency, trivially testable.
3. **Order path unchanged.** `TradingRuntime.run_symbol_cycle` calls
   `TradeCycleOrchestrator.run_cycle` (Decision → LessonFilter → RiskGate) and
   only then `OrderManager.submit` (Layer 2). The runtime re-checks kill switch /
   pause / mode *immediately before* `submit`. No LLM is called by the runtime.
4. **Controller** is the single implementation of every owner command; Telegram
   and HTTP both call it. `mode_set` accepts only `"paper"`.
5. **Kill switch** = DSOR flag OR local file flag; unreadable state = ON.
   `/resume` clears only the pause. `/unhalt` clears the DSOR flag, but is
   refused after HALT_CLOSE_FAILED / FORCE_CLOSE_FAILED / RECONCILE_MISMATCH
   (host-only `make unhalt`). The file flag is never cleared remotely.
   `Controller.stop` engages the switch *before* taking the shared book RLock;
   `submit` re-checks the switch *inside* that lock, so an order can never be
   opened after a stop without being closed by it.
6. **Telegram** via Bot API long-polling with `requests` (no new dependency).
   Updates are dropped without a reply unless both `chat.id` and `from.id` equal
   the configured positive (private-chat) id; group ids are refused. Transport
   errors are re-raised without the request URL (which embeds the token), and
   job-error logs/alerts redact bot tokens and URLs. Messages are plain text (no parse mode); LLM text is
   control-char-stripped and length-capped.
7. **Dashboard** = FastAPI + TradingView Lightweight Charts 4.2.0 (CDN, pinned),
   loaded with SRI, localhost-bound, Host-header allowlist (DNS rebinding),
   optional basic auth, no OpenAPI docs, unauthenticated `/healthz` for Docker. All routes are GET
   except `POST /api/kill-switch`, which can only engage and requires an
   `X-Trader-Action: kill-switch` header (CSRF guard). Candles come from
   Bybit public REST; markers/equity/trades/lessons/LLM costs from DSOR.
8. **TradeLedger** writes `trades` rows from paper position transitions; new
   nullable `trades.symbol` column (migration `a9f1c0d2e3b4`).
9. **Reconciliation** in the runtime compares broker positions with DSOR open
   trades. `OrderManager.reconcile()` is *not* scheduled: it flags every normal
   market-filled position as a mismatch (filled orders are not `open`) and would
   engage the kill switch on every trade. Reported for a separate execution/ fix.
10. **Post-trade review** job is code-only (`PostTradeReviewer` with no LLM
    output) — Phase 8 has no LLM reviewer agent. Shadow outcomes replay 5m
    candles after a Risk-Gate-blocked decision (SL-first on same-candle touch).
11. **Heartbeat** pings `HEALTHCHECK_PING_URL` every 60 s; the URL is never logged.
12. **Backups**: SQLite online backup daily, keep 14, optional rsync off-server.

## Consequences

- Known gaps (follow-ups): `trades.decision_id` is not linked (the orchestrator
  does not return the decision row id); a restart closes open paper trades with
  no PnL, so that loss is not counted toward daily/weekly limits.

- Paper state is in memory; a restart closes leftover open trade rows as
  `paper_state_lost_on_restart`.
- DoD items needing wall-clock time (72 h soak, external dead-man alert) are
  owner-run procedures in `docs/RUNBOOK.md`, not unit tests.
