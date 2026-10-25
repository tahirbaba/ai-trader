"""Persist a FeaturePack to the DSOR as a MarketSnapshot ORM row (Phase 3)."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from trader.dsor.models import MarketSnapshot
from trader.features.feature_pack import FeaturePack


def save_feature_pack(
    session: Session,
    fp: FeaturePack,
    session_tag: str = "",
) -> MarketSnapshot:
    """Convert *fp* into a :class:`MarketSnapshot` row and add it to *session*.

    The caller is responsible for committing the session.  This function
    never calls ``session.commit()``.
    """
    ohlcv = {
        "close": fp.close,
        "high": fp.high,
        "low": fp.low,
        "volume": fp.volume,
    }
    indicators = {
        "ema_20": fp.ema_20,
        "ema_50": fp.ema_50,
        "ema_200": fp.ema_200,
        "sma_20": fp.sma_20,
        "rsi_14": fp.rsi_14,
        "macd_line": fp.macd_line,
        "macd_signal": fp.macd_signal,
        "macd_hist": fp.macd_hist,
        "atr_14": fp.atr_14,
        "bb_upper": fp.bb_upper,
        "bb_mid": fp.bb_mid,
        "bb_lower": fp.bb_lower,
        "bb_pct_b": fp.bb_pct_b,
        "vwap": fp.vwap,
    }
    orderflow = {
        "cvd": fp.cvd,
        "cvd_divergence_bear": fp.cvd_divergence_bear,
        "cvd_divergence_bull": fp.cvd_divergence_bull,
        "book_imbalance": fp.book_imbalance,
        "spread_pct": fp.spread_pct,
        "spread_percentile": fp.spread_percentile_val,
    }

    snap = MarketSnapshot(
        ts=fp.ts,
        symbol=fp.symbol,
        timeframe=fp.timeframe,
        ohlcv_json=json.dumps(ohlcv),
        indicators_json=json.dumps(indicators),
        funding=fp.funding_rate,
        open_interest=fp.oi,
        basis=fp.basis,
        orderflow_json=json.dumps(orderflow),
        regime=fp.regime,
        session=session_tag if session_tag else None,
        confluence_score=fp.confluence_long,
        data_quality_flags=json.dumps(fp.data_quality_flags),
        features_hash=fp.features_hash,
    )
    session.add(snap)
    return snap
