"""Tests for Phase 1: Pydantic settings and config validation."""

from pathlib import Path

import pytest
import yaml

from trader.config.settings import RiskSettings, load_settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.dump(data))


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
# RiskSettings cross-field validators
# ---------------------------------------------------------------------------

class TestRiskSettingsCrossValidation:
    def test_max_loss_exceeds_margin_rejected(self, tmp_path: Path) -> None:
        risk = _minimal_risk()
        risk["max_loss_per_trade_usd"] = 3.0  # > max_margin=2
        with pytest.raises(Exception, match="max_loss_per_trade_usd"):
            RiskSettings(**risk)

    def test_daily_loss_less_than_per_trade_rejected(self, tmp_path: Path) -> None:
        risk = _minimal_risk()
        risk["daily_loss_limit_usd"] = 0.5  # < max_loss=1
        with pytest.raises(Exception, match="daily_loss_limit_usd"):
            RiskSettings(**risk)

    def test_margin_mode_not_isolated_rejected(self, tmp_path: Path) -> None:
        risk = _minimal_risk()
        risk["margin_mode"] = "cross"
        with pytest.raises(Exception, match="margin_mode"):
            RiskSettings(**risk)

    def test_valid_risk_settings_accepted(self) -> None:
        risk = _minimal_risk()
        r = RiskSettings(**risk)
        assert r.max_loss_per_trade_usd == 1.0


# ---------------------------------------------------------------------------
# Settings: live mode without approval file
# ---------------------------------------------------------------------------

class TestLiveModeGuard:
    def test_live_mode_without_approval_file_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRADING_MODE", "live")
        risk_path = tmp_path / "risk.yaml"
        _write_yaml(risk_path, _minimal_risk())
        app_path = tmp_path / "app.yaml"
        _write_yaml(app_path, _minimal_app())
        # No live_approval.yaml present
        with pytest.raises(Exception, match="live_approval"):
            load_settings(
                app_yaml=app_path,
                risk_yaml=risk_path,
                live_approval_yaml=tmp_path / "live_approval.yaml",
                env_trading_mode="live",
            )

    def test_paper_mode_starts_fine(self, tmp_path: Path) -> None:
        risk_path = tmp_path / "risk.yaml"
        _write_yaml(risk_path, _minimal_risk())
        app_path = tmp_path / "app.yaml"
        _write_yaml(app_path, _minimal_app())
        s = load_settings(
            app_yaml=app_path,
            risk_yaml=risk_path,
            live_approval_yaml=tmp_path / "live_approval.yaml",
            env_trading_mode="paper",
        )
        assert s.trading_mode == "paper"

    def test_live_mode_with_approval_file_starts(self, tmp_path: Path) -> None:
        risk_path = tmp_path / "risk.yaml"
        _write_yaml(risk_path, _minimal_risk())
        app_path = tmp_path / "app.yaml"
        _write_yaml(app_path, _minimal_app())
        approval = tmp_path / "live_approval.yaml"
        approval.write_text("approved: true\n")
        s = load_settings(
            app_yaml=app_path,
            risk_yaml=risk_path,
            live_approval_yaml=approval,
            env_trading_mode="live",
        )
        assert s.trading_mode == "live"


# ---------------------------------------------------------------------------
# Model names come from config, not hardcoded
# ---------------------------------------------------------------------------

class TestModelNamesFromConfig:
    def test_model_names_are_configurable(self, tmp_path: Path) -> None:
        risk_path = tmp_path / "risk.yaml"
        _write_yaml(risk_path, _minimal_risk())
        app_data = _minimal_app()
        app_data["models"]["decision"] = "claude-opus-4-7"
        app_path = tmp_path / "app.yaml"
        _write_yaml(app_path, app_data)
        s = load_settings(
            app_yaml=app_path,
            risk_yaml=risk_path,
            live_approval_yaml=tmp_path / "live_approval.yaml",
            env_trading_mode="paper",
        )
        assert s.app.models.decision == "claude-opus-4-7"
