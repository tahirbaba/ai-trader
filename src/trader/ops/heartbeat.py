"""Phase 9: External dead-man heartbeat.

The app pings HEALTHCHECK_PING_URL (e.g. healthchecks.io) every minute. If the
pings stop — app crashed, host down, network gone — the EXTERNAL service alerts
the owner. An in-process monitor cannot report its own death.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import requests

logger = logging.getLogger(__name__)


class Heartbeat:
    def __init__(
        self,
        ping_url: str,
        timeout_seconds: float = 10.0,
        http_get: Callable[..., Any] | None = None,
    ) -> None:
        self._url = ping_url.strip()
        self._timeout = timeout_seconds
        self._get = http_get or requests.get
        if not self._url:
            logger.warning("HEALTHCHECK_PING_URL not set — external dead-man alert DISABLED")

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    def ping(self) -> bool:
        """Send one ping. Never raises; returns True on HTTP 2xx."""
        if not self._url:
            return False
        try:
            resp = self._get(self._url, timeout=self._timeout)
            ok = 200 <= int(resp.status_code) < 300
            if not ok:
                logger.warning("heartbeat ping returned HTTP %s", resp.status_code)
            return ok
        except Exception as exc:  # noqa: BLE001
            # Never log the URL: healthcheck URLs embed a secret UUID.
            logger.warning("heartbeat ping failed: %s", type(exc).__name__)
            return False
