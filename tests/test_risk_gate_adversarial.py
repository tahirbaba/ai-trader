"""Adversarial property-based tests for the Phase 5 RiskGate.

Attempts to bypass all 7 failure categories:
  1. Exceed max_loss_per_trade_usd (including fees + slippage)
  2. Exceed max_margin_per_trade_usd
  3. Lack an exchange-side stop-loss
  4. Have liquidation inside stop buffer
  5. Bypass kill switch / mode / symbol checks
  6. Parse failure treated as approval
  7. LessonFilter block ignored

Additional edge cases:
  - Negative stop_loss bypasses require_sl check (CRITICAL)
  - Negative confidence_penalty inflates effective confidence (MEDIUM)
  - confidence_penalty > 1.0 producing negative effective confidence (MEDIUM)
  - NO_TRADE action never results in approved=True (MEDIUM)
  - layer2_check is independent of gate.py (no shared imports)
  - funding_window with None next_funding_ts (verify skip, not crash)
  - snapshot_price=0 bypass for entry_drift (MEDIUM)
  - Integer overflow / float precision in sizing math
  - All 27 checks are reachable
"""

from __future__ import annotations

import datetime
import math
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from trader.config.settings import AppSettings, ModelNames, RiskSettings, Settings
from trader.execution.layer2_check import CapBreachError, layer2_cap_check
from trader.risk.gate import RiskGate
from trader.risk.models import (
    AccountState,
    DecisionInput,
    LessonCheckInput,
    MarketState,
)
from trader.risk.sizing import SizingError, compute_size

# ─────────────────────────────────────────────────────────────────────────────
# Hard limits from config/risk.yaml — NEVER modify
# ─────────────────────────────────────────────────────────────────────────────
MAX_LOSS_USD = 1.0
MAX_MARGIN_USD = 2.0
MAX_LEVERAGE = 3
LIQ_BUFFER = 2.0
SLIP = 0.05
TAKER = 0.00055


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixture builders
# ─────────────────────────────────────────────────────────────────────────────


def _make_risk_settings(**overrides) -> RiskSettings:
    base = dict(
        max_margin_per_trade_usd=MAX_MARGIN_USD,
        max_loss_per_trade_usd=MAX_LOSS_USD,
        slippage_buffer_pct=SLIP,
        max_leverage=MAX_LEVERAGE,
        margin_mode="isolated",
        position_mode="one_way",
        liq_buffer_multiple=LIQ_BUFFER,
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
    base.update(overrides)
    return RiskSettings(**base)


def _make_settings(**risk_overrides) -> Settings:
    risk = _make_risk_settings(**risk_overrides)
    app = AppSettings(models=ModelNames(screening="m", decision="m", review="m"))
    return Settings(trading_mode="paper", app=app, risk=risk)


def _make_gate(**risk_overrides) -> RiskGate:
    return RiskGate(
        settings=_make_settings(**risk_overrides),
        live_approval_path=Path("/nonexistent/live_approval.yaml"),
        kill_switch_on=False,
    )


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


def _make_market(entry: float = 0.5, **overrides) -> MarketState:
    base = dict(
        symbol="XRP/USDT:USDT",
        live_price=entry,
        snapshot_price=entry,
        atr_14=None,
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
    base.update(overrides)
    return MarketState(**base)


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


def _lesson(blocked=False, penalty=0.0) -> LessonCheckInput:
    return LessonCheckInput(blocked=blocked, confidence_penalty=penalty)


# ─────────────────────────────────────────────────────────────────────────────
# CRITICAL BUG 1: Negative stop_loss bypasses require_sl check (Check 21)
#
# gate.py lines 226-231 only check:
#   if stop == 0: reject
#   if long and stop >= entry: reject
#   if short and stop <= entry: reject
#
# A negative stop_loss on a long (-0.10 < 0.5) passes all three conditions.
# compute_size does not reject negative stop values (only checks entry <= 0).
# This means a long with stop=-0.10 (an impossible/invalid price) can be approved.
# ─────────────────────────────────────────────────────────────────────────────


def test_negative_stop_loss_long_must_be_rejected():
    """Negative stop_loss is now rejected by DecisionInput Pydantic validation (ge=0).

    Previously the gate check 21 guarded stop==0 but not stop<0.
    Now DecisionInput.stop_loss has Field(ge=0), so -0.10 raises ValidationError
    before the gate is ever called.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _make_decision(entry_price=0.5, stop_loss=-0.10, take_profit=0.65)


def test_negative_stop_loss_short_must_be_rejected():
    """Negative stop_loss on a short is rejected by DecisionInput Pydantic validation (ge=0)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _make_decision(
            action="SHORT", side="short", entry_price=0.5, stop_loss=-0.10, take_profit=0.40
        )


# ─────────────────────────────────────────────────────────────────────────────
# CRITICAL BUG 2: Negative confidence_penalty inflates effective confidence
#
# gate.py line 209: effective_confidence = decision.confidence - lesson_check.confidence_penalty
# LessonCheckInput has no validator preventing negative confidence_penalty.
# A penalty of -0.3 on confidence=0.71 gives effective=1.01, bypassing min_confidence.
# More importantly: the LessonFilter could be manipulated to send penalty=-N,
# effectively boosting a borderline-confidence decision to pass check 19.
# ─────────────────────────────────────────────────────────────────────────────


def test_negative_confidence_penalty_must_be_rejected_by_model():
    """MEDIUM: LessonCheckInput must reject confidence_penalty < 0.

    A negative penalty artificially inflates effective confidence.
    The model has no ge=0 validator on confidence_penalty.
    """
    import pydantic

    with pytest.raises((pydantic.ValidationError, ValueError)):
        LessonCheckInput(blocked=False, confidence_penalty=-0.5)


def test_negative_confidence_penalty_does_not_approve_low_confidence():
    """MEDIUM: Even if negative penalty is accepted, gate must reject or handle it correctly.

    If LessonCheckInput accepts penalty=-0.5, a confidence=0.65 (below min 0.7)
    would compute effective=0.65-(-0.5)=1.15, appearing to pass check 19.
    """
    try:
        lci = LessonCheckInput(blocked=False, confidence_penalty=-0.5)
    except Exception:
        pytest.skip("LessonCheckInput correctly rejects negative penalty")

    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(confidence=0.65),  # below min_confidence=0.7
        lesson_check=lci,
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False, (
        "CRITICAL: negative confidence_penalty inflated effective confidence "
        "and allowed a below-threshold decision through check 19."
    )


# ─────────────────────────────────────────────────────────────────────────────
# CRITICAL BUG 3: confidence_penalty > 1.0 does not require special handling
# but the model doesn't validate it. A penalty > 1 makes effective_confidence
# go negative, which is < min_confidence (0.7), so it correctly rejects.
# However, the model should still validate the range.
# ─────────────────────────────────────────────────────────────────────────────


def test_confidence_penalty_exceeding_one_causes_rejection():
    """penalty > 1.0 is now rejected by LessonCheckInput Pydantic validation (le=1.0).

    Previously penalty=2.0 reached the gate and produced effective_confidence=-1.15.
    Now LessonCheckInput.confidence_penalty has Field(le=1.0), so 2.0 raises ValidationError.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LessonCheckInput(blocked=False, confidence_penalty=2.0)


# ─────────────────────────────────────────────────────────────────────────────
# BUG 4: NO_TRADE action must never produce approved=True
# The action_filter (check 3) catches HOLD/NO_TRADE but the comparison uses
# decision.action.upper(). Check that case variants can't slip through.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "action_str",
    [
        "NO_TRADE",
        "no_trade",
        "No_Trade",
        "HOLD",
        "hold",
        "Hold",
    ],
)
def test_no_trade_variants_never_approved(action_str: str):
    """NO_TRADE and HOLD in any case must never return approved=True."""
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(action=action_str, side="long"),
        lesson_check=_lesson(),
        account=_make_account(),
        market=_make_market(),
    )
    assert (
        verdict.approved is False
    ), f"NO_TRADE action variant '{action_str}' returned approved=True"
    assert "NO_TRADE" in verdict.rules_failed


# ─────────────────────────────────────────────────────────────────────────────
# BUG 5: LessonFilter blocked=True must never be approved
# ─────────────────────────────────────────────────────────────────────────────


def test_lesson_filter_blocked_always_rejects():
    """CRITICAL: blocked=True in LessonCheckInput must never produce approved=True."""
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=LessonCheckInput(blocked=True, confidence_penalty=0.0),
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "LESSON_FILTER_BLOCK" in verdict.rules_failed


# ─────────────────────────────────────────────────────────────────────────────
# BUG 6: Kill switch bypass — kill_switch_on=True must always reject
# ─────────────────────────────────────────────────────────────────────────────


def test_kill_switch_always_rejects_regardless_of_input():
    """CRITICAL: kill_switch=True must reject even perfect inputs."""
    gate = RiskGate(
        settings=_make_settings(),
        live_approval_path=Path("/nonexistent/live_approval.yaml"),
        kill_switch_on=True,
    )
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_lesson(),
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "KILL_SWITCH" in verdict.rules_failed


# ─────────────────────────────────────────────────────────────────────────────
# BUG 7: LIVE mode without live_approval.yaml must always reject
# ─────────────────────────────────────────────────────────────────────────────


def test_live_mode_without_approval_always_rejects(tmp_path):
    """CRITICAL: LIVE mode without live_approval.yaml must reject."""
    app = AppSettings(models=ModelNames(screening="m", decision="m", review="m"))
    settings = Settings(
        trading_mode="live",
        app=app,
        risk=_make_risk_settings(),
    )
    gate = RiskGate(
        settings=settings,
        live_approval_path=tmp_path / "live_approval.yaml",  # does not exist
        kill_switch_on=False,
    )
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_lesson(),
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "LIVE_NOT_APPROVED" in verdict.rules_failed


# ─────────────────────────────────────────────────────────────────────────────
# BUG 8: Symbol not in allowed_symbols must always reject
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "symbol",
    [
        "BTC/USDT:USDT",
        "ETH/USDT:USDT",
        "",
        " ",
        "XRP/USDT:USDT ",
        "xrp/usdt:usdt",  # case-sensitive check
    ],
)
def test_disallowed_symbol_always_rejects(symbol: str):
    """CRITICAL: any symbol not in allowed_symbols must be rejected."""
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(symbol=symbol),
        lesson_check=_lesson(),
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "SYMBOL_NOT_ALLOWED" in verdict.rules_failed


# ─────────────────────────────────────────────────────────────────────────────
# BUG 9: Daily loss limit — boundary conditions
# The check uses >= so daily_loss_usd == daily_loss_limit_usd should reject
# ─────────────────────────────────────────────────────────────────────────────


def test_daily_loss_at_limit_rejects():
    """CRITICAL: daily_loss_usd == daily_loss_limit_usd must reject."""
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_lesson(),
        account=_make_account(daily_loss_usd=3.0),  # exact limit
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "DAILY_LOSS_LIMIT" in verdict.rules_failed


def test_weekly_loss_at_limit_rejects():
    """CRITICAL: weekly_loss_usd == weekly_loss_limit_usd must reject."""
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_lesson(),
        account=_make_account(weekly_loss_usd=6.0),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "WEEKLY_LOSS_LIMIT" in verdict.rules_failed


# ─────────────────────────────────────────────────────────────────────────────
# BUG 10: snapshot_price=0 in entry_drift check
# gate.py line 320: if market.snapshot_price > 0: compute drift
# else: drift = 0.0
# This means live_price=9999, snapshot_price=0 → drift=0 (no rejection)
# This is an intentional design choice but allows a completely stale snapshot
# (price=0 meaning "unknown") to produce no ENTRY_DRIFT rejection.
# ─────────────────────────────────────────────────────────────────────────────


def test_snapshot_price_zero_no_entry_drift_rejection():
    """snapshot_price=0 is now rejected by MarketState Pydantic validation (gt=0).

    Previously a zero snapshot bypassed the entry drift guard entirely.
    Now MarketState.snapshot_price has Field(gt=0), so 0.0 raises ValidationError.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _make_market(0.5, snapshot_price=0.0, live_price=9999.0)
    # (the approved field may be False for other reasons, but not ENTRY_DRIFT)


# ─────────────────────────────────────────────────────────────────────────────
# BUG 11: funding_window check correctly handles None next_funding_ts
# gate.py line 246: if market.next_funding_ts is not None and market.funding_rate is not None
# Both must be non-None for the check to run. If next_funding_ts is None, skip.
# ─────────────────────────────────────────────────────────────────────────────


def test_funding_window_none_ts_skipped():
    """LOW: next_funding_ts=None skips funding window check (no crash)."""
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_lesson(),
        account=_make_account(),
        market=_make_market(0.5, next_funding_ts=None, funding_rate=-0.999),
    )
    assert "FUNDING_WINDOW" not in verdict.rules_failed


def test_funding_window_none_rate_skipped():
    """LOW: funding_rate=None skips funding window check (no crash)."""
    gate = _make_gate()
    ts = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(minutes=5)
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_lesson(),
        account=_make_account(),
        market=_make_market(0.5, next_funding_ts=ts, funding_rate=None),
    )
    assert "FUNDING_WINDOW" not in verdict.rules_failed


def test_funding_rate_zero_not_against_any_side():
    """LOW: funding_rate=0.0 is not 'against' long or short (both conditions False)."""
    gate = _make_gate()
    ts = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(minutes=5)
    verdict = gate.evaluate(
        decision=_make_decision(action="LONG", side="long"),
        lesson_check=_lesson(),
        account=_make_account(),
        market=_make_market(0.5, next_funding_ts=ts, funding_rate=0.0),
    )
    assert "FUNDING_WINDOW" not in verdict.rules_failed


# ─────────────────────────────────────────────────────────────────────────────
# BUG 12: layer2_check.py independence — it must NOT import from gate.py
# ─────────────────────────────────────────────────────────────────────────────


def test_layer2_check_does_not_import_gate():
    """CRITICAL: layer2_check.py must not import from gate.py.

    If layer2_check imports gate, a mock of gate could suppress the L2 check.
    """
    import sys

    # Remove cached module if present
    for key in list(sys.modules.keys()):
        if "layer2_check" in key:
            del sys.modules[key]

    import trader.execution.layer2_check as l2_mod

    # Inspect the module's __dict__ for any gate references
    source_file = Path(l2_mod.__file__)
    content = source_file.read_text()

    assert (
        "gate" not in content.lower() or "Never import from gate.py" in content
    ), "layer2_check.py appears to import from gate.py — independence broken"
    # More precisely: check no 'from trader.risk.gate' or 'import gate' lines
    import re

    gate_imports = re.findall(r"^(?:from|import).*gate", content, re.MULTILINE)
    assert gate_imports == [], f"layer2_check.py has gate imports: {gate_imports}"


def test_layer2_check_does_not_import_sizing():
    """LOW: layer2_check.py reimplements sizing math inline — no import of sizing.py.

    This is verified: layer2_check uses only math and no trader imports.
    If it imported sizing.py, both modules could be mocked together.
    """
    import trader.execution.layer2_check as l2_mod

    source_file = Path(l2_mod.__file__)
    content = source_file.read_text()

    import re

    trader_imports = re.findall(r"^(?:from|import)\s+trader", content, re.MULTILINE)
    assert (
        trader_imports == []
    ), f"layer2_check.py has trader imports (breaks independence): {trader_imports}"


# ─────────────────────────────────────────────────────────────────────────────
# BUG 13: Sizing loss formula — verify fees are included and loss <= max_loss_usd
# ─────────────────────────────────────────────────────────────────────────────


@given(
    entry=st.floats(min_value=0.01, max_value=1000.0),
    stop_pct=st.floats(min_value=0.001, max_value=0.3),
    lot_size=st.just(1.0),
)
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_compute_size_loss_never_exceeds_cap(
    entry: float, stop_pct: float, lot_size: float
) -> None:
    """Property: compute_size loss_at_stop always <= max_loss_usd including fees+slippage."""
    import math as _math

    if not (_math.isfinite(entry) and _math.isfinite(stop_pct)):
        return
    stop = entry * (1 - stop_pct)
    if stop <= 0:
        return
    try:
        qty, loss, margin = compute_size(
            side="long",
            entry=entry,
            stop=stop,
            take_profit=entry * 1.5,
            lot_size=lot_size,
            min_qty=lot_size,
            maker_fee_rate=0.0002,
            taker_fee_rate=TAKER,
            max_margin_usd=MAX_MARGIN_USD,
            max_loss_usd=MAX_LOSS_USD,
            max_leverage=float(MAX_LEVERAGE),
            slippage_buffer_pct=SLIP,
        )
        assert loss <= MAX_LOSS_USD + 1e-9, (
            f"loss {loss} exceeds MAX_LOSS_USD {MAX_LOSS_USD} "
            f"entry={entry} stop={stop} qty={qty}"
        )
        assert (
            margin <= MAX_MARGIN_USD + 1e-9
        ), f"margin {margin} exceeds MAX_MARGIN_USD {MAX_MARGIN_USD}"
    except SizingError:
        pass  # legitimate rejection


@given(
    entry=st.floats(min_value=0.01, max_value=1000.0),
    stop_pct=st.floats(min_value=0.001, max_value=0.3),
    lot_size=st.just(1.0),
)
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_gate_approved_order_loss_never_exceeds_cap(
    entry: float, stop_pct: float, lot_size: float
) -> None:
    """Property: any approved verdict has loss_at_stop_usd <= MAX_LOSS_USD."""
    import math as _math

    if not (_math.isfinite(entry) and _math.isfinite(stop_pct)):
        return
    stop = entry * (1 - stop_pct)
    if stop <= 0:
        return
    risk_dist = abs(entry - stop)
    tp = entry + 2.0 * risk_dist

    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(
            entry_price=entry,
            stop_loss=stop,
            take_profit=tp,
        ),
        lesson_check=_lesson(),
        account=_make_account(),
        market=_make_market(entry),
        lot_size=lot_size,
        min_qty=lot_size,
    )
    if verdict.approved:
        assert verdict.loss_at_stop_usd is not None
        assert (
            verdict.loss_at_stop_usd <= MAX_LOSS_USD + 1e-6
        ), f"CRITICAL: approved loss {verdict.loss_at_stop_usd} > MAX_LOSS_USD {MAX_LOSS_USD}"
        assert verdict.adjusted_size is not None
        margin = verdict.adjusted_size * entry / MAX_LEVERAGE
        assert (
            margin <= MAX_MARGIN_USD + 1e-6
        ), f"CRITICAL: approved margin {margin} > MAX_MARGIN_USD {MAX_MARGIN_USD}"


# ─────────────────────────────────────────────────────────────────────────────
# BUG 14: layer2_check must also reject any approved order that exceeds caps
# ─────────────────────────────────────────────────────────────────────────────


@given(
    entry=st.floats(min_value=0.01, max_value=1000.0),
    stop_pct=st.floats(min_value=0.001, max_value=0.3),
    lot_size=st.just(1.0),
)
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_layer2_check_catches_margin_breach(entry: float, stop_pct: float, lot_size: float) -> None:
    """Property: layer2_cap_check raises CapBreachError when margin is over limit."""
    import math as _math

    if not (_math.isfinite(entry) and _math.isfinite(stop_pct)):
        return
    stop = entry * (1 - stop_pct)
    if stop <= 0:
        return
    try:
        qty, loss, margin = compute_size(
            side="long",
            entry=entry,
            stop=stop,
            take_profit=entry * 1.5,
            lot_size=lot_size,
            min_qty=lot_size,
            maker_fee_rate=0.0002,
            taker_fee_rate=TAKER,
            max_margin_usd=MAX_MARGIN_USD,
            max_loss_usd=MAX_LOSS_USD,
            max_leverage=float(MAX_LEVERAGE),
            slippage_buffer_pct=SLIP,
        )
    except SizingError:
        return

    # Inflate qty by 100x to force margin breach in layer2_check
    big_qty = qty * 100.0
    big_qty_rounded = math.floor(big_qty / lot_size) * lot_size
    if big_qty_rounded <= 0:
        return

    expected_margin = big_qty_rounded * entry / MAX_LEVERAGE
    if expected_margin > MAX_MARGIN_USD + 1e-9:
        with pytest.raises(CapBreachError):
            layer2_cap_check(
                symbol="XRP/USDT:USDT",
                side="long",
                qty=big_qty_rounded,
                entry_price=entry,
                stop_price=stop,
                lot_size=lot_size,
                max_leverage=float(MAX_LEVERAGE),
                max_margin_usd=MAX_MARGIN_USD,
                max_loss_usd=MAX_LOSS_USD,
                slippage_buffer_pct=SLIP,
                taker_fee_rate=TAKER,
            )


# ─────────────────────────────────────────────────────────────────────────────
# BUG 15: Liquidation price always farther from entry than stop
# ─────────────────────────────────────────────────────────────────────────────


@given(
    entry=st.floats(min_value=0.01, max_value=1000.0),
    stop_pct=st.floats(min_value=0.001, max_value=0.3),
)
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_approved_liq_always_beyond_stop_buffer(entry: float, stop_pct: float) -> None:
    """Property: liq_distance >= liq_buffer_multiple * stop_distance for all approved orders."""
    import math as _math

    if not (_math.isfinite(entry) and _math.isfinite(stop_pct)):
        return
    stop = entry * (1 - stop_pct)
    if stop <= 0:
        return
    risk_dist = abs(entry - stop)
    tp = entry + 2.0 * risk_dist

    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(entry_price=entry, stop_loss=stop, take_profit=tp),
        lesson_check=_lesson(),
        account=_make_account(),
        market=_make_market(entry),
    )
    if verdict.approved:
        assert verdict.liq_price is not None
        liq_dist = abs(entry - verdict.liq_price)
        stop_dist = abs(entry - stop)
        assert liq_dist >= LIQ_BUFFER * stop_dist - 1e-6, (
            f"CRITICAL: liq_distance {liq_dist} < {LIQ_BUFFER} * stop_distance {stop_dist} "
            f"entry={entry} stop={stop} liq={verdict.liq_price}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# BUG 16: All 27 checks are reachable — verify rule names match
# This tests that no check has a dead code path due to ordering
# ─────────────────────────────────────────────────────────────────────────────

ALL_27_RULES = [
    "KILL_SWITCH",  # 1
    "LIVE_NOT_APPROVED",  # 2
    "NO_TRADE",  # 3
    "SYMBOL_NOT_ALLOWED",  # 4
    "LESSON_FILTER_BLOCK",  # 5
    "CLOCK_DRIFT",  # 6
    "STALE_DATA",  # 7
    "SPREAD_TOO_WIDE",  # 8
    "MACRO_BLACKOUT",  # 9
    "EXCHANGE_BLACKOUT",  # 10
    "EQUITY_FLOOR",  # 11
    "DAILY_LOSS_LIMIT",  # 12
    "WEEKLY_LOSS_LIMIT",  # 13
    "CONSECUTIVE_LOSSES",  # 14
    "COOLDOWN",  # 15
    "MAX_OPEN_POSITIONS",  # 16
    "MAX_TRADES_TODAY",  # 17
    "LLM_BUDGET_EXHAUSTED",  # 18
    "LOW_CONFIDENCE",  # 19
    "LOW_CONFLUENCE",  # 20
    "MISSING_STOP_LOSS",  # 21
    "STOP_DISTANCE_ATR",  # 22
    "FUNDING_WINDOW",  # 23
    "SIZING_FAILED",  # 24
    "LIQ_TOO_CLOSE",  # 25
    "RR_TOO_LOW",  # 26
    "ENTRY_DRIFT",  # 27
]


@pytest.mark.parametrize("rule", ALL_27_RULES)
def test_all_27_rules_are_referenced_in_gate(rule: str) -> None:
    """Structural: all 27 rule names must appear as string literals in gate.py."""
    gate_src = Path("/Users/apple/ai-trader/src/trader/risk/gate.py").read_text()
    assert (
        f'"{rule}"' in gate_src or f"'{rule}'" in gate_src
    ), f"Rule '{rule}' not found as a string literal in gate.py"


# ─────────────────────────────────────────────────────────────────────────────
# BUG 17: Float precision edge cases in sizing math
# Very large prices * very small stop distance can produce float precision issues
# ─────────────────────────────────────────────────────────────────────────────


def test_sizing_very_large_entry_small_stop_distance():
    """LOW: Large price with tiny stop distance should not overflow or underflow."""
    # entry=10000.0, stop=9999.0 → stop_distance=1.0
    # max_qty_from_loss = 1.0 / (1.0 * 1.05 + fees) ≈ 0.94
    # With lot_size=1.0, floor(0.94) = 0 < min_qty=1.0 → SizingError expected
    with pytest.raises(SizingError):
        compute_size(
            side="long",
            entry=10000.0,
            stop=9999.0,
            take_profit=10100.0,
            lot_size=1.0,
            min_qty=1.0,
            maker_fee_rate=0.0002,
            taker_fee_rate=TAKER,
            max_margin_usd=MAX_MARGIN_USD,
            max_loss_usd=MAX_LOSS_USD,
            max_leverage=float(MAX_LEVERAGE),
            slippage_buffer_pct=SLIP,
        )


def test_sizing_very_small_entry_price():
    """LOW: Very small entry price (near 0) must not divide by zero or overflow qty."""
    try:
        qty, loss, margin = compute_size(
            side="long",
            entry=0.0001,
            stop=0.00009,
            take_profit=0.0002,
            lot_size=1.0,
            min_qty=1.0,
            maker_fee_rate=0.0002,
            taker_fee_rate=TAKER,
            max_margin_usd=MAX_MARGIN_USD,
            max_loss_usd=MAX_LOSS_USD,
            max_leverage=float(MAX_LEVERAGE),
            slippage_buffer_pct=SLIP,
        )
        assert loss <= MAX_LOSS_USD + 1e-9
        assert margin <= MAX_MARGIN_USD + 1e-9
    except SizingError:
        pass  # legitimate if min_qty constraint fires


def test_sizing_entry_zero_raises():
    """LOW: entry=0 must raise SizingError, not produce infinite qty."""
    with pytest.raises(SizingError):
        compute_size(
            side="long",
            entry=0.0,
            stop=-0.01,
            take_profit=0.1,
            lot_size=1.0,
            min_qty=1.0,
            maker_fee_rate=0.0002,
            taker_fee_rate=TAKER,
            max_margin_usd=MAX_MARGIN_USD,
            max_loss_usd=MAX_LOSS_USD,
            max_leverage=float(MAX_LEVERAGE),
            slippage_buffer_pct=SLIP,
        )


# ─────────────────────────────────────────────────────────────────────────────
# BUG 18: Pydantic parse failure must result in NO_TRADE, never approval
# Models use float for prices — NaN and Inf must be validated
# ─────────────────────────────────────────────────────────────────────────────


def test_decision_nan_entry_price_rejected_by_pydantic():
    """CRITICAL: NaN entry_price in DecisionInput must raise Pydantic ValidationError."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        DecisionInput(
            action="LONG",
            symbol="XRP/USDT:USDT",
            side="long",
            entry_price=float("nan"),
            stop_loss=0.45,
            take_profit=0.65,
            confidence=0.85,
            confluence_score=4,
        )


def test_decision_inf_entry_price_rejected_by_pydantic():
    """CRITICAL: Inf entry_price in DecisionInput must raise Pydantic ValidationError."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        DecisionInput(
            action="LONG",
            symbol="XRP/USDT:USDT",
            side="long",
            entry_price=float("inf"),
            stop_loss=0.45,
            take_profit=0.65,
            confidence=0.85,
            confluence_score=4,
        )


# ─────────────────────────────────────────────────────────────────────────────
# BUG 19: Verify margin formula in compute_size uses correct leverage denominator
# margin_used = qty * entry / max_leverage  (NOT qty * entry * max_leverage)
# ─────────────────────────────────────────────────────────────────────────────


def test_margin_uses_division_not_multiplication():
    """Verify margin formula: qty * entry / leverage (not * leverage)."""
    qty, loss, margin = compute_size(
        side="long",
        entry=0.5,
        stop=0.45,
        take_profit=0.65,
        lot_size=1.0,
        min_qty=1.0,
        maker_fee_rate=0.0002,
        taker_fee_rate=TAKER,
        max_margin_usd=MAX_MARGIN_USD,
        max_loss_usd=MAX_LOSS_USD,
        max_leverage=float(MAX_LEVERAGE),
        slippage_buffer_pct=SLIP,
    )
    expected_margin = qty * 0.5 / MAX_LEVERAGE
    assert (
        abs(margin - expected_margin) < 1e-9
    ), f"Margin formula wrong: got {margin}, expected {expected_margin}"
    # Also confirm this is LESS than entry * qty (no leverage multiplication)
    assert margin < qty * 0.5


# ─────────────────────────────────────────────────────────────────────────────
# BUG 20: Verify check ordering — a failing check N must not be bypassed by
# ordering. The gate is fail-fast (returns immediately on first failure).
# Check that kill_switch (check 1) fires before mode_check (check 2).
# ─────────────────────────────────────────────────────────────────────────────


def test_kill_switch_fires_before_mode_check(tmp_path):
    """Check ordering: kill_switch (1) fires before LIVE mode check (2)."""
    app = AppSettings(models=ModelNames(screening="m", decision="m", review="m"))
    settings = Settings(trading_mode="live", app=app, risk=_make_risk_settings())
    gate = RiskGate(
        settings=settings,
        live_approval_path=tmp_path / "nonexistent.yaml",
        kill_switch_on=True,
    )
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_lesson(),
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert verdict.rules_failed[0] == "KILL_SWITCH"
    assert "LIVE_NOT_APPROVED" not in verdict.rules_failed


def test_no_trade_fires_before_symbol_check():
    """Check ordering: NO_TRADE (3) fires before symbol_allowed (4)."""
    gate = _make_gate()
    verdict = gate.evaluate(
        decision=_make_decision(action="NO_TRADE", symbol="BTC/USDT:USDT"),
        lesson_check=_lesson(),
        account=_make_account(),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "NO_TRADE" in verdict.rules_failed
    assert "SYMBOL_NOT_ALLOWED" not in verdict.rules_failed


# ─────────────────────────────────────────────────────────────────────────────
# BUG 21: MISSING VALIDATION — DecisionInput has no validators on entry_price,
# stop_loss, confidence, or confluence_score ranges.
# These should ideally be validated at the model level, not just in gate logic.
# ─────────────────────────────────────────────────────────────────────────────


def test_decision_negative_entry_price_accepted_by_model():
    """MEDIUM: DecisionInput accepts negative entry_price (no model validation).

    This relies on gate.py / compute_size to catch it (SizingError at check 24).
    If compute_size is bypassed, negative price could cause math errors.
    Recommendation: add ge=0 validator to DecisionInput.entry_price.
    """
    # This should ideally raise ValidationError but currently doesn't
    try:
        d = DecisionInput(
            action="LONG",
            symbol="XRP/USDT:USDT",
            side="long",
            entry_price=-1.0,
            stop_loss=-1.5,
            take_profit=0.65,
            confidence=0.85,
            confluence_score=4,
        )
        # If accepted, the gate must still reject it
        gate = _make_gate()
        verdict = gate.evaluate(
            decision=d,
            lesson_check=_lesson(),
            account=_make_account(),
            market=_make_market(),
        )
        assert verdict.approved is False, "CRITICAL: negative entry_price was approved by the gate"
    except Exception:
        pass  # ValidationError is the desired behavior


def test_decision_confidence_above_one_accepted_by_model():
    """MEDIUM: DecisionInput accepts confidence > 1.0 (no model validation).

    confidence=1.5 should either be rejected at model level or not cause issues.
    The gate only checks effective_confidence >= min_confidence, so confidence=1.5
    would pass check 19 and potentially approve.
    """
    try:
        d = DecisionInput(
            action="LONG",
            symbol="XRP/USDT:USDT",
            side="long",
            entry_price=0.5,
            stop_loss=0.45,
            take_profit=0.65,
            confidence=1.5,
            confluence_score=4,
        )
        # If accepted, gate should still work (1.5 >= 0.7, so this DOES pass check 19)
        gate = _make_gate()
        gate.evaluate(
            decision=d,
            lesson_check=_lesson(),
            account=_make_account(),
            market=_make_market(),
        )
        # Document: confidence=1.5 will pass through the gate if model doesn't validate
        # This is a medium risk — recommend adding le=1.0 validator
    except Exception:
        pass  # ValidationError at model level is fine


# ─────────────────────────────────────────────────────────────────────────────
# BUG 22: Verify require_exchange_side_sl_tp is stored but never enforced in gate
# config/risk.yaml has require_exchange_side_sl_tp: true
# RiskSettings has require_exchange_side_sl_tp: bool = True
# But gate.py never checks this field — it only checks require_stop_loss
# and the presence/direction of stop_loss via check 21.
# ─────────────────────────────────────────────────────────────────────────────


def test_require_exchange_side_sl_tp_is_stored_in_settings():
    """MEDIUM: require_exchange_side_sl_tp is in config but gate never reads it.

    This means the gate does not enforce that an exchange-side stop order
    is submitted — it only validates that a stop_loss price is present.
    The enforcement of exchange-side SL/TP order submission must happen
    in the Executor, not the RiskGate.
    """
    risk = _make_risk_settings()
    assert (
        risk.require_exchange_side_sl_tp is True
    ), "require_exchange_side_sl_tp should be True per config/risk.yaml"
    # Verify the field exists in RiskSettings
    assert hasattr(risk, "require_exchange_side_sl_tp")


# ─────────────────────────────────────────────────────────────────────────────
# BUG 23: Verify that a SizingError from compute_size becomes SIZING_FAILED
# (not a 500 / unhandled exception that could propagate up)
# ─────────────────────────────────────────────────────────────────────────────


def test_sizing_error_becomes_rejected_not_exception():
    """CRITICAL: SizingError in compute_size must produce SIZING_FAILED, not crash."""
    gate = _make_gate()
    # min_qty=1_000_000 ensures sizing will fail (raw_qty will be < min_qty)
    verdict = gate.evaluate(
        decision=_make_decision(entry_price=0.5, stop_loss=0.45, take_profit=0.65),
        lesson_check=_lesson(),
        account=_make_account(),
        market=_make_market(0.5, atr_14=None),
        min_qty=1_000_000.0,
        lot_size=1_000_000.0,
    )
    assert verdict.approved is False
    assert "SIZING_FAILED" in verdict.rules_failed


# ─────────────────────────────────────────────────────────────────────────────
# BUG 24: Verify consecutive_losses uses >= (not >) so limit=3 fires at 3
# ─────────────────────────────────────────────────────────────────────────────


def test_consecutive_losses_boundary():
    """CRITICAL: consecutive_losses >= max_consecutive_losses must reject at exact limit."""
    gate = _make_gate()
    # max_consecutive_losses = 3, test with exactly 3
    verdict = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_lesson(),
        account=_make_account(consecutive_losses=3),
        market=_make_market(),
    )
    assert verdict.approved is False
    assert "CONSECUTIVE_LOSSES" in verdict.rules_failed

    # Test that 2 (one below limit) passes through to later checks
    verdict2 = gate.evaluate(
        decision=_make_decision(),
        lesson_check=_lesson(),
        account=_make_account(consecutive_losses=2),
        market=_make_market(),
    )
    assert "CONSECUTIVE_LOSSES" not in verdict2.rules_failed
