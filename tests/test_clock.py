"""Tests for Phase 1: clock drift check."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trader.dsor.base import Base
from trader.dsor.repositories import SystemStateRepository
from trader.ops.clock import check_clock_drift


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class TestClockDrift:
    def test_drift_stored_in_system_state(self, session: Session) -> None:
        import time

        host_ms = int(time.time() * 1000)
        exchange_ms = host_ms + 200  # 200 ms ahead
        drift = check_clock_drift(session, exchange_ms)
        assert drift == pytest.approx(200, abs=50)  # allow some execution time
        repo = SystemStateRepository(session)
        stored = repo.get("last_clock_drift_ms")
        assert stored is not None
        assert int(stored) <= 500  # sanity: under 500ms in test env

    def test_negative_drift_is_abs(self, session: Session) -> None:
        import time

        host_ms = int(time.time() * 1000)
        exchange_ms = host_ms - 300
        drift = check_clock_drift(session, exchange_ms)
        assert drift >= 200  # abs value, allow test execution time
