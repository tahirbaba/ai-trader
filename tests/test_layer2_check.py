"""Tests for src/trader/execution/layer2_check.py — Layer 2 cap re-check."""

from __future__ import annotations

import pytest

from trader.execution.layer2_check import CapBreachError, layer2_cap_check

# ------------------------------------------------------------------
# Shared defaults matching config/risk.yaml
# ------------------------------------------------------------------

DEFAULTS = dict(
    symbol="XRP/USDT:USDT",
    side="long",
    qty=10.0,
    entry_price=0.5,
    stop_price=0.45,
    lot_size=1.0,
    max_leverage=3.0,
    max_margin_usd=2.0,
    max_loss_usd=1.0,
    slippage_buffer_pct=0.05,
    taker_fee_rate=0.00055,
)


def _call(**overrides) -> None:
    kw = DEFAULTS.copy()
    kw.update(overrides)
    layer2_cap_check(**kw)


# ------------------------------------------------------------------
# Valid order — no exception
# ------------------------------------------------------------------


def test_valid_order_no_exception():
    """A valid order within all limits raises no exception."""
    # qty=10, entry=0.5, stop=0.45
    # margin = 10 * 0.5 / 3 = 1.667 <= 2.0 ✓
    # stop_distance = 0.05, with_slip = 0.0525
    # loss = 10 * 0.0525 + 10*0.5*0.00055 + 10*0.45*0.00055
    #       = 0.525 + 0.00275 + 0.002475 = 0.530225 <= 1.0 ✓
    _call()  # should not raise


def test_valid_small_qty_no_exception():
    """Small qty within limits raises no exception."""
    _call(qty=1.0)


# ------------------------------------------------------------------
# Margin cap breach
# ------------------------------------------------------------------


def test_margin_breach_raises():
    """Order that would exceed max_margin_usd raises CapBreachError."""
    # qty=15, entry=0.5 → notional=7.5, margin=2.5 > 2.0
    with pytest.raises(CapBreachError, match="margin"):
        _call(qty=15.0)


def test_margin_exactly_at_cap_passes():
    """Order where margin == max_margin_usd exactly should pass (within tolerance)."""
    # margin = qty * 0.5 / 3 = 2.0 → qty = 12
    # Also verify loss_at_stop is ok
    # loss ≈ 12 * 0.05 * 1.05 + 12 * 0.5 * 0.00055 + 12 * 0.45 * 0.00055
    #       = 0.63 + 0.0033 + 0.00297 = 0.636 <= 1.0 ✓
    _call(qty=12.0)


# ------------------------------------------------------------------
# Loss-at-stop cap breach
# ------------------------------------------------------------------


def test_loss_at_stop_breach_raises():
    """Order with loss_at_stop > max_loss_usd raises CapBreachError."""
    # Use a very large stop distance with small lot
    # qty=10, entry=0.5, stop=0.30 → stop_distance=0.20, with_slip=0.21
    # loss = 10*0.21 + fees ≈ 2.1 > 1.0
    with pytest.raises(CapBreachError, match="loss_at_stop"):
        _call(qty=10.0, stop_price=0.30)


def test_loss_within_cap_passes():
    """Order with loss_at_stop < max_loss_usd passes."""
    # Very small qty → small loss
    _call(qty=1.0, stop_price=0.45)


# ------------------------------------------------------------------
# Invalid inputs
# ------------------------------------------------------------------


def test_zero_qty_raises():
    """qty=0 raises CapBreachError."""
    with pytest.raises(CapBreachError):
        _call(qty=0.0)


def test_negative_entry_raises():
    """Negative entry_price raises CapBreachError."""
    with pytest.raises(CapBreachError):
        _call(entry_price=-1.0)


def test_qty_not_multiple_of_lot_raises():
    """qty not a multiple of lot_size raises CapBreachError."""
    with pytest.raises(CapBreachError, match="lot_size"):
        _call(qty=1.5, lot_size=1.0)


def test_entry_equals_stop_raises():
    """entry_price == stop_price raises CapBreachError (zero stop distance)."""
    with pytest.raises(CapBreachError, match="zero"):
        _call(stop_price=0.5)  # same as entry_price=0.5


# ------------------------------------------------------------------
# Independence check — CapBreachError != SizingError
# ------------------------------------------------------------------


def test_cap_breach_error_is_independent():
    """CapBreachError must not be a subclass of SizingError (independence)."""
    from trader.risk.sizing import SizingError

    assert not issubclass(CapBreachError, SizingError)
    assert not issubclass(SizingError, CapBreachError)
