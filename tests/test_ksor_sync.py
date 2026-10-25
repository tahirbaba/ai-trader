"""Tests for ksor/sync.py — sync_lessons_index() updates DSOR lessons_index table."""

import textwrap
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from trader.dsor.base import Base
from trader.dsor.models import LessonIndex
from trader.ksor.sync import sync_lessons_index

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session() -> Session:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return Session(engine)


def write_lesson_md(path: Path, id: str, status: str = "active", tags: list[str] | None = None) -> None:
    tags_str = ", ".join(tags or [])
    content = textwrap.dedent(f"""\
        ---
        id: {id}
        type: lesson
        status: {status}
        tags: [{tags_str}]
        regime: []
        symbols: []
        condition: "Test condition"
        rule: null
        effect: confidence_penalty
        effect_value: 0.10
        action: "Test action"
        evidence_trades: []
        win_stats: {{}}
        created: "2026-10-25"
        ---
        Body text for {id}.
    """)
    path.write_text(content)


# ---------------------------------------------------------------------------
# After sync, rows reflect files
# ---------------------------------------------------------------------------


def test_sync_creates_rows(tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir()
    write_lesson_md(lessons_dir / "L-0001.md", "L-0001")
    write_lesson_md(lessons_dir / "L-0002.md", "L-0002")

    session = make_session()
    sync_lessons_index(session, ksor_root=tmp_path)
    session.commit()

    rows = session.execute(select(LessonIndex)).scalars().all()
    lesson_ids = {r.lesson_id for r in rows}
    assert "L-0001" in lesson_ids
    assert "L-0002" in lesson_ids


def test_sync_upserts_on_second_call(tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir()
    write_lesson_md(lessons_dir / "L-0001.md", "L-0001")

    session = make_session()
    sync_lessons_index(session, ksor_root=tmp_path)
    session.commit()

    # Run sync again (upsert, not duplicate)
    sync_lessons_index(session, ksor_root=tmp_path)
    session.commit()

    rows = session.execute(select(LessonIndex)).scalars().all()
    assert len([r for r in rows if r.lesson_id == "L-0001"]) == 1


def test_sync_status_reflected(tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir()
    write_lesson_md(lessons_dir / "L-0001.md", "L-0001", status="candidate")

    session = make_session()
    sync_lessons_index(session, ksor_root=tmp_path)
    session.commit()

    row = session.execute(
        select(LessonIndex).where(LessonIndex.lesson_id == "L-0001")
    ).scalar_one()
    assert row.status == "candidate"


# ---------------------------------------------------------------------------
# File removed → row marked deprecated
# ---------------------------------------------------------------------------


def test_removed_file_marked_deprecated(tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir()
    p = lessons_dir / "L-0001.md"
    write_lesson_md(p, "L-0001")

    session = make_session()
    sync_lessons_index(session, ksor_root=tmp_path)
    session.commit()

    # Remove the file
    p.unlink()

    # Re-sync
    sync_lessons_index(session, ksor_root=tmp_path)
    session.commit()

    row = session.execute(
        select(LessonIndex).where(LessonIndex.lesson_id == "L-0001")
    ).scalar_one()
    assert row.status == "deprecated"


def test_existing_file_not_deprecated(tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir()
    write_lesson_md(lessons_dir / "L-0001.md", "L-0001")
    write_lesson_md(lessons_dir / "L-0002.md", "L-0002")

    session = make_session()
    sync_lessons_index(session, ksor_root=tmp_path)
    session.commit()

    # Remove only L-0002
    (lessons_dir / "L-0002.md").unlink()
    sync_lessons_index(session, ksor_root=tmp_path)
    session.commit()

    row1 = session.execute(
        select(LessonIndex).where(LessonIndex.lesson_id == "L-0001")
    ).scalar_one()
    assert row1.status == "active"  # still present

    row2 = session.execute(
        select(LessonIndex).where(LessonIndex.lesson_id == "L-0002")
    ).scalar_one()
    assert row2.status == "deprecated"


# ---------------------------------------------------------------------------
# Empty lessons directory
# ---------------------------------------------------------------------------


def test_empty_directory_no_rows(tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir()

    session = make_session()
    sync_lessons_index(session, ksor_root=tmp_path)
    session.commit()

    rows = session.execute(select(LessonIndex)).scalars().all()
    assert rows == []


def test_tags_stored(tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir()
    write_lesson_md(lessons_dir / "L-0001.md", "L-0001", tags=["funding_negative", "short"])

    session = make_session()
    sync_lessons_index(session, ksor_root=tmp_path)
    session.commit()

    row = session.execute(
        select(LessonIndex).where(LessonIndex.lesson_id == "L-0001")
    ).scalar_one()
    # tags stored as JSON string or comma-separated
    assert "funding_negative" in (row.tags or "")
