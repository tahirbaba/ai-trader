"""FeaturePack — frozen Pydantic model for all computed market features (Phase 3)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FeaturePack(BaseModel):
    """Compact, frozen snapshot of all features for a single bar.

    The ``features_hash`` field is a SHA-256 digest of all numeric fields,
    sorted by key.  It is used as a determinism guard: same numeric inputs
    must always produce the same hash.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    ts: datetime
    timeframe: str

    # OHLCV snapshot
    close: float
    high: float
    low: float
    volume: float

    # Indicators
    ema_20: float | None = None
    ema_50: float | None = None
    ema_200: float | None = None
    sma_20: float | None = None
    rsi_14: float | None = None
    macd_line: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    atr_14: float | None = None
    bb_upper: float | None = None
    bb_mid: float | None = None
    bb_lower: float | None = None
    bb_pct_b: float | None = None
    vwap: float | None = None

    # Multi-timeframe alignment
    trend_5m: str = "neutral"
    trend_15m: str = "neutral"
    trend_1h: str = "neutral"
    trend_4h: str = "neutral"
    trend_1d: str = "neutral"
    htf_aligned_up: bool = False
    htf_aligned_down: bool = False

    # Derivatives
    funding_rate: float | None = None
    funding_z: float | None = None
    oi: float | None = None
    oi_change_pct: float | None = None
    oi_price_divergence: bool = False
    basis: float | None = None
    basis_z: float | None = None

    # Order flow
    cvd: float | None = None
    cvd_divergence_bear: bool = False
    cvd_divergence_bull: bool = False
    book_imbalance: float | None = None
    spread_pct: float | None = None
    spread_percentile_val: float | None = None

    # Support / resistance
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    dist_to_support_atr: float | None = None
    dist_to_resistance_atr: float | None = None

    # Regime + confluence
    regime: str = "range"
    confluence_long: int = 0
    confluence_short: int = 0

    # Data quality
    data_quality_flags: list[str] = Field(default_factory=list)
    features_hash: str = ""

    def compute_hash(self) -> str:
        """Recompute SHA-256 of numeric fields for determinism verification."""
        import hashlib
        import json

        fields = {
            k: v
            for k, v in self.model_dump().items()
            if isinstance(v, int | float) and not isinstance(v, bool) and v is not None
        }
        raw = json.dumps(fields, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()
