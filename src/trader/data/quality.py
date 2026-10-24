"""Phase 2A: Data-quality monitor.

Checks:
- staleness   → "stale_data"     when latest_ts is older than max_data_age_seconds
- outlier     → "outlier_price"  when latest price deviates > 5σ from recent history
- gap         → "gap_detected"   when price history contains a gap (None / zero values)
- clock drift → "clock_drift"    when |drift_ms| > max_clock_drift_ms
"""

from __future__ import annotations

import datetime
import logging
import math

logger = logging.getLogger(__name__)


class DataQualityMonitor:
    """Stateless quality checker; returns a list of flag strings."""

    def check(
        self,
        symbol: str,
        latest_ts: datetime.datetime | None,
        max_data_age_seconds: int,
        price_history: list[float],
        clock_drift_ms: float = 0.0,
        max_clock_drift_ms: int = 1000,
    ) -> list[str]:
        """Inspect the supplied data and return a list of quality flag strings.

        Parameters
        ----------
        symbol:
            Instrument symbol (for logging).
        latest_ts:
            UTC timestamp of the most-recently received market data tick.
            Pass ``None`` if data has never been received.
        max_data_age_seconds:
            Maximum allowed age (in seconds) before "stale_data" is raised.
        price_history:
            Ordered list of recent close prices (latest last).
            Must contain at least 2 items for gap/outlier checks.
        clock_drift_ms:
            Measured drift between local clock and exchange server time (ms).
        max_clock_drift_ms:
            Threshold above which "clock_drift" is flagged.
        """
        flags: list[str] = []
        now = datetime.datetime.utcnow()

        # --- Staleness ---------------------------------------------------
        if latest_ts is None:
            flags.append("stale_data")
        else:
            age_s = (now - latest_ts).total_seconds()
            if age_s > max_data_age_seconds:
                flags.append("stale_data")
                logger.debug(
                    "DataQualityMonitor stale_data",
                    extra={"symbol": symbol, "age_s": round(age_s, 1)},
                )

        # --- Clock drift -------------------------------------------------
        if abs(clock_drift_ms) > max_clock_drift_ms:
            flags.append("clock_drift")
            logger.debug(
                "DataQualityMonitor clock_drift",
                extra={"symbol": symbol, "drift_ms": clock_drift_ms},
            )

        # --- Gap detected ------------------------------------------------
        if len(price_history) >= 2:
            for price in price_history:
                if price is None or price == 0.0:
                    flags.append("gap_detected")
                    break

        # --- Outlier detection (> 5σ) ------------------------------------
        if len(price_history) >= 3:
            latest_price = price_history[-1]
            # Filter out None and zero values from history for statistical calc
            history = [p for p in price_history[:-1] if p is not None and p != 0.0]
            n = len(history)
            if n >= 2 and latest_price is not None:
                mean = sum(history) / n
                variance = sum((p - mean) ** 2 for p in history) / n
                std = math.sqrt(variance)
                if std > 0 and abs(latest_price - mean) > 5 * std:
                    flags.append("outlier_price")
                logger.debug(
                    "DataQualityMonitor outlier_price",
                    extra={
                        "symbol": symbol,
                        "price": latest_price,
                        "mean": round(mean, 6),
                        "std": round(std, 6),
                    },
                )

        return flags
