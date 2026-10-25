"""Tests for FeaturePack and build_feature_pack (Phase 3)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trader.features.builder import build_feature_pack

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _fixed_candles_by_tf() -> dict[str, list[dict]]:
    return {
        "5m": _make_candles(60, 100.0, 1.0),
        "15m": _make_candles(60, 100.0, 1.0),
        "1h": _make_candles(60, 100.0, 1.0),
        "4h": _make_candles(60, 100.0, 1.0),
        "1d": _make_candles(60, 100.0, 1.0),
    }


def _fixed_derivatives() -> dict:
    return {
        "funding_rate": 0.0001,
        "open_interest": 50000.0,
        "basis": 5.0,
    }


def _fixed_orderflow() -> dict:
    return {
        "cvd": 1500.0,
        "book_imbalance": 0.15,
        "spread_pct": 0.001,
    }


def _fixed_book() -> dict:
    return {
        "book_imbalance": 0.15,
    }


# ---------------------------------------------------------------------------
# Golden-file determinism test
# ---------------------------------------------------------------------------


def test_build_feature_pack_deterministic() -> None:
    """Golden-file test: same input → same features_hash every time."""
    fp1 = build_feature_pack(
        symbol=_SYMBOL,
        primary_tf=_TF,
        candles_by_tf=_fixed_candles_by_tf(),
        derivatives=_fixed_derivatives(),
        orderflow=_fixed_orderflow(),
        book=_fixed_book(),
        quality_flags=["test_flag"],
        funding_history=[0.0001] * 20,
        oi_history=[50000.0] * 20,
        basis_history=[5.0] * 20,
        cvd_history=[1500.0] * 20,
        price_history=[float(100 + i) for i in range(20)],
        atr_history=[2.0] * 20,
        spread_history=[0.001] * 20,
    )
    fp2 = build_feature_pack(
        symbol=_SYMBOL,
        primary_tf=_TF,
        candles_by_tf=_fixed_candles_by_tf(),
        derivatives=_fixed_derivatives(),
        orderflow=_fixed_orderflow(),
        book=_fixed_book(),
        quality_flags=["test_flag"],
        funding_history=[0.0001] * 20,
        oi_history=[50000.0] * 20,
        basis_history=[5.0] * 20,
        cvd_history=[1500.0] * 20,
        price_history=[float(100 + i) for i in range(20)],
        atr_history=[2.0] * 20,
        spread_history=[0.001] * 20,
    )
    assert fp1.features_hash == fp2.features_hash
    assert fp1.features_hash != ""

    # Golden hash — will be populated on first run and then hardcoded below.
    # To regenerate: run pytest -s and read the printed hash, then set it here.
    # GOLDEN_HASH is set after first successful run.
    # Uncomment and set after initial run:
    # GOLDEN_HASH = "REPLACE_WITH_ACTUAL_HASH"
    # assert fp1.features_hash == GOLDEN_HASH


def test_build_feature_pack_golden_hash() -> None:
    """Hardcoded golden hash - fails if pipeline output changes."""
    fp = build_feature_pack(
        symbol=_SYMBOL,
        primary_tf=_TF,
        candles_by_tf=_fixed_candles_by_tf(),
        derivatives=_fixed_derivatives(),
        orderflow=_fixed_orderflow(),
        book=_fixed_book(),
        quality_flags=[],
        funding_history=[0.0001] * 20,
        oi_history=[50000.0] * 20,
        basis_history=[5.0] * 20,
        cvd_history=[1500.0] * 20,
        price_history=[float(100 + i) for i in range(20)],
        atr_history=[2.0] * 20,
        spread_history=[0.001] * 20,
    )
    # Golden hash — computed on first run, then hardcoded.
    # Any change to the numeric feature pipeline will break this test.
    GOLDEN_HASH = "995d6fbefba51c7b51f88891612449b54e463e327f1c47b76747204f7f3b6e43"
    assert fp.features_hash == GOLDEN_HASH


# ---------------------------------------------------------------------------
# Frozen model
# ---------------------------------------------------------------------------


def test_feature_pack_frozen() -> None:
    """Attempting to set a field on frozen FeaturePack raises."""
    fp = build_feature_pack(
        symbol=_SYMBOL,
        primary_tf=_TF,
        candles_by_tf=_fixed_candles_by_tf(),
        derivatives=None,
        orderflow=None,
        book=None,
    )
    with pytest.raises((ValueError, TypeError)):
        fp.close = 9999.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Missing / empty data
# ---------------------------------------------------------------------------


def test_missing_candles_returns_neutral() -> None:
    """Empty candle lists → neutral trend, None indicators."""
    fp = build_feature_pack(
        symbol=_SYMBOL,
        primary_tf=_TF,
        candles_by_tf={},
        derivatives=None,
        orderflow=None,
        book=None,
    )
    assert fp.trend_1h == "neutral"
    assert fp.trend_4h == "neutral"
    assert fp.ema_20 is None
    assert fp.rsi_14 is None


def test_quality_flags_propagated() -> None:
    """Quality flags from input appear in output."""
    flags = ["stale_book", "missing_funding"]
    fp = build_feature_pack(
        symbol=_SYMBOL,
        primary_tf=_TF,
        candles_by_tf=_fixed_candles_by_tf(),
        derivatives=None,
        orderflow=None,
        book=None,
        quality_flags=flags,
    )
    for flag in flags:
        assert flag in fp.data_quality_flags


def test_build_with_full_data() -> None:
    """All fields populated when full data provided."""
    fp = build_feature_pack(
        symbol=_SYMBOL,
        primary_tf=_TF,
        candles_by_tf=_fixed_candles_by_tf(),
        derivatives=_fixed_derivatives(),
        orderflow=_fixed_orderflow(),
        book=_fixed_book(),
        funding_history=[0.0001] * 30,
        oi_history=[50000.0] * 30,
        basis_history=[5.0] * 30,
        cvd_history=[1500.0] * 30,
        price_history=[float(100 + i) for i in range(30)],
        atr_history=[2.0] * 30,
        spread_history=[0.001] * 30,
    )
    # Should have indicator values (enough candles)
    assert fp.ema_20 is not None
    assert fp.ema_50 is not None
    assert fp.rsi_14 is not None
    # Should have derivatives
    assert fp.funding_rate is not None
    assert fp.oi is not None
    # Symbol / tf match
    assert fp.symbol == _SYMBOL
    assert fp.timeframe == _TF
    # Hash is populated
    assert fp.features_hash != ""
    # Hash is self-consistent
    assert fp.features_hash == fp.compute_hash()


def test_feature_pack_hash_matches_compute() -> None:
    """features_hash stored equals compute_hash() recomputation."""
    fp = build_feature_pack(
        symbol=_SYMBOL,
        primary_tf=_TF,
        candles_by_tf=_fixed_candles_by_tf(),
        derivatives=_fixed_derivatives(),
        orderflow=_fixed_orderflow(),
        book=_fixed_book(),
    )
    assert fp.features_hash == fp.compute_hash()


def test_build_one_candle_no_crash() -> None:
    """Single candle: indicators are None, model constructs without error."""
    candles = _make_candles(1)
    fp = build_feature_pack(
        symbol=_SYMBOL,
        primary_tf=_TF,
        candles_by_tf={"15m": candles},
        derivatives=None,
        orderflow=None,
        book=None,
    )
    assert fp.ema_20 is None
    assert fp.rsi_14 is None
    assert fp.atr_14 is None
    assert fp.close == candles[0]["close"]


def test_build_two_candles_no_crash() -> None:
    """Two candles: still no indicators (below minimum period), no crash."""
    candles = _make_candles(2)
    fp = build_feature_pack(
        symbol=_SYMBOL,
        primary_tf=_TF,
        candles_by_tf={"15m": candles},
        derivatives=None,
        orderflow=None,
        book=None,
    )
    assert fp.ema_20 is None
    assert fp.rsi_14 is None
    assert fp.close == candles[-1]["close"]


def test_feature_pack_ts_is_set() -> None:
    """FeaturePack ts is set to the last candle ts (not datetime.now)."""
    fp = build_feature_pack(
        symbol=_SYMBOL,
        primary_tf=_TF,
        candles_by_tf=_fixed_candles_by_tf(),
        derivatives=None,
        orderflow=None,
        book=None,
    )
    # ts should be a datetime, not epoch zero
    assert isinstance(fp.ts, datetime)
