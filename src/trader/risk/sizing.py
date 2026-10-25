"""Phase 5: Pure-function position sizing, liquidation, and RR checks.

No I/O. All functions are deterministic and independently testable.
SizingError is defined here and raised when a position cannot be sized within limits.
"""

from __future__ import annotations

import math


class SizingError(Exception):
    """Raised when a position cannot be sized within the given limits."""


def compute_size(
    side: str,
    entry: float,
    stop: float,
    take_profit: float,
    lot_size: float,
    min_qty: float,
    maker_fee_rate: float,
    taker_fee_rate: float,
    max_margin_usd: float,
    max_loss_usd: float,
    max_leverage: float,
    slippage_buffer_pct: float,
) -> tuple[float, float, float]:
    """Compute position size within loss and margin caps.

    Parameters
    ----------
    side : "long" | "short"
    entry : entry price
    stop : stop-loss price
    take_profit : take-profit price (used for RR check elsewhere, not sizing)
    lot_size : exchange minimum lot increment (e.g. 1.0 for XRP)
    min_qty : exchange minimum order quantity
    maker_fee_rate : maker fee as a fraction (e.g. 0.0002)
    taker_fee_rate : taker fee as a fraction (e.g. 0.00055)
    max_margin_usd : hard cap on margin per trade
    max_loss_usd : hard cap on loss at stop per trade
    max_leverage : maximum allowed leverage
    slippage_buffer_pct : fraction added to stop distance for conservative loss estimate

    Returns
    -------
    (qty, loss_at_stop_usd, margin_used_usd)

    Raises
    ------
    SizingError
        If the resulting qty is below min_qty, or sizing inputs are degenerate.
    """
    if entry <= 0:
        raise SizingError(f"entry price must be positive, got {entry}")
    if lot_size <= 0:
        raise SizingError(f"lot_size must be positive, got {lot_size}")
    if max_leverage <= 0:
        raise SizingError(f"max_leverage must be positive, got {max_leverage}")

    stop_distance = abs(entry - stop)
    if stop_distance == 0:
        raise SizingError("stop_distance is zero (entry == stop)")

    stop_distance_with_slippage = stop_distance * (1.0 + slippage_buffer_pct)

    # Denominator for loss constraint: loss_at_stop per unit qty
    # loss_at_stop = qty * stop_distance_with_slippage + entry * qty * taker_fee_rate + stop * qty * taker_fee_rate
    loss_per_unit = stop_distance_with_slippage + entry * taker_fee_rate + stop * taker_fee_rate
    max_qty_from_loss = max_loss_usd / loss_per_unit

    # Margin constraint: notional = entry * qty; margin = notional / max_leverage <= max_margin_usd
    max_qty_from_margin = max_margin_usd * max_leverage / entry

    raw_qty = min(max_qty_from_loss, max_qty_from_margin)

    # Round DOWN to lot_size
    qty = math.floor(raw_qty / lot_size) * lot_size

    if qty < min_qty:
        raise SizingError(
            f"below minimum qty: computed {qty} < min_qty {min_qty} "
            f"(raw_qty={raw_qty:.6f}, lot_size={lot_size})"
        )

    # Verify final loss_at_stop does not exceed cap (rounding should only reduce it)
    final_loss = (
        qty * stop_distance_with_slippage
        + entry * qty * taker_fee_rate
        + stop * qty * taker_fee_rate
    )
    if final_loss > max_loss_usd + 1e-9:
        raise SizingError(
            f"loss_at_stop {final_loss:.6f} exceeds max_loss_usd {max_loss_usd} after rounding"
        )

    margin_used = qty * entry / max_leverage

    return (qty, final_loss, margin_used)


def compute_liquidation_price(
    side: str,
    entry: float,
    qty: float,
    margin: float,
    maintenance_margin_rate: float = 0.005,
) -> float:
    """Compute isolated-margin liquidation price (Bybit simplified formula).

    Long:  liq = entry - (margin - qty * entry * maintenance_margin_rate) / qty
    Short: liq = entry + (margin - qty * entry * maintenance_margin_rate) / qty

    Parameters
    ----------
    side : "long" | "short"
    entry : entry price
    qty : position quantity in base asset
    margin : margin allocated to this position (notional / leverage)
    maintenance_margin_rate : exchange maintenance margin rate (Bybit default ~0.5%)

    Returns
    -------
    float : liquidation price
    """
    if qty <= 0:
        raise SizingError(f"qty must be positive, got {qty}")

    maintenance_margin = qty * entry * maintenance_margin_rate
    effective_margin = margin - maintenance_margin

    if side == "long":
        liq = entry - effective_margin / qty
    elif side == "short":
        liq = entry + effective_margin / qty
    else:
        raise SizingError(f"side must be 'long' or 'short', got {side!r}")

    return liq


def check_risk_reward(
    side: str,
    entry: float,
    stop: float,
    take_profit: float,
    qty: float,
    maker_fee_rate: float,
    taker_fee_rate: float,
    min_rr: float,
) -> tuple[bool, float]:
    """Check if risk-reward ratio meets minimum after fees.

    RR = reward_after_fees / risk_after_fees

    For entry/exit fees we use taker rate on both legs conservatively.

    Parameters
    ----------
    side : "long" | "short"
    entry : entry price
    stop : stop-loss price
    take_profit : take-profit price
    qty : position size in base asset
    maker_fee_rate : maker fee rate
    taker_fee_rate : taker fee rate
    min_rr : minimum acceptable risk-reward ratio

    Returns
    -------
    (passes, actual_rr)
    """
    entry_fee = entry * qty * taker_fee_rate
    stop_fee = stop * qty * taker_fee_rate
    tp_fee = take_profit * qty * taker_fee_rate

    stop_distance = abs(entry - stop)
    tp_distance = abs(take_profit - entry)

    risk_after_fees = qty * stop_distance + entry_fee + stop_fee
    reward_after_fees = qty * tp_distance - entry_fee - tp_fee

    if risk_after_fees <= 0:
        return (False, 0.0)

    actual_rr = reward_after_fees / risk_after_fees
    return (actual_rr >= min_rr, actual_rr)
