"""Tests for ksor/lesson_writer.py — LessonWriter creates files and git commits."""

import subprocess
from pathlib import Path

import pytest

from trader.ksor.lesson_writer import LessonWriter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def init_git_repo(path: Path) -> None:
    """Initialize a git repo so LessonWriter can commit."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        check=True,
        capture_output=True,
    )


SAMPLE_FRONTMATTER = {
    "id": "L-0099",
    "type": "lesson",
    "status": "candidate",
    "tags": ["test_tag"],
    "regime": ["range"],
    "symbols": [],
    "condition": "Test condition",
    "rule": {
        "all": [
            {"field": "decision.side", "op": "eq", "value": "short"},
        ]
    },
    "effect": "confidence_penalty",
    "effect_value": 0.10,
    "action": "Test action",
    "evidence_trades": ["T-0001"],
    "win_stats": {"n": 1, "wins": 0},
    "created": "2026-10-25",
    "last_validated": None,
    "expires": "2027-04-25",
}

SAMPLE_BODY = "This is the body of the test lesson."


# ---------------------------------------------------------------------------
# Creates file at correct path
# ---------------------------------------------------------------------------


def test_creates_file(tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir(parents=True)
    init_git_repo(tmp_path)

    writer = LessonWriter(ksor_root=tmp_path)
    writer.write(
        filename="L-0099_test.md",
        frontmatter=SAMPLE_FRONTMATTER,
        body=SAMPLE_BODY,
        commit_message="Add test lesson L-0099",
        trade_ids=["T-0001"],
    )

    expected = lessons_dir / "L-0099_test.md"
    assert expected.exists()


def test_file_content_has_frontmatter(tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir(parents=True)
    init_git_repo(tmp_path)

    writer = LessonWriter(ksor_root=tmp_path)
    writer.write(
        filename="L-0099_test.md",
        frontmatter=SAMPLE_FRONTMATTER,
        body=SAMPLE_BODY,
        commit_message="Add test lesson L-0099",
        trade_ids=["T-0001"],
    )

    content = (lessons_dir / "L-0099_test.md").read_text()
    assert "L-0099" in content
    assert "confidence_penalty" in content
    assert SAMPLE_BODY in content


# ---------------------------------------------------------------------------
# Git commit created
# ---------------------------------------------------------------------------


def test_git_commit_created(tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir(parents=True)
    init_git_repo(tmp_path)

    writer = LessonWriter(ksor_root=tmp_path)
    writer.write(
        filename="L-0099_test.md",
        frontmatter=SAMPLE_FRONTMATTER,
        body=SAMPLE_BODY,
        commit_message="Add test lesson L-0099",
        trade_ids=["T-0001"],
    )

    result = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert "L-0099" in result.stdout or "test lesson" in result.stdout


def test_commit_message_includes_trade_ids(tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir(parents=True)
    init_git_repo(tmp_path)

    writer = LessonWriter(ksor_root=tmp_path)
    writer.write(
        filename="L-0099_test.md",
        frontmatter=SAMPLE_FRONTMATTER,
        body=SAMPLE_BODY,
        commit_message="Add test lesson",
        trade_ids=["T-9999"],
    )

    result = subprocess.run(
        ["git", "log", "--format=%B", "-1"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert "T-9999" in result.stdout


# ---------------------------------------------------------------------------
# Path guard: path outside /ksor/lessons raises ValueError
# ---------------------------------------------------------------------------


def test_path_outside_lessons_raises_value_error(tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir(parents=True)
    init_git_repo(tmp_path)

    writer = LessonWriter(ksor_root=tmp_path)

    with pytest.raises(ValueError, match="outside"):
        writer.write(
            filename="../../../etc/passwd",
            frontmatter=SAMPLE_FRONTMATTER,
            body=SAMPLE_BODY,
            commit_message="Evil write",
            trade_ids=[],
        )


def test_path_traversal_blocked(tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir(parents=True)
    init_git_repo(tmp_path)

    writer = LessonWriter(ksor_root=tmp_path)

    with pytest.raises(ValueError):
        writer.write(
            filename="../../config/risk.yaml",
            frontmatter=SAMPLE_FRONTMATTER,
            body=SAMPLE_BODY,
            commit_message="Malicious write attempt",
            trade_ids=[],
        )


# ---------------------------------------------------------------------------
# Overwrite existing file
# ---------------------------------------------------------------------------


def test_overwrite_existing_file(tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir(parents=True)
    init_git_repo(tmp_path)

    writer = LessonWriter(ksor_root=tmp_path)

    writer.write(
        filename="L-0099_test.md",
        frontmatter=SAMPLE_FRONTMATTER,
        body="Original body",
        commit_message="Add lesson",
        trade_ids=[],
    )
    writer.write(
        filename="L-0099_test.md",
        frontmatter={**SAMPLE_FRONTMATTER, "status": "active"},
        body="Updated body",
        commit_message="Update lesson status to active",
        trade_ids=["T-0002"],
    )

    content = (lessons_dir / "L-0099_test.md").read_text()
    assert "Updated body" in content
    assert "active" in content

    result = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    # Should have 2 commits
    lines = [line for line in result.stdout.strip().split("\n") if line]
    assert len(lines) >= 2
