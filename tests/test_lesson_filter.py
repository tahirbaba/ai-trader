"""Tests for risk/lesson_filter.py — LessonFilter.evaluate()."""

import pytest

from trader.ksor.indexer import KsorIndexer
from trader.ksor.loader import KsorEntry
from trader.ksor.retriever import KsorRetriever
from trader.risk.lesson_filter import LessonFilter, LessonFilterResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_entry(
    id: str,
    status: str = "active",
    rule: dict | None = None,
    effect: str | None = None,
    effect_value: float | None = None,
) -> KsorEntry:
    return KsorEntry(
        id=id,
        type="lesson",
        status=status,
        rule=rule,
        effect=effect,
        effect_value=effect_value,
    )


def make_filter(entries: list[KsorEntry]) -> LessonFilter:
    indexer = KsorIndexer(db_path=":memory:")
    indexer.build(entries)
    retriever = KsorRetriever(indexer)
    return LessonFilter(retriever=retriever, entries=entries)


BLOCK_RULE = {
    "all": [
        {"field": "decision.side", "op": "eq", "value": "short"},
        {"field": "features.funding_z", "op": "lt", "value": -2.0},
    ]
}

PENALTY_RULE = {
    "all": [
        {"field": "decision.side", "op": "eq", "value": "short"},
    ]
}

CONTEXT_SHORT_NEG_FUNDING = {
    "decision": {"side": "short", "symbol": "BTC/USDT"},
    "features": {"funding_z": -2.5, "regime": "range"},
}

CONTEXT_LONG = {
    "decision": {"side": "long", "symbol": "BTC/USDT"},
    "features": {"funding_z": 0.5, "regime": "range"},
}


# ---------------------------------------------------------------------------
# block effect → blocked=True
# ---------------------------------------------------------------------------


def test_matching_block_lesson_blocks():
    lesson = make_entry("L-0001", rule=BLOCK_RULE, effect="block")
    lf = make_filter([lesson])
    result = lf.evaluate(
        decision_dict=CONTEXT_SHORT_NEG_FUNDING["decision"],
        feature_dict=CONTEXT_SHORT_NEG_FUNDING["features"],
    )
    assert result.blocked is True
    assert "L-0001" in result.matched_lessons


def test_non_matching_block_lesson_does_not_block():
    lesson = make_entry("L-0001", rule=BLOCK_RULE, effect="block")
    lf = make_filter([lesson])
    result = lf.evaluate(
        decision_dict=CONTEXT_LONG["decision"],
        feature_dict=CONTEXT_LONG["features"],
    )
    assert result.blocked is False
    assert result.matched_lessons == []


# ---------------------------------------------------------------------------
# confidence_penalty → penalty accumulated
# ---------------------------------------------------------------------------


def test_confidence_penalty_accumulated():
    l1 = make_entry("L-0001", rule=PENALTY_RULE, effect="confidence_penalty", effect_value=0.20)
    l2 = make_entry("L-0002", rule=PENALTY_RULE, effect="confidence_penalty", effect_value=0.10)
    lf = make_filter([l1, l2])
    result = lf.evaluate(
        decision_dict=CONTEXT_SHORT_NEG_FUNDING["decision"],
        feature_dict=CONTEXT_SHORT_NEG_FUNDING["features"],
    )
    assert result.confidence_penalty == pytest.approx(0.30)
    assert "L-0001" in result.matched_lessons
    assert "L-0002" in result.matched_lessons


def test_confidence_penalty_none_effect_value_contributes_zero():
    lesson = make_entry("L-0001", rule=PENALTY_RULE, effect="confidence_penalty", effect_value=None)
    lf = make_filter([lesson])
    result = lf.evaluate(
        decision_dict=CONTEXT_SHORT_NEG_FUNDING["decision"],
        feature_dict=CONTEXT_SHORT_NEG_FUNDING["features"],
    )
    assert result.confidence_penalty == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# require_extra_confirmation
# ---------------------------------------------------------------------------


def test_require_extra_confirmation():
    lesson = make_entry("L-0001", rule=PENALTY_RULE, effect="require_extra_confirmation")
    lf = make_filter([lesson])
    result = lf.evaluate(
        decision_dict=CONTEXT_SHORT_NEG_FUNDING["decision"],
        feature_dict=CONTEXT_SHORT_NEG_FUNDING["features"],
    )
    assert result.require_extra_confirmation is True


def test_no_match_no_extra_confirmation():
    lesson = make_entry("L-0001", rule=BLOCK_RULE, effect="require_extra_confirmation")
    lf = make_filter([lesson])
    result = lf.evaluate(
        decision_dict=CONTEXT_LONG["decision"],
        feature_dict=CONTEXT_LONG["features"],
    )
    assert result.require_extra_confirmation is False


# ---------------------------------------------------------------------------
# Lesson with "loosening" effect rejected at load time with warning
# ---------------------------------------------------------------------------


def test_loosening_effect_rejected_at_load_time(caplog):
    """A lesson whose effect is not in {block, confidence_penalty,
    require_extra_confirmation} is rejected at load time."""
    bad_lesson = make_entry("L-0099", rule=PENALTY_RULE, effect="raise_leverage")
    import logging

    with caplog.at_level(logging.WARNING):
        lf = make_filter([bad_lesson])

    # The bad lesson should not be evaluated
    result = lf.evaluate(
        decision_dict=CONTEXT_SHORT_NEG_FUNDING["decision"],
        feature_dict=CONTEXT_SHORT_NEG_FUNDING["features"],
    )
    assert "L-0099" not in result.matched_lessons
    # A warning should have been logged
    assert any("L-0099" in r.message or "raise_leverage" in r.message for r in caplog.records)


def test_raise_size_effect_rejected(caplog):
    bad_lesson = make_entry("L-0099", rule=PENALTY_RULE, effect="raise_size")
    import logging

    with caplog.at_level(logging.WARNING):
        lf = make_filter([bad_lesson])

    result = lf.evaluate(
        decision_dict=CONTEXT_SHORT_NEG_FUNDING["decision"],
        feature_dict=CONTEXT_SHORT_NEG_FUNDING["features"],
    )
    assert result.blocked is False
    assert result.confidence_penalty == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# No active lessons → not blocked
# ---------------------------------------------------------------------------


def test_no_active_lessons_not_blocked():
    lf = make_filter([])
    result = lf.evaluate(
        decision_dict=CONTEXT_SHORT_NEG_FUNDING["decision"],
        feature_dict=CONTEXT_SHORT_NEG_FUNDING["features"],
    )
    assert result.blocked is False
    assert result.confidence_penalty == pytest.approx(0.0)
    assert result.matched_lessons == []
    assert result.require_extra_confirmation is False


# ---------------------------------------------------------------------------
# Deprecated lessons not evaluated
# ---------------------------------------------------------------------------


def test_deprecated_lesson_not_evaluated():
    lesson = make_entry("L-0002", status="deprecated", rule=BLOCK_RULE, effect="block")
    lf = make_filter([lesson])
    result = lf.evaluate(
        decision_dict=CONTEXT_SHORT_NEG_FUNDING["decision"],
        feature_dict=CONTEXT_SHORT_NEG_FUNDING["features"],
    )
    assert result.blocked is False


# ---------------------------------------------------------------------------
# Lesson with no rule (rule=None) → not matched
# ---------------------------------------------------------------------------


def test_lesson_without_rule_not_matched():
    lesson = make_entry("L-0010", rule=None, effect="block")
    lf = make_filter([lesson])
    result = lf.evaluate(
        decision_dict=CONTEXT_SHORT_NEG_FUNDING["decision"],
        feature_dict=CONTEXT_SHORT_NEG_FUNDING["features"],
    )
    assert result.blocked is False


# ---------------------------------------------------------------------------
# LessonFilterResult structure
# ---------------------------------------------------------------------------


def test_result_has_details():
    lesson = make_entry("L-0001", rule=BLOCK_RULE, effect="block")
    lf = make_filter([lesson])
    result = lf.evaluate(
        decision_dict=CONTEXT_SHORT_NEG_FUNDING["decision"],
        feature_dict=CONTEXT_SHORT_NEG_FUNDING["features"],
    )
    assert isinstance(result, LessonFilterResult)
    assert isinstance(result.details, list)
    assert len(result.details) >= 1
    detail = result.details[0]
    assert "id" in detail
    assert "effect" in detail
    assert "matched" in detail
