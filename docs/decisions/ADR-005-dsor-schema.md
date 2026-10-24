# ADR-005: DSOR Schema and ORM Design

**Date:** 2026-10-24  
**Status:** Accepted  
**Phase:** 1

## Context

Phase 1 requires a typed database layer (DSOR) for all agent state: market data, decisions, orders, trades, lessons, audit, and system counters. The schema is specified in BUILD_PLAN.md §3.

## Decision

- **SQLAlchemy 2.x** with declarative mapped classes and `Mapped[]` type annotations. This gives full mypy coverage without a separate stub package.
- **Alembic** for migrations; `env.py` reads `DATABASE_URL` from the environment so the same migration file works on SQLite (dev) and Postgres (prod).
- All 19 tables defined as in BUILD_PLAN.md §3. Foreign keys are declared but `relationship()` objects are deferred to the phase that needs them (avoids circular imports and over-engineering Phase 1).
- `system_state` is a key-value table used for mode, kill switch, and daily counters; typed via `SystemStateHelper`.
- Postgres migration tests skip with a clear message when `TEST_DATABASE_URL` is not set; SQLite tests always run.

## Alternatives considered

- **Django ORM**: rejected — overkill, we use FastAPI not Django.
- **Raw SQL / psycopg3**: rejected — no type safety, harder to migrate.
- **Tortoise ORM (async)**: rejected — Phase 1 is sync; we can swap later if needed.

## Consequences

- All DSOR reads/writes go through repository classes; no ad-hoc `session.execute` outside of repositories.
- Schema changes require an Alembic migration; `alembic upgrade head` must run on startup.
- `config/risk.yaml` is not managed by Alembic; it is owner-only.
