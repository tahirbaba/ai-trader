"""Tests for ksor/retriever.py — retrieve() returns correct entries."""

from trader.ksor.indexer import KsorIndexer
from trader.ksor.loader import KsorEntry
from trader.ksor.retriever import KsorRetriever

# ---------------------------------------------------------------------------
# Helpers — build entries in memory without files
# ---------------------------------------------------------------------------


def make_entry(
    id: str,
    status: str = "active",
    type: str = "lesson",
    tags: list[str] | None = None,
    regime: list[str] | None = None,
    symbols: list[str] | None = None,
    body: str = "",
    condition: str = "",
    rule: dict | None = None,
    effect: str | None = None,
) -> KsorEntry:
    return KsorEntry(
        id=id,
        type=type,
        status=status,
        tags=tags or [],
        regime=regime or [],
        symbols=symbols or [],
        body=body,
        condition=condition,
        rule=rule,
        effect=effect,
    )


ACTIVE_LESSON = make_entry(
    "L-0001",
    status="active",
    tags=["funding_negative", "short"],
    regime=["range"],
    body="Short with negative funding is dangerous",
    condition="Short when funding_z < -2",
    effect="confidence_penalty",
)
ACTIVE_PLAYBOOK = make_entry(
    "P-0001",
    status="active",
    type="playbook",
    tags=["htf_trend", "discipline"],
    body="Respect the higher timeframe trend always",
    condition="Core trading rules",
)
DEPRECATED_LESSON = make_entry(
    "L-0002",
    status="deprecated",
    tags=["funding_negative"],
    regime=["range"],
    body="Old deprecated lesson",
)
CANDIDATE_LESSON = make_entry(
    "L-0003",
    status="candidate",
    tags=["funding_positive", "long"],
    regime=["trend_up"],
    body="Candidate lesson, not yet approved",
)
ACTIVE_UNRELATED = make_entry(
    "L-0004",
    status="active",
    tags=["session_asia"],
    regime=["range"],
    body="Asia session rules",
)


def make_retriever(entries: list[KsorEntry]) -> KsorRetriever:
    """Build an in-memory indexer + retriever from a list of KsorEntry objects."""
    indexer = KsorIndexer(db_path=":memory:")
    indexer.build(entries)
    return KsorRetriever(indexer)


# ---------------------------------------------------------------------------
# Active entries returned
# ---------------------------------------------------------------------------


def test_returns_active_lesson():
    retriever = make_retriever([ACTIVE_LESSON, ACTIVE_PLAYBOOK])
    results = retriever.retrieve(regime="range", symbol="BTC", side="short", tags=[], k=10)
    ids = {e.id for e in results}
    assert "L-0001" in ids


def test_returns_active_playbook():
    retriever = make_retriever([ACTIVE_LESSON, ACTIVE_PLAYBOOK])
    results = retriever.retrieve(regime="range", symbol="BTC", side="short", tags=[], k=10)
    ids = {e.id for e in results}
    assert "P-0001" in ids


# ---------------------------------------------------------------------------
# deprecated never returned
# ---------------------------------------------------------------------------


def test_deprecated_not_returned():
    retriever = make_retriever([ACTIVE_LESSON, DEPRECATED_LESSON])
    results = retriever.retrieve(regime="range", symbol="BTC", side="short", tags=[], k=10)
    ids = {e.id for e in results}
    assert "L-0002" not in ids


def test_deprecated_not_returned_even_with_perfect_tag_match():
    retriever = make_retriever([DEPRECATED_LESSON])
    results = retriever.retrieve(
        regime="range",
        symbol="BTC",
        side="short",
        tags=["funding_negative"],
        k=10,
    )
    assert results == []


# ---------------------------------------------------------------------------
# candidate not returned by default
# ---------------------------------------------------------------------------


def test_candidate_not_returned_by_default():
    retriever = make_retriever([ACTIVE_LESSON, CANDIDATE_LESSON])
    results = retriever.retrieve(regime="trend_up", symbol="BTC", side="long", tags=[], k=10)
    ids = {e.id for e in results}
    assert "L-0003" not in ids


def test_candidate_returned_when_include_candidates_true():
    retriever = make_retriever([ACTIVE_LESSON, CANDIDATE_LESSON])
    results = retriever.retrieve(
        regime="trend_up",
        symbol="BTC",
        side="long",
        tags=[],
        k=10,
        include_candidates=True,
    )
    ids = {e.id for e in results}
    assert "L-0003" in ids


# ---------------------------------------------------------------------------
# k limit respected
# ---------------------------------------------------------------------------


def test_k_limits_results():
    entries = [
        make_entry(f"L-{i:04d}", status="active", body=f"lesson {i}", tags=["funding_negative"])
        for i in range(10)
    ]
    retriever = make_retriever(entries)
    results = retriever.retrieve(regime="range", symbol="BTC", side="short", tags=[], k=3)
    assert len(results) <= 3


# ---------------------------------------------------------------------------
# empty results are OK
# ---------------------------------------------------------------------------


def test_empty_results_ok():
    retriever = make_retriever([DEPRECATED_LESSON])
    results = retriever.retrieve(regime="range", symbol="BTC", side="short", tags=[], k=10)
    assert results == []


def test_no_entries_empty_results():
    retriever = make_retriever([])
    results = retriever.retrieve(regime="trend_up", symbol="ETH", side="long", tags=[], k=5)
    assert results == []


# ---------------------------------------------------------------------------
# Tag filter
# ---------------------------------------------------------------------------


def test_tag_filter_improves_relevance():
    retriever = make_retriever([ACTIVE_LESSON, ACTIVE_UNRELATED])
    # Ask specifically for funding-related tags
    results = retriever.retrieve(
        regime="range",
        symbol="BTC",
        side="short",
        tags=["funding_negative"],
        k=5,
    )
    ids = [e.id for e in results]
    # L-0001 has matching tags; should appear
    assert "L-0001" in ids
