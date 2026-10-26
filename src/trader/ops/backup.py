"""Phase 9: Daily SQLite backup with retention.

Uses SQLite's online backup API (consistent copy while the app is running).
Off-server copy: point BACKUP_DIR at a synced/mounted location, or set
BACKUP_RSYNC_TARGET (e.g. ``user@host:/backups/ai-trader/``) to rsync each new
backup off the box. See docs/RUNBOOK.md.
"""

from __future__ import annotations

import datetime
import logging
import sqlite3
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def sqlite_path_from_url(database_url: str) -> Path | None:
    """Return the file path for a ``sqlite:///...`` URL, else None."""
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    raw = database_url[len(prefix) :]
    if not raw or raw == ":memory:":
        return None
    return Path(raw)


def backup_sqlite(
    db_path: Path,
    backup_dir: Path,
    keep: int = 14,
    now: datetime.datetime | None = None,
    rsync_target: str = "",
) -> Path:
    """Write ``backup_dir/trader-YYYYMMDD-HHMMSS.db`` and prune to *keep* newest."""
    if not db_path.exists():
        raise FileNotFoundError(f"database not found: {db_path}")
    now = now or datetime.datetime.now(datetime.UTC)
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"trader-{now:%Y%m%d-%H%M%S}.db"

    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    backups = sorted(backup_dir.glob("trader-*.db"))
    for old in backups[: max(0, len(backups) - keep)]:
        old.unlink(missing_ok=True)

    if rsync_target:
        try:
            subprocess.run(
                ["rsync", "-a", str(dest), rsync_target],
                check=True,
                capture_output=True,
                timeout=300,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("off-server backup copy failed: %s", type(exc).__name__)
            raise

    logger.info("database backup written: %s", dest.name)
    return dest
