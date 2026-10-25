"""Phase pre-8: Price-shock circuit breaker — pure functions, no I/O."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field


@dataclass
class PriceShockResult:
    shock_detected: bool
    signals_fired: list[str] = field(default_factory=list)
    # "news_shock" means a single extreme trigger fired alone
    triggered_by_extreme: bool = False
    data_quality_flags: list[str] = field(default_factory=list)


def compute_price_shock_signals(
    *,
    price_1m_return: float | None,
    price_5m_return: float | None,
    atr_current: float | None,
    atr_baseline: float | None,
    volume_current: float | None,
    volume_baseline: float | None,
    spread_pct_current: float | None,
    spread_pct_baseline: float | None,
    liquidation_volume_current: float | None,
    liquidation_volume_baseline: float | None,
    # thresholds from PriceShockSettings
    min_signals_to_trigger: int,
    extreme_5m_return_pct: float,
    price_1m_return_pct: float,
    price_5m_return_pct: float,
    atr_spike_multiple: float,
    volume_spike_multiple: float,
    spread_spike_pct: float,
    liquidation_spike_multiple: float,
) -> PriceShockResult:
    """Evaluate price-shock signals from market data. Pure function — no side effects.

    Returns PriceShockResult indicating whether a shock was detected and which signals fired.
    A single extreme 5m return (>= extreme_5m_return_pct) triggers alone.
    Otherwise, min_signals_to_trigger normal signals are required.
    When 1m data is absent, 'price_1m_stale' is appended to data_quality_flags.
    """
    signals: list[str] = []
    quality_flags: list[str] = []

    # 1m return — stale flag if unavailable
    if price_1m_return is None:
        quality_flags.append("price_1m_stale")
    elif abs(price_1m_return) >= price_1m_return_pct:
        signals.append("price_1m_return")

    # 5m return
    if price_5m_return is not None and abs(price_5m_return) >= price_5m_return_pct:
        signals.append("price_5m_return")

    # ATR spike
    if (
        atr_current is not None
        and atr_baseline is not None
        and atr_baseline > 0
        and atr_current >= atr_spike_multiple * atr_baseline
    ):
        signals.append("atr_spike")

    # Volume spike
    if (
        volume_current is not None
        and volume_baseline is not None
        and volume_baseline > 0
        and volume_current >= volume_spike_multiple * volume_baseline
    ):
        signals.append("volume_spike")

    # Spread spike
    if (
        spread_pct_current is not None
        and spread_pct_baseline is not None
        and spread_pct_baseline > 0
        and spread_pct_current >= spread_pct_baseline + spread_spike_pct
    ):
        signals.append("spread_spike")

    # Liquidation spike
    if (
        liquidation_volume_current is not None
        and liquidation_volume_baseline is not None
        and liquidation_volume_baseline > 0
        and liquidation_volume_current >= liquidation_spike_multiple * liquidation_volume_baseline
    ):
        signals.append("liquidation_spike")

    # Extreme single-signal trigger (news_shock)
    extreme = (
        price_5m_return is not None
        and abs(price_5m_return) >= extreme_5m_return_pct
    )
    if extreme:
        return PriceShockResult(
            shock_detected=True,
            signals_fired=signals,
            triggered_by_extreme=True,
            data_quality_flags=quality_flags,
        )

    shock = len(signals) >= min_signals_to_trigger
    return PriceShockResult(
        shock_detected=shock,
        signals_fired=signals,
        triggered_by_extreme=False,
        data_quality_flags=quality_flags,
    )


def compute_shock_sl_amendment(
    *,
    side: str,  # "long" | "short"
    entry_price: float,
    current_sl: float,
    current_price: float,
    open_position_risk_cut_pct: float,
    move_sl_to_breakeven_on_favorable_shock: bool,
    shock_is_adverse: bool,
) -> float | None:
    """Compute new SL price for an open position during a price shock.

    Adverse shock: move SL to cut open risk by open_position_risk_cut_pct.
    Favorable shock (price moving in position's favour): move SL to breakeven (entry_price).
    Never widens the SL.
    Returns new SL price, or None if no amendment needed.
    """
    side_lower = side.lower()

    if shock_is_adverse:
        # Cut risk by reducing the distance between entry and stop
        current_risk = abs(entry_price - current_sl)
        reduction = current_risk * (open_position_risk_cut_pct / 100.0)
        if side_lower == "long":
            new_sl = current_sl + reduction
            # Never move SL past entry (would flip it)
            new_sl = min(new_sl, entry_price - 1e-8)
        else:
            new_sl = current_sl - reduction
            new_sl = max(new_sl, entry_price + 1e-8)
        return new_sl

    if move_sl_to_breakeven_on_favorable_shock:
        # Favorable: move SL to breakeven — but only if it TIGHTENS (never widens)
        if side_lower == "long":
            # Long favorable = price went up; breakeven = entry; only tighten if current_sl < entry
            if current_sl < entry_price:
                return entry_price
        else:
            # Short favorable = price went down; breakeven = entry; only tighten if current_sl > entry
            if current_sl > entry_price:
                return entry_price

    return None


def is_shock_active(shock_until: datetime.datetime | None) -> bool:
    """Return True if we are currently within a price-shock block window."""
    if shock_until is None:
        return False
    now = datetime.datetime.now(datetime.UTC)
    if shock_until.tzinfo is None:
        shock_until = shock_until.replace(tzinfo=datetime.UTC)
    return now < shock_until
