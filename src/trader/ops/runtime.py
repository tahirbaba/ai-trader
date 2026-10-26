"""Phase 9: TradingRuntime — every scheduled job, wired through TradeCycleOrchestrator.

Order path is unchanged: Decision -> LessonFilter -> RiskGate (inside
``TradeCycleOrchestrator.run_cycle``) -> ``OrderManager.submit`` (Layer 2 cap
re-check). The runtime only adds: a kill-switch / pause / mode re-check
immediately before ``submit``, alerts, and bookkeeping. No LLM is called here.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
from collections.abc import Callable
from typing import Any

from trader.config.settings import Settings
from trader.dsor.models import Decision, Review, RiskEvent, ShadowOutcome, Trade
from trader.dsor.repositories import SystemStateRepository
from trader.dsor.system_state import SystemStateHelper
from trader.execution.adapter import ExchangeAdapter
from trader.execution.exceptions import SlPlacementError
from trader.execution.layer2_check import CapBreachError
from trader.execution.order_manager import OrderManager, OrderResult
from trader.execution.position_monitor import PositionMonitor
from trader.learning.consolidation import WeeklyConsolidation
from trader.learning.outcome_tracker import compute_outcome
from trader.learning.post_trade_review import PostTradeReviewer, ReviewInput
from trader.learning.shadow_tracker import ShadowTracker
from trader.ops.alerts import Notifier
from trader.ops.clock import check_clock_drift
from trader.ops.control import Controller
from trader.ops.heartbeat import Heartbeat
from trader.ops.scheduler import Job, Scheduler
from trader.ops.shadow import shadow_outcome
from trader.ops.trade_ledger import TradeLedger
from trader.risk.models import DecisionInput, RiskVerdict

logger = logging.getLogger(__name__)

_ENTRY_ACTIONS = {"LONG", "SHORT"}
_SHADOW_MAX_AGE = datetime.timedelta(hours=24)


def _aware(ts: datetime.datetime) -> datetime.datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=datetime.UTC)


class TradingRuntime:
    def __init__(
        self,
        settings: Settings,
        session_factory: Callable[[], Any],
        orchestrator: Any,  # TradeCycleOrchestrator (duck-typed for tests)
        order_manager: OrderManager,
        broker: ExchangeAdapter,
        feed: Any,  # FeatureSource
        controller: Controller,
        notifier: Notifier,
        ledger: TradeLedger,
        position_monitor: PositionMonitor | None = None,
        heartbeat: Heartbeat | None = None,
        backup_fn: Callable[[], Any] | None = None,
        telegram_poll: Callable[[], Any] | None = None,
        server_time_ms: Callable[[], int] | None = None,
        symbols: list[str] | None = None,
        clock: Callable[[], datetime.datetime] | None = None,
    ) -> None:
        self._s = settings
        self._sf = session_factory
        self._orch = orchestrator
        self._om = order_manager
        self._broker = broker
        self._feed = feed
        self._ctl = controller
        self._notifier = notifier
        self._ledger = ledger
        self._pm = position_monitor
        self._heartbeat = heartbeat
        self._backup_fn = backup_fn
        self._telegram_poll = telegram_poll
        self._server_time_ms = server_time_ms
        self._symbols = list(symbols if symbols is not None else settings.risk.allowed_symbols)
        self._clock = clock or (lambda: datetime.datetime.now(datetime.UTC))
        self._book_lock = controller.book_lock  # broker + ledger mutations (RLock)
        self._outage: set[str] = set()

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def startup(self) -> None:
        """Close DSOR trades left open by a previous run (paper state is in-memory)."""
        session = self._sf()
        try:
            stale = session.query(Trade).filter(Trade.close_ts.is_(None)).all()
            for t in stale:
                t.close_ts = self._clock()
                t.exit_reason = "paper_state_lost_on_restart"
            if stale:
                session.add(
                    RiskEvent(
                        ts=self._clock(),
                        type="STARTUP_STALE_TRADES",
                        details=f"closed {len(stale)} stale open trade rows",
                    )
                )
            session.commit()
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Brain cycle
    # ------------------------------------------------------------------

    async def trade_cycle_job(self) -> None:
        for symbol in self._symbols:
            await self.run_symbol_cycle(symbol)

    async def run_symbol_cycle(self, symbol: str) -> Any:
        positions = [p for p in self._broker.get_positions() if p.get("symbol") == symbol]
        open_pos = positions[0] if positions else None
        if open_pos is None and self._ctl.entries_blocked() is not None:
            return None  # no new entries -> no data fetch, no LLM spend

        fp: dict[str, Any] = await asyncio.to_thread(self._feed.build, symbol)
        if fp.get("data_quality_failed"):
            if symbol not in self._outage:
                self._outage.add(symbol)
                self._notifier.data_outage(symbol, ", ".join(fp.get("data_quality_flags", [])))
        else:
            self._outage.discard(symbol)
        fp["account_state"] = self.account_state()
        fp["clock_drift_ms"] = self._last_clock_drift()

        result = await self._orch.run_cycle(
            symbol=symbol, trigger="scheduled", feature_pack=fp, open_position=open_pos
        )

        if open_pos is not None:
            if result.action == "CLOSE" and result.stopped_at == "completed":
                await asyncio.to_thread(self._force_close, symbol, "position_review_close")
            return result

        decision = result.decision
        if decision is None or decision.action not in _ENTRY_ACTIONS:
            return result
        if not result.approved or result.risk_verdict is None:
            self._notifier.risk_rejection(symbol, decision.action, result.stopped_at)
            return result

        blocked = self._ctl.entries_blocked()  # re-check immediately before the order
        if blocked is not None:
            logger.warning("approved %s %s dropped: %s", decision.action, symbol, blocked)
            return result

        decision_input = DecisionInput(
            action=decision.action,
            symbol=symbol,
            side=decision.action.lower(),
            entry_price=decision.entry_price,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            confidence=decision.confidence,
            confluence_score=int(fp.get("confluence_score", 0)),
            entry_type=decision.entry_type
            if decision.entry_type in {"market", "limit"}
            else "limit",
        )
        verdict = result.risk_verdict
        try:
            order = await asyncio.to_thread(
                self._submit_locked, verdict, decision_input, result.cycle_id
            )
        except CapBreachError as exc:
            self._notifier.risk_rejection(symbol, decision.action, f"layer2: {exc}")
            return result
        except SlPlacementError as exc:
            self._notifier.notify("🔴 SL placement failed — HALT", [str(exc)[:300]])
            return result
        if order is None:
            logger.warning(
                "approved %s %s dropped at submit: entries blocked", decision.action, symbol
            )
            return result
        self._notifier.decision_taken(symbol, decision.action, decision)
        self._notifier.order_filled(
            symbol, decision_input.side, order.filled_qty, order.avg_price, order.status
        )
        return result

    def _submit_locked(
        self, verdict: RiskVerdict, decision_input: DecisionInput, cycle_id: str
    ) -> OrderResult | None:
        """Final kill-switch/pause/mode check and submit, atomically under the book lock.

        ``Controller.stop`` engages the switch *before* taking this lock to close
        positions, so either this check sees the switch, or the position opened
        here is closed by that ``close_all``.
        """
        with self._book_lock:
            if self._ctl.entries_blocked() is not None:
                return None
            order = self._om.submit(verdict, decision_input, cycle_id)
            self._ledger.expect_entry(decision_input.symbol, None)
            self._ledger.sync(self._broker.get_positions(), {})
            return order

    # ------------------------------------------------------------------
    # Position monitor (SL/TP, kill switch, max holding, price shock)
    # ------------------------------------------------------------------

    def position_monitor_job(self) -> None:
        marks: dict[str, float] = {}
        for symbol in self._symbols:  # network I/O outside the lock
            price = self._feed.mark_price(symbol)
            if price is not None:
                marks[symbol] = price
        with self._book_lock:
            for symbol, price in marks.items():
                update = getattr(self._broker, "update_mark_price", None)
                if update is not None:
                    for fill in update(symbol, price):
                        reason = str(fill.get("reason", ""))
                        if reason:
                            self._ledger.record_exit(symbol, float(fill["price"]), reason)
            for c in self._ledger.sync(self._broker.get_positions(), marks):
                self._notifier.sl_tp_hit(c.symbol, c.reason, c.exit_price, c.pnl_usd)

        if self._ctl.entries_blocked() == "kill_switch" and self._broker.get_positions():
            with self._book_lock:
                closed = self._ctl.close_all(reason="kill_switch_monitor")
                self._ledger.sync(self._broker.get_positions(), marks)
            self._notifier.kill_switch("position_monitor", closed)
            return

        max_hold = datetime.timedelta(minutes=self._s.risk.max_holding_minutes)
        for symbol in list(self._ledger.open_symbols()):
            since = self._ledger.open_since(symbol)
            if since is not None and self._clock() - since > max_hold:
                self._force_close(symbol, "max_holding_minutes")

        if self._pm is not None:
            for symbol in list(self._ledger.open_symbols()):
                r5, r1 = self._returns(symbol)  # network I/O outside the lock
                with self._book_lock:
                    self._pm.monitor_shock(symbol, r5, r1)

    def _returns(self, symbol: str) -> tuple[float | None, float | None]:
        out: list[float | None] = []
        for tf in ("5m", "1m"):
            try:
                c = self._feed.candles(symbol, tf, 2)
                o, cl = float(c[-1]["open"]), float(c[-1]["close"])
                out.append((cl - o) / o * 100 if o else None)
            except Exception:  # noqa: BLE001
                out.append(None)
        return out[0], out[1]

    def _force_close(self, symbol: str, reason: str) -> None:
        with self._book_lock:
            res = self._om.force_close(symbol, reason)
            if res.status != "failed":
                self._ledger.record_exit(symbol, res.avg_price, reason)
            self._ledger.sync(self._broker.get_positions(), {})
        self._notifier.notify(
            f"↩️ Force close {symbol}", [f"reason: {reason}", f"status: {res.status}"]
        )

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    def reconcile_job(self) -> None:
        """Broker positions vs DSOR open trades; any mismatch halts (kill switch)."""
        for symbol in self._symbols:
            self._om.cancel_expired_orders(symbol)
        with self._book_lock:
            self._ledger.sync(self._broker.get_positions(), {})
            broker_syms = {str(p["symbol"]) for p in self._broker.get_positions()}
            session = self._sf()
            try:
                open_trades = session.query(Trade).filter(Trade.close_ts.is_(None)).count()
            finally:
                session.close()
        if open_trades != len(broker_syms) or broker_syms != self._ledger.open_symbols():
            detail = f"broker={sorted(broker_syms)} dsor_open_trades={open_trades}"
            self._ctl.stop(source="reconcile", reason=f"RECONCILE_MISMATCH {detail}")

    # ------------------------------------------------------------------
    # Learning jobs
    # ------------------------------------------------------------------

    def shadow_outcomes_job(self) -> int:
        now = self._clock()
        recorded = 0
        session = self._sf()
        try:
            done = {r.decision_id for r in session.query(ShadowOutcome.decision_id).all()}
            rows = (
                session.query(Decision)
                .filter(
                    Decision.action == "NO_TRADE",
                    Decision.side.in_(["long", "short"]),
                    Decision.ts >= (now - 2 * _SHADOW_MAX_AGE).replace(tzinfo=None),
                )
                .all()
            )
            tracker = ShadowTracker()
            for d in rows:
                if d.id in done or not (d.entry and d.stop_loss and d.take_profit):
                    continue
                ts_ms = _aware(d.ts).timestamp() * 1000
                candles = [c for c in self._feed.candles(d.symbol, "5m", 300) if c["ts_ms"] > ts_ms]
                res = shadow_outcome(d.side or "", d.entry, d.stop_loss, d.take_profit, candles)
                if res is None and now - _aware(d.ts) < _SHADOW_MAX_AGE:
                    continue
                outcome, r = res if res is not None else ("expired", 0.0)
                tracker.record(
                    session,
                    d.id,
                    d.side or "",
                    d.entry,
                    d.stop_loss,
                    d.take_profit,
                    blocked_by="risk_gate",
                    r_multiple=r,
                    outcome=outcome,
                    horizon=len(candles),
                )
                recorded += 1
            session.commit()
        finally:
            session.close()
        return recorded

    def review_job(self) -> int:
        """Code-only post-trade review (no LLM) for closed trades lacking a review."""
        reviewer = PostTradeReviewer()
        written = 0
        session = self._sf()
        try:
            reviewed = {r.trade_id for r in session.query(Review.trade_id).all()}
            trades = session.query(Trade).filter(Trade.close_ts.isnot(None)).all()
            for t in trades:
                if t.id in reviewed or t.exit is None:
                    continue
                symbol = t.symbol or ""
                if not symbol and t.decision_id is not None:
                    dec = session.get(Decision, t.decision_id)
                    symbol = dec.symbol if dec else ""
                outcome = compute_outcome(
                    {
                        "id": t.id,
                        "symbol": symbol,
                        "side": t.side,
                        "entry_price": t.entry,
                        "exit_price": t.exit,
                        "qty": t.qty,
                        "sl_price": None,
                        "open_ts": t.open_ts,
                        "close_ts": t.close_ts,
                        "regime": None,
                        "session": None,
                        "mae": t.max_adverse_excursion,
                        "mfe": t.max_favorable_excursion,
                    }
                )
                res = reviewer.review(
                    ReviewInput(
                        trade_outcome=outcome,
                        snapshot={},
                        active_lesson_ids=[],
                        trade_during_blackout=False,
                        stop_inside_noise=False,
                        ignored_active_lesson=False,
                        llm_outcome_class=None,
                        llm_what_went_right="",
                        llm_what_went_wrong="",
                        llm_lesson_candidates=[],
                    )
                )
                session.add(
                    Review(
                        trade_id=t.id,
                        outcome_class=res.outcome_class,
                        mistake_types_json=json.dumps(res.mistake_types),
                        lesson_candidates_json="[]",
                        reviewer_model="code-only",
                    )
                )
                written += 1
            session.commit()
        finally:
            session.close()
        return written

    def consolidation_job(self) -> dict[str, int]:
        session = self._sf()
        try:
            out = WeeklyConsolidation().run(session)
            session.commit()
            return out
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Ops jobs
    # ------------------------------------------------------------------

    def clock_check_job(self) -> float | None:
        if self._server_time_ms is None:
            return None
        server_ms = self._server_time_ms()
        session = self._sf()
        try:
            drift = check_clock_drift(session, server_ms)
            session.commit()
        finally:
            session.close()
        if drift > self._s.risk.max_clock_drift_ms:
            self._notifier.notify("⏱ Clock drift too high", [f"{drift:.0f} ms"])
        return drift

    def heartbeat_job(self) -> None:
        if self._heartbeat is not None:
            self._heartbeat.ping()

    def backup_job(self) -> None:
        if self._backup_fn is not None:
            self._backup_fn()

    def daily_reset_job(self) -> None:
        session = self._sf()
        try:
            SystemStateHelper(session).reset_daily_counters()
            session.commit()
        finally:
            session.close()

    def daily_summary(self) -> dict[str, Any]:
        pnl = self._ctl.pnl_today()
        since = self._clock().replace(hour=0, minute=0, second=0, microsecond=0)
        session = self._sf()
        try:
            rejected = (
                session.query(Decision)
                .filter(
                    Decision.action == "NO_TRADE",
                    Decision.side.in_(["long", "short"]),
                    Decision.ts >= since.replace(tzinfo=None),
                )
                .count()
            )
        finally:
            session.close()
        return {
            **pnl,
            "risk_rejections": rejected,
            "llm_cost_usd": round(self._ctl.llm_cost_today(), 4),
            "kill_switch": "ON" if self._ctl.entries_blocked() == "kill_switch" else "off",
            "mode": self._s.trading_mode,
        }

    def daily_summary_job(self) -> None:
        self._notifier.daily_summary(self.daily_summary())

    # ------------------------------------------------------------------
    # Account state for the Risk Gate
    # ------------------------------------------------------------------

    def account_state(self) -> dict[str, Any]:
        now = self._clock()
        day = now.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
        week = (now - datetime.timedelta(days=7)).replace(tzinfo=None)
        session = self._sf()
        try:
            closed = (
                session.query(Trade)
                .filter(Trade.close_ts.isnot(None), Trade.pnl_usd.isnot(None))
                .order_by(Trade.close_ts.desc())
                .all()
            )
            opened_today = session.query(Trade).filter(Trade.open_ts >= day).count()
        finally:
            session.close()
        today = [t for t in closed if t.close_ts and t.close_ts >= day]
        last7 = [t for t in closed if t.close_ts and t.close_ts >= week]
        consecutive = 0
        for t in closed:
            if (t.pnl_usd or 0.0) < 0:
                consecutive += 1
            else:
                break
        last_loss = next((t.close_ts for t in closed if (t.pnl_usd or 0.0) < 0), None)
        bal = self._broker.get_balance()
        return {
            "equity_usd": float(bal.get("equity_usd", 0.0)),
            "daily_loss_usd": max(0.0, -sum(t.pnl_usd or 0.0 for t in today)),
            "weekly_loss_usd": max(0.0, -sum(t.pnl_usd or 0.0 for t in last7)),
            "consecutive_losses": consecutive,
            "last_loss_ts": _aware(last_loss).isoformat() if last_loss else None,
            "open_positions": len(self._broker.get_positions()),
            "trades_today": opened_today,
            "llm_cost_today_usd": self._ctl.llm_cost_today(),
        }

    def _last_clock_drift(self) -> float:
        session = self._sf()
        try:
            raw = SystemStateRepository(session).get("last_clock_drift_ms")
            return float(raw) if raw is not None else 0.0
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Scheduler wiring
    # ------------------------------------------------------------------

    def build_scheduler(self) -> Scheduler:
        sc = self._s.app.scheduling

        def on_error(name: str, err: str) -> None:
            self._notifier.job_error(name, err)

        sched = Scheduler(on_error=on_error)
        # Per-symbol budget: orchestrator cycle timeout (60s) + feed fetch + submit.
        cycle_timeout = 150.0 * max(1, len(self._symbols))
        sched.add(
            Job("brain_cycle", self.trade_cycle_job, sc.brain_cycle_interval_seconds, cycle_timeout)
        )
        sched.add(
            Job(
                "position_monitor",
                self.position_monitor_job,
                sc.position_review_interval_seconds,
                60,
            )
        )
        sched.add(Job("reconciliation", self.reconcile_job, sc.reconciliation_interval_seconds, 60))
        sched.add(Job("clock_check", self.clock_check_job, sc.clock_check_interval_seconds, 30))
        sched.add(Job("heartbeat", self.heartbeat_job, 60, 20))
        sched.add(Job("shadow_outcomes", self.shadow_outcomes_job, 3600, 300, run_at_start=False))
        sched.add(Job("post_trade_review", self.review_job, 900, 120, run_at_start=False))
        sched.add(
            Job("weekly_consolidation", self.consolidation_job, 7 * 86400, 300, run_at_start=False)
        )
        sched.add(Job("daily_reset", self.daily_reset_job, 86400, 30, daily_at_utc="00:00"))
        sched.add(Job("db_backup", self.backup_job, 86400, 600, daily_at_utc="00:30"))
        # 19:00 UTC = 00:00 PKT
        sched.add(Job("daily_summary", self.daily_summary_job, 86400, 60, daily_at_utc="19:00"))
        if self._telegram_poll is not None:
            sched.add(Job("telegram_poll", self._telegram_poll, 1, 60))
        return sched
