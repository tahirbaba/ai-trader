"""Phase 1: Typed repository classes for DSOR tables."""

from __future__ import annotations

import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from trader.dsor.models import (
    ConfigAudit,
    Decision,
    LessonIndex,
    LlmCall,
    MarketSnapshot,
    NewsItem,
    Order,
    RiskEvent,
    SystemState,
    Trade,
)


class _BaseRepository[T]:
    """Minimal generic base; each subclass handles one mapped class."""

    _model: type

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, obj: T) -> T:
        self._session.add(obj)
        return obj

    def get_by_id(self, id: int) -> T | None:  # noqa: A002
        return self._session.get(self._model, id)


class MarketSnapshotRepository(_BaseRepository[MarketSnapshot]):
    _model = MarketSnapshot

    def get_latest_for_symbol(self, symbol: str, timeframe: str) -> MarketSnapshot | None:
        stmt = (
            select(MarketSnapshot)
            .where(
                MarketSnapshot.symbol == symbol,
                MarketSnapshot.timeframe == timeframe,
            )
            .order_by(desc(MarketSnapshot.ts))
            .limit(1)
        )
        return self._session.scalar(stmt)


class DecisionRepository(_BaseRepository[Decision]):
    _model = Decision

    def get_recent(self, n: int) -> list[Decision]:
        stmt = select(Decision).order_by(desc(Decision.ts)).limit(n)
        return list(self._session.scalars(stmt))

    def get_by_symbol_since(self, symbol: str, since: datetime.datetime) -> list[Decision]:
        stmt = (
            select(Decision)
            .where(Decision.symbol == symbol, Decision.ts >= since)
            .order_by(desc(Decision.ts))
        )
        return list(self._session.scalars(stmt))


class NewsItemRepository(_BaseRepository[NewsItem]):
    _model = NewsItem

    def get_by_dedupe_hash(self, hash_: str) -> NewsItem | None:
        stmt = select(NewsItem).where(NewsItem.dedupe_hash == hash_)
        return self._session.scalar(stmt)

    def get_recent_uninjected(self, n: int) -> list[NewsItem]:
        stmt = (
            select(NewsItem)
            .where(NewsItem.injection_flag.is_(False))
            .order_by(desc(NewsItem.ts_ingested))
            .limit(n)
        )
        return list(self._session.scalars(stmt))


class RiskEventRepository(_BaseRepository[RiskEvent]):
    _model = RiskEvent

    def list_recent(self, n: int) -> list[RiskEvent]:
        stmt = select(RiskEvent).order_by(desc(RiskEvent.ts)).limit(n)
        return list(self._session.scalars(stmt))


class OrderRepository(_BaseRepository[Order]):
    _model = Order

    def get_by_client_order_id(self, client_order_id: str) -> Order | None:
        stmt = select(Order).where(Order.client_order_id == client_order_id)
        return self._session.scalar(stmt)

    def get_open_orders(self) -> list[Order]:
        stmt = select(Order).where(Order.status == "open")
        return list(self._session.scalars(stmt))


class SystemStateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, key: str) -> str | None:
        obj = self._session.get(SystemState, key)
        return obj.value if obj is not None else None

    def set(self, key: str, value: str) -> None:
        obj = self._session.get(SystemState, key)
        if obj is None:
            obj = SystemState(key=key, value=value, updated_ts=datetime.datetime.now(datetime.UTC))
            self._session.add(obj)
        else:
            obj.value = value
            obj.updated_ts = datetime.datetime.now(datetime.UTC)


class ConfigAuditRepository(_BaseRepository[ConfigAudit]):
    _model = ConfigAudit

    def list_recent(self, n: int) -> list[ConfigAudit]:
        stmt = select(ConfigAudit).order_by(desc(ConfigAudit.ts)).limit(n)
        return list(self._session.scalars(stmt))


class LlmCallRepository(_BaseRepository[LlmCall]):
    _model = LlmCall

    def total_cost_since(self, since: datetime.datetime) -> float:
        from sqlalchemy import func

        stmt = select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(LlmCall.ts >= since)
        result = self._session.scalar(stmt)
        return float(result or 0.0)


class TradeRepository(_BaseRepository[Trade]):
    _model = Trade

    def get_open_trades(self) -> list[Trade]:
        stmt = select(Trade).where(Trade.close_ts.is_(None))
        return list(self._session.scalars(stmt))


class LessonIndexRepository(_BaseRepository[LessonIndex]):
    _model = LessonIndex

    def get_by_lesson_id(self, lesson_id: str) -> LessonIndex | None:
        stmt = select(LessonIndex).where(LessonIndex.lesson_id == lesson_id)
        return self._session.scalar(stmt)

    def get_active(self) -> list[LessonIndex]:
        stmt = select(LessonIndex).where(LessonIndex.status == "active")
        return list(self._session.scalars(stmt))
