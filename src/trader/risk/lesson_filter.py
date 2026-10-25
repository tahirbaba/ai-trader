"""LessonFilter — evaluates active lesson rules against a decision context.

This is pure code: no LLM calls, no I/O (except reading lessons passed in at construction).
Lessons can only tighten behaviour (block, reduce confidence, require confirmation).
Any other effect is rejected at load time with a warning.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from trader.ksor.loader import KsorEntry
from trader.ksor.rule_engine import RuleEngineError, evaluate_rule

if TYPE_CHECKING:
    from trader.ksor.retriever import KsorRetriever

logger = logging.getLogger(__name__)

# The only effects a lesson may produce (tighten-only)
_ALLOWED_EFFECTS = frozenset({"block", "confidence_penalty", "require_extra_confirmation"})


class LessonFilterResult(BaseModel):
    """Result of running LessonFilter.evaluate()."""

    blocked: bool
    confidence_penalty: float  # total penalty to subtract from LLM confidence
    matched_lessons: list[str]  # lesson IDs that matched
    require_extra_confirmation: bool
    details: list[dict[str, Any]]  # per-lesson: id, effect, matched


class LessonFilter:
    """Evaluates all active lesson rules against a decision + feature context.

    Parameters
    ----------
    retriever:
        A :class:`~trader.ksor.retriever.KsorRetriever` instance used for retrieval.
    entries:
        Direct list of KsorEntry objects to evaluate (bypasses retriever).
        If provided, only these entries are evaluated (useful for tests).
    """

    def __init__(
        self,
        retriever: KsorRetriever | None = None,
        entries: list[KsorEntry] | None = None,
    ) -> None:
        self._retriever = retriever
        # Populate entries: explicit list takes priority; otherwise load from retriever.
        if entries is not None:
            raw_entries = entries
        elif retriever is not None:
            raw_entries = retriever.get_all_active()
        else:
            raw_entries = []
        self._entries = self._filter_entries(raw_entries)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def evaluate(
        self,
        decision_dict: dict[str, Any],
        feature_dict: dict[str, Any],
    ) -> LessonFilterResult:
        """Evaluate all active lesson rules against the decision and feature context.

        Parameters
        ----------
        decision_dict:  Fields from the decision (e.g. {"side": "short", ...}).
        feature_dict:   Feature values (e.g. {"funding_z": -2.5, ...}).

        Returns
        -------
        LessonFilterResult
        """
        ctx = {"decision": decision_dict, "features": feature_dict}

        blocked = False
        total_penalty = 0.0
        require_extra = False
        matched_ids: list[str] = []
        details: list[dict[str, Any]] = []

        for entry in self._entries:
            if entry.status != "active":
                continue
            if entry.rule is None:
                details.append({"id": entry.id, "effect": entry.effect, "matched": False})
                continue

            try:
                matched = evaluate_rule(entry.rule, ctx)
            except RuleEngineError as exc:
                logger.warning(
                    "LessonFilter: rule evaluation error for lesson %s: %s", entry.id, exc
                )
                matched = False

            details.append({"id": entry.id, "effect": entry.effect, "matched": matched})

            if not matched:
                continue

            matched_ids.append(entry.id)

            if entry.effect == "block":
                blocked = True
            elif entry.effect == "confidence_penalty":
                total_penalty += entry.effect_value or 0.0
            elif entry.effect == "require_extra_confirmation":
                require_extra = True

        return LessonFilterResult(
            blocked=blocked,
            confidence_penalty=total_penalty,
            matched_lessons=matched_ids,
            require_extra_confirmation=require_extra,
            details=details,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_entries(entries: list[KsorEntry]) -> list[KsorEntry]:
        """Reject any entry whose effect is not in the allowed set (tighten-only).

        Logs a warning for each rejected entry. Returns only the valid ones.
        """
        valid: list[KsorEntry] = []
        for entry in entries:
            effect = entry.effect
            if effect is not None and effect not in _ALLOWED_EFFECTS:
                logger.warning(
                    "LessonFilter: rejecting lesson %s at load time — "
                    "effect %r is not in the allowed set %s. "
                    "Lessons may only tighten behaviour.",
                    entry.id,
                    effect,
                    sorted(_ALLOWED_EFFECTS),
                )
                continue
            valid.append(entry)
        return valid
