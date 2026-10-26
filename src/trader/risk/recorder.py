"""Phase 5: DSOR write helpers for risk check results and events.

These functions write to the DSOR risk_checks and risk_events tables.
They are called by the orchestrator after RiskGate.evaluate() returns.
"""

from __future__ import annotations

import datetime
import json

from sqlalchemy.orm import Session

from trader.dsor.models import RiskCheck, RiskEvent
from trader.risk.models import RiskVerdict


def record_risk_check(session: Session, decision_id: int, verdict: RiskVerdict) -> None:
    """Write a RiskCheck row to DSOR for a given decision.

    Parameters
    ----------
    session : SQLAlchemy Session
    decision_id : ID of the Decision row this check is for
    verdict : RiskVerdict from RiskGate.evaluate()
    """
    row = RiskCheck(
        decision_id=decision_id,
        approved=verdict.approved,
        rules_failed_json=json.dumps(verdict.rules_failed) if verdict.rules_failed else None,
        adjusted_size=verdict.adjusted_size,
        loss_at_stop_usd=verdict.loss_at_stop_usd,
        liq_price=verdict.liq_price,
        entry_drift_pct=verdict.entry_drift_pct,
    )
    session.add(row)
    session.flush()


def record_risk_event(session: Session, event_type: str, details: str) -> None:
    """Write a RiskEvent row to DSOR.

    Parameters
    ----------
    session : SQLAlchemy Session
    event_type : short type code (e.g. "KILL_SWITCH", "DAILY_LOSS_LIMIT")
    details : human-readable description of the event
    """
    row = RiskEvent(
        ts=datetime.datetime.now(datetime.UTC),
        type=event_type,
        details=details,
    )
    session.add(row)
    session.flush()
