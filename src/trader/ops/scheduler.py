"""Phase 9: Minimal asyncio job scheduler.

Each job runs in its own loop with a per-run timeout. A failing job is logged
and reported via ``on_error`` but never stops other jobs. Sync callables run in
a worker thread so blocking I/O never stalls the event loop.
"""

from __future__ import annotations

import asyncio
import datetime
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from trader.ops.alerts import redact_secrets

logger = logging.getLogger(__name__)


@dataclass
class Job:
    name: str
    func: Callable[[], Any]
    interval_seconds: float
    timeout_seconds: float = 120.0
    run_at_start: bool = True
    daily_at_utc: str | None = None  # "HH:MM" — overrides interval scheduling
    runs: int = field(default=0, init=False)
    failures: int = field(default=0, init=False)
    last_ok_ts: datetime.datetime | None = field(default=None, init=False)


def seconds_until_daily(hhmm: str, now: datetime.datetime) -> float:
    hour, minute = (int(x) for x in hhmm.split(":", 1))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return (target - now).total_seconds()


class Scheduler:
    def __init__(
        self,
        on_error: Callable[[str, str], None] | None = None,
        clock: Callable[[], datetime.datetime] | None = None,
    ) -> None:
        self._jobs: list[Job] = []
        self._on_error = on_error
        self._clock = clock or (lambda: datetime.datetime.now(datetime.UTC))

    @property
    def jobs(self) -> list[Job]:
        return list(self._jobs)

    def add(self, job: Job) -> None:
        if any(j.name == job.name for j in self._jobs):
            raise ValueError(f"duplicate job name: {job.name}")
        self._jobs.append(job)

    async def run_job_once(self, job: Job) -> bool:
        job.runs += 1
        try:
            if inspect.iscoroutinefunction(job.func):
                coro: Awaitable[Any] = job.func()
            else:
                coro = asyncio.to_thread(job.func)
            await asyncio.wait_for(coro, timeout=job.timeout_seconds)
            job.last_ok_ts = self._clock()
            return True
        except Exception as exc:  # noqa: BLE001  (incl. TimeoutError)
            job.failures += 1
            detail = redact_secrets(f"{type(exc).__name__}: {exc}")[:500]
            logger.error("job %s failed: %s", job.name, detail)
            if self._on_error is not None:
                try:
                    self._on_error(job.name, detail)
                except Exception:  # noqa: BLE001
                    logger.exception("on_error callback failed")
            return False

    async def _loop(self, job: Job, stop: asyncio.Event) -> None:
        first = True
        while not stop.is_set():
            if job.daily_at_utc is not None:
                delay = seconds_until_daily(job.daily_at_utc, self._clock())
            elif first and job.run_at_start:
                delay = 0.0
            else:
                delay = job.interval_seconds
            first = False
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                return  # stop requested during sleep
            except TimeoutError:
                pass
            await self.run_job_once(job)

    async def run(self, stop: asyncio.Event) -> None:
        tasks = [asyncio.create_task(self._loop(j, stop), name=j.name) for j in self._jobs]
        try:
            await stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
