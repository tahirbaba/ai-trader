"""KSOR Retriever — top-k relevant lessons and playbook rules for a given context."""

from __future__ import annotations

import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from trader.ksor.loader import KsorEntry, _validate_tighten_only

if TYPE_CHECKING:
    from trader.ksor.indexer import KsorIndexer

logger = logging.getLogger(__name__)

_ACTIVE = "active"
_CANDIDATE = "candidate"
_DEPRECATED = "deprecated"


class KsorRetriever:
    """Retrieves relevant KSOR entries for a given decision context.

    Parameters
    ----------
    indexer:
        A built :class:`~trader.ksor.indexer.KsorIndexer` instance.
    """

    def __init__(self, indexer: KsorIndexer) -> None:
        self._indexer = indexer

    def get_all_active(self) -> list[KsorEntry]:
        """Return all active (non-deprecated, non-candidate) entries.

        Used by :class:`~trader.risk.lesson_filter.LessonFilter` to load
        the full active lesson set for rule evaluation.
        """
        raw = self._indexer.get_all(status_filter=["active"])
        return [e for r in raw if (e := self._row_to_entry(r)) is not None]

    def retrieve(
        self,
        regime: str = "",
        symbol: str = "",
        side: str = "",
        tags: list[str] | None = None,
        k: int = 10,
        include_candidates: bool = False,
    ) -> list[KsorEntry]:
        """Return top-k relevant entries for the given context.

        *deprecated* entries are never returned.
        *candidate* entries are only returned if ``include_candidates=True``.

        Parameters
        ----------
        regime:             Current market regime (e.g. "range", "trend_up").
        symbol:             Symbol being traded (e.g. "BTC/USDT").
        side:               Trade side ("long" or "short").
        tags:               Extra tags to filter/search for.
        k:                  Maximum number of entries to return.
        include_candidates: If True, include candidate entries.

        Returns
        -------
        list[KsorEntry]:
            At most *k* relevant entries sorted by relevance score (descending).
        """
        tags = tags or []

        allowed_statuses = [_ACTIVE]
        if include_candidates:
            allowed_statuses.append(_CANDIDATE)

        # Use get_all to get the full candidate pool, then score each entry.
        # FTS is used only as an optional relevance boost (Phase 4 keeps it simple).
        raw = self._indexer.get_all(status_filter=allowed_statuses)

        # Score each result
        scored: list[tuple[float, KsorEntry]] = []
        for row in raw:
            entry = self._row_to_entry(row)
            if entry is None:
                continue
            if entry.status == _DEPRECATED:
                continue  # double-safety
            score = self._score(entry, regime=regime, symbol=symbol, side=side, tags=tags)
            scored.append((score, entry))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        return [entry for _, entry in scored[:k]]

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score(
        self,
        entry: KsorEntry,
        regime: str,
        symbol: str,
        side: str,
        tags: list[str],
    ) -> float:
        """Compute a relevance score for an entry given the context."""
        score = 0.0

        # Regime match
        if regime and entry.regime and regime in entry.regime:
            score += 3.0
        elif not entry.regime:
            score += 1.0  # no regime restriction → broad applicability

        # Symbol match
        sym_base = symbol.split("/")[0].upper() if symbol else ""
        if sym_base and entry.symbols and sym_base in entry.symbols:
            score += 2.0
        elif not entry.symbols:
            score += 0.5  # no symbol restriction → broad applicability

        # Tag overlap
        entry_tags = set(entry.tags)
        context_tags = set(tags)
        overlap = entry_tags & context_tags
        score += len(overlap) * 1.5

        # Body/condition text mention of side
        if side and (side in entry.condition.lower() or side in entry.body.lower()):
            score += 1.0

        # Playbook gets a small baseline boost (always useful)
        if entry.type == "playbook":
            score += 0.5

        # Active > candidate
        if entry.status == _ACTIVE:
            score += 1.0

        return score

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: dict[str, Any]) -> KsorEntry | None:
        """Convert a ksor_meta row dict to a KsorEntry."""
        rule = None
        if row.get("rule_json"):
            try:
                rule = json.loads(row["rule_json"])
            except (json.JSONDecodeError, TypeError):
                rule = None

        tags: list[str] = []
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            tags = json.loads(row.get("tags_json") or "[]")

        regime: list[str] = []
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            regime = json.loads(row.get("regime_json") or "[]")

        symbols: list[str] = []
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            symbols = json.loads(row.get("symbols_json") or "[]")

        entry = KsorEntry(
            id=row.get("entry_id", ""),
            type=row.get("type", ""),
            status=row.get("status", ""),
            tags=tags,
            regime=regime,
            symbols=symbols,
            rule=rule,
            effect=row.get("effect"),
            effect_value=row.get("effect_value"),
            file_path=row.get("file_path", ""),
        )
        try:
            _validate_tighten_only(entry)
        except ValueError as exc:
            logger.warning(
                "KsorRetriever: skipping entry %r — tighten-only violation: %s", entry.id, exc
            )
            return None
        return entry
