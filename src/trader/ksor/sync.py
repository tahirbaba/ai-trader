"""KSOR Sync — synchronises /ksor/lessons/*.md files with the lessons_index DSOR table.

Walks /ksor/lessons/*.md, upserts LessonIndex rows, marks missing files as deprecated.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from trader.dsor.models import LessonIndex
from trader.ksor.loader import load_ksor_entries

logger = logging.getLogger(__name__)

_DEFAULT_KSOR_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "ksor"


def sync_lessons_index(session: Session, ksor_root: Path | None = None) -> None:
    """Synchronise the DSOR ``lessons_index`` table with on-disk lesson files.

    - Walks ``ksor_root/lessons/*.md`` and upserts a row for each file.
    - Any lesson_id already in the table but whose file is now missing is set to
      ``status = 'deprecated'``.

    Parameters
    ----------
    session:    SQLAlchemy session (caller is responsible for committing).
    ksor_root:  Root of the KSOR directory tree.  Defaults to the repo's ``/ksor``.
    """
    root = (ksor_root or _DEFAULT_KSOR_ROOT).resolve()
    lessons_dir = root / "lessons"

    # Load all .md files from the lessons directory
    if not lessons_dir.exists():
        logger.warning("sync_lessons_index: lessons directory %s does not exist", lessons_dir)
        return

    entries = load_ksor_entries(lessons_dir)
    file_lesson_ids: set[str] = set()

    for entry in entries:
        if not entry.id:
            logger.warning("sync_lessons_index: entry at %s has no id, skipping", entry.file_path)
            continue

        file_lesson_ids.add(entry.id)

        # Upsert
        existing = session.execute(
            select(LessonIndex).where(LessonIndex.lesson_id == entry.id)
        ).scalar_one_or_none()

        if existing is not None:
            # Update mutable fields
            existing.status = entry.status
            existing.tags = json.dumps(entry.tags)
            existing.regime = json.dumps(entry.regime)
            existing.setup = entry.condition
            existing.rule_json = json.dumps(entry.rule) if entry.rule is not None else None
            existing.evidence_trade_ids = json.dumps(entry.evidence_trades)
            existing.win_stats = json.dumps(entry.win_stats)
        else:
            row = LessonIndex(
                lesson_id=entry.id,
                status=entry.status,
                tags=json.dumps(entry.tags),
                regime=json.dumps(entry.regime),
                setup=entry.condition,
                rule_json=json.dumps(entry.rule) if entry.rule is not None else None,
                evidence_trade_ids=json.dumps(entry.evidence_trades),
                win_stats=json.dumps(entry.win_stats),
            )
            session.add(row)

    # Mark missing files as deprecated
    all_rows = session.execute(select(LessonIndex)).scalars().all()
    for row in all_rows:
        if row.lesson_id not in file_lesson_ids and row.status != "deprecated":
            logger.info(
                "sync_lessons_index: marking %s as deprecated (file not found)",
                row.lesson_id,
            )
            row.status = "deprecated"
