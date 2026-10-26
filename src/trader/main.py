"""Phase 9: Composition root — runs the whole system in PAPER mode.

    uv run python -m trader.main            # scheduler + Telegram + dashboard (127.0.0.1:8000)
    uv run python -m trader.main --once     # one brain cycle + one monitor tick, then exit

This entry point refuses to start unless TRADING_MODE=paper. It never builds a
LiveAdapter; the only broker it wires is PaperBroker.

Environment variables (names only; values live in .env):
  ANTHROPIC_API_KEY            required (LLM agents)
  DATABASE_URL                 default sqlite:///./trader.db
  TELEGRAM_BOT_TOKEN           optional (alerts + commands disabled if empty)
  TELEGRAM_CHAT_ID             required if TELEGRAM_BOT_TOKEN is set
  HEALTHCHECK_PING_URL         optional (external dead-man alert disabled if empty)
  DASHBOARD_PASSWORD           optional HTTP basic-auth password for the dashboard
  BACKUP_DIR                   default ./backups
  BACKUP_RSYNC_TARGET          optional off-server rsync destination
  PAPER_INITIAL_BALANCE_USD    default 50
  KILL_SWITCH_FILE             default ./data/KILL_SWITCH
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("trader.main")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _env(name: str, default: str = "") -> str:
    """Env value, or *default* when unset OR empty (.env.example ships empty values)."""
    return os.environ.get(name, "").strip() or default


def build(args: argparse.Namespace) -> dict[str, Any]:
    from trader.agents.decision_agent import DecisionAgent
    from trader.agents.derivatives_analyst import DerivativesAnalyst
    from trader.agents.macro_analyst import MacroNewsAnalyst
    from trader.agents.orchestrator import TradeCycleOrchestrator
    from trader.agents.position_reviewer import PositionReviewAgent
    from trader.agents.technical_analyst import TechnicalAnalyst
    from trader.config.settings import get_default_settings
    from trader.dashboard.app import create_app
    from trader.dsor.db import make_session_factory
    from trader.execution.order_manager import OrderManager
    from trader.execution.paper_broker import PaperBroker
    from trader.execution.position_monitor import PositionMonitor
    from trader.ksor.indexer import KsorIndexer
    from trader.ksor.loader import load_ksor_entries
    from trader.ksor.retriever import KsorRetriever
    from trader.ksor.sync import sync_lessons_index
    from trader.llm.client import LlmClient
    from trader.ops.alerts import Notifier
    from trader.ops.backup import backup_sqlite, sqlite_path_from_url
    from trader.ops.config_audit import record_startup_config
    from trader.ops.control import Controller
    from trader.ops.heartbeat import Heartbeat
    from trader.ops.kill_switch import KillSwitch
    from trader.ops.market_feed import BybitFeatureSource
    from trader.ops.runtime import TradingRuntime
    from trader.ops.telegram_bot import HttpTelegramTransport, TelegramBot
    from trader.ops.trade_ledger import TradeLedger
    from trader.risk.gate import RiskGate
    from trader.risk.lesson_filter import LessonFilter

    settings = get_default_settings(REPO_ROOT)
    if settings.trading_mode != "paper":
        raise SystemExit("trader.main runs PAPER only. Set TRADING_MODE=paper.")
    if not _env("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set (needed by the LLM agents).")

    db_url = _env("DATABASE_URL", "sqlite:///./trader.db")
    sf = make_session_factory(db_url)
    with sf() as s:
        record_startup_config(s, REPO_ROOT)
        sync_lessons_index(s, REPO_ROOT / "ksor")
        s.commit()

    indexer = KsorIndexer(db_path=":memory:")
    indexer.build(load_ksor_entries(REPO_ROOT / "ksor"))
    retriever = KsorRetriever(indexer)

    llm = LlmClient(settings, sf)
    models = settings.app.models
    orchestrator = TradeCycleOrchestrator(
        settings=settings,
        macro_analyst=MacroNewsAnalyst(llm, models.screening),
        technical_analyst=TechnicalAnalyst(llm, models.screening),
        derivatives_analyst=DerivativesAnalyst(llm, models.screening),
        decision_agent=DecisionAgent(llm, models.decision),
        position_reviewer=PositionReviewAgent(llm, models.review),
        lesson_filter=LessonFilter(retriever=retriever),
        risk_gate=RiskGate(settings),
        ksor_retriever=retriever,
        session_factory=sf,
    )

    initial = float(_env("PAPER_INITIAL_BALANCE_USD", "50") or 50)
    broker = PaperBroker(initial_balance=initial)
    om = OrderManager(broker, settings, sf)
    ks = KillSwitch(sf, Path(_env("KILL_SWITCH_FILE", "data/KILL_SWITCH")))
    notifier = Notifier(send=None)
    ledger = TradeLedger(sf)
    controller = Controller(settings, sf, broker, om, ks, notifier, on_close=ledger.record_exit)

    poll = None
    token, chat = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID")
    if token:
        if not chat.isdigit() or int(chat) <= 0:
            raise SystemExit(
                "TELEGRAM_CHAT_ID must be the owner's numeric private chat id (groups not allowed)."
            )
        bot = TelegramBot(HttpTelegramTransport(token), int(chat), controller)
        notifier.set_sink(bot.send)
        poll = bot.poll_once
    else:
        logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram alerts/commands disabled")

    feed = BybitFeatureSource()
    db_path = sqlite_path_from_url(db_url)
    backup_dir = Path(_env("BACKUP_DIR", "backups"))
    rsync_target = _env("BACKUP_RSYNC_TARGET")

    def backup() -> None:
        if db_path is None:
            logger.warning("DB backup skipped: DATABASE_URL is not a SQLite file")
            return
        backup_sqlite(db_path, backup_dir, rsync_target=rsync_target)

    runtime = TradingRuntime(
        settings=settings,
        session_factory=sf,
        orchestrator=orchestrator,
        order_manager=om,
        broker=broker,
        feed=feed,
        controller=controller,
        notifier=notifier,
        ledger=ledger,
        position_monitor=PositionMonitor(broker, settings.risk.price_shock, sf),
        heartbeat=Heartbeat(_env("HEALTHCHECK_PING_URL")),
        backup_fn=backup,
        telegram_poll=poll,
        server_time_ms=feed.server_time_ms,
    )
    app = create_app(
        sf,
        controller,
        feed,
        list(settings.risk.allowed_symbols),
        initial,
        password=_env("DASHBOARD_PASSWORD"),
    )
    return {"runtime": runtime, "app": app, "notifier": notifier}


async def _serve(parts: dict[str, Any], host: str, port: int, dashboard: bool) -> None:
    runtime = parts["runtime"]
    runtime.startup()
    stop = asyncio.Event()
    scheduler = runtime.build_scheduler()
    parts["notifier"].notify("🟢 ai-trader started (paper)", [f"jobs: {len(scheduler.jobs)}"])
    tasks = [asyncio.create_task(scheduler.run(stop))]
    if dashboard:
        import uvicorn

        server = uvicorn.Server(
            uvicorn.Config(parts["app"], host=host, port=port, log_level="info")
        )
        await server.serve()  # returns on SIGINT/SIGTERM
        stop.set()
    else:
        try:
            await asyncio.Event().wait()
        finally:
            stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    parts["notifier"].notify("⚪ ai-trader stopped (paper)")


async def _once(parts: dict[str, Any]) -> None:
    runtime = parts["runtime"]
    runtime.startup()
    await runtime.trade_cycle_job()
    await asyncio.to_thread(runtime.position_monitor_job)


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    from trader.ops.logging import configure_logging

    p = argparse.ArgumentParser(prog="trader.main", description="ai-trader (paper only)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-dashboard", action="store_true")
    p.add_argument("--once", action="store_true")
    args = p.parse_args(argv)

    load_dotenv(REPO_ROOT / ".env", override=False)
    configure_logging(_env("LOG_LEVEL", "INFO"))
    parts = build(args)
    if args.once:
        asyncio.run(_once(parts))
    else:
        asyncio.run(_serve(parts, args.host, args.port, not args.no_dashboard))
    return 0


if __name__ == "__main__":
    sys.exit(main())
