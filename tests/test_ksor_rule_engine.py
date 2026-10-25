"""Tests for the KSOR rule DSL engine (rule_engine.py).

Covers: eq/ne/lt/le/gt/ge/in/not_in ops, all/any/not wrappers,
unknown field → False, unknown op → RuleEngineError, dot-path lookup.
"""

import pytest

from trader.ksor.rule_engine import RuleEngineError, evaluate_rule

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CONTEXT = {
    "decision": {
        "side": "short",
        "confidence": 0.75,
        "symbol": "BTC/USDT",
    },
    "features": {
        "funding_z": -2.5,
        "atr": 150.0,
        "regime": "range",
        "in_blackout": False,
        "session_tags": ["asia", "range"],
    },
}


def _rule(field: str, op: str, value: object) -> dict:
    return {"field": field, "op": op, "value": value}


# ---------------------------------------------------------------------------
# Leaf clause — eq
# ---------------------------------------------------------------------------


def test_eq_match():
    rule = _rule("decision.side", "eq", "short")
    assert evaluate_rule(rule, CONTEXT) is True


def test_eq_no_match():
    rule = _rule("decision.side", "eq", "long")
    assert evaluate_rule(rule, CONTEXT) is False


# ---------------------------------------------------------------------------
# Leaf clause — ne
# ---------------------------------------------------------------------------


def test_ne_match():
    rule = _rule("decision.side", "ne", "long")
    assert evaluate_rule(rule, CONTEXT) is True


def test_ne_no_match():
    rule = _rule("decision.side", "ne", "short")
    assert evaluate_rule(rule, CONTEXT) is False


# ---------------------------------------------------------------------------
# Leaf clause — lt / le / gt / ge
# ---------------------------------------------------------------------------


def test_lt_match():
    rule = _rule("features.funding_z", "lt", -2.0)
    assert evaluate_rule(rule, CONTEXT) is True


def test_lt_no_match():
    rule = _rule("features.funding_z", "lt", -3.0)
    assert evaluate_rule(rule, CONTEXT) is False


def test_le_match_equal():
    rule = _rule("features.funding_z", "le", -2.5)
    assert evaluate_rule(rule, CONTEXT) is True


def test_le_match_less():
    rule = _rule("features.funding_z", "le", -2.0)
    assert evaluate_rule(rule, CONTEXT) is True


def test_le_no_match():
    rule = _rule("features.funding_z", "le", -3.0)
    assert evaluate_rule(rule, CONTEXT) is False


def test_gt_match():
    rule = _rule("features.atr", "gt", 100.0)
    assert evaluate_rule(rule, CONTEXT) is True


def test_gt_no_match():
    rule = _rule("features.atr", "gt", 200.0)
    assert evaluate_rule(rule, CONTEXT) is False


def test_ge_match_equal():
    rule = _rule("features.atr", "ge", 150.0)
    assert evaluate_rule(rule, CONTEXT) is True


def test_ge_match_greater():
    rule = _rule("features.atr", "ge", 100.0)
    assert evaluate_rule(rule, CONTEXT) is True


def test_ge_no_match():
    rule = _rule("features.atr", "ge", 200.0)
    assert evaluate_rule(rule, CONTEXT) is False


# ---------------------------------------------------------------------------
# Leaf clause — in / not_in
# ---------------------------------------------------------------------------


def test_in_match():
    rule = _rule("features.regime", "in", ["range", "trend_up"])
    assert evaluate_rule(rule, CONTEXT) is True


def test_in_no_match():
    rule = _rule("features.regime", "in", ["trend_up", "trend_down"])
    assert evaluate_rule(rule, CONTEXT) is False


def test_not_in_match():
    rule = _rule("features.regime", "not_in", ["trend_up", "trend_down"])
    assert evaluate_rule(rule, CONTEXT) is True


def test_not_in_no_match():
    rule = _rule("features.regime", "not_in", ["range", "trend_up"])
    assert evaluate_rule(rule, CONTEXT) is False


# ---------------------------------------------------------------------------
# Wrappers — all
# ---------------------------------------------------------------------------


def test_all_true():
    rule = {
        "all": [
            _rule("decision.side", "eq", "short"),
            _rule("features.funding_z", "lt", -2.0),
        ]
    }
    assert evaluate_rule(rule, CONTEXT) is True


def test_all_false_one_fails():
    rule = {
        "all": [
            _rule("decision.side", "eq", "short"),
            _rule("features.funding_z", "gt", 0.0),  # False
        ]
    }
    assert evaluate_rule(rule, CONTEXT) is False


def test_all_empty_is_true():
    """Empty all-list vacuously true."""
    assert evaluate_rule({"all": []}, CONTEXT) is True


# ---------------------------------------------------------------------------
# Wrappers — any
# ---------------------------------------------------------------------------


def test_any_true_one_matches():
    rule = {
        "any": [
            _rule("decision.side", "eq", "long"),  # False
            _rule("features.funding_z", "lt", -2.0),  # True
        ]
    }
    assert evaluate_rule(rule, CONTEXT) is True


def test_any_false_none_match():
    rule = {
        "any": [
            _rule("decision.side", "eq", "long"),  # False
            _rule("features.funding_z", "gt", 0.0),  # False
        ]
    }
    assert evaluate_rule(rule, CONTEXT) is False


def test_any_empty_is_false():
    """Empty any-list vacuously false."""
    assert evaluate_rule({"any": []}, CONTEXT) is False


# ---------------------------------------------------------------------------
# Wrappers — not
# ---------------------------------------------------------------------------


def test_not_inverts_true_to_false():
    rule = {"not": _rule("decision.side", "eq", "short")}
    assert evaluate_rule(rule, CONTEXT) is False


def test_not_inverts_false_to_true():
    rule = {"not": _rule("decision.side", "eq", "long")}
    assert evaluate_rule(rule, CONTEXT) is True


# ---------------------------------------------------------------------------
# Nested dot-path lookup
# ---------------------------------------------------------------------------


def test_dot_path_two_levels():
    rule = _rule("decision.confidence", "ge", 0.7)
    assert evaluate_rule(rule, CONTEXT) is True


def test_dot_path_three_levels():
    ctx = {"a": {"b": {"c": 42}}}
    rule = _rule("a.b.c", "eq", 42)
    assert evaluate_rule(rule, ctx) is True


# ---------------------------------------------------------------------------
# Unknown field → False (never raises)
# ---------------------------------------------------------------------------


def test_unknown_field_returns_false():
    rule = _rule("features.nonexistent_field", "eq", 1)
    assert evaluate_rule(rule, CONTEXT) is False


def test_unknown_nested_field_returns_false():
    rule = _rule("decision.unknown_key", "lt", 100)
    assert evaluate_rule(rule, CONTEXT) is False


def test_missing_top_level_key_returns_false():
    rule = _rule("completely.missing.path", "eq", "x")
    assert evaluate_rule(rule, CONTEXT) is False


# ---------------------------------------------------------------------------
# Unknown op → RuleEngineError
# ---------------------------------------------------------------------------


def test_unknown_op_raises():
    rule = _rule("decision.side", "contains", "sho")
    with pytest.raises(RuleEngineError):
        evaluate_rule(rule, CONTEXT)


def test_unknown_op_exact():
    rule = _rule("features.funding_z", "between", [-3, -1])
    with pytest.raises(RuleEngineError, match="between"):
        evaluate_rule(rule, CONTEXT)


# ---------------------------------------------------------------------------
# Boolean field
# ---------------------------------------------------------------------------


def test_bool_field_false():
    rule = _rule("features.in_blackout", "eq", False)
    assert evaluate_rule(rule, CONTEXT) is True


def test_bool_field_true_fails():
    rule = _rule("features.in_blackout", "eq", True)
    assert evaluate_rule(rule, CONTEXT) is False


# ---------------------------------------------------------------------------
# Full L-0001 rule from seed file
# ---------------------------------------------------------------------------


def test_l0001_rule_matches():
    rule = {
        "all": [
            {"field": "decision.side", "op": "eq", "value": "short"},
            {"field": "features.funding_z", "op": "lt", "value": -2.0},
        ]
    }
    ctx = {"decision": {"side": "short"}, "features": {"funding_z": -2.5}}
    assert evaluate_rule(rule, ctx) is True


def test_l0001_rule_no_match_long_side():
    rule = {
        "all": [
            {"field": "decision.side", "op": "eq", "value": "short"},
            {"field": "features.funding_z", "op": "lt", "value": -2.0},
        ]
    }
    ctx = {"decision": {"side": "long"}, "features": {"funding_z": -2.5}}
    assert evaluate_rule(rule, ctx) is False


def test_l0001_rule_no_match_funding_not_extreme():
    rule = {
        "all": [
            {"field": "decision.side", "op": "eq", "value": "short"},
            {"field": "features.funding_z", "op": "lt", "value": -2.0},
        ]
    }
    ctx = {"decision": {"side": "short"}, "features": {"funding_z": -1.0}}
    assert evaluate_rule(rule, ctx) is False
