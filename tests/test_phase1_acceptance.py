"""
Phase 1 acceptance tests — four cases explicitly required by the owner:

  a) alembic upgrade head AND downgrade base work on SQLite
  b) app refuses to start in live mode without config/live_approval.yaml
  c) cross-field config validation rejects inconsistent values
  d) decisions and tv_alerts are append-only (update/delete raises)
  e) secret redaction works on a fake key

Tests b, c, e have fuller coverage in test_config.py / test_logging_redaction.py.
This file provides named, labelled acceptance tests the owner can point to directly.
"""

from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trader.config.settings import RiskSettings, load_settings
from trader.dsor.base import Base
from trader.dsor.models import Decision, TvAlert
from trader.ops.logging import redact_dict

# ---------------------------------------------------------------------------
# Helpers shared with other test modules
# ---------------------------------------------------------------------------

def _minimal_risk() -> dict:
    return {
        "mode_default": "paper",
        "max_margin_per_trade_usd": 2.0,
        "max_loss_per_trade_usd": 1.0,
        "slippage_buffer_pct": 0.05,
        "max_leverage": 3,
        "margin_mode": "isolated",
        "position_mode": "one_way",
        "require_stop_loss": True,
        "require_exchange_side_sl_tp": True,
        "liq_buffer_multiple": 2.0,
        "min_stop_atr_multiple": 0.5,
        "max_stop_atr_multiple": 3.0,
        "min_risk_reward": 1.5,
        "max_entry_drift_pct": 0.15,
        "limit_order_ttl_seconds": 300,
        "max_holding_minutes": 720,
        "avoid_funding_window_minutes": 10,
        "max_open_positions": 1,
        "max_trades_per_day": 6,
        "daily_loss_limit_usd": 3.0,
        "weekly_loss_limit_usd": 6.0,
        "equity_floor_usd": 5.0,
        "cooldown_after_loss_minutes": 60,
        "max_consecutive_losses": 3,
        "news_blackout_minutes_before": 30,
        "news_blackout_minutes_after": 30,
        "max_data_age_seconds": 60,
        "max_clock_drift_ms": 1000,
        "max_spread_pct": 0.1,
        "min_confidence": 0.7,
        "min_confluence_score": 3,
        "allowed_symbols": [],
        "total_llm_budget_usd_per_day": 5.0,
    }


def _minimal_app() -> dict:
    return {
        "trading": {"symbols": [], "timeframes": ["15m", "1h"]},
        "models": {
            "screening": "claude-haiku-4-5-20251001",
            "decision": "claude-sonnet-4-6",
            "review": "claude-sonnet-4-6",
        },
        "feature_flags": {
            "tradingview_webhook": False,
            "sentiment_analyst": False,
            "chart_image_analysis": False,
            "onchain_data": False,
        },
    }


# ---------------------------------------------------------------------------
# (a) Alembic upgrade head + downgrade base on SQLite
# ---------------------------------------------------------------------------

class TestAlembicMigrationRoundTrip:
    """Acceptance (a): alembic upgrade head AND downgrade base work on SQLite."""

    def test_upgrade_head(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-x", f"db_url=sqlite:///{db}", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stderr}"

    def test_downgrade_base(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        # First upgrade, then downgrade
        subprocess.run(
            [sys.executable, "-m", "alembic", "-x", f"db_url=sqlite:///{db}", "upgrade", "head"],
            capture_output=True, cwd=Path(__file__).parent.parent,
        )
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-x", f"db_url=sqlite:///{db}", "downgrade", "base"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        assert result.returncode == 0, f"alembic downgrade base failed:\n{result.stderr}"

    def test_upgrade_head_via_api(self) -> None:
        """Same test using Alembic's Python API with an in-memory URL."""
        from alembic import command
        from alembic.config import Config

        repo_root = Path(__file__).parent.parent
        cfg = Config(str(repo_root / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", "sqlite:///:memory:")
        cfg.set_main_option("script_location", str(repo_root / "migrations"))
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")


# ---------------------------------------------------------------------------
# (b) Live mode guard
# ---------------------------------------------------------------------------

class TestLiveModeGuard:
    """Acceptance (b): app refuses to start in live mode without live_approval.yaml."""

    def test_live_without_approval_file_raises(self, tmp_path: Path) -> None:
        (tmp_path / "app.yaml").write_text(yaml.dump(_minimal_app()))
        (tmp_path / "risk.yaml").write_text(yaml.dump(_minimal_risk()))
        with pytest.raises(ValueError, match="live_approval"):
            load_settings(
                app_yaml=tmp_path / "app.yaml",
                risk_yaml=tmp_path / "risk.yaml",
                live_approval_yaml=tmp_path / "live_approval.yaml",
                env_trading_mode="live",
            )

    def test_paper_mode_always_starts(self, tmp_path: Path) -> None:
        (tmp_path / "app.yaml").write_text(yaml.dump(_minimal_app()))
        (tmp_path / "risk.yaml").write_text(yaml.dump(_minimal_risk()))
        s = load_settings(
            app_yaml=tmp_path / "app.yaml",
            risk_yaml=tmp_path / "risk.yaml",
            live_approval_yaml=tmp_path / "live_approval.yaml",
            env_trading_mode="paper",
        )
        assert s.trading_mode == "paper"

    def test_set_mode_live_rejected_at_runtime(self) -> None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from trader.dsor.system_state import SystemStateHelper
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as s:
            helper = SystemStateHelper(s)
            with pytest.raises(ValueError, match="rejected"):
                helper.set_mode("live")


# ---------------------------------------------------------------------------
# (c) Cross-field config validation
# ---------------------------------------------------------------------------

class TestCrossFieldValidation:
    """Acceptance (c): cross-field config validation rejects inconsistent values."""

    def test_max_loss_greater_than_margin_rejected(self) -> None:
        risk = _minimal_risk()
        risk["max_loss_per_trade_usd"] = 3.0  # > max_margin=2
        with pytest.raises(Exception, match="max_loss_per_trade_usd"):
            RiskSettings(**risk)

    def test_daily_loss_less_than_per_trade_rejected(self) -> None:
        risk = _minimal_risk()
        risk["daily_loss_limit_usd"] = 0.5  # < max_loss=1
        with pytest.raises(Exception, match="daily_loss_limit_usd"):
            RiskSettings(**risk)

    def test_cross_margin_rejected(self) -> None:
        risk = _minimal_risk()
        risk["margin_mode"] = "cross"
        with pytest.raises(Exception, match="margin_mode"):
            RiskSettings(**risk)

    def test_equal_loss_and_margin_accepted(self) -> None:
        risk = _minimal_risk()
        risk["max_loss_per_trade_usd"] = 2.0  # == max_margin: boundary allowed
        r = RiskSettings(**risk)
        assert r.max_loss_per_trade_usd == 2.0


# ---------------------------------------------------------------------------
# (d) decisions and tv_alerts are append-only
# ---------------------------------------------------------------------------

@pytest.fixture()
def mem_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class TestAppendOnly:
    """Acceptance (d): decisions and tv_alerts raise on UPDATE or DELETE."""

    def _make_decision(self, session: Session) -> Decision:
        d = Decision(
            ts=datetime.datetime(2026, 1, 1, 0, 0, 0),
            action="NO_TRADE",
            symbol="XRP/USDT:USDT",
            confidence=0.0,
            confluence_score=0,
            parse_ok=True,
        )
        session.add(d)
        session.commit()
        return d

    def _make_tv_alert(self, session: Session) -> TvAlert:
        t = TvAlert(
            ts_received=datetime.datetime(2026, 1, 1, 0, 0, 0),
            alert_name="test_alert",
            symbol="XRP/USDT:USDT",
            timeframe="15m",
            signal="long",
            dedupe_hash="abc123",
        )
        session.add(t)
        session.commit()
        return t

    # --- Decision ---

    def test_decision_update_raises(self, mem_session: Session) -> None:
        d = self._make_decision(mem_session)
        d.action = "LONG"
        with pytest.raises(RuntimeError, match="append-only"):
            mem_session.flush()

    def test_decision_delete_raises(self, mem_session: Session) -> None:
        d = self._make_decision(mem_session)
        mem_session.delete(d)
        with pytest.raises(RuntimeError, match="append-only"):
            mem_session.flush()

    # --- TvAlert ---

    def test_tv_alert_update_raises(self, mem_session: Session) -> None:
        t = self._make_tv_alert(mem_session)
        t.signal = "short"
        with pytest.raises(RuntimeError, match="append-only"):
            mem_session.flush()

    def test_tv_alert_delete_raises(self, mem_session: Session) -> None:
        t = self._make_tv_alert(mem_session)
        mem_session.delete(t)
        with pytest.raises(RuntimeError, match="append-only"):
            mem_session.flush()


# ---------------------------------------------------------------------------
# (e) Secret redaction on a fake key
# ---------------------------------------------------------------------------

class TestSecretRedaction:
    """Acceptance (e): secret redaction works on a fake key."""

    def test_fake_api_key_redacted(self) -> None:
        result = redact_dict({"ANTHROPIC_API_KEY": "sk-ant-fake-000000000000"})
        assert result["ANTHROPIC_API_KEY"] == "***REDACTED***"

    def test_fake_exchange_secret_redacted(self) -> None:
        result = redact_dict({"EXCHANGE_API_SECRET": "bybit-secret-xyz"})
        assert result["EXCHANGE_API_SECRET"] == "***REDACTED***"

    def test_fake_token_redacted(self) -> None:
        result = redact_dict({"TELEGRAM_BOT_TOKEN": "123456:FAKE_TOKEN"})
        assert result["TELEGRAM_BOT_TOKEN"] == "***REDACTED***"

    def test_safe_fields_unchanged(self) -> None:
        result = redact_dict({"symbol": "XRP/USDT:USDT", "price": 0.52, "action": "NO_TRADE"})
        assert result == {"symbol": "XRP/USDT:USDT", "price": 0.52, "action": "NO_TRADE"}

    def test_nested_fake_key_redacted(self) -> None:
        result = redact_dict({"meta": {"api_key": "sk-fake", "symbol": "DOGE"}})
        assert result["meta"]["api_key"] == "***REDACTED***"
        assert result["meta"]["symbol"] == "DOGE"
