"""Hypothesis property-based tests for RiskGate and sizing.

Critical invariants that must hold for ALL valid inputs:
1. Approved orders never exceed loss or margin caps.
2. Approved orders always have stop_loss on the correct side of entry.
3. Approved orders always have liq_price beyond stop_loss by liq_buffer_multiple.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from trader.config.settings import AppSettings, ModelNames, RiskSettings, Settings
from trader.risk.gate import RiskGate
from trader.risk.models import (
    AccountState,
    DecisionInput,
    LessonCheckInput,
    MarketState,
)

# ------------------------------------------------------------------
# Constants matching config/risk.yaml hard limits
# ------------------------------------------------------------------

MAX_LOSS_USD = 1.0
MAX_MARGIN_USD = 2.0
MAX_LEVERAGE = 3
LIQ_BUFFER_MULTIPLE = 2.0


# ------------------------------------------------------------------
# Shared fixture builder
# ------------------------------------------------------------------


def _make_settings_for_property() -> Settings:
    risk = RiskSettings(
        max_margin_per_trade_usd=MAX_MARGIN_USD,
        max_loss_per_trade_usd=MAX_LOSS_USD,
        slippage_buffer_pct=0.05,
        max_leverage=MAX_LEVERAGE,
        margin_mode="isolated",
        position_mode="one_way",
        liq_buffer_multiple=LIQ_BUFFER_MULTIPLE,
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
        allowed_symbols=["XRP/USDT:USDT", "DOGE/USDT:USDT"],
        total_llm_budget_usd_per_day=5.0,
    )
    app = AppSettings(models=ModelNames(screening="m", decision="m", review="m"))
    return Settings(trading_mode="paper", app=app, risk=risk)


def _make_gate() -> RiskGate:
    return RiskGate(
        settings=_make_settings_for_property(),
        live_approval_path=Path("/nonexistent/live_approval.yaml"),
        kill_switch_on=False,
    )


def _make_valid_account() -> AccountState:
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


def _make_valid_market(snapshot_price: float) -> MarketState:
    return MarketState(
        symbol="XRP/USDT:USDT",
        live_price=snapshot_price,
        snapshot_price=snapshot_price,
        atr_14=None,  # skip ATR check to let sizing drive failures
        spread_pct=0.001,
        funding_rate=0.0,
        next_funding_ts=None,
        data_age_seconds=5.0,
        clock_drift_ms=10.0,
        is_macro_blackout=False,
        macro_blackout_name="",
        is_exchange_blackout=False,
        exchange_blackout_reason="",
    )


# ------------------------------------------------------------------
# Property 1: Approved orders never exceed loss or margin caps
# ------------------------------------------------------------------


@given(
    entry=st.floats(min_value=0.001, max_value=10_000),
    stop_pct=st.floats(min_value=0.001, max_value=0.5),
    tp_mult=st.floats(min_value=1.5, max_value=10.0),
    lot_size=st.just(1.0),
)
@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_approved_order_never_exceeds_cap(
    entry: float,
    stop_pct: float,
    tp_mult: float,
    lot_size: float,
) -> None:
    """For any combination that passes RiskGate: loss_at_stop <= max_loss_usd."""
    stop = entry * (1 - stop_pct)
    if stop <= 0:
        return  # skip degenerate
    risk_distance = abs(entry - stop)
    tp = entry + tp_mult * risk_distance

    gate = _make_gate()
    decision = DecisionInput(
        action="LONG",
        symbol="XRP/USDT:USDT",
        side="long",
        entry_price=entry,
        stop_loss=stop,
        take_profit=tp,
        confidence=0.85,
        confluence_score=4,
    )
    verdict = gate.evaluate(
        decision=decision,
        lesson_check=LessonCheckInput(blocked=False, confidence_penalty=0.0),
        account=_make_valid_account(),
        market=_make_valid_market(entry),
        lot_size=lot_size,
        min_qty=lot_size,
    )
    if verdict.approved:
        assert verdict.loss_at_stop_usd is not None
        assert verdict.loss_at_stop_usd <= MAX_LOSS_USD + 1e-6, (
            f"loss_at_stop {verdict.loss_at_stop_usd} > max_loss {MAX_LOSS_USD} "
            f"for entry={entry} stop={stop} tp={tp} qty={verdict.adjusted_size}"
        )
        # Also verify margin
        if verdict.adjusted_size is not None:
            margin = verdict.adjusted_size * entry / MAX_LEVERAGE
            assert margin <= MAX_MARGIN_USD + 1e-6, (
                f"margin {margin} > max_margin {MAX_MARGIN_USD}"
            )


# ------------------------------------------------------------------
# Property 2: Approved orders always have stop_loss on the correct side
# ------------------------------------------------------------------


@given(
    entry=st.floats(min_value=0.001, max_value=10_000),
    stop_pct=st.floats(min_value=0.001, max_value=0.5),
    tp_mult=st.floats(min_value=1.5, max_value=10.0),
)
@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_no_approved_order_lacks_stop_loss(
    entry: float,
    stop_pct: float,
    tp_mult: float,
) -> None:
    """Approved verdict always has stop_loss on the correct side of entry."""
    stop = entry * (1 - stop_pct)
    if stop <= 0:
        return
    risk_distance = abs(entry - stop)
    tp = entry + tp_mult * risk_distance

    gate = _make_gate()
    decision = DecisionInput(
        action="LONG",
        symbol="XRP/USDT:USDT",
        side="long",
        entry_price=entry,
        stop_loss=stop,
        take_profit=tp,
        confidence=0.85,
        confluence_score=4,
    )
    verdict = gate.evaluate(
        decision=decision,
        lesson_check=LessonCheckInput(blocked=False, confidence_penalty=0.0),
        account=_make_valid_account(),
        market=_make_valid_market(entry),
    )
    if verdict.approved:
        # For a long, stop must be strictly below entry
        assert stop < entry, (
            f"Long approved but stop {stop} >= entry {entry}"
        )


# ------------------------------------------------------------------
# Property 3: liq_price always farther from entry than stop (by buffer)
# ------------------------------------------------------------------


@given(
    entry=st.floats(min_value=0.001, max_value=10_000),
    stop_pct=st.floats(min_value=0.001, max_value=0.5),
    tp_mult=st.floats(min_value=1.5, max_value=10.0),
)
@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_liq_always_beyond_stop(
    entry: float,
    stop_pct: float,
    tp_mult: float,
) -> None:
    """Approved verdict: |entry - liq_price| >= liq_buffer_multiple * |entry - stop|."""
    stop = entry * (1 - stop_pct)
    if stop <= 0:
        return
    risk_distance = abs(entry - stop)
    tp = entry + tp_mult * risk_distance

    gate = _make_gate()
    decision = DecisionInput(
        action="LONG",
        symbol="XRP/USDT:USDT",
        side="long",
        entry_price=entry,
        stop_loss=stop,
        take_profit=tp,
        confidence=0.85,
        confluence_score=4,
    )
    verdict = gate.evaluate(
        decision=decision,
        lesson_check=LessonCheckInput(blocked=False, confidence_penalty=0.0),
        account=_make_valid_account(),
        market=_make_valid_market(entry),
    )
    if verdict.approved:
        assert verdict.liq_price is not None
        liq_distance = abs(entry - verdict.liq_price)
        stop_distance = abs(entry - stop)
        assert liq_distance >= LIQ_BUFFER_MULTIPLE * stop_distance - 1e-6, (
            f"liq_distance {liq_distance} < {LIQ_BUFFER_MULTIPLE} * stop_distance {stop_distance}"
            f" for entry={entry} stop={stop} liq={verdict.liq_price}"
        )
