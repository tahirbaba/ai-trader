"""Phase 1: system_state table helpers for mode, kill switch, and counters."""

from __future__ import annotations

from sqlalchemy.orm import Session

from trader.dsor.repositories import SystemStateRepository

_KEY_MODE = "trading_mode"
_KEY_KILL_SWITCH = "kill_switch"
_KEY_DAILY_LOSS = "daily_loss_usd"


class SystemStateHelper:
    """Typed façade over the key-value system_state table."""

    def __init__(self, session: Session) -> None:
        self._repo = SystemStateRepository(session)

    def get_mode(self) -> str:
        return self._repo.get(_KEY_MODE) or "paper"

    def set_mode(self, mode: str) -> None:
        allowed = {"paper"}
        if mode not in allowed:
            raise ValueError(
                f"set_mode('{mode}') rejected. Runtime can only use paper mode. "
                "Switching to live requires owner action (config/live_approval.yaml), "
                "not a runtime call."
            )
        self._repo.set(_KEY_MODE, mode)

    def is_kill_switch_on(self) -> bool:
        return self._repo.get(_KEY_KILL_SWITCH) == "1"

    def set_kill_switch(self, on: bool) -> None:
        self._repo.set(_KEY_KILL_SWITCH, "1" if on else "0")

    def get_daily_loss(self) -> float:
        raw = self._repo.get(_KEY_DAILY_LOSS)
        return float(raw) if raw is not None else 0.0

    def increment_daily_loss(self, usd: float) -> float:
        new_total = self.get_daily_loss() + usd
        self._repo.set(_KEY_DAILY_LOSS, str(new_total))
        return new_total

    def reset_daily_counters(self) -> None:
        self._repo.set(_KEY_DAILY_LOSS, "0.0")
