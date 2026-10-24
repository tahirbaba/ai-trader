"""Phase 2A: Base collector ABC and CollectorResult dataclass."""

from __future__ import annotations

import datetime
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trader.data.bybit_rest import BybitRestClient


@dataclass
class CollectorResult:
    """Container for a single collector's output."""

    symbol: str
    ts: datetime.datetime
    data: dict[str, Any]
    quality_flags: list[str] = field(default_factory=list)


class BaseCollector(ABC):
    """Abstract base for all market-data collectors."""

    def __init__(self, symbol: str, rest_client: BybitRestClient) -> None:
        self.symbol = symbol
        self._rest = rest_client

    @abstractmethod
    def collect(self) -> CollectorResult:
        """Return the latest snapshot for this collector."""
        ...

    @abstractmethod
    def backfill(self, limit: int) -> list[CollectorResult]:
        """Return up to *limit* historical snapshots (oldest first)."""
        ...
