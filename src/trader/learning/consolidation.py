"""Weekly consolidation: deprecate stale lessons, merge duplicates, regenerate INDEX."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from trader.dsor.models import LessonIndex

from .lesson_lifecycle import LessonLifecycleManager

logger = logging.getLogger(__name__)


class WeeklyConsolidation:
    def __init__(self, lifecycle: LessonLifecycleManager | None = None) -> None:
        self._lc = lifecycle or LessonLifecycleManager()

    def run(self, session: Session) -> dict[str, int]:
        """Deprecate stale, flag positive-shadow-R, return counts."""
        now = datetime.now(UTC)

        # Deprecate expired lessons
        expired_rows = (
            session.query(LessonIndex)
            .filter(
                LessonIndex.status == "active",
                LessonIndex.expires_ts.isnot(None),
                LessonIndex.expires_ts < now,
            )
            .all()
        )
        deprecated_stale = 0
        for row in expired_rows:
            result = self._lc.deprecate(session, row.lesson_id, reason="expired")
            if result:
                deprecated_stale += 1

        # Flag positive-shadow-R lessons
        positive_ids = self._lc.flag_positive_shadow_r_lessons(session)
        flagged_shadow_r = 0
        for lesson_id in positive_ids:
            result = self._lc.deprecate(session, lesson_id, reason="blocked_shadow_r_positive")
            if result:
                flagged_shadow_r += 1

        logger.info(
            "WeeklyConsolidation: deprecated_stale=%d flagged_shadow_r=%d",
            deprecated_stale,
            flagged_shadow_r,
        )
        return {"deprecated_stale": deprecated_stale, "flagged_shadow_r": flagged_shadow_r}
