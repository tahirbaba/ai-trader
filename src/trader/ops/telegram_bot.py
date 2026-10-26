"""Phase 9: Telegram bot — authorized chat ID only.

Updates from any other chat are dropped silently (logged, never answered).
Commands are parsed strictly; unknown commands get a short help text.
The bot only calls ``Controller`` — it never touches the exchange directly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

import requests

from trader.ops.control import Controller

logger = logging.getLogger(__name__)

HELP = (
    "Commands: /status /positions /pnl /stop /pause /resume /unhalt /mode [paper] "
    "/lessons /approve L-0007 /reject L-0007 /confirm <id>"
)


class TelegramTransport(Protocol):
    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]: ...

    def send_message(self, chat_id: int, text: str) -> None: ...


class HttpTelegramTransport:
    """Bot API over HTTPS with explicit timeouts. The token is never logged."""

    def __init__(self, token: str, http: Any = None) -> None:
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is empty")
        self._base = f"https://api.telegram.org/bot{token}"
        self._http = http or requests.Session()

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": '["message"]'}
        if offset is not None:
            params["offset"] = offset
        try:
            resp = self._http.get(f"{self._base}/getUpdates", params=params, timeout=timeout + 10)
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            # requests embeds the URL (which contains the bot token) in its messages.
            raise RuntimeError(f"getUpdates failed: {type(exc).__name__}") from None
        if not data.get("ok"):
            raise RuntimeError(f"getUpdates failed: {data.get('error_code')}")
        result: list[dict[str, Any]] = data.get("result", [])
        return result

    def send_message(self, chat_id: int, text: str) -> None:
        try:
            resp = self._http.post(
                f"{self._base}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                timeout=15,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"sendMessage failed: {type(exc).__name__}") from None
        if resp.status_code != 200:
            raise RuntimeError(f"sendMessage HTTP {resp.status_code}")


class TelegramBot:
    def __init__(
        self,
        transport: TelegramTransport,
        authorized_chat_id: int,
        controller: Controller,
    ) -> None:
        if authorized_chat_id <= 0:
            raise ValueError("authorized chat must be the owner's private chat (positive id)")
        self._t = transport
        self._chat_id = authorized_chat_id
        self._controller = controller
        self._offset: int | None = None
        self._commands: dict[str, Callable[[list[str]], str]] = {
            "/start": lambda _a: HELP,
            "/help": lambda _a: HELP,
            "/status": lambda _a: controller.status(),
            "/positions": lambda _a: controller.positions(),
            "/pnl": lambda _a: controller.pnl(),
            "/stop": self._cmd_stop,
            "/pause": lambda _a: controller.pause(),
            "/resume": lambda _a: controller.resume(),
            "/unhalt": lambda _a: controller.unhalt(),
            "/mode": lambda a: controller.mode_set(a[0]) if a else controller.mode_show(),
            "/lessons": lambda _a: controller.lessons_pending(),
            "/approve": lambda a: controller.approve(a[0]) if a else "Usage: /approve L-0007",
            "/reject": lambda a: controller.reject(a[0]) if a else "Usage: /reject L-0007",
            "/confirm": lambda a: controller.confirm(a[0]) if a else "Usage: /confirm <id>",
        }

    def send(self, text: str) -> None:
        """Alert sink used by ``Notifier``."""
        self._t.send_message(self._chat_id, text)

    def handle_update(self, update: dict[str, Any]) -> str | None:
        """Process one update. Returns the reply text, or None if ignored."""
        msg = update.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        sender = (msg.get("from") or {}).get("id")
        # Private chat with the owner: chat id == owner user id == sender id.
        if (
            not isinstance(chat_id, int)
            or not isinstance(sender, int)
            or chat_id != self._chat_id
            or sender != self._chat_id
        ):
            logger.warning("telegram: ignored update from unauthorized chat")
            return None
        text = msg.get("text")
        if not isinstance(text, str) or not text.startswith("/"):
            return None
        parts = text.strip().split()
        command = parts[0].split("@", 1)[0].lower()
        handler = self._commands.get(command)
        try:
            reply = handler(parts[1:2]) if handler else HELP
        except Exception as exc:  # noqa: BLE001
            logger.error("telegram command %s failed: %s", command, type(exc).__name__)
            reply = f"{command} failed: {type(exc).__name__}"
        self._t.send_message(self._chat_id, reply)
        return reply

    def poll_once(self, timeout: int = 25) -> int:
        """Long-poll once and handle every update. Returns number processed."""
        updates = self._t.get_updates(self._offset, timeout)
        for upd in updates:
            uid = upd.get("update_id")
            if isinstance(uid, int):
                self._offset = uid + 1
            self.handle_update(upd)
        return len(updates)

    def _cmd_stop(self, _args: list[str]) -> str:
        closed = self._controller.stop(source="telegram")
        return f"🔴 Kill switch ON. Closed: {', '.join(closed) if closed else 'none'}"
