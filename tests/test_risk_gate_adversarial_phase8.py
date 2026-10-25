"""Adversarial property-based tests for fix/pre-phase8 features.

Covers seven bypass hypotheses:
  1. PRICE_SHOCK: block_until in past with active=True must not block (PASS path).
  2. PRICE_SHOCK: active=True with block_until=None must always reject.
  3. EXCHANGE_SL_NOT_CAPABLE: incapable + require=False must never fire.
  4. reconcile() kill-switch: double-mismatch is idempotent.
  5. reconcile() kill-switch: DB failure path swallows exception, does not propagate.
  6. compute_shock_sl_amendment: pct=0 → no SL change; pct=100 → never crosses entry.
  7. Case/whitespace bypass: lowercase action and symbol with trailing space.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trader.config.settings import AppSettings, ModelNames, RiskSettings, Settings
from trader.dsor.base import Base
from trader.dsor.system_state import SystemStateHelper
from trader.execution.order_manager import OrderManager
from trader.execution.paper_broker import PaperBroker
from trader.risk.gate import RiskGate
from trader.risk.models import (
    AccountState,
    DecisionInput,
    LessonCheckInput,
    MarketState,
)
from trader.risk.price_shock import compute_shock_sl_amendment


# ---------------------------------------------------------------------------
# Shared builders
# ---------------------------------------------------------------------------

def _make_settings(require_exchange_sl: bool = True) -> Settings:
    risk = RiskSettings(
        max_margin_per_trade_usd=2.0,
        max_loss_per_trade_usd=1.0,
        slippage_buffer_pct=0.05,
        max_leverage=3,
        margin_mode="isolated",
        position_mode="one_way",
        require_stop_loss=True,
        require_exchange_side_sl_tp=require_exchange_sl,
        liq_buffer_multiple=2.0,
        min_stop_atr_multiple=0.5,
        max_stop_atr_multiple=3.0,
        min_risk_reward=1.5,
        max_entry_drift_pct=0.15,
        avoid_funding_window_minutes=10,
        max_open_positions=1,
        max_trades_per_day=6,
        daily_loss_limit_usd=3.0,
        weekly_loss_limit_usd=6.0,
        equity_floor_usd=5.0,
        cooldown_after_loss_minutes=60,
        max_consecutive_losses=3,
        news_blackout_minutes_before=30,
        news_blackout_minutes_after=30,
        max_data_age_seconds=60,
        max_clock_drift_ms=1000,
        max_spread_pct=0.1,
        min_confidence=0.7,
        min_confluence_score=3,
        allowed_symbols=["XRP/USDT:USDT"],
        total_llm_budget_usd_per_day=5.0,
    )
    app = AppSettings(models=ModelNames(screening="m", decision="m", review="m"))
    return Settings(trading_mode="paper", app=app, risk=risk)


def _make_gate(**kw) -> RiskGate:
    return RiskGate(
        settings=_make_settings(**kw),
        live_approval_path=Path("/nonexistent/live_approval.yaml"),
        kill_switch_on=False,
    )


def _make_decision(**overrides) -> DecisionInput:
    base = dict(
        action="LONG",
        symbol="XRP/USDT:USDT",
        side="long",
        entry_price=0.5,
        stop_loss=0.45,
        take_profit=0.65,
        confidence=0.85,
        confluence_score=4,
    )
    base.update(overrides)
    return DecisionInput(**base)


def _make_account() -> AccountState:
    return AccountState(
        equity_usd=100.0,
        daily_loss_usd=0.0,
        weekly_loss_usd=0.0,
        consecutive_losses=0,
        last_loss_ts=None,
        open_positions=0,
        trades_today=0,
        llm_cost_today_usd=0.0,
    )


def _make_market(**overrides) -> MarketState:
    base = dict(
        symbol="XRP/USDT:USDT",
        live_price=0.5,
        snapshot_price=0.5,
        atr_14=None,
        spread_pct=0.001,
        funding_rate=0.0,
        next_funding_ts=None,
        data_age_seconds=5.0,
        clock_drift_ms=0.0,
        is_macro_blackout=False,
        macro_blackout_name="",
        is_exchange_blackout=False,
        exchange_blackout_reason="",
        price_shock_active=False,
        price_shock_block_until=None,
    )
    base.update(overrides)
    return MarketState(**base)


def _make_lesson() -> LessonCheckInput:
    return LessonCheckInput(blocked=False, confidence_penalty=0.0)


# ---------------------------------------------------------------------------
# 1. PRICE_SHOCK: expired block_until with active=True must NOT block
# ---------------------------------------------------------------------------

@given(
    seconds_ago=st.floats(min_value=1.0, max_value=86400.0),
)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_price_shock_expired_window_never_blocks(seconds_ago: float) -> None:
    """PRICE_SHOCK with block_until in the past must never fire, regardless of active flag."""
    gate = _make_gate()
    block_until = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=seconds_ago)
    market = _make_market(price_shock_active=True, price_shock_block_until=block_until)
    verdict = gate.evaluate(_make_decision(), _make_lesson(), _make_account(), market)
    assert "PRICE_SHOCK" not in verdict.rules_failed, (
        f"PRICE_SHOCK fired for expired block_until ({seconds_ago}s ago)"
    )


# ---------------------------------------------------------------------------
# 2. PRICE_SHOCK: active=True + block_until=None must ALWAYS reject
# ---------------------------------------------------------------------------

def test_price_shock_active_no_until_always_rejects() -> None:
    """Invariant: active=True with no block_until timestamp is always rejected."""
    gate = _make_gate()
    market = _make_market(price_shock_active=True, price_shock_block_until=None)
    verdict = gate.evaluate(_make_decision(), _make_lesson(), _make_account(), market)
    assert verdict.approved is False
    assert "PRICE_SHOCK" in verdict.rules_failed


# Property version: active=True + None must always reject regardless of other market state
@given(
    data_age=st.floats(min_value=0.0, max_value=30.0),
    spread=st.floats(min_value=0.0001, max_value=0.05),
)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_price_shock_active_no_until_always_rejects_property(
    data_age: float, spread: float
) -> None:
    gate = _make_gate()
    market = _make_market(
        price_shock_active=True,
        price_shock_block_until=None,
        data_age_seconds=data_age,
        spread_pct=spread,
    )
    verdict = gate.evaluate(_make_decision(), _make_lesson(), _make_account(), market)
    assert verdict.approved is False
    assert "PRICE_SHOCK" in verdict.rules_failed


# ---------------------------------------------------------------------------
# 3. EXCHANGE_SL_NOT_CAPABLE: require=False never fires the rule
# ---------------------------------------------------------------------------

@given(
    entry=st.floats(min_value=0.1, max_value=100.0),
    stop_pct=st.floats(min_value=0.02, max_value=0.2),
)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_exchange_sl_not_capable_not_fired_when_not_required(
    entry: float, stop_pct: float
) -> None:
    """When require_exchange_side_sl_tp=False, EXCHANGE_SL_NOT_CAPABLE must never fire
    regardless of exchange_sl_capable value."""
    gate = RiskGate(
        settings=_make_settings(require_exchange_sl=False),
        live_approval_path=Path("/nonexistent/live_approval.yaml"),
        kill_switch_on=False,
        exchange_sl_capable=False,  # incapable adapter
    )
    stop = entry * (1 - stop_pct)
    if stop <= 0:
        return
    tp = entry + 2.0 * abs(entry - stop)
    decision = _make_decision(entry_price=entry, stop_loss=stop, take_profit=tp)
    market = _make_market(live_price=entry, snapshot_price=entry)
    verdict = gate.evaluate(decision, _make_lesson(), _make_account(), market)
    assert "EXCHANGE_SL_NOT_CAPABLE" not in verdict.rules_failed, (
        "EXCHANGE_SL_NOT_CAPABLE fired even though require_exchange_side_sl_tp=False"
    )


def test_exchange_sl_not_capable_fires_when_required() -> None:
    """Complement: incapable + required = must reject."""
    gate = RiskGate(
        settings=_make_settings(require_exchange_sl=True),
        live_approval_path=Path("/nonexistent/live_approval.yaml"),
        kill_switch_on=False,
        exchange_sl_capable=False,
    )
    verdict = gate.evaluate(_make_decision(), _make_lesson(), _make_account(), _make_market())
    assert verdict.approved is False
    assert "EXCHANGE_SL_NOT_CAPABLE" in verdict.rules_failed


# ---------------------------------------------------------------------------
# 4. reconcile() kill-switch: double-mismatch is idempotent
# ---------------------------------------------------------------------------

@pytest.fixture()
def mem_engine():
    eng = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def mem_session_factory(mem_engine):
    def _factory():
        return Session(mem_engine, autocommit=False, autoflush=True)
    return _factory


def _make_om_settings() -> Settings:
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
        allowed_symbols=["XRP/USDT:USDT"],
    )
    app = AppSettings(models=ModelNames(screening="x", decision="x", review="x"))
    return Settings(trading_mode="paper", app=app, risk=risk)


def _inject_broker_position(broker: PaperBroker, symbol: str) -> None:
    broker._mark_prices[symbol] = 1.0
    broker._positions[symbol] = {
        "symbol": symbol,
        "side": "buy",
        "qty": 5.0,
        "entry_price": 1.0,
        "margin": 1.0,
        "sl_price": None,
        "tp_price": None,
        "open_ts": None,
        "max_adverse_excursion": 0.0,
        "max_favorable_excursion": 0.0,
        "funding_paid": 0.0,
    }


def test_reconcile_double_mismatch_kill_switch_idempotent(
    mem_session_factory, mem_engine
) -> None:
    """Two consecutive reconcile() mismatches result in kill_switch=True exactly once
    and must not raise any exception. The second call must be a no-op for the flag."""
    broker = PaperBroker(initial_balance=10_000.0)
    _inject_broker_position(broker, "XRP/USDT:USDT")
    om = OrderManager(
        broker=broker, settings=_make_om_settings(), session_factory=mem_session_factory
    )

    result1 = om.reconcile("XRP/USDT:USDT")
    result2 = om.reconcile("XRP/USDT:USDT")

    assert result1.ok is False
    assert result2.ok is False

    session = mem_session_factory()
    helper = SystemStateHelper(session)
    # Must still be True (not toggled off or errored out)
    assert helper.is_kill_switch_on() is True


# ---------------------------------------------------------------------------
# 5. reconcile() kill-switch: DB failure swallows exception, does not propagate
# ---------------------------------------------------------------------------

def test_reconcile_kill_switch_db_failure_no_exception(
    mem_session_factory, mem_engine, caplog
) -> None:
    """If _set_kill_switch() encounters a DB error, no exception propagates to the caller."""
    broker = PaperBroker(initial_balance=10_000.0)
    _inject_broker_position(broker, "XRP/USDT:USDT")
    om = OrderManager(
        broker=broker, settings=_make_om_settings(), session_factory=mem_session_factory
    )

    import trader.execution.order_manager as om_mod

    critical_calls: list[str] = []

    original_critical = om_mod.logger.critical

    def _capture_critical(msg: str, *args: object, **kwargs: object) -> None:
        critical_calls.append(msg % args if args else msg)
        original_critical(msg, *args, **kwargs)

    with patch(
        "trader.dsor.system_state.SystemStateHelper.set_kill_switch",
        side_effect=RuntimeError("DB gone"),
    ), patch.object(om_mod.logger, "critical", side_effect=_capture_critical):
        result = om.reconcile("XRP/USDT:USDT")

    assert result.ok is False
    assert any("KILL_SWITCH_PERSIST_FAILED" in msg for msg in critical_calls), (
        f"Expected CRITICAL log for KILL_SWITCH_PERSIST_FAILED; got: {critical_calls}"
    )


# ---------------------------------------------------------------------------
# 6. compute_shock_sl_amendment edge cases
# ---------------------------------------------------------------------------

@given(
    entry=st.floats(min_value=1.0, max_value=1000.0),
    sl_below_entry_pct=st.floats(min_value=0.001, max_value=0.4),
)
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_shock_sl_pct_zero_no_change(entry: float, sl_below_entry_pct: float) -> None:
    """With open_position_risk_cut_pct=0, adverse shock must return SL == current_sl."""
    current_sl = entry * (1.0 - sl_below_entry_pct)
    new_sl = compute_shock_sl_amendment(
        side="long",
        entry_price=entry,
        current_sl=current_sl,
        current_price=current_sl + 0.001,
        open_position_risk_cut_pct=0.0,
        move_sl_to_breakeven_on_favorable_shock=False,
        shock_is_adverse=True,
    )
    assert new_sl is not None
    assert abs(new_sl - current_sl) < 1e-9, (
        f"pct=0 adverse shock changed SL: entry={entry} sl={current_sl} new_sl={new_sl}"
    )


@given(
    entry=st.floats(min_value=1.0, max_value=1000.0),
    sl_below_entry_pct=st.floats(min_value=0.001, max_value=0.499),
)
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_shock_sl_pct_100_never_crosses_entry_long(
    entry: float, sl_below_entry_pct: float
) -> None:
    """With pct=100 for long, SL must be clamped strictly below entry."""
    current_sl = entry * (1.0 - sl_below_entry_pct)
    new_sl = compute_shock_sl_amendment(
        side="long",
        entry_price=entry,
        current_sl=current_sl,
        current_price=current_sl + 0.001,
        open_position_risk_cut_pct=100.0,
        move_sl_to_breakeven_on_favorable_shock=False,
        shock_is_adverse=True,
    )
    assert new_sl is not None
    assert new_sl < entry, (
        f"pct=100 adverse shock crossed entry: entry={entry} new_sl={new_sl}"
    )


@given(
    entry=st.floats(min_value=1.0, max_value=1000.0),
    sl_above_entry_pct=st.floats(min_value=0.001, max_value=0.499),
)
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_shock_sl_pct_100_never_crosses_entry_short(
    entry: float, sl_above_entry_pct: float
) -> None:
    """With pct=100 for short, SL must be clamped strictly above entry."""
    current_sl = entry * (1.0 + sl_above_entry_pct)
    new_sl = compute_shock_sl_amendment(
        side="short",
        entry_price=entry,
        current_sl=current_sl,
        current_price=current_sl - 0.001,
        open_position_risk_cut_pct=100.0,
        move_sl_to_breakeven_on_favorable_shock=False,
        shock_is_adverse=True,
    )
    assert new_sl is not None
    assert new_sl > entry, (
        f"pct=100 adverse short shock crossed entry: entry={entry} new_sl={new_sl}"
    )


def test_shock_sl_favorable_no_flag_returns_none() -> None:
    """Favorable shock with move_sl_to_breakeven=False must return None (no change)."""
    result = compute_shock_sl_amendment(
        side="long",
        entry_price=1.0,
        current_sl=0.8,
        current_price=1.5,
        open_position_risk_cut_pct=50.0,
        move_sl_to_breakeven_on_favorable_shock=False,
        shock_is_adverse=False,
    )
    assert result is None


# ---------------------------------------------------------------------------
# 7. Case/whitespace bypass attempts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action_variant", [
    "long", "Long", "LONG", "lOnG",
])
def test_lowercase_action_does_not_bypass_action_filter(action_variant: str) -> None:
    """action.upper() normalisation means 'long'/'Long' are never treated as HOLD/NO_TRADE.
    They reach symbol_allowed and beyond (not short-circuited by action_filter)."""
    gate = _make_gate()
    # Use an action that is a valid trade direction in lower/mixed case
    decision = _make_decision(action=action_variant)
    verdict = gate.evaluate(decision, _make_lesson(), _make_account(), _make_market())
    # Must not be rejected as NO_TRADE
    assert "NO_TRADE" not in verdict.rules_failed


def test_no_trade_lowercase_rejected_correctly() -> None:
    """'hold' (lowercase) must be normalized to HOLD and produce a NO_TRADE verdict."""
    gate = _make_gate()
    decision = _make_decision(action="hold")
    verdict = gate.evaluate(decision, _make_lesson(), _make_account(), _make_market())
    assert verdict.approved is False
    assert "NO_TRADE" in verdict.rules_failed


def test_no_trade_mixed_case_rejected_correctly() -> None:
    """'No_Trade' (mixed case) must normalize to NO_TRADE and produce NO_TRADE verdict."""
    gate = _make_gate()
    decision = _make_decision(action="No_Trade")
    verdict = gate.evaluate(decision, _make_lesson(), _make_account(), _make_market())
    assert verdict.approved is False
    assert "NO_TRADE" in verdict.rules_failed


def test_symbol_trailing_space_blocked() -> None:
    """'XRP/USDT:USDT ' (trailing space) must not match allowed symbol 'XRP/USDT:USDT'.
    This verifies the gate does NOT silently strip whitespace from symbols."""
    gate = _make_gate()
    decision = _make_decision(symbol="XRP/USDT:USDT ")
    verdict = gate.evaluate(decision, _make_lesson(), _make_account(), _make_market())
    assert verdict.approved is False
    assert "SYMBOL_NOT_ALLOWED" in verdict.rules_failed, (
        "Trailing-space symbol bypassed SYMBOL_NOT_ALLOWED check"
    )


# ---------------------------------------------------------------------------
# Kill-switch bypass: kill_switch_on=True must always reject regardless of input
# ---------------------------------------------------------------------------

@given(
    entry=st.floats(min_value=0.1, max_value=1000.0),
    stop_pct=st.floats(min_value=0.01, max_value=0.3),
)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_kill_switch_always_rejects(entry: float, stop_pct: float) -> None:
    """Kill switch=True must reject every trade, no exceptions."""
    gate = RiskGate(
        settings=_make_settings(),
        live_approval_path=Path("/nonexistent/live_approval.yaml"),
        kill_switch_on=True,
    )
    stop = entry * (1 - stop_pct)
    if stop <= 0:
        return
    tp = entry + 2.0 * abs(entry - stop)
    decision = _make_decision(entry_price=entry, stop_loss=stop, take_profit=tp)
    market = _make_market(live_price=entry, snapshot_price=entry)
    verdict = gate.evaluate(decision, _make_lesson(), _make_account(), market)
    assert verdict.approved is False
    assert "KILL_SWITCH" in verdict.rules_failed


# ---------------------------------------------------------------------------
# LessonFilter block must never be bypassed
# ---------------------------------------------------------------------------

@given(
    blocked=st.just(True),
    penalty=st.floats(min_value=0.0, max_value=1.0),
)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_lesson_filter_block_always_rejects(blocked: bool, penalty: float) -> None:
    """A blocked LessonCheckInput must always produce a rejected verdict."""
    gate = _make_gate()
    lesson = LessonCheckInput(blocked=True, confidence_penalty=penalty)
    verdict = gate.evaluate(_make_decision(), lesson, _make_account(), _make_market())
    assert verdict.approved is False
    assert "LESSON_FILTER_BLOCK" in verdict.rules_failed


# ---------------------------------------------------------------------------
# Parse-failure safety: a rejected RiskVerdict must never be approved
# ---------------------------------------------------------------------------

def test_rejected_verdict_cannot_be_submitted_as_approved() -> None:
    """OrderManager.submit() must gate on verdict.approved, never on raw fields."""
    from trader.execution.order_manager import OrderManager
    from trader.risk.models import RiskVerdict

    broker = PaperBroker(initial_balance=10_000.0)
    broker._mark_prices["XRP/USDT:USDT"] = 0.5
    om = OrderManager(
        broker=broker,
        settings=_make_om_settings(),
        session_factory=lambda: MagicMock(),
    )

    # Craft a verdict that says approved=False but has sizing populated
    bad_verdict = RiskVerdict(
        approved=False,
        adjusted_size=100.0,  # non-None size should not matter
        rules_failed=["PARSE_FAILURE_SIMULATED"],
        loss_at_stop_usd=0.5,
        liq_price=0.3,
        entry_drift_pct=0.01,
        reject_reason="PARSE_FAILURE_SIMULATED",
    )
    result = om.submit(bad_verdict, _make_decision(), cycle_id="test-cycle")
    assert result.status == "failed", (
        "OrderManager submitted an order despite approved=False"
    )
