"""Phase 9: Engine + session-factory construction for the running system."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from trader.dsor import models  # noqa: F401  (registers all tables on Base.metadata)
from trader.dsor.base import Base


def make_session_factory(database_url: str, create_tables: bool = True) -> sessionmaker[Session]:
    """Return a sessionmaker bound to *database_url*.

    ``create_tables`` runs ``Base.metadata.create_all`` (idempotent) so a fresh
    paper-local SQLite file is usable without a separate migration step.
    """
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args, future=True)
    if create_tables:
        Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
