"""KSOR Rule DSL evaluator.

Evaluates machine-checkable lesson rules against a context dict.

Rule format (YAML/dict):
  Leaf clause:  {field: "dot.path.key", op: "eq|ne|lt|le|gt|ge|in|not_in", value: <val>}
  Wrapper:      {all: [<clause>, ...]}
                {any: [<clause>, ...]}
                {not: <clause>}

Rules:
- No eval, exec, or importlib anywhere in this module.
- Unknown field → returns False (fail-safe).
- Unknown op → raises RuleEngineError (fail-loud for configuration mistakes).
- Dot-path fields are resolved into nested dicts (e.g. "decision.side" → ctx["decision"]["side"]).
"""

from __future__ import annotations

from typing import Any

_SUPPORTED_OPS = frozenset({"eq", "ne", "lt", "le", "gt", "ge", "in", "not_in"})
_SENTINEL = object()  # marks "field not found"


class RuleEngineError(Exception):
    """Raised when the rule DSL is misconfigured (unknown op, malformed rule)."""


def _get_dotpath(ctx: dict[str, Any], field: str) -> Any:
    """Resolve a dot-separated path into a nested dict.  Returns _SENTINEL on miss."""
    parts = field.split(".")
    node: Any = ctx
    for part in parts:
        if not isinstance(node, dict):
            return _SENTINEL
        node = node.get(part, _SENTINEL)
        if node is _SENTINEL:
            return _SENTINEL
    return node


def _eval_leaf(clause: dict[str, Any], ctx: dict[str, Any]) -> bool:
    """Evaluate a single {field, op, value} clause."""
    field: str = clause["field"]
    op: str = clause["op"]
    value: Any = clause["value"]

    if op not in _SUPPORTED_OPS:
        raise RuleEngineError(f"Unknown op {op!r}. Supported: {sorted(_SUPPORTED_OPS)}")

    actual = _get_dotpath(ctx, field)
    if actual is _SENTINEL:
        # Unknown field → False (fail-safe)
        return False

    try:
        if op == "eq":
            return bool(actual == value)
        if op == "ne":
            return bool(actual != value)
        if op == "lt":
            return bool(actual < value)
        if op == "le":
            return bool(actual <= value)
        if op == "gt":
            return bool(actual > value)
        if op == "ge":
            return bool(actual >= value)
        if op == "in":
            return bool(actual in value)
        if op == "not_in":
            return bool(actual not in value)
    except (TypeError, ValueError):
        # Type mismatch in comparison → treat as non-match
        return False

    # Should never reach here given the check at top of function.
    raise RuleEngineError(f"Unhandled op {op!r}")  # pragma: no cover


def evaluate_rule(rule: dict[str, Any], ctx: dict[str, Any]) -> bool:
    """Recursively evaluate a rule dict against a context dict.

    Args:
        rule:  A rule dict (leaf clause or wrapper).
        ctx:   The context dict (e.g. {"decision": {...}, "features": {...}}).

    Returns:
        True if the rule matches, False otherwise.

    Raises:
        RuleEngineError: If the rule contains an unknown op or is structurally invalid.
    """
    if "all" in rule:
        clauses = rule["all"]
        if not isinstance(clauses, list):
            raise RuleEngineError("'all' must be a list of clauses")
        return all(evaluate_rule(c, ctx) for c in clauses)

    if "any" in rule:
        clauses = rule["any"]
        if not isinstance(clauses, list):
            raise RuleEngineError("'any' must be a list of clauses")
        return any(evaluate_rule(c, ctx) for c in clauses)

    if "not" in rule:
        inner = rule["not"]
        return not evaluate_rule(inner, ctx)

    # Leaf clause
    if "field" in rule and "op" in rule:
        return _eval_leaf(rule, ctx)

    raise RuleEngineError(f"Unrecognised rule structure: {list(rule.keys())}")
