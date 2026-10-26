"""Phase 9: Kill switch — DSOR flag + local file flag, fail-closed.

Three ways to engage it (BUILD_PLAN Phase 9 task 3):
  * Telegram ``/stop``            -> ``Controller.stop`` -> ``KillSwitch.activate``
  * API ``POST /api/kill-switch`` -> ``Controller.stop`` -> ``KillSwitch.activate``
  * Local file flag (``make kill`` / ``touch data/KILL_SWITCH``)

``is_active()`` is checked before every order and in the position-monitor job.
Any error while reading state counts as ACTIVE (fail-closed).
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trader.dsor.models import RiskEvent
from trader.dsor.system_state import SystemStateHelper

logger = logging.getLogger(__name__)

DEFAULT_FLAG_PATH = Path("data") / "KILL_SWITCH"


@dataclass(frozen=True)
class KillSwitchStatus:
    active: bool
    file_flag: bool
    db_flag: bool | None  # None = DSOR unreadable (treated as active)


class KillSwitch:
    """Authoritative kill-switch state for the running system."""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        flag_path: Path = DEFAULT_FLAG_PATH,
    ) -> None:
        self._session_factory = session_factory
        self._flag_path = flag_path

    @property
    def flag_path(self) -> Path:
        return self._flag_path

    def status(self) -> KillSwitchStatus:
        file_flag = self._file_flag_present()
        db_flag: bool | None
        try:
            session = self._session_factory()
            try:
                db_flag = SystemStateHelper(session).is_kill_switch_on()
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            logger.critical("kill switch state unreadable — treating as ACTIVE: %s", exc)
            db_flag = None
        active = file_flag or db_flag is None or db_flag
        return KillSwitchStatus(active=active, file_flag=file_flag, db_flag=db_flag)

    def is_active(self) -> bool:
        return self.status().active

    def activate(self, source: str, reason: str) -> None:
        """Engage the kill switch. Falls back to the file flag if DSOR is unwritable."""
        try:
            session = self._session_factory()
            try:
                SystemStateHelper(session).set_kill_switch(True)
                session.add(
                    RiskEvent(
                        ts=datetime.datetime.now(datetime.UTC),
                        type="KILL_SWITCH",
                        details=f"source={source} reason={reason}",
                    )
                )
                session.commit()
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            logger.critical("KILL_SWITCH DSOR write failed (%s) — writing file flag", exc)
            self._write_file_flag(f"source={source} reason={reason}")

    def clear_db_flag(self, source: str) -> KillSwitchStatus:
        """Clear the DSOR flag (owner action). The local file flag is never cleared here;
        it must be removed on the host (``make unkill``)."""
        session = self._session_factory()
        try:
            SystemStateHelper(session).set_kill_switch(False)
            session.add(
                RiskEvent(
                    ts=datetime.datetime.now(datetime.UTC),
                    type="KILL_SWITCH_CLEARED",
                    details=f"source={source}",
                )
            )
            session.commit()
        finally:
            session.close()
        return self.status()

    def _file_flag_present(self) -> bool:
        try:
            return self._flag_path.exists()
        except OSError:
            return True

    def _write_file_flag(self, text: str) -> None:
        try:
            self._flag_path.parent.mkdir(parents=True, exist_ok=True)
            self._flag_path.write_text(text + "\n")
        except OSError as exc:
            logger.critical("KILL_SWITCH file flag write failed: %s", exc)
