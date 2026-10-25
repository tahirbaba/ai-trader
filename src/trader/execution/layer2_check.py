"""Phase 5: Layer 2 cap re-check — independent enforcement path in the Executor.

This module is intentionally independent of gate.py. It recomputes limits from scratch
using only sizing.py. Never import from gate.py.

The Executor calls layer2_cap_check() immediately before sending any order to the exchange.
If limits are breached, CapBreachError is raised and the order is aborted.
"""

from __future__ import annotations

import math


class CapBreachError(Exception):
    """Raised by layer2_cap_check when an order would breach a hard cap.

    This is a defence-in-depth check: even if RiskGate approved the order,
    the Executor re-verifies independently before sending.
    """


def layer2_cap_check(
    symbol: str,
    side: str,
    qty: float,
    entry_price: float,
    stop_price: float,
    lot_size: float,
    max_leverage: float,
    max_margin_usd: float,
    max_loss_usd: float,
    slippage_buffer_pct: float,
    taker_fee_rate: float,
) -> None:
    """Re-check that an order does not breach margin or loss caps.

    This is called by the Executor immediately before sending an order.
    It recomputes from scratch — it does not trust the RiskGate's verdict.

    Parameters
    ----------
    symbol : trading symbol (for error messages)
    side : "long" | "short"
    qty : proposed order quantity in base asset
    entry_price : proposed entry price
    stop_price : stop-loss price
    lot_size : exchange minimum lot increment
    max_leverage : maximum allowed leverage
    max_margin_usd : hard margin cap per trade (USD)
    max_loss_usd : hard loss cap per trade (USD)
    slippage_buffer_pct : fraction added to stop distance for loss estimate
    taker_fee_rate : taker fee rate

    Raises
    ------
    CapBreachError
        If the order would exceed max_margin_usd or max_loss_usd.
    """
    if qty <= 0:
        raise CapBreachError(f"qty must be positive, got {qty}")
    if entry_price <= 0:
        raise CapBreachError(f"entry_price must be positive, got {entry_price}")

    # Verify qty is a valid multiple of lot_size
    if lot_size > 0:
        rounded = math.floor(qty / lot_size) * lot_size
        if abs(rounded - qty) > 1e-9:
            raise CapBreachError(f"qty {qty} is not a multiple of lot_size {lot_size} for {symbol}")

    # Margin check
    notional = entry_price * qty
    margin = notional / max_leverage
    if margin > max_margin_usd + 1e-9:
        raise CapBreachError(
            f"margin {margin:.4f} USD exceeds max_margin_usd {max_margin_usd} "
            f"for {side} {qty} {symbol} @ {entry_price}"
        )

    # Loss-at-stop check — same formula as compute_size
    stop_distance = abs(entry_price - stop_price)
    if stop_distance == 0:
        raise CapBreachError(f"stop_distance is zero (entry == stop) for {symbol}")
    stop_distance_with_slippage = stop_distance * (1.0 + slippage_buffer_pct)
    entry_fee = entry_price * qty * taker_fee_rate
    exit_fee = stop_price * qty * taker_fee_rate
    loss_at_stop = qty * stop_distance_with_slippage + entry_fee + exit_fee

    if loss_at_stop > max_loss_usd + 1e-9:
        raise CapBreachError(
            f"loss_at_stop {loss_at_stop:.4f} USD exceeds max_loss_usd {max_loss_usd} "
            f"for {side} {qty} {symbol} @ entry={entry_price} stop={stop_price}"
        )
