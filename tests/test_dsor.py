"""Tests for Phase 1: DSOR models, migrations, repositories, system_state."""

import datetime
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trader.dsor.base import Base
from trader.dsor.models import (
    ConfigAudit,
    Decision,
    MarketSnapshot,
    NewsItem,
    Order,
    RiskEvent,
)
from trader.dsor.repositories import (
    ConfigAuditRepository,
    DecisionRepository,
    MarketSnapshotRepository,
    NewsItemRepository,
    OrderRepository,
    RiskEventRepository,
    SystemStateRepository,
)
from trader.dsor.system_state import SystemStateHelper

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s


# ---------------------------------------------------------------------------
# Migration: SQLite always runs; Postgres skipped without TEST_DATABASE_URL
# ---------------------------------------------------------------------------


class TestMigrationSQLite:
    def test_all_tables_created(self, engine) -> None:
        from sqlalchemy import inspect

        insp = inspect(engine)
        tables = set(insp.get_table_names())
        expected = {
            "market_snapshots",
            "news_items",
            "macro_events",
            "tv_alerts",
            "analyst_reports",
            "decisions",
            "lesson_checks",
            "risk_checks",
            "orders",
            "fills",
            "trades",
            "shadow_outcomes",
            "reviews",
            "lessons_index",
            "baseline_runs",
            "risk_events",
            "llm_calls",
            "system_state",
            "config_audit",
        }
        missing = expected - tables
        assert not missing, f"Missing tables: {missing}"


class TestMigrationPostgres:
    def test_postgres_migration(self) -> None:
        db_url = os.environ.get("TEST_DATABASE_URL")
        if not db_url:
            pytest.skip("TEST_DATABASE_URL not set — skipping Postgres migration test")
        if "postgres" not in db_url.lower():
            pytest.skip("TEST_DATABASE_URL does not point to Postgres")
        from sqlalchemy import inspect

        engine = create_engine(db_url, echo=False)
        Base.metadata.create_all(engine)
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        expected = {"market_snapshots", "decisions", "orders", "risk_checks"}
        missing = expected - tables
        engine.dispose()
        assert not missing, f"Missing tables on Postgres: {missing}"


# ---------------------------------------------------------------------------
# Repository CRUD
# ---------------------------------------------------------------------------


class TestMarketSnapshotRepository:
    def test_add_and_get(self, session: Session) -> None:
        repo = MarketSnapshotRepository(session)
        ts = datetime.datetime.utcnow()
        snap = MarketSnapshot(
            ts=ts,
            symbol="XRP/USDT:USDT",
            timeframe="15m",
            ohlcv_json='{"open":0.5}',
            indicators_json="{}",
            confluence_score=3,
        )
        repo.add(snap)
        session.commit()
        fetched = repo.get_by_id(snap.id)
        assert fetched is not None
        assert fetched.symbol == "XRP/USDT:USDT"

    def test_get_latest_for_symbol(self, session: Session) -> None:
        repo = MarketSnapshotRepository(session)
        base_ts = datetime.datetime(2026, 1, 1, 12, 0, 0)
        for i in range(3):
            repo.add(
                MarketSnapshot(
                    ts=base_ts + datetime.timedelta(minutes=i * 15),
                    symbol="DOGE/USDT:USDT",
                    timeframe="15m",
                    ohlcv_json="{}",
                    indicators_json="{}",
                    confluence_score=2,
                )
            )
        session.commit()
        latest = repo.get_latest_for_symbol("DOGE/USDT:USDT", "15m")
        assert latest is not None
        assert latest.ts == base_ts + datetime.timedelta(minutes=30)


class TestDecisionRepository:
    def test_add_no_trade(self, session: Session) -> None:
        repo = DecisionRepository(session)
        d = Decision(
            ts=datetime.datetime.utcnow(),
            action="NO_TRADE",
            symbol="XRP/USDT:USDT",
            confidence=0.0,
            confluence_score=0,
            parse_ok=True,
        )
        repo.add(d)
        session.commit()
        fetched = repo.get_by_id(d.id)
        assert fetched is not None
        assert fetched.action == "NO_TRADE"

    def test_get_recent(self, session: Session) -> None:
        repo = DecisionRepository(session)
        for i in range(5):
            repo.add(
                Decision(
                    ts=datetime.datetime(2026, 1, 1, i, 0, 0),
                    action="NO_TRADE",
                    symbol="XRP/USDT:USDT",
                    confidence=0.0,
                    confluence_score=0,
                    parse_ok=True,
                )
            )
        session.commit()
        recent = repo.get_recent(3)
        assert len(recent) == 3


class TestNewsItemRepository:
    def test_add_and_get_by_dedupe_hash(self, session: Session) -> None:
        repo = NewsItemRepository(session)
        item = NewsItem(
            ts_published=datetime.datetime.utcnow(),
            ts_ingested=datetime.datetime.utcnow(),
            source="cryptopanic",
            title="BTC pumps",
            dedupe_hash="abc123",
        )
        repo.add(item)
        session.commit()
        found = repo.get_by_dedupe_hash("abc123")
        assert found is not None
        assert found.title == "BTC pumps"
        assert repo.get_by_dedupe_hash("nonexistent") is None


class TestRiskEventRepository:
    def test_add_and_list(self, session: Session) -> None:
        repo = RiskEventRepository(session)
        repo.add(
            RiskEvent(
                ts=datetime.datetime.utcnow(),
                type="cap_breach_attempt",
                details="loss exceeds limit",
            )
        )
        session.commit()
        events = repo.list_recent(10)
        assert len(events) == 1
        assert events[0].type == "cap_breach_attempt"


class TestOrderRepository:
    def test_add_and_get_open_orders(self, session: Session) -> None:
        repo = OrderRepository(session)
        repo.add(
            Order(
                ts=datetime.datetime.utcnow(),
                client_order_id="CL001",
                mode="paper",
                type="limit",
                side="buy",
                reduce_only=False,
                qty=100.0,
                price=0.5,
                status="open",
                symbol="XRP/USDT:USDT",
            )
        )
        session.commit()
        open_orders = repo.get_open_orders()
        assert len(open_orders) == 1


class TestSystemStateRepository:
    def test_set_and_get(self, session: Session) -> None:
        repo = SystemStateRepository(session)
        repo.set("trading_mode", "paper")
        session.commit()
        assert repo.get("trading_mode") == "paper"

    def test_get_missing_returns_none(self, session: Session) -> None:
        repo = SystemStateRepository(session)
        assert repo.get("nonexistent_key") is None


class TestConfigAuditRepository:
    def test_record_and_list(self, session: Session) -> None:
        repo = ConfigAuditRepository(session)
        repo.add(
            ConfigAudit(
                ts=datetime.datetime.utcnow(),
                file="config/risk.yaml",
                git_sha="deadbeef",
                diff="{}",
            )
        )
        session.commit()
        records = repo.list_recent(5)
        assert len(records) == 1
        assert records[0].file == "config/risk.yaml"


# ---------------------------------------------------------------------------
# system_state helpers
# ---------------------------------------------------------------------------


class TestSystemStateHelper:
    def test_mode_default_paper(self, session: Session) -> None:
        helper = SystemStateHelper(session)
        assert helper.get_mode() == "paper"

    def test_set_mode(self, session: Session) -> None:
        helper = SystemStateHelper(session)
        helper.set_mode("paper")
        session.commit()
        assert helper.get_mode() == "paper"

    def test_set_mode_live_rejected(self, session: Session) -> None:
        helper = SystemStateHelper(session)
        with pytest.raises(ValueError, match="rejected"):
            helper.set_mode("live")

    def test_kill_switch_default_off(self, session: Session) -> None:
        helper = SystemStateHelper(session)
        assert not helper.is_kill_switch_on()

    def test_kill_switch_on(self, session: Session) -> None:
        helper = SystemStateHelper(session)
        helper.set_kill_switch(True)
        session.commit()
        assert helper.is_kill_switch_on()

    def test_kill_switch_off(self, session: Session) -> None:
        helper = SystemStateHelper(session)
        helper.set_kill_switch(True)
        helper.set_kill_switch(False)
        session.commit()
        assert not helper.is_kill_switch_on()

    def test_daily_loss_increment(self, session: Session) -> None:
        helper = SystemStateHelper(session)
        helper.increment_daily_loss(0.50)
        helper.increment_daily_loss(0.25)
        session.commit()
        assert helper.get_daily_loss() == pytest.approx(0.75)

    def test_reset_daily_counters(self, session: Session) -> None:
        helper = SystemStateHelper(session)
        helper.increment_daily_loss(2.0)
        helper.reset_daily_counters()
        session.commit()
        assert helper.get_daily_loss() == 0.0
