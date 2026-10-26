"""DSOR analytics queries for Phase 8 reporting."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from trader.dsor.models import LessonIndex, Review


def repeat_mistake_rate(session: Session) -> dict[str, Any]:
    """Query repeat-mistake rate from bad-decision reviews.

    Returns:
        {
            "total_bad_trades": int,
            "repeat_mistakes": int,
            "rate": float,
            "by_type": {mistake_type: {"count": int, "had_active_lesson": bool}, ...}
        }
    """
    bad_reviews = session.query(Review).filter(Review.outcome_class.like("bad_decision%")).all()
    total_bad_trades = len(bad_reviews)

    # Build set of all terms (tags + lesson_ids) covered by active lessons.
    # A mistake type "had an active lesson" if any active lesson's tags list
    # contains that type, OR if the lesson_id equals that type exactly.
    active_lessons = session.query(LessonIndex).filter(LessonIndex.status == "active").all()
    active_covered_terms: set[str] = set()
    for lesson in active_lessons:
        active_covered_terms.add(lesson.lesson_id)
        if lesson.tags:
            try:
                tag_list = json.loads(lesson.tags)
                if isinstance(tag_list, list):
                    active_covered_terms.update(str(t) for t in tag_list)
            except Exception:  # noqa: BLE001
                pass

    by_type: dict[str, dict[str, Any]] = {}
    repeat_mistakes = 0

    for review in bad_reviews:
        mistake_types: list[str] = []
        if review.mistake_types_json:
            try:
                parsed = json.loads(review.mistake_types_json)
                if isinstance(parsed, list):
                    mistake_types = [str(m) for m in parsed]
            except Exception:  # noqa: BLE001
                mistake_types = []

        # Check if any mistake type matches an active lesson (exact match on tag or id)
        trade_has_active_lesson = False
        for mt in mistake_types:
            had_lesson = mt in active_covered_terms

            if mt not in by_type:
                by_type[mt] = {"count": 0, "had_active_lesson": had_lesson}
            by_type[mt]["count"] += 1
            if had_lesson:
                by_type[mt]["had_active_lesson"] = True
                trade_has_active_lesson = True

        if trade_has_active_lesson:
            repeat_mistakes += 1

    rate = repeat_mistakes / total_bad_trades if total_bad_trades > 0 else 0.0

    return {
        "total_bad_trades": total_bad_trades,
        "repeat_mistakes": repeat_mistakes,
        "rate": rate,
        "by_type": by_type,
    }


def lesson_block_stats(session: Session) -> list[dict[str, Any]]:
    """Query block stats for all active lessons, sorted by block_count desc."""
    rows = (
        session.query(LessonIndex)
        .filter(LessonIndex.status == "active")
        .order_by(LessonIndex.block_count.desc())
        .all()
    )
    return [
        {
            "lesson_id": r.lesson_id,
            "block_count": r.block_count,
            "blocked_shadow_r": r.blocked_shadow_r,
        }
        for r in rows
    ]
