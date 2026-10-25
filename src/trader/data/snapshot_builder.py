"""Phase 2A: Assemble all collector outputs into a MarketSnapshot DSOR row."""

from __future__ import annotations

import datetime
import json
from typing import Any

from trader.data.collector import CollectorResult
from trader.dsor.models import MarketSnapshot


def build_snapshot(
    symbol: str,
    timeframe: str,
    ohlcv: CollectorResult | None = None,
    book: CollectorResult | None = None,
    orderflow: CollectorResult | None = None,
    derivatives: CollectorResult | None = None,
    session: str | None = None,
    quality_flags: list[str] | None = None,
) -> MarketSnapshot:
    """Assemble collector outputs into a MarketSnapshot ORM instance.

    The *ts* field is taken from the *ohlcv* result if available, otherwise
    from *derivatives*, otherwise now().

    All JSON columns are serialised from the corresponding CollectorResult.data.
    """
    quality_flags = quality_flags or []

    # Accumulate all quality flags from individual collectors
    all_flags: list[str] = list(quality_flags)
    for result in [ohlcv, book, orderflow, derivatives]:
        if result is not None:
            all_flags.extend(result.quality_flags)

    # Determine canonical timestamp
    ts: datetime.datetime = datetime.datetime.now(datetime.UTC)
    for result in [ohlcv, derivatives]:
        if result is not None and result.ts is not None:
            ts = result.ts
            break

    # ---- OHLCV JSON -------------------------------------------------------
    ohlcv_data: dict[str, Any] = {}
    if ohlcv is not None:
        ohlcv_data = ohlcv.data

    # ---- Indicators JSON (book data goes here for now) --------------------
    indicators_data: dict[str, Any] = {}
    if book is not None:
        indicators_data["book"] = book.data

    # ---- Order-flow JSON --------------------------------------------------
    orderflow_data: dict[str, Any] | None = None
    if orderflow is not None:
        orderflow_data = orderflow.data

    # ---- Derivatives fields -----------------------------------------------
    deriv_data: dict[str, Any] = {}
    if derivatives is not None:
        deriv_data = derivatives.data

    funding = deriv_data.get("funding_rate")
    oi = deriv_data.get("open_interest")
    basis = deriv_data.get("basis")

    return MarketSnapshot(
        ts=ts,
        symbol=symbol,
        timeframe=timeframe,
        ohlcv_json=json.dumps(ohlcv_data),
        indicators_json=json.dumps(indicators_data),
        funding=float(funding) if funding is not None else None,
        open_interest=float(oi) if oi is not None else None,
        basis=float(basis) if basis is not None else None,
        orderflow_json=json.dumps(orderflow_data) if orderflow_data is not None else None,
        session=session,
        data_quality_flags=json.dumps(all_flags) if all_flags else json.dumps([]),
    )
