"""Phase 2B: Sentiment collector — Fear & Greed index."""

from __future__ import annotations

import datetime
import logging
from typing import Any

import requests

from trader.data.collector import CollectorResult

logger = logging.getLogger(__name__)

_FNG_URL = "https://api.alternative.me/fng/?limit=1"
_REQUEST_TIMEOUT = 10


class SentimentCollector:
    """Collect the Crypto Fear & Greed index.

    Parameters
    ----------
    symbols:
        Unused; kept for interface parity.
    rest_client:
        Unused; kept for interface parity.
    enabled:
        When False, ``collect()`` returns an empty result with ``["disabled"]``.
    """

    def __init__(
        self,
        symbols: list[str] | None = None,
        rest_client: Any = None,
        enabled: bool = True,
    ) -> None:
        self._symbols = symbols or []
        self._rest = rest_client
        self._enabled = enabled

    def collect(self) -> CollectorResult:
        """Return Fear & Greed data (or disabled/error result)."""
        now = datetime.datetime.now(datetime.UTC)

        if not self._enabled:
            return CollectorResult(
                symbol="SENTIMENT",
                ts=now,
                data={},
                quality_flags=["disabled"],
            )

        try:
            resp = requests.get(_FNG_URL, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json()
            record = payload["data"][0]

            value = int(record["value"])
            label: str = record["value_classification"]
            ts_unix = int(record["timestamp"])
            ts = datetime.datetime.fromtimestamp(ts_unix, datetime.UTC)

            return CollectorResult(
                symbol="SENTIMENT",
                ts=now,
                data={
                    "fear_greed_value": value,
                    "fear_greed_label": label,
                    "ts": ts,
                },
                quality_flags=[],
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning("Sentiment API error: %s", exc)
            return CollectorResult(
                symbol="SENTIMENT",
                ts=now,
                data={},
                quality_flags=["sentiment_error"],
            )

    def backfill(self, limit: int) -> list[CollectorResult]:
        """Not supported; returns empty list."""
        return []
