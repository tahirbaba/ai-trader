"""Post-trade review: code-detected violations override LLM classification.

LLM output MUST be validated through PostTradeReviewOutput (Pydantic) before
constructing a ReviewInput. Raw LLM dicts are never accepted directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trader.llm.schemas import LessonCandidate, PostTradeReviewOutput

from .outcome_tracker import OUTCOME_CLASSES, TradeOutcome


@dataclass
class ReviewInput:
    trade_outcome: TradeOutcome
    snapshot: dict[str, Any]
    active_lesson_ids: list[str]
    trade_during_blackout: bool
    stop_inside_noise: bool
    ignored_active_lesson: bool
    # These come from a validated PostTradeReviewOutput — never raw LLM dicts
    llm_outcome_class: str | None
    llm_what_went_right: str
    llm_what_went_wrong: str
    llm_lesson_candidates: list[LessonCandidate]  # typed, not dict[str, Any]

    @classmethod
    def from_llm_output(
        cls,
        trade_outcome: TradeOutcome,
        llm_output: PostTradeReviewOutput,
        snapshot: dict[str, Any] | None = None,
        active_lesson_ids: list[str] | None = None,
        trade_during_blackout: bool = False,
        stop_inside_noise: bool = False,
        ignored_active_lesson: bool = False,
    ) -> ReviewInput:
        """Construct ReviewInput from a validated PostTradeReviewOutput."""
        return cls(
            trade_outcome=trade_outcome,
            snapshot=snapshot or {},
            active_lesson_ids=active_lesson_ids or [],
            trade_during_blackout=trade_during_blackout,
            stop_inside_noise=stop_inside_noise,
            ignored_active_lesson=ignored_active_lesson,
            llm_outcome_class=llm_output.outcome_class,
            llm_what_went_right=llm_output.what_went_right,
            llm_what_went_wrong=llm_output.what_went_wrong,
            llm_lesson_candidates=llm_output.lesson_candidates,
        )


@dataclass
class ReviewResult:
    trade_id: int
    outcome_class: str
    mistake_types: list[str]
    what_went_right: str
    what_went_wrong: str
    lesson_candidates: list[LessonCandidate]
    code_overrode_llm: bool


class PostTradeReviewer:
    """Applies code-detected violations on top of LLM classification."""

    def review(self, inp: ReviewInput) -> ReviewResult:
        """Produce a ReviewResult, with code violations overriding LLM output."""
        # --- 1. Detect code violations ---
        code_violations: list[str] = []  # mistake types found by code

        if inp.trade_during_blackout:
            code_violations.append("ignored_news_or_calendar")

        if inp.stop_inside_noise:
            code_violations.append("stop_too_tight")

        if inp.ignored_active_lesson:
            code_violations.append("ignored_existing_lesson")

        code_overrode_llm = False

        # --- 2. Determine outcome class ---
        # Start from LLM suggestion if valid, else default
        llm_class = inp.llm_outcome_class
        if llm_class not in OUTCOME_CLASSES:
            llm_class = None

        if code_violations:
            # Override to bad_decision_*, keep actual outcome suffix
            outcome_suffix = "good_outcome" if inp.trade_outcome.r_multiple >= 0 else "bad_outcome"
            outcome_class = f"bad_decision_{outcome_suffix}"
            code_overrode_llm = llm_class != outcome_class
            mistake_types = list(code_violations)
        else:
            # Use LLM classification
            if llm_class is not None:
                outcome_class = llm_class
            else:
                # Fall back based on R-multiple
                if inp.trade_outcome.r_multiple >= 0:
                    outcome_class = "good_decision_good_outcome"
                else:
                    outcome_class = "good_decision_bad_outcome"
            code_overrode_llm = False
            mistake_types = []

        # --- 3 & 4. Determine lesson candidates ---
        if outcome_class == "good_decision_bad_outcome":
            # No corrective lesson from variance
            lesson_candidates: list[LessonCandidate] = []
        else:
            # Cap at 2 (PostTradeReviewOutput also enforces this when going through the LLM path)
            lesson_candidates = list(inp.llm_lesson_candidates)[:2]

        return ReviewResult(
            trade_id=inp.trade_outcome.trade_id,
            outcome_class=outcome_class,
            mistake_types=mistake_types,
            what_went_right=inp.llm_what_went_right,
            what_went_wrong=inp.llm_what_went_wrong,
            lesson_candidates=lesson_candidates,
            code_overrode_llm=code_overrode_llm,
        )
