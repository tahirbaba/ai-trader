"""Phase 5: Pydantic models for RiskGate I/O.

Pure data models — no SQLAlchemy, no I/O.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class DecisionInput(BaseModel):
    """What the brain produced (after LessonFilter penalty applied to confidence)."""

    action: str  # LONG | SHORT | CLOSE | HOLD | NO_TRADE
    symbol: str
    side: str  # long | short | close | hold | no_trade
    entry_price: Annotated[float, Field(gt=0)]
    stop_loss: Annotated[float, Field(ge=0)]
    take_profit: Annotated[float, Field(ge=0)]
    confidence: float  # 0-1; after LessonFilter penalty applied
    confluence_score: int
    entry_type: str = "limit"  # market | limit

    @field_validator("entry_price", "stop_loss", "take_profit", mode="after")
    @classmethod
    def _must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError(f"price fields must be finite, got {v}")
        return v


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
    live_price: Annotated[float, Field(gt=0)]
    snapshot_price: Annotated[float, Field(gt=0)]  # must be positive; zero treated as stale
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
    # News no-chase circuit breaker: set when a high-impact news item arrived
    # and price has already moved > no_chase_atr_multiplier * ATR in that direction.
    # The Risk Gate blocks new entries in that direction when this is True.
    news_chase_block: bool = False
    news_chase_direction: str = ""  # "long" | "short" | ""


class LessonCheckInput(BaseModel):
    """Result from LessonFilter passed into RiskGate."""

    blocked: bool
    confidence_penalty: Annotated[float, Field(ge=0.0, le=1.0)]


class RiskVerdict(BaseModel):
    """Output of RiskGate.evaluate()."""

    approved: bool
    adjusted_size: float | None  # qty in base asset (e.g. XRP), rounded down to lot_size
    rules_failed: list[str]  # human-readable rule names that fired
    loss_at_stop_usd: float | None
    liq_price: float | None
    entry_drift_pct: float | None
    reject_reason: str  # short summary if not approved
