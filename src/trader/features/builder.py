"""build_feature_pack — deterministic assembler for all feature computations (Phase 3).

No datetime.now(), no randomness.  Same inputs → same FeaturePack.features_hash.
"""

from __future__ import annotations

from datetime import UTC, datetime

from trader.features.alignment import htf_aligned, mtf_alignment
from trader.features.confluence import confluence_score_both
from trader.features.derivatives_features import (
    basis_zscore,
    funding_zscore,
)
from trader.features.derivatives_features import (
    oi_price_divergence as _oi_price_div,
)
from trader.features.feature_pack import FeaturePack
from trader.features.indicators import (
    atr,
    bb_pct,
    bollinger,
    ema,
    macd,
    rsi,
    sma,
    swing_highs,
    swing_lows,
    vwap,
)
from trader.features.orderflow_features import cvd_divergence, spread_percentile
from trader.features.regime import classify_regime

_ALL_TF = ["5m", "15m", "1h", "4h", "1d"]


def _extract_series(candles: list[dict[str, object]], key: str) -> list[float]:
    return [float(c[key]) for c in candles if key in c]  # type: ignore[arg-type]


def _opt_float(val: object) -> float | None:
    """Cast an object value from a dict to float, or None."""
    if val is None:
        return None
    return float(val)  # type: ignore[arg-type]


def _last_or(lst: list[float], default: float = 0.0) -> float:
    return lst[-1] if lst else default


def build_feature_pack(
    symbol: str,
    primary_tf: str,
    candles_by_tf: dict[str, list[dict[str, object]]],
    derivatives: dict[str, object] | None,
    orderflow: dict[str, object] | None,
    book: dict[str, object] | None,
    quality_flags: list[str] | tuple[str, ...] = (),
    # Historical context for z-scores
    funding_history: list[float] | tuple[float, ...] = (),
    oi_history: list[float] | tuple[float, ...] = (),
    basis_history: list[float] | tuple[float, ...] = (),
    cvd_history: list[float] | tuple[float, ...] = (),
    price_history: list[float] | tuple[float, ...] = (),
    atr_history: list[float] | tuple[float, ...] = (),
    spread_history: list[float] | tuple[float, ...] = (),
) -> FeaturePack:
    """Assemble a fully deterministic FeaturePack from raw collector data.

    The caller is responsible for providing historical context lists for
    z-score computations.  This function never calls datetime.now() or
    any non-deterministic function.
    """
    # ------------------------------------------------------------------
    # 1. Extract primary candles
    # ------------------------------------------------------------------
    primary_candles = candles_by_tf.get(primary_tf, [])
    closes = _extract_series(primary_candles, "close")
    highs = _extract_series(primary_candles, "high")
    lows = _extract_series(primary_candles, "low")
    volumes = _extract_series(primary_candles, "volume")

    # Determine timestamp from last candle; fall back to epoch zero
    if primary_candles:
        last_ts_ms = float(primary_candles[-1].get("ts_ms", 0))  # type: ignore[arg-type]
        ts = datetime.fromtimestamp(last_ts_ms / 1000.0, tz=UTC)
    elif closes:
        ts = datetime(1970, 1, 1, tzinfo=UTC)
    else:
        ts = datetime(1970, 1, 1, tzinfo=UTC)

    close = closes[-1] if closes else 0.0
    high = highs[-1] if highs else 0.0
    low = lows[-1] if lows else 0.0
    volume = volumes[-1] if volumes else 0.0

    # ------------------------------------------------------------------
    # 2. Indicators on primary timeframe
    # ------------------------------------------------------------------
    ema_20_val = ema(closes, 20)
    ema_50_val = ema(closes, 50)
    ema_200_val = ema(closes, 200)
    sma_20_val = sma(closes, 20)
    rsi_14_val = rsi(closes, 14)
    macd_line_val, macd_signal_val, macd_hist_val = macd(closes)
    atr_14_val = atr(highs, lows, closes, 14)

    bb_upper_val, bb_mid_val, bb_lower_val = bollinger(closes, 20)
    bb_pct_b_val: float | None = None
    if bb_upper_val is not None and bb_lower_val is not None:
        bb_pct_b_val = bb_pct(close, bb_upper_val, bb_lower_val)

    vwap_val = vwap(closes, volumes)

    # ------------------------------------------------------------------
    # 3. Multi-timeframe alignment
    # ------------------------------------------------------------------
    # Build close-only series for each timeframe
    closes_by_tf: dict[str, list[float]] = {
        tf: _extract_series(candles_by_tf.get(tf, []), "close") for tf in _ALL_TF
    }
    mtf = mtf_alignment(closes_by_tf)

    htf_aligned_up = htf_aligned(mtf, ["1h", "4h", "1d"])
    # Check if all HTF point down
    htf_directions_down = all(mtf.get(k, "neutral") == "down" for k in ["1h", "4h", "1d"])
    htf_aligned_down_val = htf_directions_down and not htf_aligned_up

    # HTF direction string (first non-neutral, or "neutral")
    htf_dir = "neutral"
    for k in ["1d", "4h", "1h"]:
        if mtf.get(k, "neutral") != "neutral":
            htf_dir = mtf[k]
            break

    # EMA cross direction for primary TF
    if ema_20_val is not None and ema_50_val is not None:
        if ema_20_val > ema_50_val:
            ema_cross = "up"
        elif ema_20_val < ema_50_val:
            ema_cross = "down"
        else:
            ema_cross = "neutral"
    else:
        ema_cross = "neutral"

    # ------------------------------------------------------------------
    # 4. Derivatives features
    # ------------------------------------------------------------------
    funding_rate_val: float | None = None
    funding_z_val: float | None = None
    oi_val: float | None = None
    oi_change_pct_val: float | None = None
    oi_divergence_val = False
    basis_val: float | None = None
    basis_z_val: float | None = None

    if derivatives:
        funding_rate_val = _opt_float(derivatives.get("funding_rate"))
        oi_val = _opt_float(derivatives.get("open_interest"))
        basis_val = _opt_float(derivatives.get("basis"))

    # Z-scores from historical context
    if funding_history:
        funding_z_val = funding_zscore(list(funding_history))
    if basis_history:
        basis_z_val = basis_zscore(list(basis_history))

    # OI change pct (if history available)
    if oi_history and len(oi_history) >= 2:
        prev_oi = float(oi_history[-2])
        curr_oi = oi_val if oi_val is not None else (float(oi_history[-1]) if oi_history else None)
        if curr_oi is not None and prev_oi != 0:
            oi_change_pct_val = (curr_oi - prev_oi) / prev_oi * 100.0

    # OI-price divergence
    if oi_history and price_history and len(oi_history) >= 3 and len(price_history) >= 3:
        oi_divergence_val = _oi_price_div(list(oi_history), list(price_history))

    # ------------------------------------------------------------------
    # 5. Order-flow features
    # ------------------------------------------------------------------
    cvd_val: float | None = None
    cvd_div_bear = False
    cvd_div_bull = False
    book_imbalance_val: float | None = None
    spread_pct_val: float | None = None
    spread_pctile_val: float | None = None

    if orderflow:
        cvd_val = _opt_float(orderflow.get("cvd"))
        spread_pct_val = _opt_float(orderflow.get("spread_pct"))

    if book:
        book_imbalance_val = _opt_float(book.get("book_imbalance"))
    elif orderflow:
        book_imbalance_val = _opt_float(orderflow.get("book_imbalance"))

    # CVD divergence (requires history)
    if cvd_history and price_history and len(cvd_history) >= 3 and len(price_history) >= 3:
        _cvd_hist = list(cvd_history)
        _price_hist = list(price_history)
        cvd_div_bear = cvd_divergence(_cvd_hist, _price_hist)
        # Bullish divergence: cvd up, price down — use inverted lists
        cvd_up = _cvd_hist[-1] > _cvd_hist[0]
        price_down = _price_hist[-1] < _price_hist[0]
        cvd_div_bull = bool(cvd_up and price_down)

    # Spread percentile
    if spread_history:
        spread_pctile_val = spread_percentile(list(spread_history))

    # ------------------------------------------------------------------
    # 6. Support / resistance from swing levels
    # ------------------------------------------------------------------
    nearest_support_val: float | None = None
    nearest_resistance_val: float | None = None
    dist_to_support_atr: float | None = None
    dist_to_resistance_atr: float | None = None

    if highs and lows:
        s_highs = swing_highs(highs, window=5)
        s_lows = swing_lows(lows, window=5)

        supports = [s for s in s_lows if s < close]
        resistances = [r for r in s_highs if r > close]

        if supports:
            nearest_support_val = max(supports)
        if resistances:
            nearest_resistance_val = min(resistances)

        if atr_14_val and atr_14_val > 0:
            if nearest_support_val is not None:
                dist_to_support_atr = (close - nearest_support_val) / atr_14_val
            if nearest_resistance_val is not None:
                dist_to_resistance_atr = (nearest_resistance_val - close) / atr_14_val

    # ------------------------------------------------------------------
    # 7. Regime classification
    # ------------------------------------------------------------------
    regime_val = classify_regime(
        close=close,
        ema_20=ema_20_val,
        ema_50=ema_50_val,
        ema_200=ema_200_val,
        atr_14=atr_14_val,
        atr_history=list(atr_history) if atr_history else [],
        htf_aligned_up=htf_aligned_up,
        htf_aligned_down=htf_aligned_down_val,
        has_news_shock=False,
    )

    # ------------------------------------------------------------------
    # 8. Confluence scores
    # ------------------------------------------------------------------
    confluence_long, confluence_short = confluence_score_both(
        htf_aligned=htf_aligned_up or htf_aligned_down_val,
        htf_direction=htf_dir,
        ema_cross=ema_cross,
        rsi=rsi_14_val,
        macd_hist=macd_hist_val,
        funding_z=funding_z_val,
        oi_divergence=oi_divergence_val,
        cvd_divergence_bear=cvd_div_bear,
        cvd_divergence_bull=cvd_div_bull,
        book_imbalance=book_imbalance_val,
        regime=regime_val,
        dist_to_support_atr=dist_to_support_atr,
        dist_to_resistance_atr=dist_to_resistance_atr,
    )

    # ------------------------------------------------------------------
    # 9. Assemble FeaturePack (without hash)
    # ------------------------------------------------------------------
    fp_no_hash = FeaturePack(
        symbol=symbol,
        ts=ts,
        timeframe=primary_tf,
        close=close,
        high=high,
        low=low,
        volume=volume,
        ema_20=ema_20_val,
        ema_50=ema_50_val,
        ema_200=ema_200_val,
        sma_20=sma_20_val,
        rsi_14=rsi_14_val,
        macd_line=macd_line_val,
        macd_signal=macd_signal_val,
        macd_hist=macd_hist_val,
        atr_14=atr_14_val,
        bb_upper=bb_upper_val,
        bb_mid=bb_mid_val,
        bb_lower=bb_lower_val,
        bb_pct_b=bb_pct_b_val,
        vwap=vwap_val,
        trend_5m=mtf.get("5m", "neutral"),
        trend_15m=mtf.get("15m", "neutral"),
        trend_1h=mtf.get("1h", "neutral"),
        trend_4h=mtf.get("4h", "neutral"),
        trend_1d=mtf.get("1d", "neutral"),
        htf_aligned_up=htf_aligned_up,
        htf_aligned_down=htf_aligned_down_val,
        funding_rate=funding_rate_val,
        funding_z=funding_z_val,
        oi=oi_val,
        oi_change_pct=oi_change_pct_val,
        oi_price_divergence=oi_divergence_val,
        basis=basis_val,
        basis_z=basis_z_val,
        cvd=cvd_val,
        cvd_divergence_bear=cvd_div_bear,
        cvd_divergence_bull=cvd_div_bull,
        book_imbalance=book_imbalance_val,
        spread_pct=spread_pct_val,
        spread_percentile_val=spread_pctile_val,
        nearest_support=nearest_support_val,
        nearest_resistance=nearest_resistance_val,
        dist_to_support_atr=dist_to_support_atr,
        dist_to_resistance_atr=dist_to_resistance_atr,
        regime=regime_val,
        confluence_long=confluence_long,
        confluence_short=confluence_short,
        data_quality_flags=list(quality_flags),
        features_hash="",
    )

    # 10. Compute and attach hash (requires model_copy because frozen=True)
    final_hash = fp_no_hash.compute_hash()
    return fp_no_hash.model_copy(update={"features_hash": final_hash})
