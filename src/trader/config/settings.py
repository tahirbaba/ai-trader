"""Phase 1: Typed configuration loading from .env + app.yaml + risk.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, model_validator

# ---------------------------------------------------------------------------
# Sub-models for app.yaml
# ---------------------------------------------------------------------------


class ModelNames(BaseModel):
    screening: str
    decision: str
    review: str


class TradingConfig(BaseModel):
    symbols: list[str] = []
    timeframes: list[str] = ["15m", "1h", "4h"]
    session_timezone_display: list[str] = ["UTC"]


class FeatureFlags(BaseModel):
    tradingview_webhook: bool = False
    sentiment_analyst: bool = False
    chart_image_analysis: bool = False
    onchain_data: bool = False


class SchedulingConfig(BaseModel):
    market_data_interval_seconds: int = 5
    brain_cycle_interval_seconds: int = 60
    position_review_interval_seconds: int = 30
    reconciliation_interval_seconds: int = 60
    clock_check_interval_seconds: int = 3600


class ApiConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"


class AppSettings(BaseModel):
    trading: TradingConfig = TradingConfig()
    models: ModelNames
    feature_flags: FeatureFlags = FeatureFlags()
    scheduling: SchedulingConfig = SchedulingConfig()
    api: ApiConfig = ApiConfig()
    logging: LoggingConfig = LoggingConfig()


# ---------------------------------------------------------------------------
# RiskSettings (from risk.yaml)
# ---------------------------------------------------------------------------


class PriceShockSettings(BaseModel):
    min_signals_to_trigger: int = 2
    extreme_5m_return_pct: float = 2.5
    block_minutes: int = 30
    price_1m_return_pct: float = 0.5
    price_5m_return_pct: float = 1.0
    atr_spike_multiple: float = 2.0
    volume_spike_multiple: float = 3.0
    spread_spike_pct: float = 0.3
    liquidation_spike_multiple: float = 5.0
    open_position_risk_cut_pct: float = 50.0
    move_sl_to_breakeven_on_favorable_shock: bool = True


class RiskSettings(BaseModel):
    mode_default: str = "paper"
    max_margin_per_trade_usd: float
    max_loss_per_trade_usd: float
    slippage_buffer_pct: float = 0.05
    max_leverage: int = 3
    margin_mode: str = "isolated"
    position_mode: str = "one_way"
    require_stop_loss: bool = True
    require_exchange_side_sl_tp: bool = True
    liq_buffer_multiple: float = 2.0
    min_stop_atr_multiple: float = 0.5
    max_stop_atr_multiple: float = 3.0
    min_risk_reward: float = 1.5
    max_entry_drift_pct: float = 0.15
    limit_order_ttl_seconds: int = 300
    max_holding_minutes: int = 720
    avoid_funding_window_minutes: int = 10
    max_open_positions: int = 1
    max_trades_per_day: int = 6
    daily_loss_limit_usd: float
    weekly_loss_limit_usd: float = 6.0
    equity_floor_usd: float = 5.0
    cooldown_after_loss_minutes: int = 60
    max_consecutive_losses: int = 3
    news_blackout_minutes_before: int = 30
    news_blackout_minutes_after: int = 30
    max_data_age_seconds: int = 60
    max_clock_drift_ms: int = 1000
    max_spread_pct: float = 0.1
    min_confidence: float = 0.7
    min_confluence_score: int = 3
    allowed_symbols: list[str] = []
    total_llm_budget_usd_per_day: float = 5.0
    price_shock: PriceShockSettings = PriceShockSettings()

    @model_validator(mode="after")
    def _cross_validate(self) -> RiskSettings:
        if self.max_loss_per_trade_usd > self.max_margin_per_trade_usd:
            raise ValueError(
                f"max_loss_per_trade_usd ({self.max_loss_per_trade_usd}) must not exceed "
                f"max_margin_per_trade_usd ({self.max_margin_per_trade_usd})"
            )
        if self.daily_loss_limit_usd < self.max_loss_per_trade_usd:
            raise ValueError(
                f"daily_loss_limit_usd ({self.daily_loss_limit_usd}) must be >= "
                f"max_loss_per_trade_usd ({self.max_loss_per_trade_usd})"
            )
        if self.margin_mode != "isolated":
            raise ValueError(
                f"margin_mode must be 'isolated', got '{self.margin_mode}'. "
                "Cross margin is forbidden."
            )
        return self


# ---------------------------------------------------------------------------
# Top-level Settings container
# ---------------------------------------------------------------------------


class Settings(BaseModel):
    trading_mode: str
    app: AppSettings
    risk: RiskSettings


def load_settings(
    *,
    app_yaml: Path,
    risk_yaml: Path,
    live_approval_yaml: Path,
    env_trading_mode: str = "paper",
) -> Settings:
    """Load and validate all configuration. Raises ValueError on any violation."""
    with open(app_yaml) as f:
        app_data: dict[str, Any] = yaml.safe_load(f) or {}
    with open(risk_yaml) as f:
        risk_data: dict[str, Any] = yaml.safe_load(f) or {}

    app = AppSettings(**app_data)
    risk = RiskSettings(**risk_data)

    trading_mode = env_trading_mode.lower().strip()
    if trading_mode == "live" and not live_approval_yaml.exists():
        raise ValueError(
            f"TRADING_MODE=live requires config/live_approval.yaml to exist. "
            f"Expected at: {live_approval_yaml}"
        )

    return Settings(trading_mode=trading_mode, app=app, risk=risk)


# ---------------------------------------------------------------------------
# Default loader: uses repo-relative config paths and TRADING_MODE env var
# ---------------------------------------------------------------------------


def get_default_settings(repo_root: Path | None = None) -> Settings:
    import os

    if repo_root is None:
        repo_root = Path(__file__).parent.parent.parent.parent  # src/trader/config -> repo root

    return load_settings(
        app_yaml=repo_root / "config" / "app.yaml",
        risk_yaml=repo_root / "config" / "risk.yaml",
        live_approval_yaml=repo_root / "config" / "live_approval.yaml",
        env_trading_mode=os.environ.get("TRADING_MODE", "paper"),
    )
