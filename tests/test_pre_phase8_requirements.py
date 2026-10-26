"""Tests for all fix/pre-phase8 requirements not covered by other test files.

Covers:
(1m) 1m OHLCV collection → price_1m_stale flag when missing
(sl)  PositionMonitor: PaperBroker + mocked LiveAdapter SL amendment
(e2e) End-to-end: shock fires → shock_until → next cycle blocked → expires → allowed
(hook) Hook scripts block correct patterns
"""

from __future__ import annotations

import asyncio
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from trader.agents.orchestrator import TradeCycleOrchestrator
from trader.config.settings import (
    AppSettings,
    ModelNames,
    PriceShockSettings,
    RiskSettings,
    Settings,
    TradingConfig,
)
from trader.data.macro import MacroCalendar
from trader.data.market import OHLCVCollector
from trader.dsor.base import Base
from trader.dsor.models import RiskEvent
from trader.dsor.system_state import SystemStateHelper
from trader.execution.paper_broker import PaperBroker
from trader.execution.position_monitor import PositionMonitor
from trader.llm.schemas import (
    BrainDecision,
    DerivativesReport,
    MacroNewsReport,
    PositionReview,
    TechnicalReport,
)
from trader.risk.gate import RiskGate
from trader.risk.lesson_filter import LessonFilterResult
from trader.risk.models import (
    AccountState,
    DecisionInput,
    LessonCheckInput,
    MarketState,
    RiskVerdict,
)
from trader.risk.price_shock import compute_price_shock_signals

REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings() -> Settings:
    risk = RiskSettings(
        max_margin_per_trade_usd=2.0,
        max_loss_per_trade_usd=1.0,
        slippage_buffer_pct=0.05,
        max_leverage=3,
        margin_mode="isolated",
        daily_loss_limit_usd=3.0,
        weekly_loss_limit_usd=6.0,
        equity_floor_usd=5.0,
        allowed_symbols=["XRP/USDT:USDT"],
    )
    app = AppSettings(models=ModelNames(screening="m", decision="m", review="m"))
    return Settings(trading_mode="paper", app=app, risk=risk)


def _make_shock_cfg(**overrides) -> PriceShockSettings:
    base = dict(
        min_signals_to_trigger=2,
        extreme_5m_return_pct=2.5,
        block_minutes=30,
        price_1m_return_pct=0.5,
        price_5m_return_pct=1.0,
        atr_spike_multiple=2.0,
        volume_spike_multiple=3.0,
        spread_spike_pct=0.3,
        liquidation_spike_multiple=5.0,
        open_position_risk_cut_pct=50.0,
        move_sl_to_breakeven_on_favorable_shock=True,
    )
    base.update(overrides)
    return PriceShockSettings(**base)


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


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    session = Session(engine, autocommit=False, autoflush=True)
    yield session
    session.close()
    engine.dispose()


# ===========================================================================
# 1m OHLCV collection — price_1m_stale flag
# ===========================================================================


class TestOHLCV1mStaleFlag:
    def test_1m_in_timeframes(self):
        """OHLCVCollector must include '1m' in TIMEFRAMES."""
        assert "1m" in OHLCVCollector.TIMEFRAMES

    def test_1m_missing_sets_price_1m_stale_flag(self):
        """When 1m kline returns empty, quality flag 'price_1m_stale' is set."""
        mock_rest = MagicMock()

        def side_effect(symbol, interval, limit=1):
            if interval == "1":  # 1m → Bybit interval "1"
                return []  # stale / no data
            return [[1000, 0.5, 0.6, 0.4, 0.5, 100.0, 50.0]]

        mock_rest.get_kline.side_effect = side_effect
        collector = OHLCVCollector("XRP/USDT:USDT", mock_rest)
        result = collector.collect()

        assert "price_1m_stale" in result.quality_flags

    def test_1m_error_sets_price_1m_stale_flag(self):
        """When 1m kline raises, 'price_1m_stale' is appended to quality flags."""
        mock_rest = MagicMock()

        def side_effect(symbol, interval, limit=1):
            if interval == "1":
                raise RuntimeError("Network timeout")
            return [[1000, 0.5, 0.6, 0.4, 0.5, 100.0, 50.0]]

        mock_rest.get_kline.side_effect = side_effect
        collector = OHLCVCollector("XRP/USDT:USDT", mock_rest)
        result = collector.collect()

        assert "price_1m_stale" in result.quality_flags

    def test_1m_present_no_stale_flag(self):
        """When 1m kline succeeds, 'price_1m_stale' is NOT in quality flags."""
        mock_rest = MagicMock()
        mock_rest.get_kline.return_value = [[1000, 0.5, 0.6, 0.4, 0.5, 100.0, 50.0]]
        collector = OHLCVCollector("XRP/USDT:USDT", mock_rest)
        result = collector.collect()

        assert "price_1m_stale" not in result.quality_flags


# ===========================================================================
# PositionMonitor — PaperBroker SL amendment
# ===========================================================================


class TestPositionMonitorPaperBroker:
    def _make_broker_with_position(
        self,
        side: str = "buy",
        entry: float = 1.0,
        sl: float = 0.8,
        qty: float = 10.0,
    ) -> PaperBroker:
        b = PaperBroker(initial_balance=10_000.0)
        b._mark_prices["XRP/USDT:USDT"] = entry
        b._positions["XRP/USDT:USDT"] = {
            "symbol": "XRP/USDT:USDT",
            "side": side,
            "qty": qty,
            "entry_price": entry,
            "margin": entry * qty / 3.0,
            "sl_price": sl,
            "tp_price": None,
            "open_ts": None,
            "max_adverse_excursion": 0.0,
            "max_favorable_excursion": 0.0,
            "funding_paid": 0.0,
            "unrealised_pnl": 0.0,
        }
        return b

    def test_adverse_long_shock_tightens_sl(self):
        """Adverse shock (price down) on long position → SL moves up (tightens)."""
        broker = self._make_broker_with_position(side="buy", entry=1.0, sl=0.8)
        cfg = _make_shock_cfg(open_position_risk_cut_pct=50.0)
        monitor = PositionMonitor(broker, cfg)

        # price_5m_return < 0 → price went down → adverse for long
        results = monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=-1.5, price_1m_return=None)

        assert len(results) == 1
        r = results[0]
        assert r["amended"] is True
        assert r["new_sl"] is not None
        # new SL should be above old SL (tightened for long)
        assert r["new_sl"] > r["old_sl"]
        # Verify it's actually set in the broker
        assert broker._positions["XRP/USDT:USDT"]["sl_price"] == r["new_sl"]

    def test_favorable_long_shock_moves_to_breakeven(self):
        """Favorable shock (price up) on long → SL moves to entry (breakeven)."""
        broker = self._make_broker_with_position(side="buy", entry=1.0, sl=0.8)
        broker._positions["XRP/USDT:USDT"]["unrealised_pnl"] = 3.0  # price moved up
        cfg = _make_shock_cfg(move_sl_to_breakeven_on_favorable_shock=True)
        monitor = PositionMonitor(broker, cfg)

        # price_5m_return > 0 → favorable for long
        results = monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=2.0, price_1m_return=None)

        assert len(results) == 1
        r = results[0]
        assert r["amended"] is True
        # SL should be at entry (breakeven = 1.0)
        assert abs(r["new_sl"] - 1.0) < 1e-6

    def test_never_widens_sl(self):
        """If computed new SL would widen, broker.amend_stop_loss returns False."""
        broker = self._make_broker_with_position(side="buy", entry=1.0, sl=0.8)
        cfg = _make_shock_cfg(
            move_sl_to_breakeven_on_favorable_shock=False, open_position_risk_cut_pct=0.0
        )
        monitor = PositionMonitor(broker, cfg)

        # With pct=0 adverse: no change; with no breakeven favorable: no change
        results = monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=-1.0, price_1m_return=None)

        # Should return no_amendment_needed since pct=0 means no change
        assert len(results) == 1
        r = results[0]
        # compute_shock_sl_amendment with pct=0 returns current_sl + 0 = current_sl for long
        # PaperBroker.amend_stop_loss will reject because new_sl <= current_sl
        assert r["amended"] is False or r["new_sl"] == pytest.approx(r["old_sl"], abs=1e-6)

    def test_no_position_returns_empty(self):
        """No open positions → empty results list."""
        broker = PaperBroker(initial_balance=10_000.0)
        cfg = _make_shock_cfg()
        monitor = PositionMonitor(broker, cfg)
        results = monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=-1.5, price_1m_return=None)
        assert results == []

    def test_no_return_data_skips_amendment(self):
        """If both 5m and 1m returns are None, no amendment is attempted."""
        broker = self._make_broker_with_position(side="buy", entry=1.0, sl=0.8)
        cfg = _make_shock_cfg()
        monitor = PositionMonitor(broker, cfg)
        results = monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=None, price_1m_return=None)
        assert results == []

    def test_adverse_short_shock_tightens_sl(self):
        """Adverse shock (price up) on short → SL moves down (tightens)."""
        broker = self._make_broker_with_position(side="sell", entry=1.0, sl=1.2)
        cfg = _make_shock_cfg(open_position_risk_cut_pct=50.0)
        monitor = PositionMonitor(broker, cfg)

        # price up → adverse for short
        results = monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=1.5, price_1m_return=None)

        assert len(results) == 1
        r = results[0]
        assert r["amended"] is True
        # new SL should be below old SL (tightened for short)
        assert r["new_sl"] < r["old_sl"]


# ===========================================================================
# PositionMonitor — mocked LiveAdapter SL amendment
# ===========================================================================


class TestPositionMonitorMockedLiveAdapter:
    def _make_live_adapter_mock(
        self,
        side: str = "long",
        entry: float = 1.0,
        sl: float = 0.8,
        qty: float = 10.0,
    ) -> MagicMock:
        mock_adapter = MagicMock()
        mock_adapter.get_positions.return_value = [
            {
                "symbol": "XRP/USDT:USDT",
                "side": side,
                "qty": qty,
                "entry_price": entry,
                "unrealised_pnl": 0.0,
                "sl_price": sl,
            }
        ]
        mock_adapter.amend_stop_loss.return_value = True
        return mock_adapter

    def test_live_adapter_amend_called_on_adverse_shock(self):
        """On adverse shock, amend_stop_loss is called with a tighter SL."""
        mock_adapter = self._make_live_adapter_mock(side="long", entry=1.0, sl=0.8)
        cfg = _make_shock_cfg(open_position_risk_cut_pct=50.0)
        monitor = PositionMonitor(mock_adapter, cfg)

        monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=-2.0, price_1m_return=None)

        mock_adapter.amend_stop_loss.assert_called_once()
        call_args = mock_adapter.amend_stop_loss.call_args
        symbol, new_sl = call_args[0]
        assert symbol == "XRP/USDT:USDT"
        assert new_sl > 0.8  # tightened from 0.8

    def test_live_adapter_amend_called_on_favorable_shock(self):
        """On favorable shock, amend_stop_loss is called with SL at breakeven."""
        mock_adapter = self._make_live_adapter_mock(side="long", entry=1.0, sl=0.8)
        cfg = _make_shock_cfg(move_sl_to_breakeven_on_favorable_shock=True)
        monitor = PositionMonitor(mock_adapter, cfg)

        monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=2.0, price_1m_return=None)

        mock_adapter.amend_stop_loss.assert_called_once()
        call_args = mock_adapter.amend_stop_loss.call_args
        _, new_sl = call_args[0]
        assert abs(new_sl - 1.0) < 1e-6  # breakeven

    def test_live_adapter_amend_verified_returns_result(self):
        """Result shows amended=True when broker.amend_stop_loss returns True."""
        mock_adapter = self._make_live_adapter_mock(side="long", entry=1.0, sl=0.8)
        cfg = _make_shock_cfg(open_position_risk_cut_pct=50.0)
        monitor = PositionMonitor(mock_adapter, cfg)

        results = monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=-2.0, price_1m_return=None)

        assert results[0]["amended"] is True
        assert results[0]["reason"] == "amended"

    def test_live_adapter_amend_rejected_reports_failure(self):
        """When broker.amend_stop_loss returns False, result shows amended=False."""
        mock_adapter = self._make_live_adapter_mock(side="long", entry=1.0, sl=0.8)
        mock_adapter.amend_stop_loss.return_value = False
        cfg = _make_shock_cfg(open_position_risk_cut_pct=50.0)
        monitor = PositionMonitor(mock_adapter, cfg)

        results = monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=-2.0, price_1m_return=None)

        assert results[0]["amended"] is False
        assert "rejected" in results[0]["reason"]


# ===========================================================================
# PaperBroker.amend_stop_loss direct tests
# ===========================================================================


class TestPaperBrokerAmendStopLoss:
    def _broker_with_long(self, entry: float = 1.0, sl: float = 0.8) -> PaperBroker:
        b = PaperBroker(initial_balance=10_000.0)
        b._mark_prices["XRP/USDT:USDT"] = entry
        b._positions["XRP/USDT:USDT"] = {
            "symbol": "XRP/USDT:USDT",
            "side": "buy",
            "qty": 10.0,
            "entry_price": entry,
            "margin": 1.0,
            "sl_price": sl,
            "tp_price": None,
            "open_ts": None,
            "max_adverse_excursion": 0.0,
            "max_favorable_excursion": 0.0,
            "funding_paid": 0.0,
        }
        return b

    def test_amend_up_succeeds_for_long(self):
        b = self._broker_with_long(sl=0.8)
        assert b.amend_stop_loss("XRP/USDT:USDT", 0.9) is True
        assert b._positions["XRP/USDT:USDT"]["sl_price"] == 0.9

    def test_amend_down_rejected_for_long(self):
        """Moving SL down for a long would widen it — must be rejected."""
        b = self._broker_with_long(sl=0.8)
        assert b.amend_stop_loss("XRP/USDT:USDT", 0.7) is False
        assert b._positions["XRP/USDT:USDT"]["sl_price"] == 0.8  # unchanged

    def test_amend_no_position_returns_false(self):
        b = PaperBroker(initial_balance=10_000.0)
        assert b.amend_stop_loss("XRP/USDT:USDT", 0.9) is False

    def test_amend_short_sl_down_succeeds(self):
        b = PaperBroker(initial_balance=10_000.0)
        b._positions["XRP/USDT:USDT"] = {
            "side": "sell",
            "qty": 10.0,
            "entry_price": 1.0,
            "margin": 1.0,
            "sl_price": 1.2,
        }
        assert b.amend_stop_loss("XRP/USDT:USDT", 1.1) is True

    def test_amend_short_sl_up_rejected(self):
        b = PaperBroker(initial_balance=10_000.0)
        b._positions["XRP/USDT:USDT"] = {
            "side": "sell",
            "qty": 10.0,
            "entry_price": 1.0,
            "margin": 1.0,
            "sl_price": 1.2,
        }
        assert b.amend_stop_loss("XRP/USDT:USDT", 1.3) is False


# ===========================================================================
# End-to-end: shock fires → shock_until in DB → next cycle blocked → expires → allowed
# ===========================================================================


class TestShockEndToEnd:
    def test_shock_lifecycle(self, db_session: Session):
        """Full shock lifecycle: detect → persist → block → expire → allow."""
        shock_cfg = _make_shock_cfg(block_minutes=30)

        # Step 1: detect shock
        result = compute_price_shock_signals(
            price_1m_return=None,
            price_5m_return=3.0,  # extreme trigger
            atr_current=None,
            atr_baseline=None,
            volume_current=None,
            volume_baseline=None,
            spread_pct_current=None,
            spread_pct_baseline=None,
            liquidation_volume_current=None,
            liquidation_volume_baseline=None,
            min_signals_to_trigger=shock_cfg.min_signals_to_trigger,
            extreme_5m_return_pct=shock_cfg.extreme_5m_return_pct,
            price_1m_return_pct=shock_cfg.price_1m_return_pct,
            price_5m_return_pct=shock_cfg.price_5m_return_pct,
            atr_spike_multiple=shock_cfg.atr_spike_multiple,
            volume_spike_multiple=shock_cfg.volume_spike_multiple,
            spread_spike_pct=shock_cfg.spread_spike_pct,
            liquidation_spike_multiple=shock_cfg.liquidation_spike_multiple,
        )
        assert result.shock_detected is True

        # Step 2: persist shock_until in system_state
        helper = SystemStateHelper(db_session)
        shock_until = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
            minutes=shock_cfg.block_minutes
        )
        helper.set_shock_until(shock_until)
        db_session.commit()

        # Step 3: next cycle — even with no new signals, gate rejects due to shock_until
        gate = RiskGate(
            settings=_make_settings(),
            live_approval_path=Path("/nonexistent/live_approval.yaml"),
        )
        market_blocked = _make_market(
            price_shock_active=True,
            price_shock_block_until=helper.get_shock_until(),
        )
        verdict_blocked = gate.evaluate(
            _make_decision(), _make_lesson(), _make_account(), market_blocked
        )
        assert verdict_blocked.approved is False
        assert "PRICE_SHOCK" in verdict_blocked.rules_failed

        # Step 4: after block_minutes, shock_until expires → allowed
        expired_until = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)
        helper.set_shock_until(expired_until)
        db_session.commit()

        market_expired = _make_market(
            price_shock_active=True,  # flag still set (orchestrator hasn't cleared it)
            price_shock_block_until=helper.get_shock_until(),
        )
        verdict_allowed = gate.evaluate(
            _make_decision(), _make_lesson(), _make_account(), market_expired
        )
        assert "PRICE_SHOCK" not in verdict_allowed.rules_failed

    def test_shock_until_persisted_round_trips_correctly(self, db_session: Session):
        """set_shock_until/get_shock_until round-trips the datetime via ISO-8601."""
        helper = SystemStateHelper(db_session)
        now_utc = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
        until = now_utc + datetime.timedelta(minutes=30)
        helper.set_shock_until(until)
        db_session.commit()

        retrieved = helper.get_shock_until()
        assert retrieved is not None
        assert retrieved.tzinfo is not None
        assert abs((retrieved - until).total_seconds()) < 1  # within 1 second


# ===========================================================================
# Hook tests — run the hook scripts directly and expect blocks
# ===========================================================================


def _run_block_sensitive(tool_name: str, file_path: str) -> tuple[int, str]:
    """Run block_sensitive.sh with a simulated Edit/Write PreToolUse JSON input."""
    hook = REPO_ROOT / ".claude" / "hooks" / "block_sensitive.sh"
    payload = json.dumps(
        {
            "tool_name": tool_name,
            "tool_input": {"file_path": file_path, "old_string": "x", "new_string": "y"},
        }
    )
    result = subprocess.run(
        ["bash", str(hook)],
        input=payload.encode(),
        capture_output=True,
        cwd=str(REPO_ROOT),
    )
    return result.returncode, result.stderr.decode()


def _run_block_bash(command: str) -> tuple[int, str]:
    """Run block_bash_writes.py with a simulated Bash PreToolUse JSON input."""
    hook = REPO_ROOT / ".claude" / "hooks" / "block_bash_writes.py"
    payload = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
    )
    result = subprocess.run(
        [sys.executable, str(hook)],
        input=payload.encode(),
        capture_output=True,
        cwd=str(REPO_ROOT),
    )
    return result.returncode, result.stderr.decode()


class TestHookBlocksSensitiveFiles:
    # ---- Edit tool on protected files ----

    def test_edit_risk_yaml_blocked(self):
        code, msg = _run_block_sensitive("Edit", "/repo/config/risk.yaml")
        assert code == 1
        assert "BLOCKED" in msg

    def test_edit_env_blocked(self):
        code, msg = _run_block_sensitive("Edit", "/repo/.env")
        assert code == 1
        assert "BLOCKED" in msg

    def test_edit_live_approval_blocked(self):
        code, msg = _run_block_sensitive("Edit", "/repo/config/live_approval.yaml")
        assert code == 1

    def test_edit_settings_json_blocked(self):
        code, msg = _run_block_sensitive("Edit", "/repo/.claude/settings.json")
        assert code == 1

    def test_edit_block_sensitive_hook_blocked(self):
        """Hook script protects itself."""
        code, msg = _run_block_sensitive("Edit", "/repo/.claude/hooks/block_sensitive.sh")
        assert code == 1

    def test_edit_block_bash_hook_blocked(self):
        code, msg = _run_block_sensitive("Edit", "/repo/.claude/hooks/block_bash_writes.py")
        assert code == 1

    def test_write_risk_yaml_blocked(self):
        code, msg = _run_block_sensitive("Write", "/repo/config/risk.yaml")
        assert code == 1

    def test_edit_normal_file_allowed(self):
        code, _ = _run_block_sensitive("Edit", "/repo/src/trader/risk/gate.py")
        assert code == 0

    def test_multiEdit_risk_yaml_blocked(self):
        code, msg = _run_block_sensitive("MultiEdit", "/repo/config/risk.yaml")
        assert code == 1


class TestHookBlocksBashWrites:
    # ---- Bash writes to protected files ----

    def test_sed_risk_yaml_blocked(self):
        code, msg = _run_block_bash("sed -i 's/foo/bar/' config/risk.yaml")
        assert code == 1
        assert "BLOCKED" in msg

    def test_redirect_to_risk_yaml_blocked(self):
        code, msg = _run_block_bash("echo 'x: 1' > config/risk.yaml")
        assert code == 1

    def test_tee_to_risk_yaml_blocked(self):
        code, msg = _run_block_bash("echo 'x' | tee config/risk.yaml")
        assert code == 1

    def test_mv_to_risk_yaml_blocked(self):
        code, msg = _run_block_bash("mv /tmp/new.yaml config/risk.yaml")
        assert code == 1

    def test_cp_to_risk_yaml_blocked(self):
        code, msg = _run_block_bash("cp /tmp/new.yaml config/risk.yaml")
        assert code == 1

    def test_rm_env_blocked(self):
        code, msg = _run_block_bash("rm .env")
        assert code == 1

    def test_git_checkout_risk_yaml_blocked(self):
        code, msg = _run_block_bash("git checkout -- config/risk.yaml")
        assert code == 1

    def test_git_restore_env_blocked(self):
        code, msg = _run_block_bash("git restore .env")
        assert code == 1

    def test_python_one_liner_writing_env_blocked(self):
        code, msg = _run_block_bash("python3 -c \"open('.env', 'w').write('x=1')\"")
        assert code == 1

    def test_perl_inplace_blocked(self):
        code, msg = _run_block_bash("perl -i -pe 's/foo/bar/' config/risk.yaml")
        assert code == 1

    def test_normal_echo_allowed(self):
        code, _ = _run_block_bash("echo 'hello world'")
        assert code == 0

    def test_grep_allowed(self):
        code, _ = _run_block_bash("grep -r 'foo' src/")
        assert code == 0

    def test_pytest_allowed(self):
        code, _ = _run_block_bash("uv run pytest -q")
        assert code == 0

    def test_sed_to_non_protected_allowed(self):
        code, _ = _run_block_bash("sed -i 's/foo/bar/' src/trader/risk/gate.py")
        assert code == 0

    def test_redirect_to_non_protected_allowed(self):
        code, _ = _run_block_bash("echo 'test' > /tmp/output.txt")
        assert code == 0


# ===========================================================================
# 5a: No deprecated datetime.utcnow() or datetime.utcfromtimestamp() in src/ or tests/
# ===========================================================================


class TestNoNaiveDatetimeCalls:
    """Scan all Python source files for the deprecated datetime.utcnow() and
    datetime.utcfromtimestamp() calls. These return naive datetimes and are
    deprecated in Python 3.12. All production code must use timezone-aware calls.

    The scan checks for the method call patterns .utcnow( and .utcfromtimestamp(
    on non-comment lines. Helper functions named _utcnow() are fine — they call
    datetime.now(UTC) internally and do not contain the banned dot-access pattern.
    """

    def _scan(self, patterns: list[str]) -> list[str]:
        _this_file = Path(__file__).resolve()
        violations: list[str] = []
        for root in (REPO_ROOT / "src", REPO_ROOT / "tests"):
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.py")):
                if path.resolve() == _this_file:
                    continue  # skip self (docstrings reference the banned pattern)
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    continue
                for lineno, line in enumerate(lines, 1):
                    stripped = line.lstrip()
                    if stripped.startswith("#"):
                        continue  # skip pure comment lines
                    for pat in patterns:
                        if re.search(pat, line):
                            violations.append(
                                f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.rstrip()}"
                            )
        return violations

    def test_no_utcnow_method_calls(self):
        """No .utcnow( method calls anywhere in src/ or tests/."""
        violations = self._scan([r"\.utcnow\("])
        assert not violations, (
            "Deprecated datetime.utcnow() calls found — use datetime.now(datetime.UTC):\n"
            + "\n".join(violations)
        )

    def test_no_utcfromtimestamp_method_calls(self):
        """No .utcfromtimestamp( method calls anywhere in src/ or tests/."""
        violations = self._scan([r"\.utcfromtimestamp\("])
        assert not violations, (
            "Deprecated datetime.utcfromtimestamp() calls found — "
            "use datetime.fromtimestamp(ts, tz=datetime.UTC):\n" + "\n".join(violations)
        )


# ===========================================================================
# 5b: macro_calendar_stale → Risk Gate (orchestrator) rejects entry
# ===========================================================================


def _make_orchestrator_for_5b(settings: Settings | None = None) -> TradeCycleOrchestrator:
    if settings is None:
        settings = Settings(
            trading_mode="paper",
            app=AppSettings(
                models=ModelNames(screening="m", decision="m", review="m"),
                trading=TradingConfig(symbols=["XRP/USDT:USDT"]),
            ),
            risk=RiskSettings(
                max_margin_per_trade_usd=2.0,
                max_loss_per_trade_usd=1.0,
                daily_loss_limit_usd=3.0,
                allowed_symbols=["XRP/USDT:USDT"],
                total_llm_budget_usd_per_day=5.0,
            ),
        )
    macro_analyst = MagicMock()
    macro_analyst.analyze.return_value = MacroNewsReport(
        agent="MacroNewsAnalyst", bias="neutral", confidence=0.5
    )
    technical_analyst = MagicMock()
    technical_analyst.analyze.return_value = TechnicalReport(
        agent="TechnicalAnalyst", bias="neutral", confidence=0.5
    )
    derivatives_analyst = MagicMock()
    derivatives_analyst.analyze.return_value = DerivativesReport(
        agent="DerivativesAnalyst", bias="neutral", confidence=0.5
    )
    decision_agent = MagicMock()
    decision_agent.decide.return_value = BrainDecision(action="NO_TRADE")
    position_reviewer = MagicMock()
    position_reviewer.review.return_value = PositionReview(action="HOLD", reason="none")
    lesson_filter = MagicMock()
    lesson_filter.evaluate.return_value = LessonFilterResult(
        blocked=False,
        confidence_penalty=0.0,
        matched_lessons=[],
        require_extra_confirmation=False,
        details=[],
    )
    risk_gate = MagicMock()
    risk_gate.evaluate.return_value = RiskVerdict(
        approved=False,
        adjusted_size=None,
        rules_failed=["MACRO_STALE"],
        loss_at_stop_usd=None,
        liq_price=None,
        entry_drift_pct=None,
        reject_reason="MACRO_STALE",
    )
    risk_gate._kill_switch_on = False
    return TradeCycleOrchestrator(
        settings=settings,
        macro_analyst=macro_analyst,
        technical_analyst=technical_analyst,
        derivatives_analyst=derivatives_analyst,
        decision_agent=decision_agent,
        position_reviewer=position_reviewer,
        lesson_filter=lesson_filter,
        risk_gate=risk_gate,
        ksor_retriever=None,
        session_factory=None,
        analyst_timeout_seconds=5.0,
        cycle_timeout_seconds=30.0,
    )


class TestMacroCalendarStaleBlocksEntry:
    """5b: When macro calendar has no high-impact events in next 14 days,
    data_quality_flag 'macro_calendar_stale' is emitted and the orchestrator
    stops at 'data_quality' (blocking new entry without calling analysts).
    """

    def test_stale_calendar_emits_quality_flag(self):
        """MacroCalendar with no high-impact events in next 14 days emits macro_calendar_stale."""
        # January 2027: all hardcoded 2026 events are in the past
        jan_2027 = datetime.datetime(2027, 1, 15, 12, 0, 0, tzinfo=datetime.UTC)
        with patch("trader.data.macro.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = jan_2027
            mock_dt.timedelta = datetime.timedelta
            mock_dt.UTC = datetime.UTC
            cal = MacroCalendar()
            result = cal.collect()

        assert "macro_calendar_stale" in result.quality_flags, (
            "MacroCalendar must emit 'macro_calendar_stale' when no high-impact event "
            "covers the next 14 days (Jan 2027 scenario)"
        )

    def test_orchestrator_rejects_when_macro_calendar_stale(self):
        """Orchestrator returns stopped_at='data_quality' when macro_calendar_stale is in flags."""
        orch = _make_orchestrator_for_5b()
        result = asyncio.get_event_loop().run_until_complete(
            orch.run_cycle(
                "XRP/USDT:USDT",
                "candle_close",
                feature_pack={"data_quality_flags": ["macro_calendar_stale"]},
            )
        )
        assert result.action == "NO_TRADE"
        assert result.stopped_at == "data_quality"
        orch._macro_analyst.analyze.assert_not_called()
        orch._decision_agent.decide.assert_not_called()

    def test_stale_flag_end_to_end(self):
        """Full chain: stale MacroCalendar → flag → orchestrator stops at data_quality."""
        jan_2027 = datetime.datetime(2027, 1, 15, 12, 0, 0, tzinfo=datetime.UTC)
        with patch("trader.data.macro.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = jan_2027
            mock_dt.timedelta = datetime.timedelta
            mock_dt.UTC = datetime.UTC
            cal = MacroCalendar()
            collector_result = cal.collect()

        assert "macro_calendar_stale" in collector_result.quality_flags

        orch = _make_orchestrator_for_5b()
        result = asyncio.get_event_loop().run_until_complete(
            orch.run_cycle(
                "XRP/USDT:USDT",
                "candle_close",
                feature_pack={"data_quality_flags": collector_result.quality_flags},
            )
        )
        assert result.stopped_at == "data_quality"


# ===========================================================================
# PositionMonitor failure handling: risk_event + halt on 2nd adverse failure
# ===========================================================================


def _make_pm_session_factory() -> Any:
    """Create an in-memory SQLite sessionmaker for PositionMonitor tests."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _make_mock_adapter_failing(
    side: str = "long",
    entry: float = 1.0,
    sl: float = 0.8,
    qty: float = 10.0,
) -> MagicMock:
    """Mock broker with one open position; amend_stop_loss always returns False."""
    adapter = MagicMock()
    adapter.get_positions.return_value = [
        {
            "symbol": "XRP/USDT:USDT",
            "side": side,
            "qty": qty,
            "entry_price": entry,
            "unrealised_pnl": 0.0,
            "sl_price": sl,
        }
    ]
    adapter.amend_stop_loss.return_value = False
    return adapter


class TestPositionMonitorFailureHandling:
    """Verify risk_event writing and halt behaviour on repeated adverse-shock SL failures."""

    def test_first_adverse_failure_writes_sl_amend_failure_event(self):
        """1st adverse-shock amend failure → SL_AMEND_FAILURE risk_event written to DB."""
        factory = _make_pm_session_factory()
        broker = _make_mock_adapter_failing(side="long", entry=1.0, sl=0.8)
        cfg = _make_shock_cfg(open_position_risk_cut_pct=50.0)
        monitor = PositionMonitor(broker, cfg, session_factory=factory)

        results = monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=-2.0, price_1m_return=None)

        assert len(results) == 1
        assert results[0]["amended"] is False
        assert results[0]["reason"] == "amendment_rejected_by_broker"

        with factory() as session:
            events = session.query(RiskEvent).filter_by(type="SL_AMEND_FAILURE").all()
        assert len(events) == 1
        assert "XRP/USDT:USDT" in events[0].details
        assert "adverse_fail_count=1" in events[0].details

    def test_second_adverse_failure_triggers_halt_and_close(self):
        """2nd consecutive adverse-shock failure → SL_AMEND_HALT event + emergency close + kill switch."""
        factory = _make_pm_session_factory()
        broker = _make_mock_adapter_failing(side="long", entry=1.0, sl=0.8)
        cfg = _make_shock_cfg(open_position_risk_cut_pct=50.0)
        monitor = PositionMonitor(broker, cfg, session_factory=factory)

        # 1st failure
        monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=-2.0, price_1m_return=None)
        # 2nd failure
        results2 = monitor.monitor_shock(
            "XRP/USDT:USDT", price_5m_return=-3.0, price_1m_return=None
        )

        assert len(results2) == 1
        assert results2[0]["reason"] == "halt_emergency_close"

        # Emergency close (place_order) must have been called
        broker.place_order.assert_called_once()
        call = broker.place_order.call_args
        assert (
            call.kwargs.get("reduce_only") is True
            or (len(call.args) >= 6 and call.args[5] is True)
            or call.kwargs.get("reduce_only") is True
        )

        # Kill switch must be set in DB
        from trader.dsor.system_state import SystemStateHelper  # noqa: PLC0415

        with factory() as session:
            helper = SystemStateHelper(session)
            assert helper.is_kill_switch_on() is True

        # SL_AMEND_HALT event must be written
        with factory() as session:
            halt_events = session.query(RiskEvent).filter_by(type="SL_AMEND_HALT").all()
        assert len(halt_events) == 1

    def test_non_adverse_failure_no_sl_amend_failure_event(self):
        """SL amend failure on a FAVORABLE shock does NOT write SL_AMEND_FAILURE."""
        factory = _make_pm_session_factory()
        broker = _make_mock_adapter_failing(side="long", entry=1.0, sl=0.8)
        cfg = _make_shock_cfg(move_sl_to_breakeven_on_favorable_shock=True)
        monitor = PositionMonitor(broker, cfg, session_factory=factory)

        # price_5m_return > 0 → favorable for long → not adverse
        monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=2.0, price_1m_return=None)

        with factory() as session:
            events = session.query(RiskEvent).filter_by(type="SL_AMEND_FAILURE").all()
        assert len(events) == 0, "Non-adverse failure must not write SL_AMEND_FAILURE"

    def test_success_after_failure_resets_count(self):
        """Successful amendment resets the consecutive failure counter."""
        factory = _make_pm_session_factory()
        broker = _make_mock_adapter_failing(side="long", entry=1.0, sl=0.8)
        cfg = _make_shock_cfg(open_position_risk_cut_pct=50.0)
        monitor = PositionMonitor(broker, cfg, session_factory=factory)

        # 1st failure
        monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=-2.0, price_1m_return=None)
        assert monitor._adverse_fail_count.get("XRP/USDT:USDT") == 1

        # Success
        broker.amend_stop_loss.return_value = True
        monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=-1.0, price_1m_return=None)
        assert monitor._adverse_fail_count.get("XRP/USDT:USDT") is None

        # Next failure starts from 1 again (not 2)
        broker.amend_stop_loss.return_value = False
        monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=-2.0, price_1m_return=None)
        assert monitor._adverse_fail_count.get("XRP/USDT:USDT") == 1
        broker.place_order.assert_not_called()  # no halt yet

    def test_no_session_factory_does_not_crash(self):
        """With no session_factory, failure handling logs a warning but does not crash."""
        broker = _make_mock_adapter_failing(side="long", entry=1.0, sl=0.8)
        cfg = _make_shock_cfg(open_position_risk_cut_pct=50.0)
        monitor = PositionMonitor(broker, cfg, session_factory=None)

        results = monitor.monitor_shock("XRP/USDT:USDT", price_5m_return=-2.0, price_1m_return=None)
        assert results[0]["amended"] is False  # failure recorded but no crash
