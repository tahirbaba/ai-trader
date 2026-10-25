"""Tests for src/trader/risk/sizing.py — position sizing, liquidation, RR."""

from __future__ import annotations

import math

import pytest

from trader.risk.sizing import (
    SizingError,
    check_risk_reward,
    compute_liquidation_price,
    compute_size,
)

# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------

MAKER = 0.0002
TAKER = 0.00055
MAX_MARGIN = 2.0
MAX_LOSS = 1.0
MAX_LEV = 3.0
SLIP = 0.05
LOT = 1.0
MIN_QTY = 1.0


def _long_base() -> dict:
    return dict(
        side="long",
        entry=0.5,
        stop=0.45,
        take_profit=0.60,
        lot_size=LOT,
        min_qty=MIN_QTY,
        maker_fee_rate=MAKER,
        taker_fee_rate=TAKER,
        max_margin_usd=MAX_MARGIN,
        max_loss_usd=MAX_LOSS,
        max_leverage=MAX_LEV,
        slippage_buffer_pct=SLIP,
    )


def _short_base() -> dict:
    return dict(
        side="short",
        entry=0.5,
        stop=0.55,
        take_profit=0.40,
        lot_size=LOT,
        min_qty=MIN_QTY,
        maker_fee_rate=MAKER,
        taker_fee_rate=TAKER,
        max_margin_usd=MAX_MARGIN,
        max_loss_usd=MAX_LOSS,
        max_leverage=MAX_LEV,
        slippage_buffer_pct=SLIP,
    )


# ------------------------------------------------------------------
# compute_size — long
# ------------------------------------------------------------------


def test_long_sizing_basic():
    """Long: qty, loss_at_stop, margin all within caps."""
    qty, loss, margin = compute_size(**_long_base())

    # qty must be >= 1 (min_qty)
    assert qty >= MIN_QTY
    # loss_at_stop must not exceed cap
    assert loss <= MAX_LOSS + 1e-9
    # margin must not exceed cap
    assert margin <= MAX_MARGIN + 1e-9
    # qty must be a multiple of lot_size
    assert abs(round(qty / LOT) * LOT - qty) < 1e-9


def test_long_sizing_loss_at_stop_formula():
    """Verify loss_at_stop uses the exact formula from the spec."""
    kw = _long_base()
    qty, loss, _margin = compute_size(**kw)

    stop_dist = abs(kw["entry"] - kw["stop"])
    stop_dist_slip = stop_dist * (1 + SLIP)
    expected_loss = qty * stop_dist_slip + kw["entry"] * qty * TAKER + kw["stop"] * qty * TAKER
    assert abs(loss - expected_loss) < 1e-9


# ------------------------------------------------------------------
# compute_size — short
# ------------------------------------------------------------------


def test_short_sizing_basic():
    """Short: qty, loss_at_stop, margin all within caps."""
    qty, loss, margin = compute_size(**_short_base())

    assert qty >= MIN_QTY
    assert loss <= MAX_LOSS + 1e-9
    assert margin <= MAX_MARGIN + 1e-9
    assert abs(round(qty / LOT) * LOT - qty) < 1e-9


# ------------------------------------------------------------------
# Minimum qty rejection
# ------------------------------------------------------------------


def test_below_min_qty_raises():
    """If computed qty < min_qty, SizingError must be raised."""
    kw = _long_base()
    # Tiny entry, large stop, tight caps → resulting qty will be < 1
    kw["entry"] = 0.001
    kw["stop"] = 0.0001
    kw["max_loss_usd"] = 0.0001
    kw["max_margin_usd"] = 0.0001
    with pytest.raises(SizingError, match="below minimum qty"):
        compute_size(**kw)


# ------------------------------------------------------------------
# Rounding: raw_qty not a multiple of lot_size → rounds down
# ------------------------------------------------------------------


def test_rounding_down_to_lot_size():
    """Raw qty that is not a multiple of lot_size must be floored."""
    kw = _long_base()
    kw["lot_size"] = 5.0
    kw["min_qty"] = 5.0
    qty, _loss, _margin = compute_size(**kw)
    # qty must be a multiple of 5
    assert qty % 5.0 < 1e-9


def test_rounded_qty_loss_at_stop_within_cap():
    """After rounding, loss_at_stop must still be <= max_loss_usd."""
    kw = _long_base()
    kw["lot_size"] = 5.0
    kw["min_qty"] = 5.0
    qty, loss, _margin = compute_size(**kw)
    assert loss <= MAX_LOSS + 1e-9


# ------------------------------------------------------------------
# Degenerate inputs
# ------------------------------------------------------------------


def test_entry_equals_stop_raises():
    """entry == stop → stop_distance zero → SizingError."""
    kw = _long_base()
    kw["stop"] = kw["entry"]
    with pytest.raises(SizingError, match="zero"):
        compute_size(**kw)


def test_negative_entry_raises():
    """Negative entry price → SizingError."""
    kw = _long_base()
    kw["entry"] = -1.0
    with pytest.raises(SizingError):
        compute_size(**kw)


# ------------------------------------------------------------------
# compute_liquidation_price
# ------------------------------------------------------------------


def test_long_liq_is_below_entry():
    """For a long position, liquidation price must be below entry."""
    qty, _loss, margin = compute_size(**_long_base())
    liq = compute_liquidation_price(side="long", entry=0.5, qty=qty, margin=margin)
    assert liq < 0.5, f"Expected liq < entry=0.5, got liq={liq}"


def test_short_liq_is_above_entry():
    """For a short position, liquidation price must be above entry."""
    qty, _loss, margin = compute_size(**_short_base())
    liq = compute_liquidation_price(side="short", entry=0.5, qty=qty, margin=margin)
    assert liq > 0.5, f"Expected liq > entry=0.5, got liq={liq}"


def test_liq_formula_long():
    """Verify long liq formula: entry - (margin - qty*entry*mmr) / qty."""
    qty = 10.0
    entry = 0.5
    margin = 1.0
    mmr = 0.005
    expected = entry - (margin - qty * entry * mmr) / qty
    result = compute_liquidation_price("long", entry, qty, margin, mmr)
    assert abs(result - expected) < 1e-9


def test_liq_formula_short():
    """Verify short liq formula: entry + (margin - qty*entry*mmr) / qty."""
    qty = 10.0
    entry = 0.5
    margin = 1.0
    mmr = 0.005
    expected = entry + (margin - qty * entry * mmr) / qty
    result = compute_liquidation_price("short", entry, qty, margin, mmr)
    assert abs(result - expected) < 1e-9


def test_liq_invalid_side_raises():
    """Invalid side raises SizingError."""
    with pytest.raises(SizingError, match="side"):
        compute_liquidation_price("flat", 0.5, 10.0, 1.0)


def test_liq_zero_qty_raises():
    """Zero qty raises SizingError."""
    with pytest.raises(SizingError):
        compute_liquidation_price("long", 0.5, 0.0, 1.0)


# ------------------------------------------------------------------
# check_risk_reward
# ------------------------------------------------------------------


def test_rr_passes_when_tp_far_enough():
    """RR passes when take_profit is well above min_rr."""
    # entry=0.5, stop=0.45 (risk=0.05), tp=0.60 (reward=0.10) → RR=2 before fees
    passes, rr = check_risk_reward(
        side="long",
        entry=0.5,
        stop=0.45,
        take_profit=0.60,
        qty=10.0,
        maker_fee_rate=MAKER,
        taker_fee_rate=TAKER,
        min_rr=1.5,
    )
    assert passes is True
    assert rr > 1.5


def test_rr_fails_when_tp_too_close():
    """RR fails when take_profit barely moves (reward < min_rr * risk)."""
    # entry=0.5, stop=0.45, tp=0.51 → tiny reward
    passes, rr = check_risk_reward(
        side="long",
        entry=0.5,
        stop=0.45,
        take_profit=0.51,
        qty=10.0,
        maker_fee_rate=MAKER,
        taker_fee_rate=TAKER,
        min_rr=1.5,
    )
    assert passes is False
    assert rr < 1.5


def test_rr_short_passes():
    """Short RR check: stop above entry, tp below entry."""
    # entry=0.5, stop=0.55, tp=0.40 → risk=0.05, reward=0.10 → RR~2
    passes, rr = check_risk_reward(
        side="short",
        entry=0.5,
        stop=0.55,
        take_profit=0.40,
        qty=10.0,
        maker_fee_rate=MAKER,
        taker_fee_rate=TAKER,
        min_rr=1.5,
    )
    assert passes is True
    assert rr > 1.5


def test_rr_returns_false_when_reward_negative():
    """check_risk_reward returns False when reward_after_fees is negative (TP below entry fee cost)."""
    # Take-profit exactly at entry → tp_distance=0, reward_after_fees = -entry_fee - tp_fee < 0
    # With negative reward, rr < 0 → fails
    passes, rr = check_risk_reward(
        side="long",
        entry=0.5,
        stop=0.45,
        take_profit=0.5,  # TP at entry → zero gross reward but fees subtract
        qty=10.0,
        maker_fee_rate=MAKER,
        taker_fee_rate=TAKER,
        min_rr=1.5,
    )
    assert passes is False
    assert rr < 1.5


def test_rr_returns_false_when_risk_zero():
    """check_risk_reward returns (False, 0.0) when risk_after_fees <= 0."""
    # To make risk_after_fees <= 0, entry must equal stop (stop_distance=0)
    # and qty * stop_distance + entry_fee + stop_fee could be 0 or negative.
    # With entry==stop, stop_distance=0; fees are both entry*qty*taker
    # risk_after_fees = 0 + entry*qty*taker + entry*qty*taker = positive
    # So to hit the guard, we need very high negative fees — not realistic.
    # Instead test with qty=0 → risk_after_fees = fees only but positive
    # The guard fires when risk_after_fees <= 0: use negative values trick via
    # a degenerate qty=0 (zero fees too)
    passes, rr = check_risk_reward(
        side="long",
        entry=0.5,
        stop=0.5,  # stop == entry → stop_distance = 0
        take_profit=0.6,
        qty=0.0,  # zero qty → all terms zero → risk_after_fees = 0
        maker_fee_rate=MAKER,
        taker_fee_rate=TAKER,
        min_rr=1.5,
    )
    assert passes is False
    assert rr == 0.0


# ------------------------------------------------------------------
# compute_size — additional branch coverage
# ------------------------------------------------------------------


def test_lot_size_zero_raises():
    """lot_size <= 0 → SizingError."""
    kw = _long_base()
    kw["lot_size"] = 0.0
    with pytest.raises(SizingError, match="lot_size"):
        compute_size(**kw)


def test_max_leverage_zero_raises():
    """max_leverage <= 0 → SizingError."""
    kw = _long_base()
    kw["max_leverage"] = 0.0
    with pytest.raises(SizingError, match="max_leverage"):
        compute_size(**kw)


def test_final_loss_exceeds_cap_raises(monkeypatch):
    """Post-rounding safety guard: if final_loss > max_loss_usd, SizingError is raised.

    This guard is defensive — it should never trigger in normal usage because
    rounding down only reduces qty. We use monkeypatch to simulate the edge case.
    """
    import trader.risk.sizing as sizing_module

    original_floor = math.floor

    # Make math.floor return a value *larger* than raw_qty to simulate the pathological case
    call_count = 0

    def fake_floor(x: float) -> int:
        nonlocal call_count
        call_count += 1
        result = original_floor(x)
        # On the first call (floor(raw_qty / lot_size)), return a larger value
        if call_count == 1:
            return result + 1000  # inflate qty → loss will exceed cap
        return result

    monkeypatch.setattr(sizing_module.math, "floor", fake_floor)

    kw = _long_base()
    with pytest.raises(SizingError, match="exceeds max_loss_usd"):
        compute_size(**kw)
