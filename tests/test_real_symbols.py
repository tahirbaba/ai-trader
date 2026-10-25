"""Phase 7: Real-symbol integration tests for XRP/USDT:USDT and DOGE/USDT:USDT.

Tests cover:
- PaperBroker: market long fill, SL trigger, TP trigger per symbol
- RiskGate integration: DecisionInput + MarketState with real symbols, no crash
"""

from __future__ import annotations

import pytest

from trader.config.settings import AppSettings, ModelNames, RiskSettings, Settings
from trader.execution.paper_broker import PaperBroker
from trader.risk.gate import RiskGate
from trader.risk.models import (
    AccountState,
    DecisionInput,
    LessonCheckInput,
    MarketState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REAL_SYMBOLS = ["XRP/USDT:USDT", "DOGE/USDT:USDT"]

# Approximate realistic prices for the two symbols
_PRICES: dict[str, float] = {
    "XRP/USDT:USDT": 0.60,
    "DOGE/USDT:USDT": 0.12,
}


def make_settings(allowed_symbols: list[str] | None = None) -> Settings:
    if allowed_symbols is None:
        allowed_symbols = ["XRP/USDT:USDT", "DOGE/USDT:USDT"]
    risk = RiskSettings(
        max_margin_per_trade_usd=200.0,
        max_loss_per_trade_usd=100.0,
        slippage_buffer_pct=0.05,
        max_leverage=10,
        margin_mode="isolated",
        position_mode="one_way",
        daily_loss_limit_usd=300.0,
        weekly_loss_limit_usd=600.0,
        equity_floor_usd=5.0,
        allowed_symbols=allowed_symbols,
    )
    app = AppSettings(
        models=ModelNames(screening="x", decision="x", review="x"),
    )
    return Settings(trading_mode="paper", app=app, risk=risk)


def make_broker() -> PaperBroker:
    return PaperBroker(initial_balance=10_000.0)


def make_decision(symbol: str, price: float) -> DecisionInput:
    sl = round(price * 0.90, 6)
    tp = round(price * 1.20, 6)
    return DecisionInput(
        action="LONG",
        symbol=symbol,
        side="long",
        entry_price=price,
        stop_loss=sl,
        take_profit=tp,
        confidence=0.85,
        confluence_score=4,
        entry_type="market",
    )


def make_account_state() -> AccountState:
    return AccountState(
        equity_usd=10_000.0,
        daily_loss_usd=0.0,
        weekly_loss_usd=0.0,
        consecutive_losses=0,
        last_loss_ts=None,
        open_positions=0,
        trades_today=0,
        llm_cost_today_usd=0.0,
    )


def make_market_state(symbol: str, price: float) -> MarketState:
    return MarketState(
        symbol=symbol,
        live_price=price,
        snapshot_price=price,
        atr_14=None,
        spread_pct=0.001,
        funding_rate=0.0001,
        next_funding_ts=None,
        data_age_seconds=1.0,
        clock_drift_ms=0.0,
        is_macro_blackout=False,
        macro_blackout_name="",
        is_exchange_blackout=False,
        exchange_blackout_reason="",
    )


# ---------------------------------------------------------------------------
# PaperBroker: market long fill
# ---------------------------------------------------------------------------


class TestPaperBrokerRealSymbolsFill:
    @pytest.mark.parametrize("symbol", _REAL_SYMBOLS)
    def test_market_long_fills_immediately(self, symbol: str) -> None:
        broker = make_broker()
        price = _PRICES[symbol]
        broker._mark_prices[symbol] = price

        result = broker.place_order(
            symbol=symbol,
            side="buy",
            qty=100.0,
            order_type="market",
            client_order_id=f"real-fill-{symbol[:3]}",
        )

        assert result["status"] == "filled"
        assert result["filled_qty"] == 100.0
        assert result["avg_price"] > 0


# ---------------------------------------------------------------------------
# PaperBroker: SL trigger
# ---------------------------------------------------------------------------


class TestPaperBrokerRealSymbolsSl:
    @pytest.mark.parametrize("symbol", _REAL_SYMBOLS)
    def test_sl_triggers_on_long(self, symbol: str) -> None:
        broker = make_broker()
        price = _PRICES[symbol]
        sl_price = round(price * 0.90, 6)
        broker._mark_prices[symbol] = price

        broker.place_order(
            symbol=symbol,
            side="buy",
            qty=100.0,
            order_type="market",
            stop_loss=sl_price,
            client_order_id=f"real-sl-{symbol[:3]}",
        )

        # Price above SL — no trigger
        fills = broker.update_mark_price(symbol, round(price * 0.95, 6))
        assert len(fills) == 0

        # Price at or below SL — triggers
        fills = broker.update_mark_price(symbol, sl_price)
        assert len(fills) == 1
        assert fills[0]["reason"] == "sl"
        assert broker.get_positions() == []


# ---------------------------------------------------------------------------
# PaperBroker: TP trigger
# ---------------------------------------------------------------------------


class TestPaperBrokerRealSymbolsTp:
    @pytest.mark.parametrize("symbol", _REAL_SYMBOLS)
    def test_tp_triggers_on_long(self, symbol: str) -> None:
        broker = make_broker()
        price = _PRICES[symbol]
        tp_price = round(price * 1.20, 6)
        broker._mark_prices[symbol] = price

        broker.place_order(
            symbol=symbol,
            side="buy",
            qty=100.0,
            order_type="market",
            take_profit=tp_price,
            client_order_id=f"real-tp-{symbol[:3]}",
        )

        # Price at TP — triggers
        fills = broker.update_mark_price(symbol, tp_price)
        assert len(fills) == 1
        assert fills[0]["reason"] == "tp"
        assert broker.get_positions() == []


# ---------------------------------------------------------------------------
# RiskGate integration: real symbols must not crash
# ---------------------------------------------------------------------------


class TestRiskGateRealSymbols:
    _KNOWN_VERDICTS = {
        True,   # approved
        False,  # rejected — any known rule
    }

    @pytest.mark.parametrize("symbol", _REAL_SYMBOLS)
    def test_risk_gate_evaluate_does_not_crash(self, symbol: str) -> None:
        """RiskGate.evaluate() must return a RiskVerdict (not raise) for real symbols."""
        settings = make_settings(allowed_symbols=_REAL_SYMBOLS)
        price = _PRICES[symbol]

        gate = RiskGate(settings=settings)
        decision = make_decision(symbol, price)
        lesson_check = LessonCheckInput(blocked=False, confidence_penalty=0.0)
        account = make_account_state()
        market = make_market_state(symbol, price)

        verdict = gate.evaluate(
            decision=decision,
            lesson_check=lesson_check,
            account=account,
            market=market,
            lot_size=1.0,
            min_qty=1.0,
            maker_fee_rate=0.0002,
            taker_fee_rate=0.00055,
        )

        # Must return a valid verdict — either approved or a named rejection
        assert verdict.approved in self._KNOWN_VERDICTS
        if not verdict.approved:
            assert verdict.reject_reason != ""

    @pytest.mark.parametrize("symbol", _REAL_SYMBOLS)
    def test_risk_gate_rejects_disallowed_symbol(self, symbol: str) -> None:
        """RiskGate rejects a symbol that is not in allowed_symbols."""
        # Intentionally exclude the symbol under test
        other_symbols = [s for s in _REAL_SYMBOLS if s != symbol]
        settings = make_settings(allowed_symbols=other_symbols if other_symbols else ["BTC/USDT:USDT"])
        price = _PRICES[symbol]

        gate = RiskGate(settings=settings)
        decision = make_decision(symbol, price)
        lesson_check = LessonCheckInput(blocked=False, confidence_penalty=0.0)
        account = make_account_state()
        market = make_market_state(symbol, price)

        verdict = gate.evaluate(
            decision=decision,
            lesson_check=lesson_check,
            account=account,
            market=market,
        )

        assert verdict.approved is False
        assert verdict.reject_reason == "SYMBOL_NOT_ALLOWED"
