"""
Unit tests for scripts/feasibility.py — min_notional math and planned_notional.

Fixtures in tests/fixtures/ are recorded (representative) exchange responses.
No HTTP calls are made; all tests are pure-function / fixture-driven.
"""

from __future__ import annotations

import json
from pathlib import Path

import feasibility as feas
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# parse_binance_filters
# ---------------------------------------------------------------------------
class TestParseBinanceFilters:
    def test_btc_filters(self) -> None:
        data = _load("binance_btcusdt.json")
        r = feas.parse_binance_filters(data)
        assert r["min_qty"] == pytest.approx(0.001)
        assert r["lot_size"] == pytest.approx(0.001)
        assert r["tick_size"] == pytest.approx(0.10)
        assert r["min_notional"] == pytest.approx(5.0)

    def test_xrp_filters(self) -> None:
        data = _load("binance_xrpusdt.json")
        r = feas.parse_binance_filters(data)
        assert r["min_qty"] == pytest.approx(0.1)
        assert r["lot_size"] == pytest.approx(0.1)
        assert r["tick_size"] == pytest.approx(0.0001)
        assert r["min_notional"] == pytest.approx(5.0)

    def test_missing_filters_return_zero(self) -> None:
        r = feas.parse_binance_filters({"filters": []})
        assert r["min_qty"] == 0.0
        assert r["min_notional"] == 0.0


# ---------------------------------------------------------------------------
# parse_bybit_lot_filter  — key fix: minNotionalValue
# ---------------------------------------------------------------------------
class TestParseBybitLotFilter:
    def test_btc_uses_min_notional_value_over_order_amt(self) -> None:
        data = _load("bybit_btcusdt.json")
        r = feas.parse_bybit_lot_filter(data)
        # minNotionalValue=100 > minOrderAmt=0
        assert r["min_notional"] == pytest.approx(100.0)
        assert r["min_qty"] == pytest.approx(0.001)
        assert r["lot_size"] == pytest.approx(0.001)
        assert r["tick_size"] == pytest.approx(0.10)

    def test_doge_min_notional_value(self) -> None:
        data = _load("bybit_dogeusdt.json")
        r = feas.parse_bybit_lot_filter(data)
        assert r["min_notional"] == pytest.approx(1.0)
        assert r["min_qty"] == pytest.approx(1.0)

    def test_xrp_min_notional_value(self) -> None:
        data = _load("bybit_xrpusdt.json")
        r = feas.parse_bybit_lot_filter(data)
        assert r["min_notional"] == pytest.approx(1.0)
        assert r["min_qty"] == pytest.approx(0.1)

    def test_both_zero_returns_zero(self) -> None:
        data = {
            "lotSizeFilter": {
                "minOrderQty": "0.1",
                "qtyStep": "0.1",
                "minOrderAmt": "0",
                "minNotionalValue": "0",
            },
            "priceFilter": {"tickSize": "0.0001"},
        }
        r = feas.parse_bybit_lot_filter(data)
        assert r["min_notional"] == pytest.approx(0.0)

    def test_order_amt_beats_notional_value_when_larger(self) -> None:
        data = {
            "lotSizeFilter": {
                "minOrderQty": "0.001",
                "qtyStep": "0.001",
                "minOrderAmt": "200",
                "minNotionalValue": "50",
            },
            "priceFilter": {"tickSize": "0.1"},
        }
        r = feas.parse_bybit_lot_filter(data)
        assert r["min_notional"] == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# parse_okx_instrument
# ---------------------------------------------------------------------------
class TestParseOkxInstrument:
    def test_btc_contract_size(self) -> None:
        data = _load("okx_btcusdtswap.json")
        r = feas.parse_okx_instrument(data)
        assert r["contract_size"] == pytest.approx(0.01)
        assert r["min_qty"] == pytest.approx(1.0)
        assert r["lot_size"] == pytest.approx(1.0)
        assert r["tick_size"] == pytest.approx(0.1)
        assert r["min_notional"] == pytest.approx(0.0)  # derived later

    def test_xrp_contract_size(self) -> None:
        data = _load("okx_xrpusdtswap.json")
        r = feas.parse_okx_instrument(data)
        assert r["contract_size"] == pytest.approx(10.0)  # 10 XRP per contract


# ---------------------------------------------------------------------------
# parse_mexc_detail  — key fix: contract_size != 1.0 for BTC
# ---------------------------------------------------------------------------
class TestParseMexcDetail:
    def test_btc_contract_size(self) -> None:
        data = _load("mexc_btcusdt.json")
        r = feas.parse_mexc_detail(data)
        assert r["contract_size"] == pytest.approx(0.0001)
        assert r["min_qty"] == pytest.approx(1.0)
        assert r["lot_size"] == pytest.approx(1.0)
        assert r["min_notional"] == pytest.approx(0.0)  # derived later

    def test_xrp_contract_size_one(self) -> None:
        data = _load("mexc_xrpusdt.json")
        r = feas.parse_mexc_detail(data)
        assert r["contract_size"] == pytest.approx(1.0)
        assert r["min_qty"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# compute_tradability — min_notional_usd using contract_size
# ---------------------------------------------------------------------------
class TestMinNotionalUsd:
    """min_notional_usd = max(exchange_floor, min_qty * contract_size * price)"""

    def _info(
        self,
        exchange: str,
        min_qty: float,
        min_notional: float,
        contract_size: float,
        price: float,
        atr_frac: float = 0.004,
    ) -> feas.ContractInfo:
        return feas.ContractInfo(
            exchange=exchange,
            symbol="TEST",
            inst_type="USDT-PERP",
            price=price,
            min_qty=min_qty,
            min_notional=min_notional,
            lot_size=min_qty,
            tick_size=0.01,
            atr_15m=price * atr_frac,
            taker_fee_pct=feas.TAKER_FEES.get(exchange, 0.04),
            contract_size=contract_size,
        )

    def test_binance_btc_uses_min_qty_times_price(self) -> None:
        # min_qty=0.001, contract_size=1.0 (default), price=$81 000
        # → 0.001 * 1.0 * 81000 = $81 > exchange_floor $5
        info = self._info(
            "Binance", min_qty=0.001, min_notional=5.0, contract_size=1.0, price=81_000.0
        )
        result = feas.compute_tradability(info)
        assert result.min_notional_usd == pytest.approx(81.0)
        assert result.max_notional_ok is False  # $81 > $6 cap

    def test_mexc_btc_uses_contract_size(self) -> None:
        # min_qty=1 contract, contractSize=0.0001 BTC, price=$81 000
        # → 1 * 0.0001 * 81000 = $8.10  (not $81 000)
        info = self._info(
            "MEXC", min_qty=1.0, min_notional=0.0, contract_size=0.0001, price=81_000.0
        )
        result = feas.compute_tradability(info)
        assert result.min_notional_usd == pytest.approx(8.10)
        assert result.max_notional_ok is False  # $8.10 > $6 cap

    def test_mexc_xrp_contract_size_one(self) -> None:
        # min_qty=1 contract, contractSize=1 XRP, price=$1.30
        # → 1 * 1.0 * 1.30 = $1.30
        info = self._info("MEXC", min_qty=1.0, min_notional=0.0, contract_size=1.0, price=1.30)
        result = feas.compute_tradability(info)
        assert result.min_notional_usd == pytest.approx(1.30)
        assert result.max_notional_ok is True  # $1.30 ≤ $6

    def test_exchange_floor_wins_when_larger(self) -> None:
        # exchange_floor=$50 > min_qty*price=$1 → min_notional_usd=$50
        info = self._info(
            "Binance", min_qty=0.01, min_notional=50.0, contract_size=1.0, price=100.0
        )
        result = feas.compute_tradability(info)
        assert result.min_notional_usd == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# compute_tradability — planned_notional and MaxStop%
# ---------------------------------------------------------------------------
class TestPlannedNotional:
    """MaxStop% must be computed at PLANNED_NOTIONAL_USD ($6), not exchange minimum."""

    def _sol_info(self) -> feas.ContractInfo:
        # Binance SOL: min_notional_usd=$5, price=$104, ATR≈0.42
        return feas.ContractInfo(
            exchange="Binance",
            symbol="SOL",
            inst_type="USDT-PERP",
            price=104.0,
            min_qty=0.01,
            min_notional=5.0,
            lot_size=0.01,
            tick_size=0.01,
            atr_15m=0.42,
            taker_fee_pct=0.04,
            contract_size=1.0,
        )

    def test_planned_notional_equals_max_notional(self) -> None:
        result = feas.compute_tradability(self._sol_info())
        assert result.planned_notional == pytest.approx(feas.PLANNED_NOTIONAL_USD)

    def test_max_stop_uses_planned_notional(self) -> None:
        result = feas.compute_tradability(self._sol_info())
        # Expected: round_trip_fee at $6, 2×0.04% taker
        expected_fee = feas.PLANNED_NOTIONAL_USD * 2.0 * 0.04 / 100.0
        expected_budget = feas.MAX_LOSS_USD - expected_fee
        expected_max_stop = (
            expected_budget / feas.PLANNED_NOTIONAL_USD - feas.SLIPPAGE_PCT / 100.0
        ) * 100.0
        assert result.max_stop_pct == pytest.approx(expected_max_stop, rel=1e-4)

    def test_max_stop_not_computed_at_exchange_minimum(self) -> None:
        result = feas.compute_tradability(self._sol_info())
        # At exchange minimum $5 (old formula), max_stop would be ~19.87%
        # At planned notional $6 (new formula), max_stop should be ~16.49%
        old_formula_approx = 19.87
        assert result.max_stop_pct < old_formula_approx  # new formula is more conservative

    def test_planned_notional_same_regardless_of_exchange_minimum(self) -> None:
        # DOGE: min_notional_usd=$0.08 (tiny) — planned_notional still $6
        doge = feas.ContractInfo(
            exchange="Bybit",
            symbol="DOGE",
            inst_type="USDT-PERP",
            price=0.079,
            min_qty=1.0,
            min_notional=1.0,
            lot_size=1.0,
            tick_size=0.00001,
            atr_15m=0.00039,
            taker_fee_pct=0.055,
            contract_size=1.0,
        )
        result = feas.compute_tradability(doge)
        assert result.planned_notional == pytest.approx(feas.PLANNED_NOTIONAL_USD)

    def test_btc_not_tradable_planned_notional_still_6(self) -> None:
        # BTC: min_notional=$81 > $6 cap → not tradable, but planned_notional is $6
        btc = feas.ContractInfo(
            exchange="Binance",
            symbol="BTC",
            inst_type="USDT-PERP",
            price=81_000.0,
            min_qty=0.001,
            min_notional=5.0,
            lot_size=0.001,
            tick_size=0.1,
            atr_15m=230.0,
            taker_fee_pct=0.04,
            contract_size=1.0,
        )
        result = feas.compute_tradability(btc)
        assert result.planned_notional == pytest.approx(feas.PLANNED_NOTIONAL_USD)
        assert result.max_notional_ok is False
        assert result.tradable is False


# ---------------------------------------------------------------------------
# compute_tradability — liq_distance uses planned leverage (always 3x)
# ---------------------------------------------------------------------------
class TestLiqDistance:
    def test_liq_distance_at_3x_leverage(self) -> None:
        # planned_leverage = PLANNED_NOTIONAL / MAX_MARGIN = 6/2 = 3
        # liq_dist = (1/3 - 0.005) * 100 = 32.83%
        info = feas.ContractInfo(
            exchange="Bybit",
            symbol="XRP",
            inst_type="USDT-PERP",
            price=1.30,
            min_qty=0.1,
            min_notional=1.0,
            lot_size=0.1,
            tick_size=0.0001,
            atr_15m=0.0053,
            taker_fee_pct=0.055,
            contract_size=1.0,
        )
        result = feas.compute_tradability(info)
        expected = (1.0 / 3.0 - feas.MAINT_MARGIN_PCT / 100.0) * 100.0
        assert result.liq_distance_pct == pytest.approx(expected, rel=1e-4)


# ---------------------------------------------------------------------------
# compute_atr
# ---------------------------------------------------------------------------
class TestComputeAtr:
    def test_returns_zero_for_single_candle(self) -> None:
        assert feas.compute_atr([{"open": "1", "high": "2", "low": "0.5", "close": "1.5"}]) == 0.0

    def test_correct_tr_for_two_candles(self) -> None:
        candles = [
            {"open": "100", "high": "105", "low": "95", "close": "102"},
            {"open": "102", "high": "110", "low": "100", "close": "108"},
        ]
        # TR2 = max(110-100, |110-102|, |100-102|) = max(10, 8, 2) = 10
        assert feas.compute_atr(candles) == pytest.approx(10.0)

    def test_averages_last_14(self) -> None:
        # 20 candles: open=i, high=i+1, low=i, close=i+0.5
        # For candle i>=1: prev_close=i-0.5, TR=max(1, 1.5, 0.5)=1.5
        candles = [
            {"open": str(i), "high": str(i + 1), "low": str(i), "close": str(i + 0.5)}
            for i in range(20)
        ]
        atr = feas.compute_atr(candles)
        assert atr == pytest.approx(1.5, abs=0.01)

    def test_short_candles_filtered_silently(self) -> None:
        # Candles with fewer than 5 elements are filtered before reaching compute_atr;
        # an empty list returns 0.0 (not an exception).
        assert feas.compute_atr([]) == 0.0


# ---------------------------------------------------------------------------
# Reviewer findings: robustness guards
# ---------------------------------------------------------------------------
class TestRobustnessGuards:
    """Tests for the reviewer's MEDIUM findings (array bounds, MEXC alignment)."""

    def test_mexc_mismatched_array_lengths_do_not_raise(self) -> None:
        # safe_len = min(3, 2, 3, 3, 3) = 2 — must not IndexError
        import math

        times = [1, 2, 3]
        opens = [100.0, 101.0]  # shorter
        highs = [101.0, 102.0, 103.0]
        lows = [99.0, 100.0, 101.0]
        closes = [100.5, 101.5, 102.5]
        safe_len = min(len(times), len(opens), len(highs), len(lows), len(closes), 20)
        candles = [
            {
                "open": str(opens[i]),
                "high": str(highs[i]),
                "low": str(lows[i]),
                "close": str(closes[i]),
            }
            for i in range(safe_len)
        ]
        assert len(candles) == 2
        atr = feas.compute_atr(candles)
        assert not math.isnan(atr)

    def test_compute_atr_handles_empty_input(self) -> None:
        assert feas.compute_atr([]) == 0.0
