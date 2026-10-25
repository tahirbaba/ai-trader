"""KSOR Loader — loads .md files with YAML frontmatter into KsorEntry models."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import frontmatter  # python-frontmatter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class KsorEntry(BaseModel):
    """Canonical in-memory representation of any KSOR entry (.md file)."""

    id: str = ""
    type: str = ""
    status: str = ""
    tags: list[str] = Field(default_factory=list)
    regime: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    condition: str = ""
    rule: dict[str, Any] | None = None
    effect: str | None = None
    effect_value: float | None = None
    action: str = ""
    evidence_trades: list[str] = Field(default_factory=list)
    win_stats: dict[str, Any] = Field(default_factory=dict)
    created: str = ""
    last_validated: str | None = None
    expires: str | None = None
    body: str = ""
    file_path: str = ""


def load_ksor_entries(root: Path) -> list[KsorEntry]:
    """Walk *root* recursively, parse every .md file, return a list of KsorEntry.

    Files that fail to parse are skipped with a warning (never raises).
    """
    entries: list[KsorEntry] = []

    for path in sorted(root.rglob("*.md")):
        try:
            post = frontmatter.load(str(path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("KSOR loader: skipping %s — parse error: %s", path, exc)
            continue

        meta: dict[str, Any] = dict(post.metadata)
        body: str = post.content.strip()

        try:
            entry = KsorEntry(
                id=str(meta.get("id", "")),
                type=str(meta.get("type", "")),
                status=str(meta.get("status", "")),
                tags=_to_str_list(meta.get("tags", [])),
                regime=_to_str_list(meta.get("regime", [])),
                symbols=_to_str_list(meta.get("symbols", [])),
                condition=str(meta.get("condition", "")),
                rule=meta.get("rule") or None,
                effect=meta.get("effect") or None,
                effect_value=_to_float_or_none(meta.get("effect_value")),
                action=str(meta.get("action", "")),
                evidence_trades=_to_str_list(meta.get("evidence_trades", [])),
                win_stats=meta.get("win_stats") or {},
                created=str(meta.get("created", "")),
                last_validated=_to_str_or_none(meta.get("last_validated")),
                expires=_to_str_or_none(meta.get("expires")),
                body=body,
                file_path=str(path.resolve()),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("KSOR loader: skipping %s — model error: %s", path, exc)
            continue

        entries.append(entry)

    return entries


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value)
    return s if s not in ("None", "null", "") else None
