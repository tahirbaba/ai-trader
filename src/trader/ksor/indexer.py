"""KSOR Indexer — SQLite FTS5 full-text + tag index over KsorEntry objects."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trader.ksor.loader import KsorEntry

logger = logging.getLogger(__name__)

_CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS ksor_fts USING fts5(
    entry_id,
    status,
    type,
    tags,
    regime,
    symbols,
    condition,
    body,
    tokenize = "unicode61"
);
"""

_CREATE_META = """
CREATE TABLE IF NOT EXISTS ksor_meta (
    entry_id TEXT PRIMARY KEY,
    status   TEXT NOT NULL,
    type     TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    regime_json TEXT NOT NULL DEFAULT '[]',
    symbols_json TEXT NOT NULL DEFAULT '[]',
    rule_json TEXT,
    effect   TEXT,
    effect_value REAL,
    file_path TEXT NOT NULL DEFAULT ''
);
"""


class KsorIndexer:
    """Builds and queries an in-process SQLite FTS5 index of KSOR entries.

    Parameters
    ----------
    db_path:
        Path to the SQLite DB file, or ``":memory:"`` for a transient in-memory DB
        (useful for tests and the retriever).
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, entries: list[KsorEntry]) -> None:
        """(Re-)populate the index from a list of KsorEntry objects.

        Clears any existing data before inserting.
        """
        conn = self._get_conn()
        conn.execute("DELETE FROM ksor_fts")
        conn.execute("DELETE FROM ksor_meta")
        for entry in entries:
            self._upsert(conn, entry)
        conn.commit()

    def upsert(self, entry: KsorEntry) -> None:
        """Insert or replace a single entry in the index."""
        conn = self._get_conn()
        self._upsert(conn, entry)
        conn.commit()

    def search(
        self,
        query: str,
        status_filter: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Full-text search over entry text.

        Parameters
        ----------
        query:          FTS5 query string (e.g. ``"funding short"``).
        status_filter:  If provided, only return entries with these statuses.
        limit:          Maximum number of results.

        Returns a list of dicts with keys: entry_id, status, type, tags_json,
        regime_json, symbols_json, rule_json, effect, effect_value, file_path.
        """
        conn = self._get_conn()
        if query.strip():
            sql = """
                SELECT m.*
                FROM ksor_fts f
                JOIN ksor_meta m ON m.entry_id = f.entry_id
                WHERE ksor_fts MATCH ?
            """
            params: list[Any] = [query]
        else:
            sql = "SELECT m.* FROM ksor_meta m WHERE 1=1"
            params = []

        if status_filter:
            placeholders = ",".join("?" * len(status_filter))
            sql += f" AND m.status IN ({placeholders})"
            params.extend(status_filter)

        sql += " LIMIT ?"
        params.append(limit)

        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]

    def get_all(self, status_filter: list[str] | None = None) -> list[dict[str, Any]]:
        """Return all entries (optionally filtered by status)."""
        conn = self._get_conn()
        sql = "SELECT * FROM ksor_meta"
        params: list[Any] = []
        if status_filter:
            placeholders = ",".join("?" * len(status_filter))
            sql += f" WHERE status IN ({placeholders})"
            params.extend(status_filter)
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        conn = self._get_conn()
        conn.execute(_CREATE_FTS)
        conn.execute(_CREATE_META)
        conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _upsert(self, conn: sqlite3.Connection, entry: KsorEntry) -> None:
        entry_id = entry.id or entry.file_path

        # Build FTS document text
        tags_str = " ".join(entry.tags)
        regime_str = " ".join(entry.regime)
        symbols_str = " ".join(entry.symbols)

        # Delete existing FTS row (if any) before re-inserting
        conn.execute("DELETE FROM ksor_fts WHERE entry_id = ?", (entry_id,))
        conn.execute(
            """
            INSERT INTO ksor_fts(entry_id, status, type, tags, regime, symbols, condition, body)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                entry.status,
                entry.type,
                tags_str,
                regime_str,
                symbols_str,
                entry.condition,
                entry.body,
            ),
        )

        conn.execute(
            """
            INSERT OR REPLACE INTO ksor_meta
            (entry_id, status, type, tags_json, regime_json, symbols_json,
             rule_json, effect, effect_value, file_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                entry.status,
                entry.type,
                json.dumps(entry.tags),
                json.dumps(entry.regime),
                json.dumps(entry.symbols),
                json.dumps(entry.rule) if entry.rule is not None else None,
                entry.effect,
                entry.effect_value,
                entry.file_path,
            ),
        )
