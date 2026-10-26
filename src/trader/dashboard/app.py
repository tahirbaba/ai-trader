"""Phase 9: Read-only dashboard (FastAPI + TradingView Lightweight Charts).

Every endpoint is a GET except ``POST /api/kill-switch``, which can only ENGAGE
the kill switch (there is no endpoint that clears it or changes mode).

Exposure: bind to 127.0.0.1 (paper-local) or publish the container port on
127.0.0.1 only (docker-compose) and reach it over an SSH tunnel. If
DASHBOARD_PASSWORD is set, HTTP basic auth is also required.
"""

from __future__ import annotations

import datetime
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import func
from starlette.middleware.trustedhost import TrustedHostMiddleware

from trader.dsor.models import Decision, LessonIndex, LlmCall, Trade, TvAlert
from trader.ops.control import Controller

_STATIC = Path(__file__).parent / "static"
_TIMEFRAMES = {"5m", "15m", "1h", "4h", "1d"}


def _epoch(ts: datetime.datetime | None) -> int | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.UTC)
    return int(ts.timestamp())


def create_app(
    session_factory: Callable[[], Any],
    controller: Controller,
    feed: Any,
    symbols: list[str],
    initial_equity: float,
    password: str = "",
    allowed_hosts: list[str] | None = None,
) -> FastAPI:
    app = FastAPI(title="ai-trader dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    # DNS-rebinding guard: only answer for localhost names (SSH tunnel / local use).
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=allowed_hosts or ["127.0.0.1", "localhost"]
    )
    basic = HTTPBasic(auto_error=False)

    def auth(creds: HTTPBasicCredentials | None = Depends(basic)) -> None:  # noqa: B008
        if not password:
            return
        ok = creds is not None and secrets.compare_digest(
            creds.password.encode(), password.encode()
        )
        if not ok:
            raise HTTPException(401, "auth required", headers={"WWW-Authenticate": "Basic"})

    def _symbol(symbol: str) -> str:
        if symbol not in symbols:
            raise HTTPException(400, "unknown symbol")
        return symbol

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def index() -> str:
        return (_STATIC / "index.html").read_text()

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        # Unauthenticated liveness probe (Docker HEALTHCHECK); reveals nothing.
        return {"ok": True}

    @app.get("/api/health", dependencies=[Depends(auth)])
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "mode": controller.mode_show(),
            "entries_blocked": controller.entries_blocked(),
            "symbols": symbols,
        }

    @app.get("/api/candles", dependencies=[Depends(auth)])
    def candles(symbol: str, tf: str = Query("15m")) -> list[dict[str, Any]]:
        _symbol(symbol)
        if tf not in _TIMEFRAMES:
            raise HTTPException(400, "unknown timeframe")
        rows = feed.candles(symbol, tf, 300)
        return [
            {
                "time": int(float(c["ts_ms"]) // 1000),
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
            }
            for c in rows
        ]

    @app.get("/api/markers", dependencies=[Depends(auth)])
    def markers(symbol: str) -> dict[str, Any]:
        _symbol(symbol)
        session = session_factory()
        try:
            trades = session.query(Trade).filter(Trade.symbol == symbol).all()
            blocks = (
                session.query(Decision)
                .filter(Decision.symbol == symbol, Decision.action == "NO_TRADE")
                .filter(
                    Decision.side.in_(["long", "short"])
                    | (Decision.thesis == "lesson_filter_blocked")
                )
                .order_by(Decision.ts.desc())
                .limit(200)
                .all()
            )
            alerts = (
                session.query(TvAlert)
                .filter(TvAlert.symbol == symbol)
                .order_by(TvAlert.ts_received.desc())
                .limit(200)
                .all()
            )
            out: list[dict[str, Any]] = []
            for t in trades:
                long_ = t.side == "buy"
                out.append(
                    {"time": _epoch(t.open_ts), "kind": "entry", "side": t.side, "price": t.entry}
                )
                if t.close_ts is not None:
                    out.append(
                        {
                            "time": _epoch(t.close_ts),
                            "kind": "exit",
                            "side": t.side,
                            "price": t.exit,
                            "reason": t.exit_reason,
                            "pnl": t.pnl_usd,
                            "long": long_,
                        }
                    )
            for d in blocks:
                reason = "lesson_filter" if d.thesis == "lesson_filter_blocked" else "risk_gate"
                out.append(
                    {"time": _epoch(d.ts), "kind": "block", "reason": reason, "side": d.side}
                )
            for a in alerts:
                out.append(
                    {"time": _epoch(a.ts_received), "kind": "tv_alert", "name": a.alert_name}
                )
            positions = controller.open_positions(symbol)
            lines = [
                {"kind": "sl", "price": p.get("sl_price")} for p in positions if p.get("sl_price")
            ]
            last = (
                session.query(Decision)
                .filter(Decision.symbol == symbol, Decision.action.in_(["LONG", "SHORT"]))
                .order_by(Decision.ts.desc())
                .first()
            )
            if positions and last is not None and last.take_profit:
                lines.append({"kind": "tp", "price": last.take_profit})
            return {"markers": sorted(out, key=lambda m: m["time"] or 0), "lines": lines}
        finally:
            session.close()

    @app.get("/api/equity", dependencies=[Depends(auth)])
    def equity() -> list[dict[str, Any]]:
        session = session_factory()
        try:
            rows = (
                session.query(Trade)
                .filter(Trade.close_ts.isnot(None), Trade.pnl_usd.isnot(None))
                .order_by(Trade.close_ts)
                .all()
            )
            eq = initial_equity
            points: list[dict[str, Any]] = []
            for t in rows:
                eq += t.pnl_usd or 0.0
                points.append({"time": _epoch(t.close_ts), "value": round(eq, 6)})
            return points
        finally:
            session.close()

    @app.get("/api/trades", dependencies=[Depends(auth)])
    def trades(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        session = session_factory()
        try:
            rows = session.query(Trade).order_by(Trade.open_ts.desc()).limit(limit).all()
            return [
                {
                    "id": t.id,
                    "symbol": t.symbol,
                    "side": t.side,
                    "entry": t.entry,
                    "exit": t.exit,
                    "qty": t.qty,
                    "pnl_usd": t.pnl_usd,
                    "r": t.r_multiple,
                    "open": _epoch(t.open_ts),
                    "close": _epoch(t.close_ts),
                    "reason": t.exit_reason,
                }
                for t in rows
            ]
        finally:
            session.close()

    @app.get("/api/lessons", dependencies=[Depends(auth)])
    def lessons() -> list[dict[str, Any]]:
        session = session_factory()
        try:
            rows = session.query(LessonIndex).order_by(LessonIndex.lesson_id).all()
            return [
                {
                    "id": r.lesson_id,
                    "status": r.status,
                    "setup": r.setup,
                    "blocks": r.block_count,
                    "blocked_shadow_r": r.blocked_shadow_r,
                }
                for r in rows
            ]
        finally:
            session.close()

    @app.get("/api/llm-costs", dependencies=[Depends(auth)])
    def llm_costs(days: int = Query(14, ge=1, le=90)) -> list[dict[str, Any]]:
        since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)
        session = session_factory()
        try:
            day = func.date(LlmCall.ts)
            rows = (
                session.query(day, LlmCall.purpose, func.sum(LlmCall.cost_usd), func.count())
                .filter(LlmCall.ts >= since.replace(tzinfo=None))
                .group_by(day, LlmCall.purpose)
                .order_by(day)
                .all()
            )
            return [
                {"day": str(d), "purpose": p, "cost_usd": round(float(c or 0.0), 6), "calls": n}
                for d, p, c, n in rows
            ]
        finally:
            session.close()

    @app.post("/api/kill-switch", dependencies=[Depends(auth)])
    def kill_switch(
        x_trader_action: str | None = Header(default=None),  # noqa: B008
    ) -> dict[str, Any]:
        # CSRF guard: a custom header cannot be sent cross-origin without a CORS
        # preflight, and this app grants no CORS.
        if x_trader_action != "kill-switch":
            raise HTTPException(403, "missing X-Trader-Action header")
        closed = controller.stop(source="api")
        return {"kill_switch": "on", "closed": closed}

    return app
