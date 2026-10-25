"""Tests for price-shock circuit breaker (pre-phase8).

Covers:
- compute_price_shock_signals: single extreme trigger, multi-signal trigger,
  insufficient signals, 1m stale flag
- compute_shock_sl_amendment: adverse/favorable/no-op cases, never-widen guarantee
- is_shock_active: active/expired window
- RiskGate PRICE_SHOCK rule: rejects when active, passes when expired
- RiskGate EXCHANGE_SL_NOT_CAPABLE rule (DoD #6)
- SystemStateHelper set/get shock_until
- OrderManager.reconcile kill switch on mismatch (DoD #7)
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trader.config.settings import (
    AppSettings,
    ModelNames,
    RiskSettings,
    Settings,
)
from trader.dsor.base import Base
from trader.dsor.models import RiskEvent
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
from trader.risk.price_shock import (
    compute_price_shock_signals,
    compute_shock_sl_amendment,
    is_shock_active,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.datetime(2026, 6, 1, 12, 0, 0, tzinfo=datetime.UTC)


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
        atr_14=0.01,
        spread_pct=0.001,
        funding_rate=0.0001,
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


def _shock_cfg(**overrides):
    base = dict(
        min_signals_to_trigger=2,
        extreme_5m_return_pct=2.5,
        price_1m_return_pct=0.5,
        price_5m_return_pct=1.0,
        atr_spike_multiple=2.0,
        volume_spike_multiple=3.0,
        spread_spike_pct=0.3,
        liquidation_spike_multiple=5.0,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# compute_price_shock_signals
# ---------------------------------------------------------------------------


class TestComputePriceShockSignals:
    def test_extreme_5m_return_alone_triggers(self):
        result = compute_price_shock_signals(
            price_1m_return=None,
            price_5m_return=3.0,  # >= extreme_5m_return_pct=2.5
            atr_current=None,
            atr_baseline=None,
            volume_current=None,
            volume_baseline=None,
            spread_pct_current=None,
            spread_pct_baseline=None,
            liquidation_volume_current=None,
            liquidation_volume_baseline=None,
            **_shock_cfg(),
        )
        assert result.shock_detected is True
        assert result.triggered_by_extreme is True

    def test_extreme_negative_5m_return_triggers(self):
        result = compute_price_shock_signals(
            price_1m_return=None,
            price_5m_return=-3.0,
            atr_current=None,
            atr_baseline=None,
            volume_current=None,
            volume_baseline=None,
            spread_pct_current=None,
            spread_pct_baseline=None,
            liquidation_volume_current=None,
            liquidation_volume_baseline=None,
            **_shock_cfg(),
        )
        assert result.shock_detected is True
        assert result.triggered_by_extreme is True

    def test_two_normal_signals_trigger(self):
        result = compute_price_shock_signals(
            price_1m_return=0.6,  # >= 0.5
            price_5m_return=1.1,  # >= 1.0 but < extreme 2.5
            atr_current=None,
            atr_baseline=None,
            volume_current=None,
            volume_baseline=None,
            spread_pct_current=None,
            spread_pct_baseline=None,
            liquidation_volume_current=None,
            liquidation_volume_baseline=None,
            **_shock_cfg(),
        )
        assert result.shock_detected is True
        assert result.triggered_by_extreme is False
        assert "price_1m_return" in result.signals_fired
        assert "price_5m_return" in result.signals_fired

    def test_one_signal_insufficient(self):
        result = compute_price_shock_signals(
            price_1m_return=0.6,
            price_5m_return=0.5,  # below threshold
            atr_current=None,
            atr_baseline=None,
            volume_current=None,
            volume_baseline=None,
            spread_pct_current=None,
            spread_pct_baseline=None,
            liquidation_volume_current=None,
            liquidation_volume_baseline=None,
            **_shock_cfg(),
        )
        assert result.shock_detected is False

    def test_1m_stale_sets_quality_flag(self):
        result = compute_price_shock_signals(
            price_1m_return=None,
            price_5m_return=0.5,
            atr_current=None,
            atr_baseline=None,
            volume_current=None,
            volume_baseline=None,
            spread_pct_current=None,
            spread_pct_baseline=None,
            liquidation_volume_current=None,
            liquidation_volume_baseline=None,
            **_shock_cfg(),
        )
        assert "price_1m_stale" in result.data_quality_flags

    def test_atr_spike_counts_as_signal(self):
        result = compute_price_shock_signals(
            price_1m_return=0.6,  # signal 1
            price_5m_return=None,
            atr_current=4.0,
            atr_baseline=1.0,  # ratio=4 >= multiple=2 → signal 2
            volume_current=None,
            volume_baseline=None,
            spread_pct_current=None,
            spread_pct_baseline=None,
            liquidation_volume_current=None,
            liquidation_volume_baseline=None,
            **_shock_cfg(),
        )
        assert result.shock_detected is True
        assert "atr_spike" in result.signals_fired

    def test_volume_and_liquidation_spike(self):
        result = compute_price_shock_signals(
            price_1m_return=None,
            price_5m_return=None,
            atr_current=None,
            atr_baseline=None,
            volume_current=300.0,
            volume_baseline=100.0,  # ratio=3 >= 3 → signal
            spread_pct_current=None,
            spread_pct_baseline=None,
            liquidation_volume_current=50.0,
            liquidation_volume_baseline=5.0,  # ratio=10 >= 5 → signal
            **_shock_cfg(),
        )
        assert result.shock_detected is True
        assert "volume_spike" in result.signals_fired
        assert "liquidation_spike" in result.signals_fired

    def test_spread_spike_counts(self):
        result = compute_price_shock_signals(
            price_1m_return=0.6,  # signal 1
            price_5m_return=None,
            atr_current=None,
            atr_baseline=None,
            volume_current=None,
            volume_baseline=None,
            spread_pct_current=0.5,
            spread_pct_baseline=0.1,  # delta=0.4 >= spread_spike_pct=0.3 → signal 2
            liquidation_volume_current=None,
            liquidation_volume_baseline=None,
            **_shock_cfg(),
        )
        assert result.shock_detected is True
        assert "spread_spike" in result.signals_fired


# ---------------------------------------------------------------------------
# compute_shock_sl_amendment
# ---------------------------------------------------------------------------


class TestComputeShockSlAmendment:
    def test_adverse_long_tightens_sl(self):
        new_sl = compute_shock_sl_amendment(
            side="long",
            entry_price=1.0,
            current_sl=0.8,  # risk = 0.2
            current_price=0.85,
            open_position_risk_cut_pct=50.0,
            move_sl_to_breakeven_on_favorable_shock=True,
            shock_is_adverse=True,
        )
        # new_sl = 0.8 + 0.1 = 0.9
        assert new_sl is not None
        assert abs(new_sl - 0.9) < 1e-6

    def test_adverse_short_tightens_sl(self):
        new_sl = compute_shock_sl_amendment(
            side="short",
            entry_price=1.0,
            current_sl=1.2,  # risk = 0.2
            current_price=1.1,
            open_position_risk_cut_pct=50.0,
            move_sl_to_breakeven_on_favorable_shock=True,
            shock_is_adverse=True,
        )
        # new_sl = 1.2 - 0.1 = 1.1
        assert new_sl is not None
        assert abs(new_sl - 1.1) < 1e-6

    def test_favorable_long_moves_to_breakeven(self):
        new_sl = compute_shock_sl_amendment(
            side="long",
            entry_price=1.0,
            current_sl=0.8,
            current_price=1.3,
            open_position_risk_cut_pct=50.0,
            move_sl_to_breakeven_on_favorable_shock=True,
            shock_is_adverse=False,
        )
        assert new_sl == 1.0  # breakeven

    def test_favorable_short_moves_to_breakeven(self):
        new_sl = compute_shock_sl_amendment(
            side="short",
            entry_price=1.0,
            current_sl=1.2,
            current_price=0.7,
            open_position_risk_cut_pct=50.0,
            move_sl_to_breakeven_on_favorable_shock=True,
            shock_is_adverse=False,
        )
        assert new_sl == 1.0

    def test_favorable_sl_already_at_breakeven_returns_none(self):
        """If SL is already at entry (breakeven), no amendment needed."""
        new_sl = compute_shock_sl_amendment(
            side="long",
            entry_price=1.0,
            current_sl=1.0,  # already at breakeven
            current_price=1.3,
            open_position_risk_cut_pct=50.0,
            move_sl_to_breakeven_on_favorable_shock=True,
            shock_is_adverse=False,
        )
        assert new_sl is None

    def test_adverse_never_passes_entry(self):
        """Even with 100% cut, SL cannot move past entry."""
        new_sl = compute_shock_sl_amendment(
            side="long",
            entry_price=1.0,
            current_sl=0.99,  # very close, 100% cut would push above entry
            current_price=0.995,
            open_position_risk_cut_pct=100.0,
            move_sl_to_breakeven_on_favorable_shock=False,
            shock_is_adverse=True,
        )
        assert new_sl is not None
        assert new_sl < 1.0  # strictly below entry

    def test_no_amendment_when_favorable_and_flag_off(self):
        new_sl = compute_shock_sl_amendment(
            side="long",
            entry_price=1.0,
            current_sl=0.8,
            current_price=1.3,
            open_position_risk_cut_pct=50.0,
            move_sl_to_breakeven_on_favorable_shock=False,
            shock_is_adverse=False,
        )
        assert new_sl is None


# ---------------------------------------------------------------------------
# is_shock_active
# ---------------------------------------------------------------------------


class TestIsShockActive:
    def test_active_when_in_window(self):
        future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=10)
        assert is_shock_active(future) is True

    def test_inactive_when_expired(self):
        past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)
        assert is_shock_active(past) is False

    def test_inactive_when_none(self):
        assert is_shock_active(None) is False

    def test_naive_dt_treated_as_utc(self):
        future_naive = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + datetime.timedelta(minutes=10)
        assert is_shock_active(future_naive) is True


# ---------------------------------------------------------------------------
# RiskGate PRICE_SHOCK rule
# ---------------------------------------------------------------------------


class TestRiskGatePriceShock:
    def _gate(self, **kwargs) -> RiskGate:
        return RiskGate(
            settings=_make_settings(),
            live_approval_path=Path("/nonexistent/live_approval.yaml"),
            **kwargs,
        )

    def test_price_shock_active_rejects(self):
        gate = self._gate()
        block_until = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=25)
        market = _make_market(
            price_shock_active=True,
            price_shock_block_until=block_until,
        )
        verdict = gate.evaluate(_make_decision(), _make_lesson(), _make_account(), market)
        assert verdict.approved is False
        assert "PRICE_SHOCK" in verdict.rules_failed

    def test_price_shock_expired_passes(self):
        gate = self._gate()
        block_until = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)
        market = _make_market(
            price_shock_active=True,  # active flag still set
            price_shock_block_until=block_until,  # but window expired
        )
        verdict = gate.evaluate(_make_decision(), _make_lesson(), _make_account(), market)
        # Should not be rejected for PRICE_SHOCK
        assert "PRICE_SHOCK" not in verdict.rules_failed

    def test_price_shock_inactive_no_block(self):
        gate = self._gate()
        market = _make_market(price_shock_active=False, price_shock_block_until=None)
        verdict = gate.evaluate(_make_decision(), _make_lesson(), _make_account(), market)
        assert "PRICE_SHOCK" not in verdict.rules_failed

    def test_price_shock_active_no_until_still_rejects(self):
        """If price_shock_active=True but block_until=None, should still reject."""
        gate = self._gate()
        market = _make_market(price_shock_active=True, price_shock_block_until=None)
        verdict = gate.evaluate(_make_decision(), _make_lesson(), _make_account(), market)
        assert verdict.approved is False
        assert "PRICE_SHOCK" in verdict.rules_failed


# ---------------------------------------------------------------------------
# RiskGate EXCHANGE_SL_NOT_CAPABLE (DoD #6)
# ---------------------------------------------------------------------------


class TestRiskGateExchangeSlCapable:
    def test_rejects_when_sl_not_capable_and_required(self):
        gate = RiskGate(
            settings=_make_settings(require_exchange_sl=True),
            live_approval_path=Path("/nonexistent/live_approval.yaml"),
            exchange_sl_capable=False,
        )
        verdict = gate.evaluate(_make_decision(), _make_lesson(), _make_account(), _make_market())
        assert verdict.approved is False
        assert "EXCHANGE_SL_NOT_CAPABLE" in verdict.rules_failed

    def test_passes_when_sl_capable(self):
        gate = RiskGate(
            settings=_make_settings(require_exchange_sl=True),
            live_approval_path=Path("/nonexistent/live_approval.yaml"),
            exchange_sl_capable=True,
        )
        verdict = gate.evaluate(_make_decision(), _make_lesson(), _make_account(), _make_market())
        assert "EXCHANGE_SL_NOT_CAPABLE" not in verdict.rules_failed

    def test_passes_when_exchange_sl_not_required(self):
        gate = RiskGate(
            settings=_make_settings(require_exchange_sl=False),
            live_approval_path=Path("/nonexistent/live_approval.yaml"),
            exchange_sl_capable=False,
        )
        verdict = gate.evaluate(_make_decision(), _make_lesson(), _make_account(), _make_market())
        assert "EXCHANGE_SL_NOT_CAPABLE" not in verdict.rules_failed


# ---------------------------------------------------------------------------
# SystemStateHelper shock_until persistence
# ---------------------------------------------------------------------------


@pytest.fixture()
def ss_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    session = Session(engine, autocommit=False, autoflush=True)
    yield session
    session.close()
    engine.dispose()


class TestSystemStateShockUntil:
    def test_set_and_get_shock_until(self, ss_session: Session):
        helper = SystemStateHelper(ss_session)
        until = datetime.datetime(2026, 6, 1, 13, 0, 0, tzinfo=datetime.UTC)
        helper.set_shock_until(until)
        ss_session.commit()
        retrieved = helper.get_shock_until()
        assert retrieved is not None
        assert retrieved.year == 2026
        assert retrieved.minute == 0

    def test_get_shock_until_returns_none_when_unset(self, ss_session: Session):
        helper = SystemStateHelper(ss_session)
        assert helper.get_shock_until() is None

    def test_set_shock_until_naive_converted_to_utc(self, ss_session: Session):
        helper = SystemStateHelper(ss_session)
        naive = datetime.datetime(2026, 6, 1, 13, 0, 0)
        helper.set_shock_until(naive)
        ss_session.commit()
        retrieved = helper.get_shock_until()
        assert retrieved is not None
        assert retrieved.tzinfo is not None


# ---------------------------------------------------------------------------
# OrderManager.reconcile() — kill switch on mismatch (DoD #7)
# ---------------------------------------------------------------------------


@pytest.fixture()
def om_engine():
    eng = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def om_session_factory(om_engine):
    def _factory():
        return Session(om_engine, autocommit=False, autoflush=True)
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


class TestReconcileKillSwitch:
    def test_reconcile_mismatch_sets_kill_switch(
        self, om_session_factory, om_engine
    ):
        broker = PaperBroker(initial_balance=10_000.0)
        broker._mark_prices["XRP/USDT:USDT"] = 1.0
        # Inject a position into broker without corresponding DSOR order
        broker._positions["XRP/USDT:USDT"] = {
            "symbol": "XRP/USDT:USDT",
            "side": "buy",
            "qty": 10.0,
            "entry_price": 1.0,
            "margin": 1.0,
            "sl_price": None,
            "tp_price": None,
            "open_ts": None,
            "max_adverse_excursion": 0.0,
            "max_favorable_excursion": 0.0,
            "funding_paid": 0.0,
        }
        om = OrderManager(
            broker=broker,
            settings=_make_om_settings(),
            session_factory=om_session_factory,
        )
        result = om.reconcile("XRP/USDT:USDT")
        assert result.ok is False

        # Verify kill switch is set in DSOR
        session = om_session_factory()
        from trader.dsor.system_state import SystemStateHelper
        helper = SystemStateHelper(session)
        assert helper.is_kill_switch_on() is True

    def test_reconcile_mismatch_records_risk_event(
        self, om_session_factory, om_engine
    ):
        broker = PaperBroker(initial_balance=10_000.0)
        broker._mark_prices["XRP/USDT:USDT"] = 1.0
        broker._positions["XRP/USDT:USDT"] = {
            "symbol": "XRP/USDT:USDT",
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
        om = OrderManager(
            broker=broker,
            settings=_make_om_settings(),
            session_factory=om_session_factory,
        )
        om.reconcile("XRP/USDT:USDT")

        session = om_session_factory()
        events = session.query(RiskEvent).all()
        assert any(e.type == "RECONCILE_MISMATCH" for e in events)

    def test_reconcile_ok_no_kill_switch(self, om_session_factory, om_engine):
        """No mismatch → kill switch NOT set."""
        broker = PaperBroker(initial_balance=10_000.0)
        broker._mark_prices["XRP/USDT:USDT"] = 1.0
        om = OrderManager(
            broker=broker,
            settings=_make_om_settings(),
            session_factory=om_session_factory,
        )
        result = om.reconcile("XRP/USDT:USDT")
        assert result.ok is True

        session = om_session_factory()
        from trader.dsor.system_state import SystemStateHelper
        helper = SystemStateHelper(session)
        assert helper.is_kill_switch_on() is False
