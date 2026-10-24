"""Phase 2A: Integration tests for snapshot_builder.build_snapshot."""

from __future__ import annotations

import datetime
import json

import pytest

from trader.data.collector import CollectorResult
from trader.data.snapshot_builder import build_snapshot
from trader.dsor.models import MarketSnapshot


def _make_ohlcv() -> CollectorResult:
    return CollectorResult(
        symbol="XRPUSDT",
        ts=datetime.datetime(2026, 1, 15, 12, 0, 0),
        data={
            "5m": {"ts_ms": 1700000000000, "open": 0.60, "high": 0.62, "low": 0.59, "close": 0.61,
                   "volume": 1000000.0, "turnover": 610000.0},
            "1h": {"ts_ms": 1699996800000, "open": 0.58, "high": 0.63, "low": 0.57, "close": 0.61,
                   "volume": 5000000.0, "turnover": 3050000.0},
        },
        quality_flags=[],
    )


def _make_book() -> CollectorResult:
    return CollectorResult(
        symbol="XRPUSDT",
        ts=datetime.datetime(2026, 1, 15, 12, 0, 1),
        data={
            "best_bid": 0.6095,
            "best_ask": 0.6105,
            "spread": 0.001,
            "spread_pct": 0.164,
            "mid_price": 0.6100,
            "bids": [[0.6095, 5000.0], [0.609, 3000.0]],
            "asks": [[0.6105, 4000.0], [0.611, 2000.0]],
        },
        quality_flags=[],
    )


def _make_orderflow() -> CollectorResult:
    return CollectorResult(
        symbol="XRPUSDT",
        ts=datetime.datetime(2026, 1, 15, 12, 0, 2),
        data={
            "taker_buy_volume": 350000.0,
            "taker_sell_volume": 150000.0,
            "cvd": 200000.0,
            "book_imbalance": 0.43,
            "trade_count": 60,
        },
        quality_flags=[],
    )


def _make_derivatives() -> CollectorResult:
    return CollectorResult(
        symbol="XRPUSDT",
        ts=datetime.datetime(2026, 1, 15, 12, 0, 3),
        data={
            "funding_rate": 0.0001,
            "next_funding_time_ms": 1700020000000,
            "open_interest": 250000000.0,
            "mark_price": 0.6049,
            "index_price": 0.6045,
            "basis": 0.0004,
            "long_ratio": 0.55,
            "short_ratio": 0.45,
        },
        quality_flags=[],
    )


class TestBuildSnapshot:
    def test_returns_market_snapshot_instance(self):
        snap = build_snapshot(
            symbol="XRPUSDT",
            timeframe="5m",
            ohlcv=_make_ohlcv(),
            book=_make_book(),
            orderflow=_make_orderflow(),
            derivatives=_make_derivatives(),
            session="london_ny_overlap",
        )
        assert isinstance(snap, MarketSnapshot)

    def test_symbol_and_timeframe_set(self):
        snap = build_snapshot("DOGEUSDT", "1h", ohlcv=_make_ohlcv())
        assert snap.symbol == "DOGEUSDT"
        assert snap.timeframe == "1h"

    def test_ohlcv_json_non_empty(self):
        snap = build_snapshot("XRPUSDT", "5m", ohlcv=_make_ohlcv())
        parsed = json.loads(snap.ohlcv_json)
        assert "5m" in parsed
        assert parsed["5m"]["close"] == pytest.approx(0.61)

    def test_indicators_json_has_book(self):
        snap = build_snapshot("XRPUSDT", "5m", book=_make_book())
        parsed = json.loads(snap.indicators_json)
        assert "book" in parsed
        assert parsed["book"]["best_bid"] == pytest.approx(0.6095)

    def test_orderflow_json_populated(self):
        snap = build_snapshot("XRPUSDT", "5m", orderflow=_make_orderflow())
        assert snap.orderflow_json is not None
        parsed = json.loads(snap.orderflow_json)
        assert "cvd" in parsed
        assert parsed["cvd"] == pytest.approx(200000.0)

    def test_derivatives_fields_set(self):
        snap = build_snapshot("XRPUSDT", "5m", derivatives=_make_derivatives())
        assert snap.funding == pytest.approx(0.0001)
        assert snap.open_interest == pytest.approx(250000000.0)
        assert snap.basis == pytest.approx(0.0004)

    def test_session_field(self):
        snap = build_snapshot("XRPUSDT", "5m", session="london_ny_overlap")
        assert snap.session == "london_ny_overlap"

    def test_data_quality_flags_json(self):
        ohlcv = CollectorResult("XRPUSDT", datetime.datetime.utcnow(), {}, ["stale_data"])
        snap = build_snapshot("XRPUSDT", "5m", ohlcv=ohlcv, quality_flags=["clock_drift"])
        flags = json.loads(snap.data_quality_flags)
        assert "stale_data" in flags
        assert "clock_drift" in flags

    def test_empty_quality_flags(self):
        snap = build_snapshot("XRPUSDT", "5m")
        flags = json.loads(snap.data_quality_flags)
        assert flags == []

    def test_ts_taken_from_ohlcv(self):
        expected_ts = datetime.datetime(2026, 1, 15, 12, 0, 0)
        ohlcv = CollectorResult("XRPUSDT", expected_ts, {})
        snap = build_snapshot("XRPUSDT", "5m", ohlcv=ohlcv)
        assert snap.ts == expected_ts

    def test_ts_fallback_to_derivatives(self):
        deriv_ts = datetime.datetime(2026, 1, 15, 10, 0, 0)
        deriv = CollectorResult("XRPUSDT", deriv_ts, {"funding_rate": 0.0001})
        snap = build_snapshot("XRPUSDT", "5m", derivatives=deriv)
        assert snap.ts == deriv_ts

    def test_all_collectors_combined(self):
        snap = build_snapshot(
            symbol="XRPUSDT",
            timeframe="1h",
            ohlcv=_make_ohlcv(),
            book=_make_book(),
            orderflow=_make_orderflow(),
            derivatives=_make_derivatives(),
            session="london",
            quality_flags=[],
        )
        assert snap.symbol == "XRPUSDT"
        assert snap.timeframe == "1h"
        assert snap.session == "london"
        assert json.loads(snap.ohlcv_json) != {}
        assert json.loads(snap.indicators_json) != {}
        assert snap.orderflow_json is not None
        assert snap.funding is not None
        assert snap.basis is not None

    def test_minimal_snapshot_no_collectors(self):
        snap = build_snapshot("DOGEUSDT", "4h")
        assert snap.symbol == "DOGEUSDT"
        assert snap.timeframe == "4h"
        assert json.loads(snap.ohlcv_json) == {}
        assert snap.funding is None
        assert snap.basis is None
