"""Phase 1: Clock drift check between host and exchange server time."""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

from trader.dsor.repositories import SystemStateRepository


def check_clock_drift(session: Session, exchange_time_ms: int) -> float:
    """Compute |host_ms - exchange_ms|, persist it, and return the drift in ms."""
    host_ms = int(time.time() * 1000)
    drift = abs(host_ms - exchange_time_ms)
    repo = SystemStateRepository(session)
    repo.set("last_clock_drift_ms", str(drift))
    return float(drift)
