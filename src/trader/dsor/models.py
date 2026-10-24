"""Phase 1: SQLAlchemy ORM models for all DSOR tables (BUILD_PLAN.md §3)."""

from __future__ import annotations

import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from trader.dsor.base import Base


def _make_append_only(cls: type) -> type:
    """Register ORM events that forbid UPDATE and DELETE on *cls*."""

    @event.listens_for(cls, "before_update")
    def _no_update(mapper: object, connection: object, target: object) -> None:
        raise RuntimeError(f"{cls.__name__} is append-only; UPDATE is forbidden")

    @event.listens_for(cls, "before_delete")
    def _no_delete(mapper: object, connection: object, target: object) -> None:
        raise RuntimeError(f"{cls.__name__} is append-only; DELETE is forbidden")

    return cls


def _utcnow() -> datetime.datetime:
    return datetime.datetime.utcnow()


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    ohlcv_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    indicators_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    funding: Mapped[float | None] = mapped_column(Float, nullable=True)
    open_interest: Mapped[float | None] = mapped_column(Float, nullable=True)
    basis: Mapped[float | None] = mapped_column(Float, nullable=True)
    orderflow_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    cross_asset_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    regime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    session: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confluence_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data_quality_flags: Mapped[str | None] = mapped_column(Text, nullable=True)
    features_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_published: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    ts_ingested: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    impact: Mapped[str | None] = mapped_column(String(16), nullable=True)
    symbols: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    injection_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class MacroEvent(Base):
    __tablename__ = "macro_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str | None] = mapped_column(String(32), nullable=True)
    importance: Mapped[str | None] = mapped_column(String(16), nullable=True)
    actual: Mapped[str | None] = mapped_column(String(64), nullable=True)
    forecast: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous: Mapped[str | None] = mapped_column(String(64), nullable=True)


@_make_append_only
class TvAlert(Base):
    __tablename__ = "tv_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_received: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    alert_name: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    signal: Mapped[str] = mapped_column(String(32), nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dedupe_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    used_in_decision_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("decisions.id"), nullable=True
    )


class AnalystReport(Base):
    __tablename__ = "analyst_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    agent: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("market_snapshots.id"), nullable=True
    )
    report_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)


@_make_append_only
class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True)
    snapshot_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("market_snapshots.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confluence_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    lessons_considered: Mapped[str | None] = mapped_column(Text, nullable=True)
    tv_alert_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class LessonCheck(Base):
    __tablename__ = "lesson_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[int] = mapped_column(Integer, ForeignKey("decisions.id"), nullable=False)
    lessons_matched_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confidence_penalty: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class RiskCheck(Base):
    __tablename__ = "risk_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[int] = mapped_column(Integer, ForeignKey("decisions.id"), nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rules_failed_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    adjusted_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    loss_at_stop_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    liq_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_drift_pct: Mapped[float | None] = mapped_column(Float, nullable=True)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("decisions.id"), nullable=True
    )
    client_order_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="paper")
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    trigger_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    ts: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    ttl_ts: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("orders.id"), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    fee: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("decisions.id"), nullable=True
    )
    open_ts: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    close_ts: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    entry: Mapped[float] = mapped_column(Float, nullable=False)
    exit: Mapped[float | None] = mapped_column(Float, nullable=True)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    pnl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    funding_paid: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_adverse_excursion: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_favorable_excursion: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ShadowOutcome(Base):
    __tablename__ = "shadow_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[int] = mapped_column(Integer, ForeignKey("decisions.id"), nullable=False)
    hypothetical_side: Mapped[str] = mapped_column(String(8), nullable=False)
    entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    horizon: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blocked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(Integer, ForeignKey("trades.id"), nullable=False)
    outcome_class: Mapped[str] = mapped_column(String(64), nullable=False)
    mistake_types_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    what_went_right: Mapped[str | None] = mapped_column(Text, nullable=True)
    what_went_wrong: Mapped[str | None] = mapped_column(Text, nullable=True)
    lesson_candidates_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_model: Mapped[str | None] = mapped_column(String(64), nullable=True)


class LessonIndex(Base):
    __tablename__ = "lessons_index"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lesson_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="candidate")
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    regime: Mapped[str | None] = mapped_column(String(64), nullable=True)
    setup: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_trade_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    win_stats: Mapped[str | None] = mapped_column(Text, nullable=True)
    block_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_shadow_r: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_ts: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    last_validated_ts: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    expires_ts: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)


class BaselineRun(Base):
    __tablename__ = "baseline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expectancy_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_dd: Mapped[float | None] = mapped_column(Float, nullable=True)


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SystemState(Base):
    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_ts: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class ConfigAudit(Base):
    __tablename__ = "config_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    file: Mapped[str] = mapped_column(String(128), nullable=False)
    git_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    diff: Mapped[str | None] = mapped_column(Text, nullable=True)
