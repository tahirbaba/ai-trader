"""Phase 9: Ops, Monitoring, Control — scheduler, Telegram, kill switch, heartbeat,
dashboard, backups, runtime wiring.

DoD covered here:
  * /mode cannot enable live
  * kill switch closes paper positions within seconds (Telegram, API, file flag)
  * unauthorized Telegram chats are ignored
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trader.agents.orchestrator import CycleResult
from trader.config.settings import Settings, get_default_settings
from trader.dashboard.app import create_app
from trader.dsor.base import Base
from trader.dsor.models import Decision, LessonIndex, Review, RiskEvent, ShadowOutcome, Trade
from trader.dsor.system_state import SystemStateHelper
from trader.execution.order_manager import OrderManager
from trader.execution.paper_broker import PaperBroker
from trader.llm.schemas import BrainDecision
from trader.ops.alerts import Notifier, clean_text
from trader.ops.backup import backup_sqlite, sqlite_path_from_url
from trader.ops.control import Controller
from trader.ops.heartbeat import Heartbeat
from trader.ops.kill_switch import KillSwitch
from trader.ops.market_feed import to_exchange_symbol
from trader.ops.runtime import TradingRuntime
from trader.ops.scheduler import Job, Scheduler, seconds_until_daily
from trader.ops.shadow import shadow_outcome
from trader.ops.telegram_bot import TelegramBot
from trader.ops.trade_ledger import TradeLedger
from trader.risk.models import RiskVerdict

REPO_ROOT = Path(__file__).parent.parent
XRP = "XRP/USDT:USDT"
DOGE = "DOGE/USDT:USDT"
OWNER_CHAT = 424242


def _run(coro: Any) -> Any:
    """Run *coro* on a private loop. asyncio.run() would leave no current event
    loop behind, breaking later tests that call asyncio.get_event_loop()."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


@pytest.fixture()
def sf() -> Any:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def settings() -> Settings:
    return get_default_settings(REPO_ROOT)


class FakeTransport:
    def __init__(self, updates: list[dict[str, Any]] | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self._updates = updates or []

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        out, self._updates = self._updates, []
        return out

    def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class FakeFeed:
    def __init__(self, marks: dict[str, float] | None = None) -> None:
        self.marks = marks or {XRP: 0.5, DOGE: 0.1}
        self.builds: list[str] = []
        self.candle_rows: list[dict[str, Any]] = []

    def build(self, symbol: str) -> dict[str, Any]:
        self.builds.append(symbol)
        return {"confluence_score": 5, "live_price": self.marks[symbol], "data_age_seconds": 0.1}

    def mark_price(self, symbol: str) -> float | None:
        return self.marks.get(symbol)

    def candles(self, symbol: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
        return self.candle_rows


class FakeOrchestrator:
    def __init__(self, result_fn: Any) -> None:
        self._fn = result_fn
        self.calls: list[dict[str, Any]] = []

    async def run_cycle(self, **kw: Any) -> CycleResult:
        self.calls.append(kw)
        res: CycleResult = self._fn(kw)
        return res


def _approved_long(kw: dict[str, Any]) -> CycleResult:
    sym = kw["symbol"]
    decision = BrainDecision(
        action="LONG",
        symbol=sym,
        entry_type="market",
        entry_price=0.5,
        stop_loss=0.49,
        take_profit=0.53,
        confidence=0.8,
        thesis="test thesis",
    )
    verdict = RiskVerdict(
        approved=True,
        adjusted_size=10.0,
        rules_failed=[],
        loss_at_stop_usd=0.1,
        liq_price=0.3,
        entry_drift_pct=0.0,
        reject_reason="",
    )
    return CycleResult(
        cycle_id="c" * 16,
        symbol=sym,
        trigger="scheduled",
        action="LONG",
        approved=True,
        stopped_at="completed",
        decision=decision,
        risk_verdict=verdict,
    )


def _rejected_long(kw: dict[str, Any]) -> CycleResult:
    res = _approved_long(kw)
    return res.model_copy(
        update={"approved": False, "action": "NO_TRADE", "stopped_at": "risk_gate:rr_too_low"}
    )


class Stack:
    """Real PaperBroker/OrderManager/Controller/KillSwitch around an in-memory DB."""

    def __init__(self, sf: Any, settings: Settings, tmp_path: Path, orch: Any = None) -> None:
        self.sf = sf
        self.settings = settings
        self.broker = PaperBroker(initial_balance=50.0)
        self.om = OrderManager(self.broker, settings, sf)
        self.ks = KillSwitch(sf, tmp_path / "KILL_SWITCH")
        self.sent: list[str] = []
        self.notifier = Notifier(send=self.sent.append)
        self.ledger = TradeLedger(sf)
        self.controller = Controller(
            settings,
            sf,
            self.broker,
            self.om,
            self.ks,
            self.notifier,
            on_close=self.ledger.record_exit,
        )
        self.feed = FakeFeed()
        self.orch = orch or FakeOrchestrator(_approved_long)
        self.runtime = TradingRuntime(
            settings=settings,
            session_factory=sf,
            orchestrator=self.orch,
            order_manager=self.om,
            broker=self.broker,
            feed=self.feed,
            controller=self.controller,
            notifier=self.notifier,
            ledger=self.ledger,
            symbols=[XRP],
        )

    def open_paper_position(self, symbol: str = XRP, qty: float = 10.0) -> None:
        self.broker.update_mark_price(symbol, self.feed.marks[symbol])
        self.broker.place_order(
            symbol=symbol,
            side="buy",
            qty=qty,
            order_type="market",
            stop_loss=self.feed.marks[symbol] * 0.98,
            take_profit=self.feed.marks[symbol] * 1.06,
        )
        self.ledger.sync(self.broker.get_positions(), {})


@pytest.fixture()
def stack(sf: Any, settings: Settings, tmp_path: Path) -> Stack:
    return Stack(sf, settings, tmp_path)


def _msg(
    text: str, chat_id: int = OWNER_CHAT, update_id: int = 1, sender: int | None = None
) -> dict[str, Any]:
    frm = {"id": chat_id if sender is None else sender}
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "from": frm, "text": text}}


KILL_HEADERS = {"X-Trader-Action": "kill-switch"}
LOCAL = "http://127.0.0.1"


# ---------------------------------------------------------------------------
# Mode safety (DoD: /mode cannot enable live)
# ---------------------------------------------------------------------------


class TestModeCannotEnableLive:
    @pytest.mark.parametrize("arg", ["live", "LIVE", " live ", "testnet", "real", "paper;live"])
    def test_controller_refuses(self, stack: Stack, arg: str) -> None:
        reply = stack.controller.mode_set(arg)
        assert "Refused" in reply
        with stack.sf() as s:
            assert SystemStateHelper(s).get_mode() == "paper"

    def test_telegram_mode_live_refused(self, stack: Stack) -> None:
        t = FakeTransport()
        bot = TelegramBot(t, OWNER_CHAT, stack.controller)
        reply = bot.handle_update(_msg("/mode live"))
        assert reply is not None and "Refused" in reply
        assert stack.settings.trading_mode == "paper"

    def test_telegram_mode_show_and_downgrade(self, stack: Stack) -> None:
        bot = TelegramBot(FakeTransport(), OWNER_CHAT, stack.controller)
        assert "paper" in (bot.handle_update(_msg("/mode")) or "")
        assert bot.handle_update(_msg("/mode paper")) == "mode: paper"

    def test_main_refuses_non_paper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import argparse

        from trader import main as main_mod

        monkeypatch.setenv("TRADING_MODE", "testnet")
        with pytest.raises(SystemExit, match="PAPER only"):
            main_mod.build(argparse.Namespace())


# ---------------------------------------------------------------------------
# Kill switch (DoD: closes paper positions within seconds)
# ---------------------------------------------------------------------------


class TestKillSwitch:
    def test_telegram_stop_closes_positions_fast(self, stack: Stack) -> None:
        stack.open_paper_position()
        assert stack.broker.get_positions()
        bot = TelegramBot(FakeTransport(), OWNER_CHAT, stack.controller)
        t0 = time.monotonic()
        reply = bot.handle_update(_msg("/stop"))
        elapsed = time.monotonic() - t0
        assert stack.broker.get_positions() == []
        assert elapsed < 2.0
        assert reply is not None and XRP in reply
        assert stack.ks.is_active()

    def test_api_endpoint_closes_positions(self, stack: Stack) -> None:
        stack.open_paper_position()
        app = create_app(stack.sf, stack.controller, stack.feed, [XRP], 50.0)
        r = TestClient(app, base_url=LOCAL).post("/api/kill-switch", headers=KILL_HEADERS)
        assert r.status_code == 200
        assert r.json()["closed"] == [XRP]
        assert stack.broker.get_positions() == []
        assert stack.ks.is_active()

    def test_file_flag_closes_positions_in_monitor(self, stack: Stack) -> None:
        stack.open_paper_position()
        stack.ks.flag_path.write_text("manual\n")
        t0 = time.monotonic()
        stack.runtime.position_monitor_job()
        assert stack.broker.get_positions() == []
        assert time.monotonic() - t0 < 2.0
        with stack.sf() as s:
            t = s.query(Trade).one()
            assert t.close_ts is not None and t.exit_reason == "kill_switch_monitor"

    def test_fail_closed_when_db_unreadable(self, tmp_path: Path) -> None:
        def broken() -> Any:
            raise RuntimeError("db down")

        assert KillSwitch(broken, tmp_path / "K").is_active() is True

    def test_activate_falls_back_to_file_flag(self, tmp_path: Path) -> None:
        def broken() -> Any:
            raise RuntimeError("db down")

        ks = KillSwitch(broken, tmp_path / "K")
        ks.activate("test", "x")
        assert (tmp_path / "K").exists()

    def test_unhalt_does_not_clear_file_flag(self, stack: Stack) -> None:
        stack.ks.flag_path.write_text("manual\n")
        msg = stack.controller.unhalt()
        assert "file flag" in msg.lower()
        assert stack.ks.is_active()
        assert stack.ks.flag_path.exists()

    def test_resume_never_clears_kill_switch(self, stack: Stack) -> None:
        stack.controller.stop(source="test")
        msg = stack.controller.resume()
        assert "still ON" in msg
        assert stack.ks.is_active()

    def test_unhalt_clears_owner_stop(self, stack: Stack) -> None:
        stack.controller.stop(source="telegram")
        assert "cleared" in stack.controller.unhalt()
        assert not stack.ks.is_active()

    @pytest.mark.parametrize(
        ("etype", "details"),
        [
            ("HALT_CLOSE_FAILED", "SL failed and close failed"),
            ("RECONCILE_MISMATCH", "broker has position"),
            ("FORCE_CLOSE_FAILED", "x"),
            ("KILL_SWITCH", "source=reconcile reason=RECONCILE_MISMATCH broker=[]"),
        ],
    )
    def test_unhalt_refused_after_host_only_halt(
        self, stack: Stack, etype: str, details: str
    ) -> None:
        stack.ks.activate("system", "x")
        with stack.sf() as s:
            s.add(RiskEvent(ts=datetime.datetime.now(datetime.UTC), type=etype, details=details))
            s.commit()
        reply = stack.controller.unhalt()
        assert "Refused" in reply and "make unhalt" in reply
        assert stack.ks.is_active()

    def test_reconcile_halt_refuses_unhalt_end_to_end(self, stack: Stack) -> None:
        with stack.sf() as s:
            s.add(Trade(open_ts=datetime.datetime.now(datetime.UTC), side="buy", entry=1, qty=1))
            s.commit()
        stack.runtime.reconcile_job()
        assert "Refused" in stack.controller.unhalt()
        assert stack.ks.is_active()
        # Host-side clear (make unhalt) works and later halts are evaluated afresh.
        stack.ks.clear_db_flag(source="host_make_unhalt")
        assert not stack.ks.is_active()

    def test_stop_records_risk_event_and_alert(self, stack: Stack) -> None:
        stack.controller.stop(source="telegram")
        with stack.sf() as s:
            assert s.query(RiskEvent).filter(RiskEvent.type == "KILL_SWITCH").count() == 1
        assert any("KILL SWITCH" in m for m in stack.sent)


# ---------------------------------------------------------------------------
# Telegram bot
# ---------------------------------------------------------------------------


class TestTelegramBot:
    def test_unauthorized_chat_ignored(self, stack: Stack) -> None:
        t = FakeTransport()
        bot = TelegramBot(t, OWNER_CHAT, stack.controller)
        stack.open_paper_position()
        assert bot.handle_update(_msg("/stop", chat_id=999)) is None
        assert t.sent == []
        assert stack.broker.get_positions()  # nothing closed
        assert not stack.ks.is_active()

    @pytest.mark.parametrize("bad", [None, "424242", 424242.0, {"id": 1}])
    def test_malformed_chat_id_ignored(self, stack: Stack, bad: Any) -> None:
        t = FakeTransport()
        bot = TelegramBot(t, OWNER_CHAT, stack.controller)
        upd = {"update_id": 1, "message": {"chat": {"id": bad}, "text": "/stop"}}
        assert bot.handle_update(upd) is None
        assert not stack.ks.is_active()

    def test_poll_once_advances_offset(self, stack: Stack) -> None:
        t = FakeTransport([_msg("/status", update_id=7), _msg("/pnl", update_id=8)])
        bot = TelegramBot(t, OWNER_CHAT, stack.controller)
        assert bot.poll_once() == 2
        assert bot._offset == 9
        assert len(t.sent) == 2

    def test_group_member_other_than_owner_ignored(self, stack: Stack) -> None:
        t = FakeTransport()
        bot = TelegramBot(t, OWNER_CHAT, stack.controller)
        assert bot.handle_update(_msg("/stop", sender=777)) is None
        assert t.sent == [] and not stack.ks.is_active()

    def test_missing_sender_ignored(self, stack: Stack) -> None:
        t = FakeTransport()
        bot = TelegramBot(t, OWNER_CHAT, stack.controller)
        upd = {"update_id": 1, "message": {"chat": {"id": OWNER_CHAT}, "text": "/stop"}}
        assert bot.handle_update(upd) is None
        assert t.sent == [] and not stack.ks.is_active()

    def test_group_chat_id_rejected_at_construction(self, stack: Stack) -> None:
        with pytest.raises(ValueError):
            TelegramBot(FakeTransport(), -100123, stack.controller)

    def test_transport_errors_never_contain_token(self) -> None:
        import requests

        from trader.ops.telegram_bot import HttpTelegramTransport

        token = "123456:AAE-secretTOKEN_value"

        class Boom:
            def get(self, url: str, **kw: Any) -> Any:
                raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

            def post(self, url: str, **kw: Any) -> Any:
                raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

        tr = HttpTelegramTransport(token, http=Boom())
        for call in (lambda: tr.get_updates(None, 1), lambda: tr.send_message(1, "x")):
            with pytest.raises(RuntimeError) as ei:
                call()
            assert token not in str(ei.value)
            assert ei.value.__cause__ is None and ei.value.__suppress_context__

    def test_job_error_redacts_token_and_urls(self, caplog: Any) -> None:
        sent: list[str] = []
        notifier = Notifier(sent.append)
        sched = Scheduler(on_error=lambda n, e: notifier.job_error(n, e))

        def leak() -> None:
            raise RuntimeError(
                "url: https://api.telegram.org/bot123456:AAE-secret/getUpdates "
                "and https://hc-ping.com/SECRET-UUID bot987:XYZ_tok"
            )

        with caplog.at_level(logging.ERROR):
            _run(sched.run_job_once(Job("telegram_poll", leak, 1)))
        for text in (caplog.text, *sent):
            assert "AAE-secret" not in text and "SECRET-UUID" not in text and "XYZ_tok" not in text

    @pytest.mark.parametrize(
        "cmd",
        ["/status", "/positions", "/pnl", "/lessons", "/pause", "/resume", "/unhalt", "/help"],
    )
    def test_commands_reply(self, stack: Stack, cmd: str) -> None:
        t = FakeTransport()
        reply = TelegramBot(t, OWNER_CHAT, stack.controller).handle_update(_msg(cmd))
        assert reply
        assert t.sent == [(OWNER_CHAT, reply)]

    def test_pause_blocks_entries(self, stack: Stack) -> None:
        bot = TelegramBot(FakeTransport(), OWNER_CHAT, stack.controller)
        bot.handle_update(_msg("/pause"))
        assert stack.controller.entries_blocked() == "paused"
        bot.handle_update(_msg("/resume"))
        assert stack.controller.entries_blocked() is None

    def test_command_with_bot_suffix(self, stack: Stack) -> None:
        reply = TelegramBot(FakeTransport(), OWNER_CHAT, stack.controller).handle_update(
            _msg("/status@my_trader_bot")
        )
        assert reply is not None and "mode: paper" in reply

    def test_confirm_never_acts(self, stack: Stack) -> None:
        reply = TelegramBot(FakeTransport(), OWNER_CHAT, stack.controller).handle_update(
            _msg("/confirm abc")
        )
        assert reply is not None and "Phase 11" in reply

    def test_handler_exception_is_contained(self, stack: Stack, monkeypatch: Any) -> None:
        monkeypatch.setattr(stack.controller, "status", lambda: 1 / 0)
        reply = TelegramBot(FakeTransport(), OWNER_CHAT, stack.controller).handle_update(
            _msg("/status")
        )
        assert reply == "/status failed: ZeroDivisionError"


class TestLessonCommands:
    def _add(self, sf: Any, lesson_id: str, evidence: list[int], rule: dict[str, Any]) -> None:
        with sf() as s:
            s.add(
                LessonIndex(
                    lesson_id=lesson_id,
                    status="candidate",
                    setup="funding extreme short",
                    rule_json=json.dumps(rule),
                    evidence_trade_ids=json.dumps(evidence),
                )
            )
            s.commit()

    def test_approve_with_evidence(self, stack: Stack) -> None:
        self._add(stack.sf, "L-0007", [1, 2], {"action": "block"})
        bot = TelegramBot(FakeTransport(), OWNER_CHAT, stack.controller)
        assert "L-0007" in (bot.handle_update(_msg("/lessons")) or "")
        assert "approved" in (bot.handle_update(_msg("/approve L-0007")) or "")
        with stack.sf() as s:
            assert s.query(LessonIndex).one().status == "active"

    def test_approve_without_evidence_refused(self, stack: Stack) -> None:
        self._add(stack.sf, "L-0008", [1], {"action": "block"})
        reply = stack.controller.approve("L-0008")
        assert "not approved" in reply
        with stack.sf() as s:
            assert s.query(LessonIndex).one().status == "candidate"

    def test_reject(self, stack: Stack) -> None:
        self._add(stack.sf, "L-0009", [], {"action": "block"})
        assert "rejected" in stack.controller.reject("L-0009")
        with stack.sf() as s:
            row = s.query(LessonIndex).one()
            assert row.status == "deprecated"
            assert json.loads(row.win_stats)["deprecation_reason"] == "owner_rejected"

    @pytest.mark.parametrize("bad", ["L-7", "x", "L-0007; DROP", "../L-0007"])
    def test_bad_lesson_id(self, stack: Stack, bad: str) -> None:
        assert stack.controller.approve(bad).startswith("Usage")
        assert stack.controller.reject(bad).startswith("Usage")


# ---------------------------------------------------------------------------
# Runtime wiring
# ---------------------------------------------------------------------------


class TestRuntimeCycle:
    def test_approved_decision_places_paper_order(self, stack: Stack) -> None:
        stack.broker.update_mark_price(XRP, 0.5)
        _run(stack.runtime.trade_cycle_job())
        assert len(stack.broker.get_positions()) == 1
        assert any("Decision LONG" in m and "test thesis" in m for m in stack.sent)
        call = stack.orch.calls[0]
        assert call["feature_pack"]["account_state"]["equity_usd"] == pytest.approx(50.0)

    def test_kill_switch_engaged_after_approval_blocks_order(
        self, sf: Any, settings: Settings, tmp_path: Path
    ) -> None:
        holder: dict[str, Stack] = {}

        def approve_then_kill(kw: dict[str, Any]) -> CycleResult:
            holder["s"].ks.activate("race", "engaged mid-cycle")
            return _approved_long(kw)

        st = Stack(sf, settings, tmp_path, orch=FakeOrchestrator(approve_then_kill))
        holder["s"] = st
        st.broker.update_mark_price(XRP, 0.5)
        _run(st.runtime.trade_cycle_job())
        assert st.broker.get_positions() == []

    def test_paused_skips_feed_and_llm(self, stack: Stack) -> None:
        stack.controller.pause()
        _run(stack.runtime.trade_cycle_job())
        assert stack.feed.builds == []
        assert stack.orch.calls == []

    def test_kill_switch_skips_feed_and_llm(self, stack: Stack) -> None:
        stack.ks.activate("t", "t")
        _run(stack.runtime.trade_cycle_job())
        assert stack.feed.builds == [] and stack.orch.calls == []

    def test_rejection_alert_no_order(self, sf: Any, settings: Settings, tmp_path: Path) -> None:
        st = Stack(sf, settings, tmp_path, orch=FakeOrchestrator(_rejected_long))
        _run(st.runtime.trade_cycle_job())
        assert st.broker.get_positions() == []
        assert any("Risk Gate rejected" in m for m in st.sent)

    def test_kill_switch_engaged_while_submit_waits_on_lock(
        self, sf: Any, settings: Settings, tmp_path: Path
    ) -> None:
        """Switch engaged after the early re-check but while the book lock is held:
        the in-lock re-check must drop the order."""
        st = Stack(sf, settings, tmp_path)
        st.broker.update_mark_price(XRP, 0.5)

        async def scenario() -> None:
            st.controller.book_lock.acquire()  # e.g. monitor tick in progress
            task = asyncio.create_task(st.runtime.run_symbol_cycle(XRP))
            await asyncio.sleep(0.2)  # cycle passed early check, now waiting on lock
            st.ks.activate("telegram", "stop during wait")
            st.controller.book_lock.release()
            await asyncio.wait_for(task, 5)

        _run(scenario())
        assert st.broker.get_positions() == []

    def test_stop_concurrent_with_monitor_is_consistent(self, stack: Stack) -> None:
        import threading

        stack.open_paper_position()
        stack.feed.marks[XRP] = 0.45  # SL hit on the monitor tick
        threads = [
            threading.Thread(target=stack.runtime.position_monitor_job),
            threading.Thread(target=lambda: stack.controller.stop(source="telegram")),
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join(5)
        stack.runtime.position_monitor_job()
        assert stack.broker.get_positions() == []
        with stack.sf() as s:
            rows = s.query(Trade).all()
            assert len(rows) == 1 and rows[0].close_ts is not None

    def test_layer2_cap_breach_does_not_place(
        self, sf: Any, settings: Settings, tmp_path: Path
    ) -> None:
        def oversized(kw: dict[str, Any]) -> CycleResult:
            res = _approved_long(kw)
            assert res.risk_verdict is not None
            v = res.risk_verdict.model_copy(update={"adjusted_size": 1_000_000.0})
            return res.model_copy(update={"risk_verdict": v})

        st = Stack(sf, settings, tmp_path, orch=FakeOrchestrator(oversized))
        st.broker.update_mark_price(XRP, 0.5)
        _run(st.runtime.trade_cycle_job())
        assert st.broker.get_positions() == []
        assert any("layer2" in m for m in st.sent)

    def test_open_position_routes_to_review_and_close(
        self, sf: Any, settings: Settings, tmp_path: Path
    ) -> None:
        def review_close(kw: dict[str, Any]) -> CycleResult:
            assert kw["open_position"] is not None
            return CycleResult(
                cycle_id="r",
                symbol=kw["symbol"],
                trigger="scheduled",
                action="CLOSE",
                approved=True,
                stopped_at="completed",
            )

        st = Stack(sf, settings, tmp_path, orch=FakeOrchestrator(review_close))
        st.open_paper_position()
        _run(st.runtime.trade_cycle_job())
        assert st.broker.get_positions() == []

    def test_data_outage_alert_once(self, stack: Stack, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            stack.feed,
            "build",
            lambda s: {"data_quality_failed": True, "data_quality_flags": ["feed_error"]},
        )
        _run(stack.runtime.trade_cycle_job())
        _run(stack.runtime.trade_cycle_job())
        assert sum("Data outage" in m for m in stack.sent) == 1


class TestRuntimeMonitor:
    def test_sl_hit_closes_trade_row_and_alerts(self, stack: Stack) -> None:
        stack.open_paper_position()
        stack.feed.marks[XRP] = 0.45  # below SL (0.49)
        stack.runtime.position_monitor_job()
        assert stack.broker.get_positions() == []
        with stack.sf() as s:
            t = s.query(Trade).one()
            assert t.exit_reason == "sl" and t.symbol == XRP
            assert t.pnl_usd is not None and t.pnl_usd < 0
            assert t.r_multiple is not None and t.r_multiple < 0
        assert any("SL hit" in m for m in stack.sent)

    def test_max_holding_force_close(self, stack: Stack, monkeypatch: Any) -> None:
        stack.open_paper_position()
        later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
            minutes=stack.settings.risk.max_holding_minutes + 1
        )
        monkeypatch.setattr(stack.runtime, "_clock", lambda: later)
        stack.runtime.position_monitor_job()
        assert stack.broker.get_positions() == []

    def test_reconcile_mismatch_engages_kill_switch(self, stack: Stack) -> None:
        with stack.sf() as s:  # DSOR says a trade is open; broker has none
            s.add(Trade(open_ts=datetime.datetime.now(datetime.UTC), side="buy", entry=1, qty=1))
            s.commit()
        stack.runtime.reconcile_job()
        assert stack.ks.is_active()

    def test_reconcile_ok_with_normal_position(self, stack: Stack) -> None:
        stack.open_paper_position()
        stack.runtime.reconcile_job()
        assert not stack.ks.is_active()

    def test_startup_closes_stale_trades(self, stack: Stack) -> None:
        with stack.sf() as s:
            s.add(Trade(open_ts=datetime.datetime.now(datetime.UTC), side="buy", entry=1, qty=1))
            s.commit()
        stack.runtime.startup()
        stack.runtime.reconcile_job()
        assert not stack.ks.is_active()


class TestLearningJobs:
    def test_shadow_outcome_recorded_for_risk_rejection(self, stack: Stack) -> None:
        ts = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
        with stack.sf() as s:
            s.add(
                Decision(
                    ts=ts,
                    action="NO_TRADE",
                    symbol=XRP,
                    side="long",
                    entry=1.0,
                    stop_loss=0.9,
                    take_profit=1.2,
                    confidence=0.8,
                    confluence_score=4,
                )
            )
            s.commit()
        ms = ts.timestamp() * 1000
        stack.feed.candle_rows = [
            {"ts_ms": ms + 300_000, "open": 1.0, "high": 1.05, "low": 0.98, "close": 1.0},
            {"ts_ms": ms + 600_000, "open": 1.0, "high": 1.25, "low": 0.99, "close": 1.2},
        ]
        assert stack.runtime.shadow_outcomes_job() == 1
        assert stack.runtime.shadow_outcomes_job() == 0  # idempotent
        with stack.sf() as s:
            row = s.query(ShadowOutcome).one()
            assert row.outcome == "win" and row.r_multiple == pytest.approx(2.0)

    def test_review_job_code_only(self, stack: Stack) -> None:
        stack.open_paper_position()
        stack.feed.marks[XRP] = 0.6  # above TP
        stack.runtime.position_monitor_job()
        assert stack.runtime.review_job() == 1
        assert stack.runtime.review_job() == 0
        with stack.sf() as s:
            r = s.query(Review).one()
            assert r.reviewer_model == "code-only"
            assert r.outcome_class == "good_decision_good_outcome"

    def test_consolidation_job_runs(self, stack: Stack) -> None:
        assert isinstance(stack.runtime.consolidation_job(), dict)

    def test_daily_summary_has_utc_and_pkt(self, stack: Stack) -> None:
        stack.runtime.daily_summary_job()
        msg = stack.sent[-1]
        assert "Daily summary" in msg and "UTC" in msg and "PKT" in msg

    def test_scheduler_has_all_jobs(self, stack: Stack) -> None:
        names = {j.name for j in stack.runtime.build_scheduler().jobs}
        assert {
            "brain_cycle",
            "position_monitor",
            "reconciliation",
            "clock_check",
            "heartbeat",
            "shadow_outcomes",
            "post_trade_review",
            "weekly_consolidation",
            "daily_reset",
            "db_backup",
            "daily_summary",
        } <= names


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class TestScheduler:
    def test_failing_job_isolated_and_reported(self) -> None:
        errors: list[tuple[str, str]] = []
        sched = Scheduler(on_error=lambda n, e: errors.append((n, e)))
        ok = Job("ok", lambda: None, 1)
        bad = Job("bad", lambda: 1 / 0, 1)
        assert _run(sched.run_job_once(bad)) is False
        assert _run(sched.run_job_once(ok)) is True
        assert errors and errors[0][0] == "bad" and "ZeroDivisionError" in errors[0][1]
        assert bad.failures == 1 and ok.last_ok_ts is not None

    def test_job_timeout(self) -> None:
        async def slow() -> None:
            await asyncio.sleep(5)

        job = Job("slow", slow, 1, timeout_seconds=0.05)
        assert _run(Scheduler().run_job_once(job)) is False

    def test_run_and_stop(self) -> None:
        hits: list[int] = []

        async def main() -> None:
            stop = asyncio.Event()
            sched = Scheduler()
            sched.add(Job("tick", lambda: hits.append(1), 0.01))
            task = asyncio.create_task(sched.run(stop))
            await asyncio.sleep(0.1)
            stop.set()
            await asyncio.wait_for(task, 2)

        _run(main())
        assert len(hits) >= 2

    def test_duplicate_job_rejected(self) -> None:
        s = Scheduler()
        s.add(Job("a", lambda: None, 1))
        with pytest.raises(ValueError):
            s.add(Job("a", lambda: None, 1))

    def test_seconds_until_daily(self) -> None:
        now = datetime.datetime(2026, 1, 1, 18, 0, tzinfo=datetime.UTC)
        assert seconds_until_daily("19:00", now) == 3600
        assert seconds_until_daily("18:00", now) == 86400


# ---------------------------------------------------------------------------
# Heartbeat, backup, alerts, helpers
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, code: int) -> None:
        self.status_code = code


class TestHeartbeat:
    def test_disabled_without_url(self) -> None:
        hb = Heartbeat("")
        assert not hb.enabled and hb.ping() is False

    def test_ping_ok(self) -> None:
        calls: list[Any] = []
        hb = Heartbeat(
            "https://hc.example/abc",
            http_get=lambda u, timeout: calls.append(timeout) or _Resp(200),
        )
        assert hb.ping() is True and calls == [10.0]

    def test_ping_failure_never_raises_or_logs_url(self, caplog: Any) -> None:
        def boom(url: str, timeout: float) -> Any:
            raise ConnectionError(url)

        hb = Heartbeat("https://hc.example/SECRET-UUID", http_get=boom)
        with caplog.at_level(logging.WARNING):
            assert hb.ping() is False
        assert "SECRET-UUID" not in caplog.text

    def test_heartbeat_job_runs_every_minute(self, stack: Stack) -> None:
        job = next(j for j in stack.runtime.build_scheduler().jobs if j.name == "heartbeat")
        assert job.interval_seconds == 60


class TestBackup:
    def test_backup_and_prune(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        con = sqlite3.connect(db)
        con.execute("create table x(a)")
        con.execute("insert into x values (1)")
        con.commit()
        con.close()
        out = tmp_path / "b"
        base = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        for i in range(5):
            backup_sqlite(db, out, keep=3, now=base + datetime.timedelta(days=i))
        files = sorted(out.glob("trader-*.db"))
        assert len(files) == 3
        assert sqlite3.connect(files[-1]).execute("select a from x").fetchone() == (1,)

    def test_sqlite_path_from_url(self) -> None:
        assert sqlite_path_from_url("sqlite:///./trader.db") == Path("./trader.db")
        assert sqlite_path_from_url("sqlite:///:memory:") is None
        assert sqlite_path_from_url("postgresql://x") is None


class TestAlerts:
    def test_utc_and_pkt_on_every_alert(self) -> None:
        sent: list[str] = []
        Notifier(sent.append).order_filled(XRP, "long", 10, 0.5, "filled")
        assert "UTC" in sent[0] and "PKT" in sent[0]

    def test_untrusted_text_cleaned(self) -> None:
        s = clean_text("a\x00b\x1bc" + "x" * 2000)
        assert "\x00" not in s and "\x1b" not in s and len(s) <= 600

    def test_send_failure_swallowed(self) -> None:
        def boom(_t: str) -> None:
            raise RuntimeError("telegram down")

        Notifier(boom).notify("hi")  # must not raise


def test_to_exchange_symbol() -> None:
    assert to_exchange_symbol(XRP) == "XRPUSDT"
    assert to_exchange_symbol("DOGE/USDT:USDT") == "DOGEUSDT"


@pytest.mark.parametrize(
    ("side", "rows", "expected"),
    [
        ("long", [{"high": 1.25, "low": 0.95}], ("win", 2.0)),
        ("long", [{"high": 1.25, "low": 0.85}], ("loss", -1.0)),  # both touched -> SL
        ("short", [{"high": 1.05, "low": 0.75}], ("win", 2.0)),
        ("long", [{"high": 1.05, "low": 0.95}], None),
    ],
)
def test_shadow_outcome(side: str, rows: list[dict[str, Any]], expected: Any) -> None:
    stop, tp = (0.9, 1.2) if side == "long" else (1.1, 0.8)
    res = shadow_outcome(side, 1.0, stop, tp, rows)
    if expected is None:
        assert res is None
    else:
        assert res is not None and res[0] == expected[0]
        assert res[1] == pytest.approx(expected[1])


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class TestDashboard:
    def _client(self, stack: Stack, password: str = "") -> TestClient:
        stack.feed.candle_rows = [
            {"ts_ms": 1_700_000_000_000, "open": 1, "high": 2, "low": 0.5, "close": 1.5}
        ]
        app = create_app(stack.sf, stack.controller, stack.feed, [XRP], 50.0, password)
        return TestClient(app, base_url=LOCAL)

    def test_read_endpoints(self, stack: Stack) -> None:
        stack.open_paper_position()
        stack.feed.marks[XRP] = 0.45
        stack.runtime.position_monitor_job()
        c = self._client(stack)
        assert "Lightweight Charts" in c.get("/").text
        assert c.get("/api/health").json()["ok"] is True
        assert c.get(f"/api/candles?symbol={XRP}&tf=15m").json()[0]["time"] == 1_700_000_000
        kinds = {m["kind"] for m in c.get(f"/api/markers?symbol={XRP}").json()["markers"]}
        assert {"entry", "exit"} <= kinds
        assert len(c.get("/api/equity").json()) == 1
        assert c.get("/api/trades").json()[0]["symbol"] == XRP
        assert c.get("/api/lessons").status_code == 200
        assert c.get("/api/llm-costs").status_code == 200

    def test_unknown_symbol_and_tf_rejected(self, stack: Stack) -> None:
        c = self._client(stack)
        assert c.get("/api/candles?symbol=BTCUSDT&tf=15m").status_code == 400
        assert c.get(f"/api/candles?symbol={XRP}&tf=1w").status_code == 400

    def test_only_kill_switch_is_writable(self, stack: Stack) -> None:
        app = create_app(stack.sf, stack.controller, stack.feed, [XRP], 50.0)
        writable = {
            r.path for r in app.routes if set(getattr(r, "methods", set())) - {"GET", "HEAD"}
        }
        assert writable == {"/api/kill-switch"}

    def test_basic_auth_when_password_set(self, stack: Stack) -> None:
        c = self._client(stack, password="pw")
        assert c.get("/api/health").status_code == 401
        assert c.post("/api/kill-switch", headers=KILL_HEADERS).status_code == 401
        assert not stack.ks.is_active()
        assert c.get("/api/health", auth=("owner", "pw")).status_code == 200
        assert c.get("/healthz").json() == {"ok": True}  # Docker probe needs no auth

    def test_kill_switch_post_requires_csrf_header(self, stack: Stack) -> None:
        stack.open_paper_position()
        c = self._client(stack)
        assert c.post("/api/kill-switch").status_code == 403
        assert c.post("/api/kill-switch", headers={"X-Trader-Action": "x"}).status_code == 403
        assert stack.broker.get_positions() and not stack.ks.is_active()

    def test_foreign_host_header_rejected(self, stack: Stack) -> None:
        app = create_app(stack.sf, stack.controller, stack.feed, [XRP], 50.0)
        c = TestClient(app, base_url="http://evil.example")
        assert c.get("/api/health").status_code == 400
        assert TestClient(app, base_url="http://localhost:8000").get("/healthz").status_code == 200

    def test_cdn_script_pinned_with_sri(self) -> None:
        html = (REPO_ROOT / "src/trader/dashboard/static/index.html").read_text()
        assert "lightweight-charts@4.2.0" in html and 'integrity="sha384-' in html

    def test_no_openapi_docs(self, stack: Stack) -> None:
        c = self._client(stack)
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404


# ---------------------------------------------------------------------------
# Composition root (no network, dummy env)
# ---------------------------------------------------------------------------


def test_main_build_wires_paper_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    from trader import main as main_mod
    from trader.execution.paper_broker import PaperBroker as PB

    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-a-key")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'p.db'}")
    monkeypatch.setenv("KILL_SWITCH_FILE", str(tmp_path / "KILL"))
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "HEALTHCHECK_PING_URL"):
        monkeypatch.delenv(var, raising=False)
    parts = main_mod.build(argparse.Namespace())
    runtime = parts["runtime"]
    assert isinstance(runtime._broker, PB)
    assert {j.name for j in runtime.build_scheduler().jobs} >= {"brain_cycle", "heartbeat"}
    assert "telegram_poll" not in {j.name for j in runtime.build_scheduler().jobs}
    health = TestClient(parts["app"], base_url=LOCAL).get("/api/health").json()
    assert health["ok"] is True and "paper" in health["mode"]


def test_main_requires_numeric_chat_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    from trader import main as main_mod

    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-a-key")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'p.db'}")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    for bad in ("@someone", "-100123456"):  # username, group id
        monkeypatch.setenv("TELEGRAM_CHAT_ID", bad)
        with pytest.raises(SystemExit, match="numeric private chat id"):
            main_mod.build(argparse.Namespace())
