"""Phase 9: Owner alerts (Telegram). Every message carries UTC + PKT times.

LLM-produced text (e.g. thesis) is untrusted: it is sent as plain text (no
parse_mode), control characters are stripped, and length is capped.
"""

from __future__ import annotations

import datetime
import logging
import re
from collections.abc import Callable
from typing import Any

from trader.data.session import format_for_display

logger = logging.getLogger(__name__)

_MAX_FIELD = 600
_MAX_MESSAGE = 3900  # Telegram hard limit is 4096
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SECRETS = [
    (re.compile(r"bot\d+:[A-Za-z0-9_-]+"), "bot***"),
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]+"), "sk-ant-***"),
]


def redact_secrets(text: str) -> str:
    """Strip bot tokens, URLs (healthcheck URLs embed secrets) and API keys."""
    for pat, repl in _SECRETS:
        text = pat.sub(repl, text)
    return text


def clean_text(text: object, limit: int = _MAX_FIELD) -> str:
    s = redact_secrets(_CTRL.sub("", str(text)))
    return s if len(s) <= limit else s[: limit - 1] + "…"


def stamp(now: datetime.datetime | None = None) -> str:
    return format_for_display(now or datetime.datetime.now(datetime.UTC))


class Notifier:
    """Formats alerts and hands them to *send* (e.g. ``TelegramBot.send``)."""

    def __init__(
        self,
        send: Callable[[str], None] | None,
        clock: Callable[[], datetime.datetime] | None = None,
    ) -> None:
        self._send = send
        self._clock = clock or (lambda: datetime.datetime.now(datetime.UTC))

    def set_sink(self, send: Callable[[str], None] | None) -> None:
        self._send = send

    def notify(self, title: str, lines: list[str] | None = None) -> str:
        body = "\n".join([title, *(lines or []), stamp(self._clock())])
        body = body[:_MAX_MESSAGE]
        if self._send is None:
            logger.info("alert (no Telegram configured): %s", title)
            return body
        try:
            self._send(body)
        except Exception as exc:  # noqa: BLE001
            logger.error("alert send failed: %s", type(exc).__name__)
        return body

    # ---- typed alerts ----------------------------------------------------

    def decision_taken(self, symbol: str, action: str, decision: Any) -> str:
        return self.notify(
            f"🧠 Decision {action} {symbol}",
            [
                f"entry={getattr(decision, 'entry_price', 0)} "
                f"SL={getattr(decision, 'stop_loss', 0)} TP={getattr(decision, 'take_profit', 0)} "
                f"conf={getattr(decision, 'confidence', 0):.2f}",
                f"thesis: {clean_text(getattr(decision, 'thesis', ''))}",
            ],
        )

    def order_filled(self, symbol: str, side: str, qty: float, price: float, status: str) -> str:
        return self.notify(f"✅ Order {status} {symbol}", [f"{side} qty={qty} @ {price}"])

    def sl_tp_hit(self, symbol: str, reason: str, price: float, pnl_usd: float | None) -> str:
        pnl = f"{pnl_usd:+.4f} USD" if pnl_usd is not None else "n/a"
        return self.notify(f"🎯 {reason.upper()} hit {symbol}", [f"exit @ {price}  PnL {pnl}"])

    def risk_rejection(self, symbol: str, action: str, reason: str) -> str:
        return self.notify(
            f"🛑 Risk Gate rejected {action} {symbol}", [f"reason: {clean_text(reason)}"]
        )

    def data_outage(self, symbol: str, detail: str) -> str:
        return self.notify(f"⚠️ Data outage {symbol}", [clean_text(detail)])

    def kill_switch(self, source: str, closed: list[str]) -> str:
        return self.notify(
            "🔴 KILL SWITCH ENGAGED",
            [
                f"source: {source}",
                f"closed: {', '.join(closed) if closed else 'no open positions'}",
            ],
        )

    def job_error(self, job: str, error: str) -> str:
        return self.notify(f"⚠️ Job error: {job}", [clean_text(error)])

    def daily_summary(self, summary: dict[str, Any]) -> str:
        return self.notify(
            "📊 Daily summary",
            [f"{k}: {v}" for k, v in summary.items()],
        )
