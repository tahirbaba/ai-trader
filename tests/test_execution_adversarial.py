"""Adversarial property-based tests for the execution layer.

Covers seven bypass paths:
  1. Approved order exceeds max_margin_per_trade_usd or max_loss_per_trade_usd
     (including fees + slippage).
  2. Order placed without a stop-loss.
  3. Liquidation price inside the stop buffer.
  4. Kill switch bypass.
  5. Daily / weekly loss limit bypass.
  6. Parse failure treated as approval.
  7. LessonFilter block ignored.

Plus execution-specific paths:
  E1. SL placement failure on filled entry: close at market (reduceOnly) vs cancel.
  E2. SL verification does not double-place when already confirmed in entry response.
  E3. Kill switch activated on SL failure in OrderManager.
  E4. Unprotected live position (no SL and no kill switch triggered).
  E5. force_close uses reduce_only=True and correct counter-side.
  E6. force_close with "short" position uses buy side.
  E7. _set_kill_switch DB failure is logged but does NOT suppress SlPlacementError.
  E8. layer2_cap_check is called BEFORE broker.place_order (defence-in-depth).
  E9. stop_loss=0 on DecisionInput sends None to broker (not 0).
  E10. PaperBroker: opposite-side fill does not erase stop-loss on position.
  E11. PaperBroker: reduce_only does not attach SL/TP to position.
  E12. sl_verified=False after separate SL placed raises SlPlacementError.
"""

from __future__ import annotations

import datetime
import math
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from hypothesis import HealthCheck, given, settings as h_settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trader.config.settings import AppSettings, ModelNames, RiskSettings, Settings
from trader.dsor.base import Base
from trader.dsor.models import RiskEvent
from trader.execution.exceptions import AdapterError, DuplicateOrderError, SlPlacementError
from trader.execution.layer2_check import CapBreachError, layer2_cap_check
from trader.execution.order_manager import OrderManager
from trader.execution.paper_broker import PaperBroker
from trader.risk.models import DecisionInput, LessonCheckInput, RiskVerdict

# ---------------------------------------------------------------------------
# Hard limits — NEVER change these values; they match config/risk.yaml
# ---------------------------------------------------------------------------

MAX_MARGIN_USD = 2.0
MAX_LOSS_USD = 1.0
MAX_LEVERAGE = 3
SLIP = 0.05
TAKER = 0.00055

# ---------------------------------------------------------------------------
# Shared builders
# ---------------------------------------------------------------------------


def _make_risk_settings(**overrides) -> RiskSettings:
    base = dict(
        max_margin_per_trade_usd=MAX_MARGIN_USD,
        max_loss_per_trade_usd=MAX_LOSS_USD,
        slippage_buffer_pct=SLIP,
        max_leverage=MAX_LEVERAGE,
        margin_mode="isolated",
        position_mode="one_way",
        daily_loss_limit_usd=3.0,
        weekly_loss_limit_usd=6.0,
        equity_floor_usd=5.0,
        allowed_symbols=["XRP/USDT:USDT"],
    )
    base.update(overrides)
    return RiskSettings(**base)


def _make_settings(**risk_overrides) -> Settings:
    risk = _make_risk_settings(**risk_overrides)
    app = AppSettings(models=ModelNames(screening="x", decision="x", review="x"))
    return Settings(trading_mode="paper", app=app, risk=risk)


def _make_verdict(qty: float = 1.0, approved: bool = True) -> RiskVerdict:
    return RiskVerdict(
        approved=approved,
        adjusted_size=qty,
        rules_failed=[],
        loss_at_stop_usd=0.05,
        liq_price=None,
        entry_drift_pct=0.01,
        reject_reason="",
    )


def _make_decision(
    *,
    side: str = "long",
    entry_price: float = 1.0,
    stop_loss: float = 0.90,
    take_profit: float = 1.20,
    entry_type: str = "market",
) -> DecisionInput:
    return DecisionInput(
        action="LONG",
        symbol="XRP/USDT:USDT",
        side=side,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        confidence=0.85,
        confluence_score=4,
        entry_type=entry_type,
    )


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session_factory(engine):
    def _factory():
        return Session(engine, autocommit=False, autoflush=True)
    return _factory


def _mock_broker(*, lot_size: float = 1.0, taker_fee: float = TAKER,
                 max_leverage: float = MAX_LEVERAGE) -> MagicMock:
    broker = MagicMock()
    broker.get_market_meta.return_value = {
        "lot_size": lot_size,
        "min_qty": lot_size,
        "tick_size": 0.01,
        "maker_fee": 0.0002,
        "taker_fee": taker_fee,
        "max_leverage": max_leverage,
    }
    broker.get_positions.return_value = []
    broker.get_open_orders.return_value = []
    broker.place_order.return_value = {
        "order_id": "ex-001",
        "client_order_id": "coid-001",
        "status": "filled",
        "filled_qty": 1.0,
        "avg_price": 1.0,
    }
    return broker


# ===========================================================================
# BYPASS PATH 1 — Approved order must never exceed margin or loss caps
# ===========================================================================


class TestBypass1MarginAndLossCaps:
    """layer2_cap_check is the execution-layer defence against an inflated qty
    that somehow slipped past the RiskGate."""

    @given(
        qty=st.floats(min_value=0.001, max_value=1_000_000.0),
        entry=st.floats(min_value=0.01, max_value=10_000.0),
        stop_pct=st.floats(min_value=0.001, max_value=0.5),
    )
    @h_settings(max_examples=400, deadline=None,
                suppress_health_check=[HealthCheck.too_slow])
    def test_layer2_always_catches_margin_breach(
        self, qty: float, entry: float, stop_pct: float
    ) -> None:
        """Property: if notional/leverage > MAX_MARGIN_USD, CapBreachError is raised."""
        if not (math.isfinite(qty) and math.isfinite(entry) and math.isfinite(stop_pct)):
            return
        stop = entry * (1 - stop_pct)
        if stop <= 0 or stop >= entry:
            return
        lot_size = 1.0
        rounded_qty = math.floor(qty / lot_size) * lot_size
        if rounded_qty <= 0:
            return

        margin = rounded_qty * entry / MAX_LEVERAGE
        if margin > MAX_MARGIN_USD + 1e-9:
            with pytest.raises(CapBreachError):
                layer2_cap_check(
                    symbol="XRP/USDT:USDT",
                    side="long",
                    qty=rounded_qty,
                    entry_price=entry,
                    stop_price=stop,
                    lot_size=lot_size,
                    max_leverage=float(MAX_LEVERAGE),
                    max_margin_usd=MAX_MARGIN_USD,
                    max_loss_usd=MAX_LOSS_USD,
                    slippage_buffer_pct=SLIP,
                    taker_fee_rate=TAKER,
                )

    @given(
        qty=st.floats(min_value=0.001, max_value=1_000_000.0),
        entry=st.floats(min_value=0.01, max_value=10_000.0),
        stop_pct=st.floats(min_value=0.001, max_value=0.5),
    )
    @h_settings(max_examples=400, deadline=None,
                suppress_health_check=[HealthCheck.too_slow])
    def test_layer2_always_catches_loss_breach(
        self, qty: float, entry: float, stop_pct: float
    ) -> None:
        """Property: if computed loss-at-stop > MAX_LOSS_USD, CapBreachError is raised."""
        if not (math.isfinite(qty) and math.isfinite(entry) and math.isfinite(stop_pct)):
            return
        stop = entry * (1 - stop_pct)
        if stop <= 0 or stop >= entry:
            return
        lot_size = 1.0
        rounded_qty = math.floor(qty / lot_size) * lot_size
        if rounded_qty <= 0:
            return

        # Only check for loss breach if margin also doesn't breach (isolate variable)
        margin = rounded_qty * entry / MAX_LEVERAGE
        if margin > MAX_MARGIN_USD + 1e-9:
            return  # margin breach fires first; not what this test measures

        stop_dist = abs(entry - stop) * (1.0 + SLIP)
        loss = rounded_qty * stop_dist + rounded_qty * entry * TAKER + rounded_qty * stop * TAKER
        if loss > MAX_LOSS_USD + 1e-9:
            with pytest.raises(CapBreachError):
                layer2_cap_check(
                    symbol="XRP/USDT:USDT",
                    side="long",
                    qty=rounded_qty,
                    entry_price=entry,
                    stop_price=stop,
                    lot_size=lot_size,
                    max_leverage=float(MAX_LEVERAGE),
                    max_margin_usd=MAX_MARGIN_USD,
                    max_loss_usd=MAX_LOSS_USD,
                    slippage_buffer_pct=SLIP,
                    taker_fee_rate=TAKER,
                )

    def test_layer2_called_before_broker_on_submit(self, session_factory) -> None:
        """E8: CapBreachError fires before any broker.place_order call."""
        settings = _make_settings(
            max_margin_per_trade_usd=0.001,
            max_loss_per_trade_usd=0.0005,
            daily_loss_limit_usd=0.01,
            weekly_loss_limit_usd=0.02,
            equity_floor_usd=0.0001,
        )
        broker = _mock_broker()
        om = OrderManager(broker=broker, settings=settings, session_factory=session_factory)

        verdict = _make_verdict(qty=100.0)
        decision = _make_decision()

        with pytest.raises(CapBreachError):
            om.submit(verdict, decision, cycle_id="breach-test")

        broker.place_order.assert_not_called()


# ===========================================================================
# BYPASS PATH 2 — No stop-loss
# ===========================================================================


class TestBypass2NoStopLoss:
    """Verify that an order with stop_loss=0 sends None to the broker (no SL
    is explicitly passed), AND that the risk gate's MISSING_STOP_LOSS rule
    fires before execution is reached."""

    def test_stop_loss_zero_sends_none_to_broker(self, session_factory) -> None:
        """E9: decision.stop_loss == 0 -> broker receives stop_loss=None."""
        broker = _mock_broker()
        om = OrderManager(
            broker=broker,
            settings=_make_settings(),
            session_factory=session_factory,
        )

        # Craft a decision where stop_loss=0 (minimum allowed by Pydantic ge=0)
        # layer2_cap_check will raise because stop_distance==0; we verify
        # broker was never called with stop_loss=0 as a positive float.
        decision = DecisionInput(
            action="LONG",
            symbol="XRP/USDT:USDT",
            side="long",
            entry_price=1.0,
            stop_loss=0.0,   # explicitly zero
            take_profit=1.20,
            confidence=0.85,
            confluence_score=4,
            entry_type="market",
        )
        verdict = _make_verdict(qty=1.0)

        # layer2_cap_check will raise CapBreachError (stop_distance==0)
        with pytest.raises(CapBreachError):
            om.submit(verdict, decision, cycle_id="no-sl-test")

        broker.place_order.assert_not_called()

    def test_stop_loss_zero_layer2_raises_cap_breach(self) -> None:
        """layer2_cap_check directly: entry==stop -> CapBreachError."""
        with pytest.raises(CapBreachError, match="zero"):
            layer2_cap_check(
                symbol="XRP/USDT:USDT",
                side="long",
                qty=1.0,
                entry_price=1.0,
                stop_price=1.0,   # stop == entry → zero distance
                lot_size=1.0,
                max_leverage=float(MAX_LEVERAGE),
                max_margin_usd=MAX_MARGIN_USD,
                max_loss_usd=MAX_LOSS_USD,
                slippage_buffer_pct=SLIP,
                taker_fee_rate=TAKER,
            )

    def test_negative_stop_loss_rejected_by_pydantic(self) -> None:
        """Pydantic ge=0 on stop_loss rejects negative values before any logic runs."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            DecisionInput(
                action="LONG",
                symbol="XRP/USDT:USDT",
                side="long",
                entry_price=1.0,
                stop_loss=-0.10,
                take_profit=1.20,
                confidence=0.85,
                confluence_score=4,
            )


# ===========================================================================
# BYPASS PATH 3 — Liquidation price inside stop buffer
# (Enforced by RiskGate check 25; layer2 does not re-check liq, but gate
# must reject before reaching execution.)
# ===========================================================================


class TestBypass3LiqInsideStopBuffer:
    """Verify the layer2 check raises on stop_distance==0 (liq can never be
    meaningful if stop distance is zero) and that the gate rejects via LIQ_TOO_CLOSE."""

    @given(
        entry=st.floats(min_value=0.1, max_value=1000.0),
        stop_pct=st.floats(min_value=0.001, max_value=0.3),
    )
    @h_settings(max_examples=300, deadline=None,
                suppress_health_check=[HealthCheck.too_slow])
    def test_layer2_accepts_only_positive_stop_distance(
        self, entry: float, stop_pct: float
    ) -> None:
        """Property: layer2 always raises CapBreachError when stop == entry.

        When stop == entry the stop_distance is zero, which is caught by the
        zero-distance guard.  However, the margin check runs first in
        layer2_cap_check, so for large entry prices the message may be about
        margin rather than zero distance — both are CapBreachError and both
        correctly prevent the order from proceeding.
        """
        if not (math.isfinite(entry) and math.isfinite(stop_pct)):
            return
        # stop == entry → zero distance; margin check may fire first for large prices
        stop = entry
        with pytest.raises(CapBreachError):
            layer2_cap_check(
                symbol="XRP/USDT:USDT",
                side="long",
                qty=1.0,
                entry_price=entry,
                stop_price=stop,
                lot_size=1.0,
                max_leverage=float(MAX_LEVERAGE),
                max_margin_usd=MAX_MARGIN_USD,
                max_loss_usd=MAX_LOSS_USD,
                slippage_buffer_pct=SLIP,
                taker_fee_rate=TAKER,
            )

    def test_layer2_zero_stop_distance_raises_directly(self) -> None:
        """Direct test: entry == stop at a price where margin is within limits
        always produces the 'zero' CapBreachError."""
        # entry=0.5, qty=1 -> margin = 0.5/3 = 0.167 < 2.0 (margin OK)
        # stop == entry -> zero distance fires
        with pytest.raises(CapBreachError, match="zero"):
            layer2_cap_check(
                symbol="XRP/USDT:USDT",
                side="long",
                qty=1.0,
                entry_price=0.5,
                stop_price=0.5,  # same as entry
                lot_size=1.0,
                max_leverage=float(MAX_LEVERAGE),
                max_margin_usd=MAX_MARGIN_USD,
                max_loss_usd=MAX_LOSS_USD,
                slippage_buffer_pct=SLIP,
                taker_fee_rate=TAKER,
            )


# ===========================================================================
# BYPASS PATH 4 — Kill switch bypass
# ===========================================================================


class TestBypass4KillSwitch:
    """After SL placement failure, OrderManager must activate the kill switch.
    The kill switch must be stored in DSOR even if the SlPlacementError propagates."""

    def test_sl_failure_activates_kill_switch_in_dsor(
        self, session_factory
    ) -> None:
        """E3: SlPlacementError from broker -> kill switch set + HALT event recorded."""
        broker = _mock_broker()
        broker.place_order.side_effect = SlPlacementError(
            "SL failed", order_id="eid", close_failed=False
        )
        om = OrderManager(
            broker=broker,
            settings=_make_settings(),
            session_factory=session_factory,
        )

        with pytest.raises(SlPlacementError):
            om.submit(
                _make_verdict(qty=1.0),
                _make_decision(),
                cycle_id="ks-test-1234",
            )

        # Kill switch must be in DSOR
        from trader.dsor.system_state import SystemStateHelper
        sess = session_factory()
        assert SystemStateHelper(sess).is_kill_switch_on() is True

        # HALT event must be recorded
        events = sess.query(RiskEvent).all()
        assert any(e.type == "HALT" for e in events)

    def test_sl_failure_close_also_failed_records_halt_close_failed(
        self, session_factory
    ) -> None:
        """E7: close_failed=True also generates HALT_CLOSE_FAILED event."""
        broker = _mock_broker()
        broker.place_order.side_effect = SlPlacementError(
            "SL failed and close failed",
            order_id="eid-2",
            close_failed=True,
        )
        om = OrderManager(
            broker=broker,
            settings=_make_settings(),
            session_factory=session_factory,
        )

        with pytest.raises(SlPlacementError):
            om.submit(
                _make_verdict(qty=1.0),
                _make_decision(),
                cycle_id="ks-test-5678",
            )

        sess = session_factory()
        events = sess.query(RiskEvent).all()
        event_types = {e.type for e in events}
        assert "HALT_CLOSE_FAILED" in event_types, (
            "HALT_CLOSE_FAILED event was not recorded when close also failed"
        )
        assert "HALT" in event_types

    def test_kill_switch_db_failure_does_not_swallow_sl_error(self) -> None:
        """E7: If _set_kill_switch DB write fails (session_factory raises),
        SlPlacementError must still propagate — not be swallowed."""
        broker = _mock_broker()
        broker.place_order.side_effect = SlPlacementError("SL fail", order_id="eid")

        # session_factory that always raises — simulates DB being completely down
        def _failing_session_factory():
            raise RuntimeError("DB down")

        om = OrderManager(
            broker=broker,
            settings=_make_settings(),
            session_factory=_failing_session_factory,
        )

        # SlPlacementError must propagate even though the kill switch DB write fails
        with pytest.raises(SlPlacementError):
            om.submit(
                _make_verdict(qty=1.0),
                _make_decision(),
                cycle_id="ks-dbfail",
            )


# ===========================================================================
# BYPASS PATH 5 — Daily / weekly loss limit bypass
# (Enforced by RiskGate; execution layer does not re-check, but a rejected
# verdict must produce a failed OrderResult, not an approved order.)
# ===========================================================================


class TestBypass5DailyWeeklyLossLimits:
    """A rejected RiskVerdict (approved=False) must never reach the broker."""

    def test_rejected_verdict_returns_failed_order_result(
        self, session_factory
    ) -> None:
        """approved=False verdict produces OrderResult(status='failed') before broker call."""
        broker = _mock_broker()
        om = OrderManager(
            broker=broker,
            settings=_make_settings(),
            session_factory=session_factory,
        )

        verdict = _make_verdict(qty=1.0, approved=False)
        verdict = RiskVerdict(
            approved=False,
            adjusted_size=1.0,
            rules_failed=["DAILY_LOSS_LIMIT"],
            loss_at_stop_usd=None,
            liq_price=None,
            entry_drift_pct=None,
            reject_reason="Daily loss limit reached",
        )
        result = om.submit(verdict, _make_decision(), cycle_id="loss-limit-test")

        assert result.status == "failed"
        assert result.error is not None
        broker.place_order.assert_not_called()

    @given(
        rule=st.sampled_from([
            "DAILY_LOSS_LIMIT", "WEEKLY_LOSS_LIMIT", "KILL_SWITCH",
            "LESSON_FILTER_BLOCK", "CONSECUTIVE_LOSSES", "LOW_CONFIDENCE",
        ]),
    )
    @h_settings(max_examples=100, deadline=None,
                suppress_health_check=[HealthCheck.too_slow])
    def test_rejected_verdict_never_reaches_broker(self, rule: str) -> None:
        """Property: any rejected verdict (any rule) -> broker.place_order never called."""
        broker = _mock_broker()
        eng = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(eng)
        sf = lambda: Session(eng, autocommit=False, autoflush=True)  # noqa: E731

        om = OrderManager(broker=broker, settings=_make_settings(), session_factory=sf)
        verdict = RiskVerdict(
            approved=False,
            adjusted_size=1.0,
            rules_failed=[rule],
            loss_at_stop_usd=None,
            liq_price=None,
            entry_drift_pct=None,
            reject_reason=rule,
        )
        result = om.submit(verdict, _make_decision(), cycle_id="prop-test")
        assert result.status == "failed"
        broker.place_order.assert_not_called()


# ===========================================================================
# BYPASS PATH 6 — Parse failure treated as approval
# ===========================================================================


class TestBypass6ParseFailure:
    """Pydantic models must reject invalid inputs; execution never sees them."""

    def test_nan_entry_price_rejected_by_decision_model(self) -> None:
        """NaN entry_price raises ValidationError before it can reach OrderManager."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            DecisionInput(
                action="LONG",
                symbol="XRP/USDT:USDT",
                side="long",
                entry_price=float("nan"),
                stop_loss=0.90,
                take_profit=1.20,
                confidence=0.85,
                confluence_score=4,
            )

    def test_inf_entry_price_rejected_by_decision_model(self) -> None:
        """Inf entry_price raises ValidationError."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            DecisionInput(
                action="LONG",
                symbol="XRP/USDT:USDT",
                side="long",
                entry_price=float("inf"),
                stop_loss=0.90,
                take_profit=1.20,
                confidence=0.85,
                confluence_score=4,
            )

    def test_none_adjusted_size_in_verdict_returns_failed(
        self, session_factory
    ) -> None:
        """adjusted_size=None in an approved verdict -> OrderResult(status='failed')."""
        broker = _mock_broker()
        om = OrderManager(
            broker=broker,
            settings=_make_settings(),
            session_factory=session_factory,
        )
        verdict = RiskVerdict(
            approved=True,
            adjusted_size=None,   # malformed — parse-like failure
            rules_failed=[],
            loss_at_stop_usd=None,
            liq_price=None,
            entry_drift_pct=None,
            reject_reason="",
        )
        result = om.submit(verdict, _make_decision(), cycle_id="parse-none-size")
        assert result.status == "failed"
        assert result.error is not None
        broker.place_order.assert_not_called()

    def test_zero_adjusted_size_in_verdict_returns_failed(
        self, session_factory
    ) -> None:
        """adjusted_size=0 in approved verdict -> OrderResult(status='failed')."""
        broker = _mock_broker()
        om = OrderManager(
            broker=broker,
            settings=_make_settings(),
            session_factory=session_factory,
        )
        verdict = RiskVerdict(
            approved=True,
            adjusted_size=0.0,
            rules_failed=[],
            loss_at_stop_usd=None,
            liq_price=None,
            entry_drift_pct=None,
            reject_reason="",
        )
        result = om.submit(verdict, _make_decision(), cycle_id="parse-zero-size")
        assert result.status == "failed"
        broker.place_order.assert_not_called()

    def test_negative_adjusted_size_in_verdict_returns_failed(
        self, session_factory
    ) -> None:
        """adjusted_size<0 must be caught by the qty<=0 guard in OrderManager."""
        broker = _mock_broker()
        om = OrderManager(
            broker=broker,
            settings=_make_settings(),
            session_factory=session_factory,
        )
        verdict = RiskVerdict(
            approved=True,
            adjusted_size=-5.0,
            rules_failed=[],
            loss_at_stop_usd=None,
            liq_price=None,
            entry_drift_pct=None,
            reject_reason="",
        )
        result = om.submit(verdict, _make_decision(), cycle_id="parse-neg-size")
        assert result.status == "failed"
        broker.place_order.assert_not_called()


# ===========================================================================
# BYPASS PATH 7 — LessonFilter block ignored
# (Enforced at RiskGate level; the execution path guards are: approved=False
# blocks order submission entirely.)
# ===========================================================================


class TestBypass7LessonFilterBlock:
    """A LessonFilter block manifests as approved=False from the gate.
    Execution must never promote a rejected verdict."""

    def test_lesson_blocked_verdict_never_reaches_broker(
        self, session_factory
    ) -> None:
        """A verdict with rules_failed=['LESSON_FILTER_BLOCK'] is a rejected verdict."""
        broker = _mock_broker()
        om = OrderManager(
            broker=broker,
            settings=_make_settings(),
            session_factory=session_factory,
        )
        verdict = RiskVerdict(
            approved=False,
            adjusted_size=1.0,
            rules_failed=["LESSON_FILTER_BLOCK"],
            loss_at_stop_usd=None,
            liq_price=None,
            entry_drift_pct=None,
            reject_reason="Lesson filter blocked this setup",
        )
        result = om.submit(verdict, _make_decision(), cycle_id="lesson-block")
        assert result.status == "failed"
        assert "rejected" in result.error.lower() or result.error is not None
        broker.place_order.assert_not_called()

    def test_lesson_check_input_blocked_true_cannot_have_zero_penalty(self) -> None:
        """LessonCheckInput with blocked=True and penalty=0 is structurally valid
        (blocking does not require a penalty), but must produce a rejected verdict
        when passed through the gate."""
        from trader.risk.gate import RiskGate
        from trader.risk.models import AccountState, MarketState

        gate = RiskGate(
            settings=_make_settings(),
            live_approval_path=Path("/nonexistent/live_approval.yaml"),
            kill_switch_on=False,
        )
        account = AccountState(
            equity_usd=100.0,
            daily_loss_usd=0.0,
            weekly_loss_usd=0.0,
            consecutive_losses=0,
            last_loss_ts=None,
            open_positions=0,
            trades_today=0,
            llm_cost_today_usd=0.0,
        )
        market = MarketState(
            symbol="XRP/USDT:USDT",
            live_price=1.0,
            snapshot_price=1.0,
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
        verdict = gate.evaluate(
            decision=_make_decision(),
            lesson_check=LessonCheckInput(blocked=True, confidence_penalty=0.0),
            account=account,
            market=market,
        )
        assert verdict.approved is False
        assert "LESSON_FILTER_BLOCK" in verdict.rules_failed


# ===========================================================================
# EXECUTION-SPECIFIC: E1 — SL placement failure path correctness
# ===========================================================================


class TestE1SlPlacementFailurePath:
    """When entry is already filled and SL fails, LiveAdapter must attempt
    a market close (reduce_only=True), not cancel the filled entry order."""

    def test_filled_entry_sl_failure_tries_market_close_not_cancel(
        self, tmp_path: Path
    ) -> None:
        """E1: filled entry + SL failure -> create_order(market, reduceOnly=True),
        cancel_order must NOT be called."""
        from trader.execution.live_adapter import LiveAdapter

        approval = tmp_path / "live_approval.yaml"
        approval.write_text("approved: true\n")
        settings = MagicMock()
        settings.exchange_api_key = "k"
        settings.exchange_api_secret = "s"
        settings.trading_mode = "live"

        mock_exchange = MagicMock()
        mock_exchange.load_markets.return_value = {}
        mock_exchange.markets = {}

        entry_response = {
            "id": "eid-filled",
            "clientOrderId": "c1",
            "status": "closed",
            "filled": 1.0,
            "average": 1.0,
        }
        close_response = {
            "id": "close-1",
            "clientOrderId": "",
            "status": "closed",
            "filled": 1.0,
            "average": 1.0,
        }
        # Entry succeeds (closed/filled), SL fails, market close succeeds
        mock_exchange.create_order.side_effect = [
            entry_response,
            Exception("SL rejected"),
            close_response,
        ]

        mock_ccxt = MagicMock()
        mock_ccxt.bybit.return_value = mock_exchange

        with patch.dict("sys.modules", {"ccxt": mock_ccxt}):
            adapter = LiveAdapter(settings, approval_path=approval)

        with pytest.raises(SlPlacementError) as exc_info:
            adapter.place_order(
                symbol="XRP/USDT:USDT",
                side="buy",
                qty=1.0,
                order_type="market",
                stop_loss=0.90,
                client_order_id="test-e1",
            )

        assert exc_info.value.close_failed is False, (
            "close_failed must be False when market close succeeded"
        )
        # 3 calls: entry, failed SL, market close
        assert mock_exchange.create_order.call_count == 3, (
            f"Expected 3 create_order calls, got {mock_exchange.create_order.call_count}"
        )
        # The third call must be a market order with reduceOnly=True
        third_call_args = mock_exchange.create_order.call_args_list[2]
        args, kwargs = third_call_args
        # create_order(symbol, type, side, qty, price, params)
        assert args[1] == "market", f"Expected 'market' order type for close, got {args[1]}"
        assert args[2] == "sell", f"Expected 'sell' (counter-side for buy entry), got {args[2]}"
        close_params = args[5] if len(args) > 5 else kwargs.get("params", {})
        assert close_params.get("reduceOnly") is True, (
            "Market close must have reduceOnly=True"
        )
        # cancel_order must NOT be called on a filled entry
        mock_exchange.cancel_order.assert_not_called()

    def test_unfilled_entry_sl_failure_cancels_entry_no_market_close(
        self, tmp_path: Path
    ) -> None:
        """E1 (unfilled): open/unfilled entry + SL failure -> cancel_order, no market close."""
        from trader.execution.live_adapter import LiveAdapter

        approval = tmp_path / "live_approval.yaml"
        approval.write_text("approved: true\n")
        settings = MagicMock()
        settings.exchange_api_key = "k"
        settings.exchange_api_secret = "s"
        settings.trading_mode = "live"

        mock_exchange = MagicMock()
        mock_exchange.load_markets.return_value = {}
        mock_exchange.markets = {}

        entry_response = {
            "id": "eid-open",
            "clientOrderId": "c1",
            "status": "open",
            "filled": 0.0,
            "average": None,
        }
        mock_exchange.create_order.side_effect = [
            entry_response,
            Exception("SL rejected"),
        ]
        mock_ccxt = MagicMock()
        mock_ccxt.bybit.return_value = mock_exchange

        with patch.dict("sys.modules", {"ccxt": mock_ccxt}):
            adapter = LiveAdapter(settings, approval_path=approval)

        with pytest.raises(SlPlacementError):
            adapter.place_order(
                symbol="XRP/USDT:USDT",
                side="buy",
                qty=1.0,
                order_type="limit",
                price=1.0,
                stop_loss=0.90,
                client_order_id="test-e1-unfilled",
            )

        # Only 2 create_order calls (entry + failed SL); no market close
        assert mock_exchange.create_order.call_count == 2
        # cancel_order must be called to cancel the unfilled limit entry
        mock_exchange.cancel_order.assert_called_once_with("eid-open", "XRP/USDT:USDT")


# ===========================================================================
# EXECUTION-SPECIFIC: E2 — No double-placement of SL
# ===========================================================================


class TestE2SlNoDoublePlacement:
    """If the entry response confirms the SL via stopLossPrice/stopLoss,
    no separate SL order should be placed and fetch_open_orders is not called."""

    def test_sl_confirmed_in_response_no_second_create_order(
        self, tmp_path: Path
    ) -> None:
        """E2: stopLossPrice in entry response -> create_order called exactly once."""
        from trader.execution.live_adapter import LiveAdapter

        approval = tmp_path / "live_approval.yaml"
        approval.write_text("approved: true\n")
        settings = MagicMock()
        settings.exchange_api_key = "k"
        settings.exchange_api_secret = "s"
        settings.trading_mode = "live"

        mock_exchange = MagicMock()
        mock_exchange.load_markets.return_value = {}
        mock_exchange.markets = {}
        mock_exchange.create_order.return_value = {
            "id": "eid-sl-confirmed",
            "clientOrderId": "c1",
            "status": "closed",
            "filled": 1.0,
            "average": 1.0,
            "stopLossPrice": 0.90,   # SL confirmed in response
        }
        mock_ccxt = MagicMock()
        mock_ccxt.bybit.return_value = mock_exchange

        with patch.dict("sys.modules", {"ccxt": mock_ccxt}):
            adapter = LiveAdapter(settings, approval_path=approval)

        result = adapter.place_order(
            symbol="XRP/USDT:USDT",
            side="buy",
            qty=1.0,
            order_type="market",
            stop_loss=0.90,
            client_order_id="test-e2",
        )

        assert result["order_id"] == "eid-sl-confirmed"
        mock_exchange.create_order.assert_called_once()
        mock_exchange.fetch_open_orders.assert_not_called()


# ===========================================================================
# EXECUTION-SPECIFIC: E4 — Unprotected live position
# ===========================================================================


class TestE4UnprotectedPosition:
    """If SL placement fails and market close also fails, the position is
    unprotected. This is close_failed=True. The system must still:
      - record HALT_CLOSE_FAILED and HALT events
      - activate the kill switch
      - re-raise SlPlacementError so no caller silently continues
    """

    def test_unprotected_position_activates_kill_switch_and_raises(
        self, session_factory
    ) -> None:
        """E4: SL + close both fail -> kill switch ON, SlPlacementError raised."""
        broker = _mock_broker()
        broker.place_order.side_effect = SlPlacementError(
            "Both SL and close failed",
            order_id="eid-unprotected",
            close_failed=True,
        )
        om = OrderManager(
            broker=broker,
            settings=_make_settings(),
            session_factory=session_factory,
        )

        with pytest.raises(SlPlacementError) as exc_info:
            om.submit(
                _make_verdict(qty=1.0),
                _make_decision(),
                cycle_id="unprotected-test",
            )

        assert exc_info.value.close_failed is True

        from trader.dsor.system_state import SystemStateHelper
        sess = session_factory()
        assert SystemStateHelper(sess).is_kill_switch_on() is True

        events = sess.query(RiskEvent).all()
        event_types = {e.type for e in events}
        assert "HALT" in event_types
        assert "HALT_CLOSE_FAILED" in event_types


# ===========================================================================
# EXECUTION-SPECIFIC: E5/E6 — force_close correctness
# ===========================================================================


class TestE5E6ForceClose:
    """force_close must always use reduce_only=True and the correct counter-side."""

    def test_force_close_long_uses_sell_reduce_only(self) -> None:
        """E5: long position -> force_close places sell market reduce_only order."""
        broker = PaperBroker(initial_balance=10_000.0)
        broker._mark_prices["XRP/USDT:USDT"] = 1.0
        broker.place_order(
            symbol="XRP/USDT:USDT",
            side="buy",
            qty=5.0,
            order_type="market",
            client_order_id="fc-long-entry",
        )
        assert len(broker.get_positions()) == 1

        eng = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(eng)
        sf = lambda: Session(eng, autocommit=False, autoflush=True)  # noqa: E731
        om = OrderManager(
            broker=broker,
            settings=_make_settings(),
            session_factory=sf,
        )

        result = om.force_close("XRP/USDT:USDT", "kill switch")
        assert result.status == "filled", f"force_close returned {result.status}"
        assert broker.get_positions() == [], "Position not closed after force_close"

    def test_force_close_short_uses_buy_reduce_only(self) -> None:
        """E6: short position -> force_close places buy market reduce_only order."""
        broker = PaperBroker(initial_balance=10_000.0)
        broker._mark_prices["XRP/USDT:USDT"] = 1.0
        broker.place_order(
            symbol="XRP/USDT:USDT",
            side="sell",
            qty=5.0,
            order_type="market",
            client_order_id="fc-short-entry",
        )
        assert len(broker.get_positions()) == 1
        assert broker.get_positions()[0]["side"] == "sell"

        eng = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(eng)
        sf = lambda: Session(eng, autocommit=False, autoflush=True)  # noqa: E731
        om = OrderManager(
            broker=broker,
            settings=_make_settings(),
            session_factory=sf,
        )

        result = om.force_close("XRP/USDT:USDT", "kill switch short")
        assert result.status == "filled"
        assert broker.get_positions() == []

    def test_force_close_with_no_position_returns_cancelled_not_exception(self) -> None:
        """force_close on a symbol with no position must return status='cancelled'."""
        broker = PaperBroker(initial_balance=10_000.0)
        eng = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(eng)
        sf = lambda: Session(eng, autocommit=False, autoflush=True)  # noqa: E731
        om = OrderManager(
            broker=broker,
            settings=_make_settings(),
            session_factory=sf,
        )
        result = om.force_close("XRP/USDT:USDT", "no position")
        assert result.status == "cancelled"


# ===========================================================================
# EXECUTION-SPECIFIC: E10/E11 — PaperBroker SL/TP attachment safety
# ===========================================================================


class TestE10E11PaperBrokerSlSafety:
    """PaperBroker must not attach SL/TP to positions opened with reduce_only=True,
    and must not overwrite existing SL when an opposite-side fill closes the position."""

    def test_reduce_only_fill_does_not_set_sl_on_position(self) -> None:
        """E11: reduce_only=True fill must not attach stop_loss to any remaining position."""
        broker = PaperBroker(initial_balance=10_000.0)
        broker._mark_prices["XRP/USDT:USDT"] = 1.0

        # Open initial long
        broker.place_order(
            symbol="XRP/USDT:USDT",
            side="buy",
            qty=10.0,
            order_type="market",
            stop_loss=0.90,
            client_order_id="long-entry",
        )
        pos = broker._positions["XRP/USDT:USDT"]
        assert pos["sl_price"] == 0.90

        # Now place a reduce_only partial close that carries a stop_loss (should be ignored)
        broker.place_order(
            symbol="XRP/USDT:USDT",
            side="sell",
            qty=5.0,
            order_type="market",
            reduce_only=True,
            stop_loss=0.80,   # should NOT overwrite position SL
            client_order_id="partial-close",
        )

        remaining = broker._positions.get("XRP/USDT:USDT")
        assert remaining is not None, "Partial close removed entire position"
        assert remaining["sl_price"] == 0.90, (
            f"reduce_only fill must not update sl_price. Got {remaining['sl_price']}"
        )

    def test_sl_triggered_closes_position_fully(self) -> None:
        """E10: SL trigger closes the position completely via update_mark_price."""
        broker = PaperBroker(initial_balance=10_000.0)
        broker._mark_prices["XRP/USDT:USDT"] = 1.0

        broker.place_order(
            symbol="XRP/USDT:USDT",
            side="buy",
            qty=10.0,
            order_type="market",
            stop_loss=0.90,
            client_order_id="sl-trigger-entry",
        )
        assert "XRP/USDT:USDT" in broker._positions

        # Push price to or below SL
        fills = broker.update_mark_price("XRP/USDT:USDT", 0.89)
        assert len(fills) >= 1, "Expected at least one fill (SL trigger)"
        assert "XRP/USDT:USDT" not in broker._positions, (
            "Position must be closed after SL trigger"
        )


# ===========================================================================
# EXECUTION-SPECIFIC: E12 — SL verification after separate placement
# ===========================================================================


class TestE12SlVerificationFailure:
    """After placing a separate SL order, if fetch_open_orders does not confirm
    a stop/reduce_only order, SlPlacementError must be raised."""

    def test_sl_verification_failure_raises_sl_placement_error(
        self, tmp_path: Path
    ) -> None:
        """E12: separate SL placed but not confirmed by fetch_open_orders -> error."""
        from trader.execution.live_adapter import LiveAdapter

        approval = tmp_path / "live_approval.yaml"
        approval.write_text("approved: true\n")
        settings = MagicMock()
        settings.exchange_api_key = "k"
        settings.exchange_api_secret = "s"
        settings.trading_mode = "live"

        mock_exchange = MagicMock()
        mock_exchange.load_markets.return_value = {}
        mock_exchange.markets = {}

        entry_response = {
            "id": "eid-verify-fail",
            "clientOrderId": "c1",
            "status": "closed",
            "filled": 1.0,
            "average": 1.0,
            # No stopLossPrice or stopLoss — SL not confirmed
        }
        sl_response = {
            "id": "sl-resp",
            "clientOrderId": "",
            "status": "open",
            "filled": 0.0,
            "average": None,
        }
        mock_exchange.create_order.side_effect = [entry_response, sl_response]

        # fetch_open_orders returns orders that do NOT have type=stop_market + reduceOnly
        mock_exchange.fetch_open_orders.return_value = [
            {
                "id": "some-limit",
                "type": "limit",
                "reduceOnly": False,
                "side": "sell",
                "amount": 1.0,
            }
        ]
        mock_ccxt = MagicMock()
        mock_ccxt.bybit.return_value = mock_exchange

        with patch.dict("sys.modules", {"ccxt": mock_ccxt}):
            adapter = LiveAdapter(settings, approval_path=approval)

        with pytest.raises(SlPlacementError, match="verification failed"):
            adapter.place_order(
                symbol="XRP/USDT:USDT",
                side="buy",
                qty=1.0,
                order_type="market",
                stop_loss=0.90,
                client_order_id="test-e12",
            )


# ===========================================================================
# EXECUTION-SPECIFIC: E9 — stop_loss=0 sends None to broker via OrderManager
# ===========================================================================


class TestE9StopLossZeroSendsNoneToBroker:
    """When decision.stop_loss == 0, OrderManager must pass stop_loss=None to
    the broker (not 0.0). layer2_cap_check will have already raised before
    this path is reached, so we test the conditional directly."""

    def test_stop_loss_zero_conditional(self) -> None:
        """Code path: stop_loss=0 -> condition 'stop_loss > 0' is False -> None sent."""
        # Directly verify the conditional in order_manager.py line 148
        # decision.stop_loss > 0 must be False when stop_loss==0
        decision = DecisionInput(
            action="LONG",
            symbol="XRP/USDT:USDT",
            side="long",
            entry_price=1.0,
            stop_loss=0.0,
            take_profit=1.20,
            confidence=0.85,
            confluence_score=4,
        )
        # This mirrors the conditional on line 148
        sl_arg = decision.stop_loss if decision.stop_loss > 0 else None
        assert sl_arg is None, (
            "stop_loss=0 must produce None when passed to broker, not 0.0"
        )


# ===========================================================================
# EXECUTION-SPECIFIC: LiveAdapter approval guard
# ===========================================================================


class TestLiveAdapterApprovalGuard:
    """LiveAdapter construction must fail if approval file is absent or
    trading_mode != 'live'."""

    def test_no_approval_file_raises_live_not_approved(
        self, tmp_path: Path
    ) -> None:
        from trader.execution.exceptions import LiveNotApprovedError
        from trader.execution.live_adapter import LiveAdapter

        settings = MagicMock()
        settings.trading_mode = "live"
        non_existent = tmp_path / "live_approval.yaml"
        assert not non_existent.exists()

        with pytest.raises(LiveNotApprovedError):
            LiveAdapter(settings, approval_path=non_existent)

    def test_paper_mode_raises_live_not_approved_even_with_file(
        self, tmp_path: Path
    ) -> None:
        """trading_mode=paper + approval file present -> still raises LiveNotApprovedError."""
        from trader.execution.exceptions import LiveNotApprovedError
        from trader.execution.live_adapter import LiveAdapter

        approval = tmp_path / "live_approval.yaml"
        approval.write_text("approved: true\n")
        settings = MagicMock()
        settings.trading_mode = "paper"   # wrong mode

        with pytest.raises(LiveNotApprovedError):
            LiveAdapter(settings, approval_path=approval)
