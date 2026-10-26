"""Record and query shadow outcomes for blocked/no-trade setups."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from trader.dsor.models import LessonIndex, ShadowOutcome


class ShadowTracker:
    """Persists shadow outcomes and updates lesson statistics."""

    def record(
        self,
        session: Session,
        decision_id: int,
        hypothetical_side: str,
        entry: float | None,
        stop: float | None,
        tp: float | None,
        blocked_by: str | None,
        r_multiple: float | None,
        outcome: str | None,
        horizon: int | None,
    ) -> ShadowOutcome:
        """Persist a shadow outcome row."""
        row = ShadowOutcome(
            decision_id=decision_id,
            hypothetical_side=hypothetical_side,
            entry=entry,
            stop=stop,
            tp=tp,
            blocked_by=blocked_by,
            r_multiple=r_multiple,
            outcome=outcome,
            horizon=horizon,
        )
        session.add(row)
        session.flush()
        return row

    def update_lesson_shadow_stats(
        self,
        session: Session,
        lesson_id: str,
        r_multiple: float,
    ) -> None:
        """Add r_multiple to a lesson's blocked_shadow_r and increment block_count."""
        row = session.query(LessonIndex).filter(LessonIndex.lesson_id == lesson_id).first()
        if row is None:
            return
        row.blocked_shadow_r = (row.blocked_shadow_r or 0.0) + r_multiple
        row.block_count = (row.block_count or 0) + 1
        session.flush()
