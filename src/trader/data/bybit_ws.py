"""Phase 2A: Bybit V5 WebSocket client.

Runs in a daemon thread with its own asyncio event loop.
Reconnects with exponential back-off + jitter (1 s → 60 s cap).
Subscribes to kline.5, kline.15, orderbook.50, tickers, publicTrade per symbol.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import ssl
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_WS_URL = "wss://stream.bybit.com/v5/public/linear"
_PING_INTERVAL = 20  # seconds
_MAX_BACKOFF = 60  # seconds
_MIN_BACKOFF = 1  # seconds


def _build_topics(symbols: list[str]) -> list[str]:
    topics: list[str] = []
    for sym in symbols:
        topics += [
            f"kline.5.{sym}",
            f"kline.15.{sym}",
            f"kline.60.{sym}",
            f"kline.240.{sym}",
            f"kline.D.{sym}",
            f"orderbook.50.{sym}",
            f"tickers.{sym}",
            f"publicTrade.{sym}",
        ]
    return topics


class BybitWsClient:
    """WebSocket client for Bybit V5 public linear stream.

    Usage::

        def on_msg(msg: dict) -> None:
            print(msg)

        client = BybitWsClient(["XRPUSDT", "DOGEUSDT"], on_msg)
        client.start()
        ...
        client.stop()
    """

    def __init__(
        self,
        symbols: list[str],
        on_message_callback: Callable[[dict[str, Any]], None],
        ws_url: str = _WS_URL,
    ) -> None:
        self._symbols = symbols
        self._on_message = on_message_callback
        self._ws_url = ws_url
        self._stop_event = threading.Event()
        self._connected = False
        self._reconnect_count = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="bybit-ws")
        self._thread.start()
        logger.info("BybitWsClient started", extra={"symbols": self._symbols})

    def stop(self) -> None:
        """Signal the background thread to stop and wait for it."""
        self._stop_event.set()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._connected = False
        logger.info("BybitWsClient stopped")

    def is_connected(self) -> bool:
        return self._connected

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    # ------------------------------------------------------------------
    # Internal: thread entry point
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._reconnect_loop())
        finally:
            self._loop.close()

    async def _reconnect_loop(self) -> None:
        backoff = float(_MIN_BACKOFF)
        while not self._stop_event.is_set():
            try:
                await self._connect_and_run()
                backoff = float(_MIN_BACKOFF)  # reset on clean exit
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                self._connected = False
                self._reconnect_count += 1
                jitter = random.uniform(0, backoff * 0.2)
                wait = min(backoff + jitter, _MAX_BACKOFF)
                logger.warning(
                    "BybitWsClient disconnected, reconnecting",
                    extra={
                        "error": str(exc),
                        "backoff_s": round(wait, 2),
                        "reconnect_count": self._reconnect_count,
                    },
                )
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, _MAX_BACKOFF)

    async def _connect_and_run(self) -> None:
        try:
            import websockets  # noqa: PLC0415 — optional import kept local
            import websockets.exceptions  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("websockets library is required: uv add websockets") from exc

        topics = _build_topics(self._symbols)
        subscribe_msg = json.dumps({"op": "subscribe", "args": topics})

        try:
            import certifi  # noqa: PLC0415

            _ssl_ctx: ssl.SSLContext | bool = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            _ssl_ctx = True  # fallback: default SSL context

        logger.info("BybitWsClient connecting", extra={"url": self._ws_url})
        async with websockets.connect(
            self._ws_url,
            ssl=_ssl_ctx,
            open_timeout=10,
            ping_interval=None,  # we handle pings manually
            close_timeout=5,
        ) as ws:
            logger.info("BybitWsClient connected")
            await ws.send(subscribe_msg)

            # Schedule periodic pings
            ping_task = asyncio.create_task(self._ping_loop(ws))
            try:
                async for raw in ws:
                    if self._stop_event.is_set():
                        break
                    try:
                        msg: dict[str, Any] = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.debug("BybitWsClient non-JSON frame", extra={"raw": raw[:120]})
                        continue

                    op = msg.get("op")
                    if op == "pong":
                        continue  # handled
                    if op == "subscribe":
                        if not msg.get("success", False):
                            logger.warning(
                                "BybitWsClient subscription rejected",
                                extra={"msg": msg},
                            )
                        else:
                            self._connected = True
                            logger.debug(
                                "BybitWsClient subscribed",
                                extra={"msg": msg},
                            )
                        continue

                    try:
                        self._on_message(msg)
                    except Exception as cb_exc:
                        logger.error(
                            "BybitWsClient callback error",
                            extra={"error": str(cb_exc)},
                            exc_info=True,
                        )
            finally:
                ping_task.cancel()
                self._connected = False

    async def _ping_loop(self, ws: Any) -> None:
        """Send a ping every _PING_INTERVAL seconds to keep the connection alive."""
        while True:
            await asyncio.sleep(_PING_INTERVAL)
            try:
                await ws.send(json.dumps({"op": "ping"}))
            except Exception as exc:
                logger.warning("BybitWsClient ping failed", extra={"error": str(exc)})
                break
