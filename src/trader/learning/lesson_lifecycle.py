"""Lesson lifecycle: candidate → active → deprecated."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from trader.dsor.models import LessonIndex
from trader.learning.constants import TIGHTEN_ONLY_ALLOWED_ACTIONS, TIGHTEN_ONLY_FORBIDDEN_KEYS

logger = logging.getLogger(__name__)

MIN_LESSON_EVIDENCE = 5  # default; can be overridden
MIN_OWNER_EVIDENCE = 2  # for owner-approved promotion
MAX_ACTIVE_LESSONS_DEFAULT = 25

# Re-export canonical names expected by external callers
ALLOWED_ACTIONS = TIGHTEN_ONLY_ALLOWED_ACTIONS
FORBIDDEN_KEYS = TIGHTEN_ONLY_FORBIDDEN_KEYS


class LessonLifecycleManager:
    def __init__(self, max_active_lessons: int = MAX_ACTIVE_LESSONS_DEFAULT) -> None:
        self.max_active_lessons = max_active_lessons

    def validate_tighten_only(self, rule: dict[str, Any] | None) -> None:
        """Raise ValueError if rule would loosen safety rather than tighten."""
        if rule is None:
            return
        for key in TIGHTEN_ONLY_FORBIDDEN_KEYS:
            if key in rule:
                raise ValueError(f"Lesson rule would loosen safety: contains forbidden key {key!r}")
        action = rule.get("action", "")
        if action and action not in TIGHTEN_ONLY_ALLOWED_ACTIONS:
            raise ValueError(
                f"Lesson rule has disallowed action {action!r}. Allowed: {ALLOWED_ACTIONS}"
            )

    def promote_by_evidence(
        self,
        session: Session,
        lesson_id: str,
        supporting_trade_ids: list[int],
        contradicting_trade_ids: list[int],
        min_evidence: int = MIN_LESSON_EVIDENCE,
    ) -> bool:
        """Promote candidate → active if evidence threshold met. Returns True if promoted."""
        row = (
            session.query(LessonIndex)
            .filter(LessonIndex.lesson_id == lesson_id, LessonIndex.status == "candidate")
            .first()
        )
        if row is None:
            return False

        if len(supporting_trade_ids) < min_evidence:
            return False

        if len(contradicting_trade_ids) > 0:
            return False

        # Validate tighten-only
        rule: dict[str, Any] | None = None
        if row.rule_json:
            try:
                rule = json.loads(row.rule_json)
            except Exception:  # noqa: BLE001
                rule = None
        self.validate_tighten_only(rule)

        # Update evidence_trade_ids (union with existing)
        existing_ids: list[int] = []
        if row.evidence_trade_ids:
            try:
                existing_ids = json.loads(row.evidence_trade_ids)
            except Exception:  # noqa: BLE001
                existing_ids = []
        merged = list(set(existing_ids) | set(supporting_trade_ids))

        row.status = "active"
        row.last_validated_ts = datetime.now(UTC)
        row.evidence_trade_ids = json.dumps(merged)
        session.flush()
        return True

    def owner_approve(
        self,
        session: Session,
        lesson_id: str,
        evidence_ids: list[int],
    ) -> bool:
        """Owner manually approves a candidate lesson. Requires >= 2 evidence pieces."""
        row = (
            session.query(LessonIndex)
            .filter(LessonIndex.lesson_id == lesson_id, LessonIndex.status == "candidate")
            .first()
        )
        if row is None:
            return False

        if len(evidence_ids) < MIN_OWNER_EVIDENCE:
            raise ValueError("owner_approve requires >= 2 evidence pieces")

        # Validate tighten-only
        rule: dict[str, Any] | None = None
        if row.rule_json:
            try:
                rule = json.loads(row.rule_json)
            except Exception:  # noqa: BLE001
                rule = None
        self.validate_tighten_only(rule)

        row.status = "active"
        row.last_validated_ts = datetime.now(UTC)
        row.evidence_trade_ids = json.dumps(evidence_ids)
        session.flush()
        return True

    def deprecate(
        self,
        session: Session,
        lesson_id: str,
        reason: str,
    ) -> bool:
        """Deprecate an active lesson. Returns True if deprecated."""
        row = (
            session.query(LessonIndex)
            .filter(LessonIndex.lesson_id == lesson_id, LessonIndex.status == "active")
            .first()
        )
        if row is None:
            return False

        row.status = "deprecated"
        row.last_validated_ts = datetime.now(UTC)
        # Store reason in win_stats JSON
        existing_stats: dict[str, Any] = {}
        if row.win_stats:
            try:
                existing_stats = json.loads(row.win_stats)
            except Exception:  # noqa: BLE001
                existing_stats = {}
        existing_stats["deprecation_reason"] = reason
        row.win_stats = json.dumps(existing_stats)
        session.flush()
        return True

    def check_lesson_creep(
        self,
        session: Session,
    ) -> dict[str, Any]:
        """Return lesson creep report: active count, over-limit flag, blocking lessons."""
        active_count: int = (
            session.query(LessonIndex).filter(LessonIndex.status == "active").count()
        )
        blocking_rows = (
            session.query(LessonIndex)
            .filter(LessonIndex.status == "active", LessonIndex.block_count > 0)
            .order_by(LessonIndex.blocked_shadow_r.desc())
            .all()
        )
        top_blocking = [
            {
                "lesson_id": r.lesson_id,
                "block_count": r.block_count,
                "blocked_shadow_r": r.blocked_shadow_r,
            }
            for r in blocking_rows
        ]
        return {
            "active_count": active_count,
            "over_limit": active_count > self.max_active_lessons,
            "max_allowed": self.max_active_lessons,
            "top_blocking": top_blocking,
        }

    def flag_positive_shadow_r_lessons(
        self,
        session: Session,
    ) -> list[str]:
        """Return lesson_ids where blocked_shadow_r > 0 (blocked setups were net positive)."""
        rows = (
            session.query(LessonIndex)
            .filter(LessonIndex.status == "active", LessonIndex.blocked_shadow_r > 0.0)
            .all()
        )
        return [r.lesson_id for r in rows]
