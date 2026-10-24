# Phase 1 Spec: Config, Secrets, and DSOR

## Goal
Typed config, safe secret handling, and the database.

## Acceptance Criteria

### AC-1: Pydantic settings
- Settings loads from `.env` + `config/app.yaml` + `config/risk.yaml`.
- Cross-field validation raises on: `max_loss_per_trade_usd > max_margin_per_trade_usd`,
  `daily_loss_limit_usd < max_loss_per_trade_usd`, `margin_mode != isolated`.
- Raises `ValidationError` if `TRADING_MODE=live` and `config/live_approval.yaml` is absent.
- Model names come from config, never hardcoded.

### AC-2: DSOR models and migrations
- All tables from BUILD_PLAN.md §3 exist as SQLAlchemy mapped classes.
- `alembic upgrade head` + `alembic downgrade base` round-trip cleanly on SQLite.
- Same migration works on Postgres when `TEST_DATABASE_URL` is set (skipped otherwise).

### AC-3: Repositories
- Each table has a typed repository class with `add`, `get_by_id`, and at least one domain query.
- All calls go through a provided `Session`; no global session state.

### AC-4: system_state helpers
- `get_mode()`, `set_kill_switch(on)`, `is_kill_switch_on()`, `increment_daily_loss(usd)`,
  `get_daily_loss()`, `reset_daily_counters()`.

### AC-5: Logging
- Structured JSON log lines (timestamp, level, logger, message, extra fields).
- Secret redaction filter: values whose keys match `API_KEY`, `SECRET`, `TOKEN`, `PASSWORD`,
  `WEBHOOK_SECRET` (case-insensitive) are replaced with `***REDACTED***`.
- Redaction works on nested dicts in the `extra` field.

### AC-6: Config audit
- On `record_startup_config()`: reads git HEAD SHA and computes SHA-256 of `config/app.yaml`
  + `config/risk.yaml`; writes a `config_audit` row.

### AC-7: Clock check
- `check_clock_drift(session, exchange_time_ms)` computes `|host_ms − exchange_ms|`.
- Stores result in `system_state` table under key `last_clock_drift_ms`.
- Returns the drift value.
