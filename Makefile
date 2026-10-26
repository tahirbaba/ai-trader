# ai-trader — Phase 9 operations. All targets run PAPER mode only.
.PHONY: help install test lint typecheck check migrate paper-local paper-once kill unkill unhalt \
        backup docker-build docker-up docker-down docker-logs

DB_URL ?= sqlite:///./trader.db

help:
	@echo "install      uv sync (incl. dev deps)"
	@echo "check        ruff + mypy + pytest"
	@echo "migrate      alembic upgrade head on DATABASE_URL ($(DB_URL))"
	@echo "paper-local  run the full system in PAPER mode with uv only (no Docker)"
	@echo "paper-once   one brain cycle + one monitor tick, then exit (smoke test)"
	@echo "kill         engage the local kill-switch file flag"
	@echo "unkill       remove the local kill-switch file flag"
	@echo "unhalt       clear the DB kill-switch flag (after verifying positions)"
	@echo "backup       one-off SQLite backup into ./backups"
	@echo "docker-*     build / up / down / logs (VPS)"

install:
	uv sync

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy src/trader

check: lint typecheck test

migrate:
	DATABASE_URL=$${DATABASE_URL:-$(DB_URL)} uv run alembic upgrade head

paper-local: migrate
	mkdir -p data backups
	TRADING_MODE=paper uv run python -m trader.main --host 127.0.0.1 --port 8000

paper-once: migrate
	mkdir -p data
	TRADING_MODE=paper uv run python -m trader.main --once

kill:
	mkdir -p data
	date -u +"manual %Y-%m-%dT%H:%M:%SZ" > data/KILL_SWITCH
	@echo "kill-switch file flag set: data/KILL_SWITCH"

unkill:
	rm -f data/KILL_SWITCH
	@echo "file flag removed. If the DB flag is also set, send /unhalt on Telegram (or make unhalt)."

unhalt:
	@echo "Clearing the DB kill-switch flag (host-side owner action)."
	uv run python -c "import os; from pathlib import Path; from trader.dsor.db import make_session_factory; from trader.ops.kill_switch import KillSwitch; ks = KillSwitch(make_session_factory(os.environ.get('DATABASE_URL') or '$(DB_URL)'), Path('data/KILL_SWITCH')); print(ks.clear_db_flag(source='host_make_unhalt'))"

backup:
	uv run python -c "from pathlib import Path; from trader.ops.backup import backup_sqlite; print(backup_sqlite(Path('trader.db'), Path('backups')))"

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f --tail=200
