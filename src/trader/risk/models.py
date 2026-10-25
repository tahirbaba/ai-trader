"""Phase 5: Pydantic models for RiskGate I/O.

Pure data models — no SQLAlchemy, no I/O.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DecisionInput(BaseModel):
    """What the brain produced (after LessonFilter penalty applied to confidence)."""

    action: str  # LONG | SHORT | CLOSE | HOLD | NO_TRADE
    symbol: str
    side: str  # long | short | close | hold | no_trade
    entry_price: float
    stop_loss: float
    take_profit: float  # single TP only (Phase 5)
    confidence: float  # 0-1; after LessonFilter penalty applied
    confluence_score: int
    entry_type: str = "limit"  # market | limit


class AccountState(BaseModel):
    """Current account state at time of RiskGate evaluation."""

    equity_usd: float
    daily_loss_usd: float  # realized losses today (positive = loss)
    weekly_loss_usd: float
    consecutive_losses: int
    last_loss_ts: datetime | None
    open_positions: int
    trades_today: int
    llm_cost_today_usd: float


class MarketState(BaseModel):
    """Current market state at time of RiskGate evaluation."""

    symbol: str
    live_price: float  # freshest price (for entry-drift check)
    snapshot_price: float  # price at time of brain decision
    atr_14: float | None
    spread_pct: float | None
    funding_rate: float | None
    next_funding_ts: datetime | None
    data_age_seconds: float  # seconds since last data update
    clock_drift_ms: float
    is_macro_blackout: bool
    macro_blackout_name: str
    is_exchange_blackout: bool
    exchange_blackout_reason: str


class LessonCheckInput(BaseModel):
    """Result from LessonFilter passed into RiskGate."""

    blocked: bool
    confidence_penalty: float


class RiskVerdict(BaseModel):
    """Output of RiskGate.evaluate()."""

    approved: bool
    adjusted_size: float | None  # qty in base asset (e.g. XRP), rounded down to lot_size
    rules_failed: list[str]  # human-readable rule names that fired
    loss_at_stop_usd: float | None
    liq_price: float | None
    entry_drift_pct: float | None
    reject_reason: str  # short summary if not approved
