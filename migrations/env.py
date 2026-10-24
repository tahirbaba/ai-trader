"""Alembic env.py — uses trader.dsor.base:Base for autogenerate."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import trader.dsor.models  # noqa: F401 — registers all mapped classes
from trader.dsor.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    # -x db_url=... passed via `alembic -x db_url=sqlite:///...` overrides everything
    x_db_url = context.get_x_argument(as_dictionary=True).get("db_url")
    if x_db_url:
        return x_db_url
    return os.environ.get(
        "DATABASE_URL", config.get_main_option("sqlalchemy.url") or "sqlite:///./trader.db"
    )


def run_migrations_offline() -> None:
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section) or {}
    cfg["sqlalchemy.url"] = _get_url()
    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
