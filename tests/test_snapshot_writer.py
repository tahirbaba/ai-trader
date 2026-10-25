"""Tests for snapshot_writer.save_feature_pack (Phase 3)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trader.dsor.base import Base
from trader.features.builder import build_feature_pack
from trader.features.snapshot_writer import save_feature_pack

_TS = datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC)
_SYMBOL = "BTCUSDT"
_TF = "15m"


def _make_candles(n: int = 60, start: float = 100.0, step: float = 1.0) -> list[dict]:
    result = []
    for i in range(n):
        close = start + i * step
        result.append(
            {
                "close": close,
                "high": close + 1.0,
                "low": close - 1.0,
                "volume": 100.0,
                "ts_ms": 1_700_000_000_000 + i * 60_000,
            }
        )
    return result


@pytest.fixture()
def db_session():
    """In-memory SQLite session for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def sample_fp():
    return build_feature_pack(
        symbol=_SYMBOL,
        primary_tf=_TF,
        candles_by_tf={
            "5m": _make_candles(60),
            "15m": _make_candles(60),
            "1h": _make_candles(60),
            "4h": _make_candles(60),
            "1d": _make_candles(60),
        },
        derivatives={
            "funding_rate": 0.0001,
            "open_interest": 50000.0,
            "basis": 5.0,
        },
        orderflow={
            "cvd": 1500.0,
            "book_imbalance": 0.15,
            "spread_pct": 0.001,
        },
        book={"book_imbalance": 0.15},
        quality_flags=["test_flag"],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_feature_pack_creates_row(db_session, sample_fp) -> None:
    """save_feature_pack adds a MarketSnapshot row to the session."""
    from trader.dsor.models import MarketSnapshot

    save_feature_pack(db_session, sample_fp)
    db_session.commit()

    rows = db_session.query(MarketSnapshot).all()
    assert len(rows) == 1
    assert rows[0].symbol == _SYMBOL
    assert rows[0].timeframe == _TF


def test_features_hash_stored(db_session, sample_fp) -> None:
    """features_hash column matches fp.features_hash."""
    from trader.dsor.models import MarketSnapshot

    save_feature_pack(db_session, sample_fp)
    db_session.commit()

    row = db_session.query(MarketSnapshot).first()
    assert row is not None
    assert row.features_hash == sample_fp.features_hash


def test_regime_stored(db_session, sample_fp) -> None:
    """regime column matches fp.regime."""
    from trader.dsor.models import MarketSnapshot

    save_feature_pack(db_session, sample_fp)
    db_session.commit()

    row = db_session.query(MarketSnapshot).first()
    assert row is not None
    assert row.regime == sample_fp.regime


def test_save_does_not_commit(db_session, sample_fp) -> None:
    """save_feature_pack does NOT commit; caller must commit."""
    from trader.dsor.models import MarketSnapshot

    # Save but don't commit
    save_feature_pack(db_session, sample_fp)
    # Create a fresh session and check nothing is committed
    engine = db_session.bind
    with Session(engine) as fresh_session:
        rows = fresh_session.query(MarketSnapshot).all()
        # No rows because db_session hasn't committed yet
        assert len(rows) == 0


def test_save_with_session_tag(db_session, sample_fp) -> None:
    """session_tag parameter is accepted without error."""
    snap = save_feature_pack(db_session, sample_fp, session_tag="asia")
    db_session.commit()
    assert snap is not None
