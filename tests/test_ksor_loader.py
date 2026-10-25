"""Tests for ksor/loader.py — parsing .md files with YAML frontmatter into KsorEntry."""

import textwrap
from pathlib import Path

import pytest

from trader.ksor.loader import KsorEntry, load_ksor_entries

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_md(tmp_path: Path, filename: str, content: str) -> Path:
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content))
    return p


# ---------------------------------------------------------------------------
# Parse a full frontmatter → KsorEntry
# ---------------------------------------------------------------------------


FULL_MD = """\
    ---
    id: L-0001
    type: lesson
    status: active
    tags: [funding_negative, short, squeeze]
    regime: [range, trend_up]
    symbols: []
    condition: "Short signal while funding is strongly negative"
    rule:
      all:
        - {field: decision.side, op: eq, value: short}
        - {field: features.funding_z, op: lt, value: -2.0}
    effect: confidence_penalty
    effect_value: 0.20
    action: "Reduce confidence by 0.20"
    evidence_trades: [T-001, T-002]
    win_stats: {n: 2, wins: 1}
    created: "2026-10-25"
    last_validated: null
    expires: "2027-04-25"
    ---

    This is the body text.
    It can span multiple lines.
"""


def test_parse_full_entry(tmp_path):
    write_md(tmp_path, "L-0001.md", FULL_MD)
    entries = load_ksor_entries(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e.id == "L-0001"
    assert e.type == "lesson"
    assert e.status == "active"
    assert "funding_negative" in e.tags
    assert "short" in e.tags
    assert "range" in e.regime
    assert e.condition == "Short signal while funding is strongly negative"
    assert e.rule is not None
    assert "all" in e.rule
    assert e.effect == "confidence_penalty"
    assert e.effect_value == pytest.approx(0.20)
    assert e.action == "Reduce confidence by 0.20"
    assert e.evidence_trades == ["T-001", "T-002"]
    assert e.win_stats == {"n": 2, "wins": 1}
    assert e.created == "2026-10-25"
    assert e.last_validated is None
    assert e.expires == "2027-04-25"
    assert "body text" in e.body
    assert str(tmp_path) in e.file_path


# ---------------------------------------------------------------------------
# Body capture
# ---------------------------------------------------------------------------


def test_body_captured(tmp_path):
    md = """\
        ---
        id: P-0001
        type: playbook
        status: active
        ---

        Some **markdown** body here.
        Second paragraph.
    """
    write_md(tmp_path, "P-0001.md", md)
    entries = load_ksor_entries(tmp_path)
    assert "markdown" in entries[0].body
    assert "Second paragraph" in entries[0].body


# ---------------------------------------------------------------------------
# Missing required field → graceful defaults
# ---------------------------------------------------------------------------


def test_missing_id_defaults(tmp_path):
    md = """\
        ---
        type: lesson
        status: active
        ---
        No id here.
    """
    write_md(tmp_path, "no_id.md", md)
    entries = load_ksor_entries(tmp_path)
    # should not raise; id defaults to empty string or filename-based default
    assert len(entries) == 1
    e = entries[0]
    assert e.type == "lesson"
    assert e.status == "active"


def test_missing_tags_defaults_empty_list(tmp_path):
    md = """\
        ---
        id: L-0002
        type: lesson
        status: active
        ---
    """
    write_md(tmp_path, "L-0002.md", md)
    entries = load_ksor_entries(tmp_path)
    assert entries[0].tags == []


def test_missing_rule_defaults_none(tmp_path):
    md = """\
        ---
        id: P-0001
        type: playbook
        status: active
        ---
    """
    write_md(tmp_path, "P-0001.md", md)
    entries = load_ksor_entries(tmp_path)
    assert entries[0].rule is None


# ---------------------------------------------------------------------------
# File path captured
# ---------------------------------------------------------------------------


def test_file_path_is_absolute(tmp_path):
    md = """\
        ---
        id: L-0010
        type: lesson
        status: active
        ---
    """
    p = write_md(tmp_path, "L-0010.md", md)
    entries = load_ksor_entries(tmp_path)
    assert entries[0].file_path == str(p)


# ---------------------------------------------------------------------------
# Multiple files
# ---------------------------------------------------------------------------


def test_load_multiple_files(tmp_path):
    for i in range(3):
        md = f"""\
            ---
            id: L-{i:04d}
            type: lesson
            status: active
            ---
        """
        write_md(tmp_path, f"L-{i:04d}.md", md)
    entries = load_ksor_entries(tmp_path)
    assert len(entries) == 3
    ids = {e.id for e in entries}
    assert ids == {"L-0000", "L-0001", "L-0002"}


# ---------------------------------------------------------------------------
# Subdirectory walk
# ---------------------------------------------------------------------------


def test_walks_subdirectories(tmp_path):
    subdir = tmp_path / "lessons"
    subdir.mkdir()
    md = """\
        ---
        id: L-0005
        type: lesson
        status: active
        ---
    """
    write_md(subdir, "L-0005.md", md)
    entries = load_ksor_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0].id == "L-0005"


# ---------------------------------------------------------------------------
# Invalid/unparseable file skipped gracefully
# ---------------------------------------------------------------------------


def test_invalid_yaml_skipped(tmp_path):
    # This file has a colon in the middle that breaks YAML
    bad_md = "---\nid: bad: yaml: here\n---\nbody\n"
    (tmp_path / "bad.md").write_text(bad_md)
    # Valid file alongside it
    write_md(tmp_path, "good.md", "---\nid: G-0001\ntype: playbook\nstatus: active\n---\n")
    entries = load_ksor_entries(tmp_path)
    # At least the good one loads; bad one is skipped
    ids = {e.id for e in entries}
    assert "G-0001" in ids


# ---------------------------------------------------------------------------
# rule: null in frontmatter
# ---------------------------------------------------------------------------


def test_rule_null_parsed_as_none(tmp_path):
    md = """\
        ---
        id: P-0099
        type: playbook
        status: active
        rule: null
        ---
    """
    write_md(tmp_path, "P-0099.md", md)
    entries = load_ksor_entries(tmp_path)
    assert entries[0].rule is None


# ---------------------------------------------------------------------------
# KsorEntry model direct instantiation
# ---------------------------------------------------------------------------


def test_ksor_entry_defaults():
    e = KsorEntry(id="X-001", type="lesson", status="active")
    assert e.tags == []
    assert e.regime == []
    assert e.symbols == []
    assert e.rule is None
    assert e.effect is None
    assert e.body == ""
    assert e.file_path == ""


# ---------------------------------------------------------------------------
# Empty directory
# ---------------------------------------------------------------------------


def test_empty_directory_returns_empty_list(tmp_path):
    """load_ksor_entries on an empty dir returns [] without error."""
    entries = load_ksor_entries(tmp_path)
    assert entries == []


# ---------------------------------------------------------------------------
# Negative effect_value rejected
# ---------------------------------------------------------------------------


def test_negative_effect_value_skipped(tmp_path):
    """A lesson file with a negative effect_value is skipped (model validation fails)."""
    md = """\
        ---
        id: L-BAD
        type: lesson
        status: active
        effect: confidence_penalty
        effect_value: -0.30
        ---
        Bad lesson
    """
    write_md(tmp_path, "L-BAD.md", md)
    entries = load_ksor_entries(tmp_path)
    ids = {e.id for e in entries}
    assert "L-BAD" not in ids


def test_positive_effect_value_accepted(tmp_path):
    """A lesson file with a positive effect_value loads correctly."""
    md = """\
        ---
        id: L-GOOD
        type: lesson
        status: active
        effect: confidence_penalty
        effect_value: 0.20
        ---
        Good lesson
    """
    write_md(tmp_path, "L-GOOD.md", md)
    entries = load_ksor_entries(tmp_path)
    ids = {e.id for e in entries}
    assert "L-GOOD" in ids
    good = next(e for e in entries if e.id == "L-GOOD")
    assert good.effect_value == pytest.approx(0.20)
