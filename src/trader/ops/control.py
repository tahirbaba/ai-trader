"""Phase 9: Controller — the single implementation of every owner command.

Telegram commands and the HTTP kill-switch endpoint both call into this class,
so behaviour is identical regardless of the channel.

Mode safety: ``mode_set`` accepts only "paper". There is no code path here (or
anywhere at runtime) that enables live trading.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import threading
from collections.abc import Callable
from typing import Any

from trader.config.settings import Settings
from trader.dsor.models import LessonIndex, RiskEvent, Trade
from trader.dsor.repositories import LlmCallRepository, SystemStateRepository
from trader.dsor.system_state import SystemStateHelper
from trader.execution.adapter import ExchangeAdapter
from trader.execution.order_manager import OrderManager
from trader.learning.lesson_lifecycle import LessonLifecycleManager
from trader.ops.alerts import Notifier
from trader.ops.kill_switch import KillSwitch

logger = logging.getLogger(__name__)

_KEY_PAUSED = "paused"
_HALT_TYPES = [
    "KILL_SWITCH",
    "HALT",
    "HALT_CLOSE_FAILED",
    "RECONCILE_MISMATCH",
    "FORCE_CLOSE_FAILED",
    "SL_AMEND_HALT",
]
_HOST_ONLY_HALTS = ("HALT_CLOSE_FAILED", "RECONCILE_MISMATCH", "FORCE_CLOSE_FAILED")
LESSON_ID_RE = re.compile(r"^L-\d{4,6}$")


def _utc_midnight(now: datetime.datetime) -> datetime.datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


class Controller:
    def __init__(
        self,
        settings: Settings,
        session_factory: Callable[[], Any],
        broker: ExchangeAdapter,
        order_manager: OrderManager,
        kill_switch: KillSwitch,
        notifier: Notifier,
        lifecycle: LessonLifecycleManager | None = None,
        clock: Callable[[], datetime.datetime] | None = None,
        on_close: Callable[[str, float, str], None] | None = None,
    ) -> None:
        self._on_close = on_close
        # Shared with TradingRuntime: every broker + ledger mutation holds it.
        self.book_lock = threading.RLock()
        self._settings = settings
        self._sf = session_factory
        self._broker = broker
        self._om = order_manager
        self._ks = kill_switch
        self._notifier = notifier
        self._lc = lifecycle or LessonLifecycleManager(settings.risk.max_active_lessons)
        self._clock = clock or (lambda: datetime.datetime.now(datetime.UTC))

    # ------------------------------------------------------------------
    # Kill switch / pause
    # ------------------------------------------------------------------

    def stop(self, source: str, reason: str = "owner_stop") -> list[str]:
        """Engage the kill switch and market-close every open position."""
        self._ks.activate(source=source, reason=reason)
        closed = self.close_all(reason=f"kill_switch:{source}")
        self._notifier.kill_switch(source, closed)
        return closed

    def close_all(self, reason: str) -> list[str]:
        with self.book_lock:
            return self._close_all(reason)

    def _close_all(self, reason: str) -> list[str]:
        closed: list[str] = []
        for pos in list(self._broker.get_positions()):
            symbol = str(pos.get("symbol"))
            result = self._om.force_close(symbol, reason)
            if result.status != "failed":
                closed.append(symbol)
                if self._on_close is not None:
                    self._on_close(symbol, result.avg_price, reason)
            else:
                logger.critical("close_all: force_close failed for %s: %s", symbol, result.error)
        return closed

    def is_paused(self) -> bool:
        try:
            session = self._sf()
            try:
                return SystemStateRepository(session).get(_KEY_PAUSED) == "1"
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            logger.critical("pause state unreadable — treating as PAUSED: %s", exc)
            return True

    def entries_blocked(self) -> str | None:
        """Return why new entries are blocked right now, or None if allowed."""
        if self._ks.is_active():
            return "kill_switch"
        if self.is_paused():
            return "paused"
        if self._settings.trading_mode != "paper":
            return "mode_not_paper"
        return None

    def pause(self) -> str:
        self._set_state(_KEY_PAUSED, "1", "PAUSED")
        return "⏸ Paused: no new entries. Open positions are still monitored (SL/TP stay live)."

    def resume(self) -> str:
        """Clear the PAUSE only. Never clears the kill switch (see ``unhalt``)."""
        self._set_state(_KEY_PAUSED, "0", "RESUMED")
        if self._ks.is_active():
            return "▶️ Pause cleared. Kill switch is still ON — use /unhalt after checking why."
        return "▶️ Resumed. New entries allowed (paper)."

    def last_halt(self) -> RiskEvent | None:
        """Most recent halt-type risk event since the kill switch was last cleared."""
        session = self._sf()
        try:
            cleared = (
                session.query(RiskEvent.id)
                .filter(RiskEvent.type == "KILL_SWITCH_CLEARED")
                .order_by(RiskEvent.id.desc())
                .first()
            )
            q = session.query(RiskEvent).filter(RiskEvent.type.in_(_HALT_TYPES))
            if cleared is not None:
                q = q.filter(RiskEvent.id > cleared[0])
            row: RiskEvent | None = q.order_by(RiskEvent.id.desc()).first()
            return row
        finally:
            session.close()

    def unhalt(self, source: str = "telegram") -> str:
        """Clear the DSOR kill switch remotely, unless the halt needs the host.

        Refused (must be cleared on the host with ``make unhalt``) when any halt
        since the last clear was a failed close or a reconciliation mismatch, or
        while the local file flag is present.
        """
        halt = self.last_halt()
        if halt is not None and (
            halt.type in _HOST_ONLY_HALTS
            or any(tag in (halt.details or "") for tag in _HOST_ONLY_HALTS)
        ):
            return (
                f"❌ Refused: last halt was {halt.type} ({(halt.details or '')[:120]}). "
                "Verify positions on the host, then run `make unhalt` there."
            )
        if self._ks.status().file_flag:
            return (
                f"❌ Local file flag present ({self._ks.flag_path}). Run `make unkill` on the host."
            )
        status = self._ks.clear_db_flag(source=source)
        if status.active:
            return "❌ Kill switch still ACTIVE (state unreadable)."
        return "✅ Kill switch cleared. /resume if paused."

    # ------------------------------------------------------------------
    # Mode — show or downgrade only
    # ------------------------------------------------------------------

    def mode_show(self) -> str:
        return f"mode: {self._settings.trading_mode} (runtime can only downgrade to paper)"

    def mode_set(self, requested: str) -> str:
        requested = requested.strip().lower()
        if requested != "paper":
            logger.warning("mode change to %r rejected", requested)
            return (
                f"❌ Refused: '{requested}' is not allowed. Runtime can only set paper. "
                "Live requires owner action outside this bot."
            )
        session = self._sf()
        try:
            SystemStateHelper(session).set_mode("paper")
            session.commit()
        finally:
            session.close()
        return "mode: paper"

    # ------------------------------------------------------------------
    # Read-only views
    # ------------------------------------------------------------------

    def status(self) -> str:
        ks = self._ks.status()
        bal = self._broker.get_balance()
        return "\n".join(
            [
                f"mode: {self._settings.trading_mode}",
                f"kill_switch: {'ON' if ks.active else 'off'} (file={ks.file_flag}, db={ks.db_flag})",
                f"paused: {self.is_paused()}",
                f"equity: {float(bal.get('equity_usd', 0.0)):.4f} USD",
                f"open positions: {len(self._broker.get_positions())}",
                f"LLM cost today: {self.llm_cost_today():.4f} USD",
            ]
        )

    def open_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        rows = self._broker.get_positions()
        return [p for p in rows if symbol is None or p.get("symbol") == symbol]

    def positions(self) -> str:
        rows = self._broker.get_positions()
        if not rows:
            return "No open positions."
        return "\n".join(
            f"{p['symbol']} {p['side']} qty={p['qty']} entry={p['entry_price']} "
            f"SL={p.get('sl_price')} uPnL={float(p.get('unrealised_pnl', 0.0)):+.4f}"
            for p in rows
        )

    def pnl_today(self) -> dict[str, float | int]:
        since = _utc_midnight(self._clock())
        session = self._sf()
        try:
            trades = (
                session.query(Trade)
                .filter(Trade.close_ts.isnot(None), Trade.close_ts >= since.replace(tzinfo=None))
                .all()
            )
            realised = sum(t.pnl_usd or 0.0 for t in trades)
            wins = sum(1 for t in trades if (t.pnl_usd or 0.0) > 0)
            return {"trades_closed": len(trades), "wins": wins, "realised_pnl_usd": realised}
        finally:
            session.close()

    def pnl(self) -> str:
        p = self.pnl_today()
        upnl = sum(float(x.get("unrealised_pnl", 0.0)) for x in self._broker.get_positions())
        return (
            f"today: {p['trades_closed']} closed, {p['wins']} wins, "
            f"realised {p['realised_pnl_usd']:+.4f} USD, unrealised {upnl:+.4f} USD"
        )

    def llm_cost_today(self) -> float:
        session = self._sf()
        try:
            return LlmCallRepository(session).total_cost_since(_utc_midnight(self._clock()))
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Lessons
    # ------------------------------------------------------------------

    def lessons_pending(self) -> str:
        session = self._sf()
        try:
            rows = session.query(LessonIndex).filter(LessonIndex.status == "candidate").all()
            if not rows:
                return "No pending lesson candidates."
            return "\n".join(
                f"{r.lesson_id}: {(r.setup or '')[:120]} (evidence={r.evidence_trade_ids or '[]'})"
                for r in rows
            )
        finally:
            session.close()

    def approve(self, lesson_id: str) -> str:
        if not LESSON_ID_RE.match(lesson_id):
            return "Usage: /approve L-0007"
        session = self._sf()
        try:
            row = session.query(LessonIndex).filter(LessonIndex.lesson_id == lesson_id).first()
            if row is None or row.status != "candidate":
                return f"{lesson_id}: no such candidate."
            try:
                evidence = [int(x) for x in json.loads(row.evidence_trade_ids or "[]")]
            except (ValueError, TypeError):
                evidence = []
            try:
                ok = self._lc.owner_approve(session, lesson_id, evidence)
            except ValueError as exc:
                session.rollback()
                return f"{lesson_id}: not approved — {exc}"
            session.add(
                RiskEvent(ts=self._clock(), type="LESSON_APPROVED", details=f"{lesson_id} by owner")
            )
            session.commit()
            return f"{lesson_id}: approved → active." if ok else f"{lesson_id}: not approved."
        finally:
            session.close()

    def reject(self, lesson_id: str) -> str:
        if not LESSON_ID_RE.match(lesson_id):
            return "Usage: /reject L-0007"
        session = self._sf()
        try:
            row = (
                session.query(LessonIndex)
                .filter(LessonIndex.lesson_id == lesson_id, LessonIndex.status == "candidate")
                .first()
            )
            if row is None:
                return f"{lesson_id}: no such candidate."
            row.status = "deprecated"
            stats: dict[str, Any] = {}
            try:
                stats = json.loads(row.win_stats or "{}")
            except ValueError:
                stats = {}
            stats["deprecation_reason"] = "owner_rejected"
            row.win_stats = json.dumps(stats)
            row.last_validated_ts = self._clock()
            session.add(
                RiskEvent(ts=self._clock(), type="LESSON_REJECTED", details=f"{lesson_id} by owner")
            )
            session.commit()
            return f"{lesson_id}: rejected."
        finally:
            session.close()

    def confirm(self, confirm_id: str) -> str:
        # Phase 11 (live confirmation flow) is not built; never acts.
        return f"/confirm {confirm_id[:32]}: nothing to confirm (live confirmations are Phase 11)."

    # ------------------------------------------------------------------

    def _set_state(self, key: str, value: str, event: str) -> None:
        session = self._sf()
        try:
            SystemStateRepository(session).set(key, value)
            session.add(RiskEvent(ts=self._clock(), type=event, details="owner command"))
            session.commit()
        finally:
            session.close()
