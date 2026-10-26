"""Shared constants for lesson safety constraints (tighten-only policy)."""

from __future__ import annotations

TIGHTEN_ONLY_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "block",
    "reduce_confidence",
    "require_confirmation",
    "add_checklist_item",
    "reduce_size",
})

TIGHTEN_ONLY_FORBIDDEN_KEYS: frozenset[str] = frozenset({
    "raise_size",
    "raise_leverage",
    "relax_limit",
    "increase_max_margin",
    "increase_leverage",
})
