"""Tests for src/trader/risk/gate.py — one test per rejection rule.

Each test:
 - Builds a valid base scenario that passes all checks.
 - Tweaks exactly one field to trigger the rule under test.
 - Asserts verdict.approved is False and the rule name is in verdict.rules_failed.

Plus a full happy-path test.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from trader.config.settings import AppSettings, ModelNames, RiskSettings, Settings
from trader.risk.gate import RiskGate
from trader.risk.models import (
    AccountState,
    DecisionInput,
    LessonCheckInput,
    MarketState,
)

# ------------------------------------------------------------------
# Helpers: build valid base objects
# ------------------------------------------------------------------


def _make_settings(live_approval_exists: bool = False) -> Settings:
    """Build a minimal Settings object with sensible risk defaults."""
    risk = RiskSettings(
        max_margin_per_trade_usd=2.0,
        max_loss_per_trade_usd=1.0,
        slippage_buffer_pct=0.05,
        max_leverage=3,
        margin_mode="isolated",
        position_mode="one_way",
        require_stop_loss=True,
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
        allowed_symbols=["XRP/USDT:USDT", "DOGE/USDT:USDT"],
        total_llm_budget_usd_per_day=5.0,
    )
    app = AppSettings(
        models=ModelNames(screening="m", decision="m", review="m"),
    )
    return Settings(trading_mode="paper", app=app, risk=risk)


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
        entry_type="limit",
    )
    base.update(overrides)
    return DecisionInput(**base)


def _make_lesson_check(**overrides) -> LessonCheckInput:
    base = dict(blocked=False, confidence_penalty=0.0)
    base.update(overrides)
    return LessonCheckInput(**base)


def _make_account(**overrides) -> AccountState:
    base = dict(
        equity_usd=100.0,
        daily_loss_usd=0.0,
        weekly_loss_usd=0.0,
        consecutive_losses=0,
        last_loss_ts=None,
        open_positions=0,
        trades_today=0,
        llm_cost_today_usd=0.0,
    )
    base.update(overrides)
    return AccountState(**base)


def _make_market(**overrides) -> MarketState:
    base = dict(
        symbol="XRP/USDT:USDT",
        live_price=0.5,
        snapshot_price=0.5,
        atr_14=0.05,  # stop_distance=0.05 = 1.0 × ATR → within [0.5, 3.0]
        spread_pct=0.001,
        funding_rate=0.0001,
        next_funding_ts=None,
        data_age_seconds=5.0,
        clock_drift_ms=10.0,
        is_macro_blackout=False,
        macro_blackout_name="",
        is_exchange_blackout=False,
        exchange_blackout_reason="",
    )
    base.update(overrides)
    return MarketState(**base)


def _make_gate(kill_switch_on: bool = False, live_approval_path: Path | None = None) -> RiskGate:
    settings = _make_settings()
    return RiskGate(
        settings=settings,
        live_approval_path=live_approval_path or Path("/nonexistent/live_approval.yaml"),
        kill_switch_on=kill_switch_on,
    )


def _default_eval(gate: RiskGate, **decision_overrides):
    """Helper: run evaluate() with base valid scenario."""
    return gate.evaluate(
        decision=_make_decision(**decision_overrides),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(),
    )


# ------------------------------------------------------------------
# Happy path
# ------------------------------------------------------------------


def test_happy_path_approved():
    """All checks pass → verdict.approved is True."""
    gate = _make_gate()
    verdict = _default_eval(gate)
    assert verdict.approved is True
    assert verdict.rules_failed == []
    assert verdict.adjusted_size is not None
    assert verdict.adjusted_size > 0
    assert verdict.loss_at_stop_usd is not None
    assert verdict.loss_at_stop_usd <= 1.0 + 1e-9


# ------------------------------------------------------------------
# Rule 1: kill_switch
# ------------------------------------------------------------------


def test_kill_switch_rejects():
    gate = _make_gate(kill_switch_on=True)
    verdict = _default_eval(gate)
    assert verdict.approved is False
    assert "KILL_SWITCH" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 2: mode_check (LIVE without live_approval.yaml)
# ------------------------------------------------------------------


def test_live_mode_without_approval_rejects(tmp_path):
    settings = _make_settings()
    settings = Settings(trading_mode="live", app=settings.app, risk=settings.risk)
    gate = RiskGate(
        settings=settings,
        live_approval_path=tmp_path / "live_approval.yaml",  # does not exist
        kill_switch_on=False,
    )
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "LIVE_NOT_APPROVED" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 3: action_filter (HOLD / NO_TRADE)
# ------------------------------------------------------------------


def test_hold_action_no_trade():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(action="HOLD", side="hold"),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "NO_TRADE" in verdict.rules_failed


def test_no_trade_action():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(action="NO_TRADE", side="no_trade"),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "NO_TRADE" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 4: symbol_allowed
# ------------------------------------------------------------------


def test_symbol_not_allowed_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(symbol="BTC/USDT:USDT"),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(symbol="BTC/USDT:USDT"),
    )
    assert verdict.approved is False
    assert "SYMBOL_NOT_ALLOWED" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 5: lesson_filter_blocked
# ------------------------------------------------------------------


def test_lesson_filter_blocked_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(blocked=True),
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "LESSON_FILTER_BLOCK" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 6: clock_drift
# ------------------------------------------------------------------


def test_clock_drift_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(clock_drift_ms=1500),  # > 1000
    )
    assert verdict.approved is False
    assert "CLOCK_DRIFT" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 7: data_freshness
# ------------------------------------------------------------------


def test_stale_data_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(data_age_seconds=120),  # > 60
    )
    assert verdict.approved is False
    assert "STALE_DATA" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 8: spread_check
# ------------------------------------------------------------------


def test_spread_too_wide_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(spread_pct=0.2),  # > 0.1
    )
    assert verdict.approved is False
    assert "SPREAD_TOO_WIDE" in verdict.rules_failed


def test_spread_none_skipped():
    """spread_pct=None → spread check is skipped, gate proceeds."""
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(spread_pct=None),
    )
    assert verdict.approved is True


# ------------------------------------------------------------------
# Rule 9: macro_blackout
# ------------------------------------------------------------------


def test_macro_blackout_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(is_macro_blackout=True, macro_blackout_name="FOMC"),
    )
    assert verdict.approved is False
    assert "MACRO_BLACKOUT" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 10: exchange_blackout
# ------------------------------------------------------------------


def test_exchange_blackout_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(is_exchange_blackout=True, exchange_blackout_reason="maintenance"),
    )
    assert verdict.approved is False
    assert "EXCHANGE_BLACKOUT" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 11: equity_floor
# ------------------------------------------------------------------


def test_equity_floor_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(equity_usd=3.0),  # < 5.0
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "EQUITY_FLOOR" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 12: daily_loss_limit
# ------------------------------------------------------------------


def test_daily_loss_limit_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(daily_loss_usd=3.0),  # >= 3.0
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "DAILY_LOSS_LIMIT" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 13: weekly_loss_limit
# ------------------------------------------------------------------


def test_weekly_loss_limit_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(weekly_loss_usd=6.0),  # >= 6.0
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "WEEKLY_LOSS_LIMIT" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 14: consecutive_losses
# ------------------------------------------------------------------


def test_consecutive_losses_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(consecutive_losses=3),  # >= 3
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "CONSECUTIVE_LOSSES" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 15: cooldown
# ------------------------------------------------------------------


def test_cooldown_rejects():
    gate = _make_gate()
    # Last loss was 30 minutes ago — within 60-minute cooldown
    recent_loss = datetime.datetime.utcnow() - datetime.timedelta(minutes=30)
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(last_loss_ts=recent_loss),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "COOLDOWN" in verdict.rules_failed


def test_cooldown_expired_passes():
    gate = _make_gate()
    # Last loss was 90 minutes ago — cooldown expired
    old_loss = datetime.datetime.utcnow() - datetime.timedelta(minutes=90)
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(last_loss_ts=old_loss),
        market=_make_market(),
    )
    assert verdict.approved is True


# ------------------------------------------------------------------
# Rule 16: max_open_positions
# ------------------------------------------------------------------


def test_max_open_positions_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(open_positions=1),  # >= 1
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "MAX_OPEN_POSITIONS" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 17: max_trades_today
# ------------------------------------------------------------------


def test_max_trades_today_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(trades_today=6),  # >= 6
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "MAX_TRADES_TODAY" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 18: llm_budget
# ------------------------------------------------------------------


def test_llm_budget_exhausted_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(llm_cost_today_usd=5.0),  # >= 5.0
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "LLM_BUDGET_EXHAUSTED" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 19: confidence
# ------------------------------------------------------------------


def test_low_confidence_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(confidence=0.65),  # effective = 0.65 < 0.7
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "LOW_CONFIDENCE" in verdict.rules_failed


def test_confidence_penalty_causes_rejection():
    gate = _make_gate()
    # confidence=0.80 - penalty=0.15 = 0.65 < 0.70
    verdict = gate.evaluate(
        decision=_make_decision(confidence=0.80),
        lesson_check=_make_lesson_check(confidence_penalty=0.15),
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "LOW_CONFIDENCE" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 20: confluence
# ------------------------------------------------------------------


def test_low_confluence_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(confluence_score=2),  # < 3
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "LOW_CONFLUENCE" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 21: require_sl
# ------------------------------------------------------------------


def test_stop_loss_zero_rejects():
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(stop_loss=0),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "MISSING_STOP_LOSS" in verdict.rules_failed


def test_long_stop_above_entry_rejects():
    """Long with stop_loss >= entry_price → MISSING_STOP_LOSS."""
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(side="long", entry_price=0.5, stop_loss=0.55),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "MISSING_STOP_LOSS" in verdict.rules_failed


def test_short_stop_below_entry_rejects():
    """Short with stop_loss <= entry_price → MISSING_STOP_LOSS."""
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(
            action="SHORT", side="short", entry_price=0.5, stop_loss=0.45, take_profit=0.40
        ),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "MISSING_STOP_LOSS" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 22: stop_atr_range
# ------------------------------------------------------------------


def test_stop_distance_below_atr_min_rejects():
    """stop_distance < min_stop_atr_multiple * atr_14 → STOP_DISTANCE_ATR."""
    gate = _make_gate()
    # atr_14=0.05, min_multiple=0.5 → min_stop=0.025
    # Make stop_distance=0.001 (very tiny)
    verdict = gate.evaluate(
        decision=_make_decision(entry_price=0.5, stop_loss=0.499, take_profit=0.65),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(atr_14=0.05),
    )
    assert verdict.approved is False
    assert "STOP_DISTANCE_ATR" in verdict.rules_failed


def test_stop_distance_above_atr_max_rejects():
    """stop_distance > max_stop_atr_multiple * atr_14 → STOP_DISTANCE_ATR."""
    gate = _make_gate()
    # atr_14=0.05, max_multiple=3.0 → max_stop=0.15
    # Make stop_distance=0.20 (too large)
    verdict = gate.evaluate(
        decision=_make_decision(entry_price=0.5, stop_loss=0.30, take_profit=0.90),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(atr_14=0.05),
    )
    assert verdict.approved is False
    assert "STOP_DISTANCE_ATR" in verdict.rules_failed


def test_stop_atr_check_skipped_when_atr_none():
    """atr_14=None → ATR check skipped, gate can proceed."""
    gate = _make_gate()
    # Without ATR check, this would pass (stop distance within sizing limits)
    verdict = gate.evaluate(
        decision=_make_decision(entry_price=0.5, stop_loss=0.45, take_profit=0.65),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(atr_14=None),
    )
    # Should pass all remaining checks
    assert "STOP_DISTANCE_ATR" not in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 23: funding_window
# ------------------------------------------------------------------


def test_funding_window_against_long_rejects():
    """Within funding window AND negative funding (against longs) → FUNDING_WINDOW."""
    gate = _make_gate()
    # next_funding in 5 minutes (within 10-minute window), negative rate (against longs)
    next_ts = datetime.datetime.utcnow() + datetime.timedelta(minutes=5)
    next_ts_utc = next_ts.replace(tzinfo=datetime.UTC)
    verdict = gate.evaluate(
        decision=_make_decision(action="LONG", side="long"),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(
            next_funding_ts=next_ts_utc,
            funding_rate=-0.001,  # negative → against longs
        ),
    )
    assert verdict.approved is False
    assert "FUNDING_WINDOW" in verdict.rules_failed


def test_funding_window_against_short_rejects():
    """Within funding window AND positive funding (against shorts) → FUNDING_WINDOW."""
    gate = _make_gate()
    next_ts = datetime.datetime.utcnow() + datetime.timedelta(minutes=5)
    next_ts_utc = next_ts.replace(tzinfo=datetime.UTC)
    verdict = gate.evaluate(
        decision=_make_decision(action="SHORT", side="short", stop_loss=0.55, take_profit=0.40),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(
            next_funding_ts=next_ts_utc,
            funding_rate=0.001,  # positive → against shorts
        ),
    )
    assert verdict.approved is False
    assert "FUNDING_WINDOW" in verdict.rules_failed


def test_funding_window_not_against_long_passes():
    """Within window but funding is positive (favorable for longs) → NOT rejected."""
    gate = _make_gate()
    next_ts = datetime.datetime.utcnow() + datetime.timedelta(minutes=5)
    next_ts_utc = next_ts.replace(tzinfo=datetime.UTC)
    verdict = gate.evaluate(
        decision=_make_decision(action="LONG", side="long"),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(
            next_funding_ts=next_ts_utc,
            funding_rate=0.001,  # positive → longs RECEIVE, not rejected
        ),
    )
    # Should not fail on FUNDING_WINDOW
    assert "FUNDING_WINDOW" not in verdict.rules_failed


def test_funding_window_outside_window_passes():
    """Far from funding window → not rejected."""
    gate = _make_gate()
    next_ts = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    next_ts_utc = next_ts.replace(tzinfo=datetime.UTC)
    verdict = gate.evaluate(
        decision=_make_decision(action="LONG", side="long"),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(
            next_funding_ts=next_ts_utc,
            funding_rate=-0.001,
        ),
    )
    assert "FUNDING_WINDOW" not in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 24: sizing (SIZING_FAILED)
# ------------------------------------------------------------------


def test_sizing_failed_rejects():
    """Very tight entry/stop with high min_qty → SIZING_FAILED."""
    gate = _make_gate()
    # entry=0.5, stop=0.499 → stop_distance=0.001
    # With max_loss=1.0, max_qty_from_loss ≈ 1.0 / (0.001*1.05 + fees) ≈ 900+
    # But with min_qty=10000 and lot_size=10000, floor(raw_qty/10000)*10000 < 10000 → SizingError
    verdict = gate.evaluate(
        decision=_make_decision(
            entry_price=0.5,
            stop_loss=0.499,
            take_profit=0.60,
            symbol="XRP/USDT:USDT",
        ),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(
            atr_14=None,  # skip ATR check
            snapshot_price=0.5,
            live_price=0.5,
        ),
        min_qty=10_000.0,  # impossibly large min_qty → sizing will fail
        lot_size=10_000.0,
    )
    assert verdict.approved is False
    assert "SIZING_FAILED" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 25: liq_check (LIQ_TOO_CLOSE)
# ------------------------------------------------------------------


def test_liq_too_close_rejects():
    """Liquidation price too close to stop → LIQ_TOO_CLOSE."""
    # Use very high leverage to push liq close to entry → liq_distance < 2 * stop_distance
    # entry=0.5, stop=0.45 (stop_distance=0.05)
    # liq_buffer_multiple=2.0 → need liq_distance >= 0.10
    # With max_leverage=3, margin per lot ≈ entry/leverage
    # We can force liq too close by overriding liq_buffer_multiple via a custom settings
    from trader.config.settings import AppSettings, ModelNames, RiskSettings, Settings

    risk = RiskSettings(
        max_margin_per_trade_usd=2.0,
        max_loss_per_trade_usd=1.0,
        slippage_buffer_pct=0.05,
        max_leverage=3,
        margin_mode="isolated",
        position_mode="one_way",
        liq_buffer_multiple=10.0,  # extremely high → will cause liq check to fail
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
    settings = Settings(trading_mode="paper", app=app, risk=risk)
    custom_gate = RiskGate(
        settings=settings,
        live_approval_path=Path("/nonexistent/live_approval.yaml"),
        kill_switch_on=False,
    )
    verdict = custom_gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(atr_14=None),
    )
    assert verdict.approved is False
    assert "LIQ_TOO_CLOSE" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 26: rr_check (RR_TOO_LOW)
# ------------------------------------------------------------------


def test_rr_too_low_rejects():
    """Take-profit too close to entry → RR_TOO_LOW."""
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(
            entry_price=0.5,
            stop_loss=0.45,
            take_profit=0.51,  # tiny TP → RR << 1.5
        ),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(atr_14=None),
    )
    assert verdict.approved is False
    assert "RR_TOO_LOW" in verdict.rules_failed


# ------------------------------------------------------------------
# Rule 27: entry_drift
# ------------------------------------------------------------------


def test_entry_drift_rejects():
    """Live price drifted > max_entry_drift_pct from snapshot → ENTRY_DRIFT."""
    gate = _make_gate()
    # snapshot=0.5, live=0.6 → drift = 0.1/0.5 = 0.20 > 0.15
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(snapshot_price=0.5, live_price=0.6, atr_14=None),
    )
    assert verdict.approved is False
    assert "ENTRY_DRIFT" in verdict.rules_failed


def test_entry_drift_within_limit_passes():
    """Small drift within limit → not rejected for ENTRY_DRIFT."""
    gate = _make_gate()
    # snapshot=0.5, live=0.505 → drift = 0.005/0.5 = 0.01 < 0.15
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(snapshot_price=0.5, live_price=0.505, atr_14=None),
    )
    assert "ENTRY_DRIFT" not in verdict.rules_failed


def test_snapshot_price_zero_rejected_by_model():
    """snapshot_price=0 is now rejected by MarketState Pydantic validation (gt=0).

    Previously the gate silently skipped the drift check; now the model prevents
    a zero price from ever reaching the gate.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _make_market(snapshot_price=0.0, live_price=0.5, atr_14=None)


# ------------------------------------------------------------------
# Branch coverage: naive datetime in funding_ts
# ------------------------------------------------------------------


def test_funding_window_naive_datetime_handled():
    """next_funding_ts with no tzinfo (naive) is handled correctly without error."""
    gate = _make_gate()
    # Provide a naive datetime (no tzinfo) — gate should normalize it to UTC
    naive_next_ts = datetime.datetime.utcnow() + datetime.timedelta(minutes=5)
    # Negative funding → against longs
    verdict = gate.evaluate(
        decision=_make_decision(action="LONG", side="long"),
        lesson_check=_make_lesson_check(),
        account=_make_account(),
        market=_make_market(
            next_funding_ts=naive_next_ts,
            funding_rate=-0.001,
        ),
    )
    # Should have rejected on FUNDING_WINDOW (naive ts treated as UTC)
    assert verdict.approved is False
    assert "FUNDING_WINDOW" in verdict.rules_failed
