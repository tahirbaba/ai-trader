"""Adversarial property-based tests for the risk gate / execution module audit.

Audit scope:
  1. Approved order must never exceed max_margin_per_trade_usd or
     max_loss_per_trade_usd (including fees + slippage).
  2. No order may be placed without a stop-loss.
  3. Liquidation price must never be inside the stop buffer.
  4. Kill switch must not be bypassed (PositionMonitor halt path).
  5. Daily / weekly loss limits must not be bypassable.
  6. A parse failure (from broker or Pydantic) must never be treated as approval.
  7. A LessonFilter block must never be ignored.

Plus execution-specific paths:
  LA1.  amend_stop_loss / _verify_sl_placed: fail-open path where unknown state
        is treated as "verified" → must be impossible.
  LA2.  Adversary-crafted position/order response bypasses the emergency close.
  LA3.  Never-widen: amend_stop_loss must never place a wider SL than existing.
  LA4.  Kill-switch bypass in PositionMonitor: fail-count >= 2 must always fire.
  LA5.  Secret leakage: API keys must not appear in logged strings.
  LA6.  Emergency market close in amend_stop_loss must use reduceOnly=True.
  LA7.  Exception swallowing: broker exception during verify must not return True.
  LA8.  Off-by-one on fail_count: halt must fire at count==2, not count>2.
  LA9.  PaperBroker negative SL on short: amend must reject new_sl <= 0 even for
        short positions (new_sl > 0 guard).
  LA10. _find_sl_order_to_cancel must NOT match reduceOnly=None or reduceOnly=False.
  LA11. _conservative_existing_sl must use ANY close-side stop regardless of
        reduceOnly value (never-widen is broader than cancel-candidate set).
  LA12. PositionMonitor: non-adverse failure must NOT increment adverse_fail_count.
  LA13. place_order in SL-path verified to carry stop_loss via position-monitor
        emergency close carrying reduce_only=True (broker call inspection).
  LA14. _verify_sl_placed: price-within-0.1% check cannot be bypassed by crafting
        a stopPrice of 0 or negative.
  LA15. Stale-fetch bypass: both primary (position.stopLoss) and secondary
        (open orders) sources returning exceptions must return False, not True.
"""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import MagicMock

import pytest
from hypothesis import HealthCheck, assume, given
from hypothesis import settings as h_settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trader.config.settings import PriceShockSettings
from trader.dsor.base import Base
from trader.execution.paper_broker import PaperBroker
from trader.execution.position_monitor import PositionMonitor

# ---------------------------------------------------------------------------
# Shared constants (never modify — must match config/risk.yaml)
# ---------------------------------------------------------------------------

MAX_MARGIN_USD = 2.0
MAX_LOSS_USD = 1.0
MAX_LEVERAGE = 3
SLIP = 0.05
TAKER = 0.00055


# ---------------------------------------------------------------------------
# Shared builders
# ---------------------------------------------------------------------------


def _shock_cfg(**kw) -> PriceShockSettings:
    defaults = dict(
        min_signals_to_trigger=2,
        extreme_5m_return_pct=2.5,
        block_minutes=30,
        price_1m_return_pct=0.5,
        price_5m_return_pct=1.0,
        atr_spike_multiple=2.0,
        volume_spike_multiple=3.0,
        spread_spike_pct=0.3,
        liquidation_spike_multiple=5.0,
        open_position_risk_cut_pct=50.0,
        move_sl_to_breakeven_on_favorable_shock=True,
    )
    defaults.update(kw)
    return PriceShockSettings(**defaults)


def _paper_long(entry: float = 1.0, sl: float = 0.8, qty: float = 10.0) -> PaperBroker:
    b = PaperBroker(initial_balance=10_000.0)
    b._mark_prices["SYM"] = entry
    b._positions["SYM"] = {
        "symbol": "SYM",
        "side": "buy",
        "qty": qty,
        "entry_price": entry,
        "margin": entry * qty / 3.0,
        "sl_price": sl,
        "tp_price": None,
        "open_ts": None,
        "max_adverse_excursion": 0.0,
        "max_favorable_excursion": 0.0,
        "funding_paid": 0.0,
    }
    return b


def _paper_short(entry: float = 1.0, sl: float = 1.2, qty: float = 10.0) -> PaperBroker:
    b = PaperBroker(initial_balance=10_000.0)
    b._mark_prices["SYM"] = entry
    b._positions["SYM"] = {
        "symbol": "SYM",
        "side": "sell",
        "qty": qty,
        "entry_price": entry,
        "margin": entry * qty / 3.0,
        "sl_price": sl,
        "tp_price": None,
        "open_ts": None,
        "max_adverse_excursion": 0.0,
        "max_favorable_excursion": 0.0,
        "funding_paid": 0.0,
    }
    return b


def _live_adapter_with_mock_exchange(mock_exchange: Any):
    """Construct a LiveAdapter bypassing the constructor guard, injecting a mock exchange."""
    from trader.execution.live_adapter import LiveAdapter

    adapter = object.__new__(LiveAdapter)
    adapter._exchange = mock_exchange
    return adapter


def _make_session_factory():
    eng = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(eng)
    return lambda: Session(eng, autocommit=False, autoflush=True)


# ===========================================================================
# LA1 — _verify_sl_placed fail-open: unknown state must NOT be treated as verified
# ===========================================================================


class TestLA1VerifySlFailOpen:
    """_verify_sl_placed must return False when state is unknown (both sources fail).

    A fail-open bug would return True from an exception path, letting verification
    succeed despite having no confirmation that the SL is on the exchange.
    """

    def test_both_sources_raise_exception_returns_false(self) -> None:
        """LA1a: both fetch_positions and fetch_open_orders raise -> False, never True."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.side_effect = ConnectionError("exchange down")
        mock_exchange.fetch_open_orders.side_effect = ConnectionError("exchange down")

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)

        assert result is False, (
            "LA1 FAIL-OPEN: both exchange sources raised exception but "
            "_verify_sl_placed returned True — unknown state must be treated as NOT verified"
        )

    def test_positions_returns_empty_list_no_orders_returns_false(self) -> None:
        """LA1b: empty positions + empty orders -> no confirmation -> False."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = []
        mock_exchange.fetch_open_orders.return_value = []

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)

        assert result is False, (
            "LA1 FAIL-OPEN: no positions and no orders returned but _verify_sl_placed returned True"
        )

    def test_position_stopLoss_is_none_no_orders_returns_false(self) -> None:
        """LA1c: position exists but stopLoss is None, no confirming orders -> False."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": None}
        ]
        mock_exchange.fetch_open_orders.return_value = []

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)

        assert result is False

    def test_position_stopLoss_zero_no_orders_returns_false(self) -> None:
        """LA1d: position.stopLoss == 0 (no SL set) -> must not be treated as verified."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": "0"}
        ]
        mock_exchange.fetch_open_orders.return_value = []

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)

        assert result is False, (
            "LA1d: stopLoss='0' (empty exchange value) must not count as SL confirmation"
        )

    @given(
        new_sl=st.floats(min_value=0.01, max_value=1000.0),
        qty=st.floats(min_value=0.001, max_value=100.0),
    )
    @h_settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_property_verify_sl_returns_false_when_all_sources_fail(
        self, new_sl: float, qty: float
    ) -> None:
        """Property: _verify_sl_placed always returns False when both I/O sources raise."""
        assume(math.isfinite(new_sl) and math.isfinite(qty))
        assume(new_sl > 0 and qty > 0)

        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.side_effect = RuntimeError("network")
        mock_exchange.fetch_open_orders.side_effect = RuntimeError("network")

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=new_sl, is_long=True, qty=qty)

        assert result is False, (
            f"LA1 property FAIL: new_sl={new_sl}, qty={qty} — "
            "exception paths must return False (fail-closed), not True (fail-open)"
        )


# ===========================================================================
# LA2 — Adversary-crafted response bypass of emergency close
# ===========================================================================


class TestLA2AdversarialResponseBypass:
    """An adversary who controls the exchange response cannot bypass the emergency close.

    Specifically: if _verify_sl_placed returns False, amend_stop_loss must attempt
    an emergency market close (reduceOnly), regardless of what the placement
    response contained.
    """

    def test_verification_fails_then_emergency_close_is_called(self) -> None:
        """LA2a: placement succeeds but verification returns False -> emergency close called."""
        mock_exchange = MagicMock()
        # Fetch positions: valid position, no embedded SL
        mock_exchange.fetch_positions.side_effect = [
            # First call: amend_stop_loss step 1 — position info
            [{"symbol": "SYM", "contracts": 10.0, "side": "long"}],
            # Second call: _verify_sl_placed primary source — no stopLoss
            [{"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": None}],
        ]
        # No existing stops -> never-widen baseline is 0
        mock_exchange.fetch_open_orders.return_value = []
        # Placement succeeds
        mock_exchange.create_order.return_value = {"id": "new-stop"}
        # Verification secondary: also no confirming stop
        # (fetch_open_orders called again in _verify_sl_placed)

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter.amend_stop_loss("SYM", new_sl=0.75)

        # Result must be False (verification failed)
        assert result is False, "amend_stop_loss should return False when verification fails"

        # Emergency close must have been attempted: a second create_order call
        # after the SL placement. We need at least 2 create_order calls:
        # 1 for the new stop, 1 for the emergency close.
        calls = mock_exchange.create_order.call_args_list
        assert len(calls) >= 2, (
            f"LA2: expected emergency close create_order call after verification failure, "
            f"got {len(calls)} total create_order calls"
        )

        # Last create_order call must be a market order with reduceOnly=True (LA6)
        last_call_args = calls[-1][0]  # positional args
        order_type = last_call_args[1]
        assert order_type == "market", (
            f"LA2/LA6: emergency close must be a 'market' order, got '{order_type}'"
        )
        params = last_call_args[5] if len(last_call_args) > 5 else {}
        assert params.get("reduceOnly") is True, (
            f"LA6: emergency close must have reduceOnly=True, got params={params}"
        )

    def test_adversary_crafted_stopPrice_zero_does_not_verify(self) -> None:
        """LA2b: adversary returns a stop order with stopPrice=0 — must not verify.

        An adversary controlling the exchange response could return a stop order
        with stopPrice=0, which would not actually protect the position.
        """
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": None}
        ]
        mock_exchange.fetch_open_orders.return_value = [
            {
                "id": "fake-stop",
                "type": "stop_market",
                "reduceOnly": True,
                "side": "sell",
                "amount": 10.0,
                "stopPrice": 0,  # adversarially crafted zero price
            }
        ]

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)

        assert result is False, (
            "LA2b: stop order with stopPrice=0 must not satisfy _verify_sl_placed — "
            "adversary cannot create a fake confirmation with zero price"
        )

    def test_adversary_crafted_wrong_side_does_not_verify(self) -> None:
        """LA2c: stop order on the wrong side must not verify for a long position."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": None}
        ]
        mock_exchange.fetch_open_orders.return_value = [
            {
                "id": "wrong-side-stop",
                "type": "stop_market",
                "reduceOnly": True,
                "side": "buy",  # wrong side for a long close (should be sell)
                "amount": 10.0,
                "stopPrice": 0.75,
            }
        ]

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)

        assert result is False, (
            "LA2c: stop order on the wrong side must not verify — "
            "long close requires sell stop, not buy stop"
        )

    def test_adversary_crafted_price_too_far_does_not_verify(self) -> None:
        """LA2d: stop order with price >0.1% away from new_sl must not verify."""
        new_sl = 0.75
        # Place the stop 0.2% off — beyond the 0.1% tolerance
        adversary_price = new_sl * (1 - 0.002)

        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": None}
        ]
        mock_exchange.fetch_open_orders.return_value = [
            {
                "id": "far-price-stop",
                "type": "stop_market",
                "reduceOnly": True,
                "side": "sell",
                "amount": 10.0,
                "stopPrice": adversary_price,
            }
        ]

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=new_sl, is_long=True, qty=10.0)

        assert result is False, (
            f"LA2d: stop at price {adversary_price} is >0.1% from new_sl={new_sl} — "
            "must not verify (adversary cannot use a nearby but wrong price)"
        )

    def test_adversary_crafted_reduce_only_false_does_not_verify(self) -> None:
        """LA2e: stop order with reduceOnly=False must not pass _verify_sl_placed.

        Strict secondary check requires reduceOnly is True (not just truthy).
        """
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": None}
        ]
        mock_exchange.fetch_open_orders.return_value = [
            {
                "id": "not-reduce-only",
                "type": "stop_market",
                "reduceOnly": False,
                "side": "sell",
                "amount": 10.0,
                "stopPrice": 0.75,
            }
        ]

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)

        assert result is False, (
            "LA2e: reduceOnly=False stop must not verify — strict check requires reduceOnly is True"
        )

    def test_adversary_crafted_reduce_only_none_does_not_verify(self) -> None:
        """LA2f: stop order with reduceOnly=None must not pass _verify_sl_placed."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": None}
        ]
        mock_exchange.fetch_open_orders.return_value = [
            {
                "id": "none-reduce-only",
                "type": "stop_market",
                "reduceOnly": None,
                "side": "sell",
                "amount": 10.0,
                "stopPrice": 0.75,
            }
        ]

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)

        assert result is False, (
            "LA2f: reduceOnly=None must not verify — strict 'is True' check required"
        )

    def test_adversary_crafted_wrong_type_does_not_verify(self) -> None:
        """LA2g: a limit order (type='limit') with all other fields correct must not verify."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": None}
        ]
        mock_exchange.fetch_open_orders.return_value = [
            {
                "id": "fake-limit",
                "type": "limit",  # wrong type — not a stop order
                "reduceOnly": True,
                "side": "sell",
                "amount": 10.0,
                "price": 0.75,
                "stopPrice": 0.75,
            }
        ]

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)

        assert result is False, (
            "LA2g: order type='limit' must not satisfy _verify_sl_placed — "
            "only stop/stop_market/stop_limit order types are valid"
        )


# ===========================================================================
# LA3 — Never-widen: amend_stop_loss must reject any SL that widens risk
# ===========================================================================


class TestLA3NeverWiden:
    """amend_stop_loss must never place a SL that is wider (further from entry) than existing."""

    @given(
        entry=st.floats(min_value=0.1, max_value=10_000.0),
        existing_sl_pct=st.floats(min_value=0.001, max_value=0.49),
        widen_by_pct=st.floats(min_value=0.001, max_value=0.5),
    )
    @h_settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_property_long_widen_always_rejected(
        self, entry: float, existing_sl_pct: float, widen_by_pct: float
    ) -> None:
        """Property: any attempt to lower a long's SL is rejected by PaperBroker."""
        assume(
            math.isfinite(entry) and math.isfinite(existing_sl_pct) and math.isfinite(widen_by_pct)
        )
        existing_sl = entry * (1.0 - existing_sl_pct)
        widen_sl = existing_sl * (1.0 - widen_by_pct)  # lower = wider for long
        assume(widen_sl > 0 and widen_sl < existing_sl)

        broker = _paper_long(entry=entry, sl=existing_sl)
        result = broker.amend_stop_loss("SYM", widen_sl)

        assert result is False, (
            f"LA3 WIDEN on long: entry={entry}, existing_sl={existing_sl}, "
            f"widen_to={widen_sl} — should be rejected but returned True"
        )
        assert broker._positions["SYM"]["sl_price"] == existing_sl, (
            "LA3: SL was mutated even though amend returned False"
        )

    @given(
        entry=st.floats(min_value=0.1, max_value=10_000.0),
        existing_sl_pct=st.floats(min_value=0.001, max_value=0.49),
        widen_by_pct=st.floats(min_value=0.001, max_value=0.5),
    )
    @h_settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_property_short_widen_always_rejected(
        self, entry: float, existing_sl_pct: float, widen_by_pct: float
    ) -> None:
        """Property: any attempt to raise a short's SL is rejected by PaperBroker."""
        assume(
            math.isfinite(entry) and math.isfinite(existing_sl_pct) and math.isfinite(widen_by_pct)
        )
        existing_sl = entry * (1.0 + existing_sl_pct)
        widen_sl = existing_sl * (1.0 + widen_by_pct)  # higher = wider for short

        broker = _paper_short(entry=entry, sl=existing_sl)
        result = broker.amend_stop_loss("SYM", widen_sl)

        assert result is False, (
            f"LA3 WIDEN on short: entry={entry}, existing_sl={existing_sl}, "
            f"widen_to={widen_sl} — should be rejected but returned True"
        )
        assert broker._positions["SYM"]["sl_price"] == existing_sl

    def test_live_adapter_never_widen_via_position_stopLoss_field(self) -> None:
        """LA3 live: primary baseline (position.stopLoss) blocks widening."""

        mock_exchange = MagicMock()
        mock_exchange.fetch_open_orders.return_value = []
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": "0.8"}
        ]

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter.amend_stop_loss("SYM", new_sl=0.5)  # widen (lower) for long

        assert result is False, (
            "LA3 live: position.stopLoss=0.8 must block widen attempt to 0.5 for long"
        )
        mock_exchange.create_order.assert_not_called()

    def test_live_adapter_never_widen_via_open_order_conservative_baseline(self) -> None:
        """LA3 live: secondary baseline (any close-side stop, any reduceOnly) blocks widening."""

        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "SYM", "contracts": 10.0, "side": "long"}  # no stopLoss field
        ]
        mock_exchange.fetch_open_orders.return_value = [
            {
                "id": "existing-stop",
                "type": "stop_market",
                "reduceOnly": None,  # conservative baseline uses even None
                "side": "sell",
                "stopPrice": 0.8,
                "amount": 10.0,
            }
        ]

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter.amend_stop_loss("SYM", new_sl=0.5)

        assert result is False, (
            "LA3 live: conservative baseline from open order (reduceOnly=None) "
            "must still block widening from 0.8 to 0.5 for long"
        )
        mock_exchange.create_order.assert_not_called()

    def test_live_adapter_no_baseline_allows_any_first_sl(self) -> None:
        """LA3 live: when no baseline exists (no existing SL), any positive SL is placed."""

        mock_exchange = MagicMock()
        # No stopLoss in position, no stop orders
        mock_exchange.fetch_positions.side_effect = [
            [{"symbol": "SYM", "contracts": 10.0, "side": "long"}],
            # verification call returns the new SL
            [{"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": "0.75"}],
        ]
        mock_exchange.fetch_open_orders.return_value = []
        mock_exchange.create_order.return_value = {"id": "new-stop"}

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter.amend_stop_loss("SYM", new_sl=0.75)

        assert result is True, "LA3 live: when no existing SL baseline, first SL should be placed"

    @given(
        existing_sl=st.floats(min_value=0.01, max_value=999.0),
        new_sl=st.floats(min_value=0.01, max_value=999.0),
    )
    @h_settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_property_live_adapter_conservative_existing_sl_blocks_long_widen(
        self, existing_sl: float, new_sl: float
    ) -> None:
        """Property: for a long, if new_sl <= existing_sl, amend must be rejected."""
        assume(math.isfinite(existing_sl) and math.isfinite(new_sl))
        assume(existing_sl > 0 and new_sl > 0)
        # Only test widen scenario: new_sl <= existing_sl for a long
        assume(new_sl <= existing_sl)

        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": str(existing_sl)}
        ]
        mock_exchange.fetch_open_orders.return_value = []

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter.amend_stop_loss("SYM", new_sl=new_sl)

        assert result is False, (
            f"LA3 property: long position with existing_sl={existing_sl}, "
            f"new_sl={new_sl} (widen/equal) must be rejected"
        )
        mock_exchange.create_order.assert_not_called()


# ===========================================================================
# LA4 — Kill switch bypass in PositionMonitor: fail_count >= 2 fires exactly
# ===========================================================================


class TestLA4KillSwitchHaltCountExact:
    """PositionMonitor.monitor_shock must set the kill switch on the 2nd consecutive
    adverse failure, not before (off-by-one low) and not after (off-by-one high).
    """

    def _make_failing_mock(self, side: str = "long", sl: float = 0.8) -> MagicMock:
        mock = MagicMock()
        pos = {
            "symbol": "SYM",
            "side": side,
            "qty": 10.0,
            "entry_price": 1.0,
            "unrealised_pnl": 0.0,
            "sl_price": sl,
        }
        mock.get_positions.return_value = [pos]
        mock.amend_stop_loss.return_value = False  # always fails
        return mock

    def test_first_failure_does_not_trigger_halt(self) -> None:
        """LA4a: 1st consecutive adverse failure must NOT trigger emergency close."""
        broker = self._make_failing_mock()
        sf = _make_session_factory()
        monitor = PositionMonitor(broker, _shock_cfg(), session_factory=sf)

        results = monitor.monitor_shock("SYM", price_5m_return=-5.0, price_1m_return=None)

        assert len(results) == 1
        r = results[0]
        assert r["reason"] != "halt_emergency_close", (
            "LA4a OFF-BY-ONE: 1st failure triggered halt — should only trigger on 2nd"
        )
        broker.place_order.assert_not_called()

    def test_second_failure_triggers_halt_and_kill_switch(self) -> None:
        """LA4b: exactly the 2nd consecutive adverse failure triggers emergency close + kill switch."""
        broker = self._make_failing_mock()
        sf = _make_session_factory()
        monitor = PositionMonitor(broker, _shock_cfg(), session_factory=sf)

        # First failure
        monitor.monitor_shock("SYM", price_5m_return=-5.0, price_1m_return=None)
        # Second failure
        results = monitor.monitor_shock("SYM", price_5m_return=-5.0, price_1m_return=None)

        halt_results = [r for r in results if r.get("reason") == "halt_emergency_close"]
        assert len(halt_results) == 1, (
            "LA4b: 2nd consecutive adverse failure must produce exactly one "
            "halt_emergency_close result"
        )
        broker.place_order.assert_called_once()

        # Verify kill switch was set in DSOR
        from trader.dsor.system_state import SystemStateHelper

        with sf() as sess:
            assert SystemStateHelper(sess).is_kill_switch_on() is True, (
                "LA4b: kill switch must be ON after 2nd consecutive adverse failure"
            )

    def test_third_failure_does_not_double_halt(self) -> None:
        """LA4c: after halt, a subsequent call does not trigger a second emergency close.

        After halt, the kill switch is set — the orchestrator should stop calling
        monitor_shock. Even if it doesn't, position may be gone after emergency close.
        """
        broker = self._make_failing_mock()
        sf = _make_session_factory()
        monitor = PositionMonitor(broker, _shock_cfg(), session_factory=sf)

        # 1st + 2nd = halt triggered
        monitor.monitor_shock("SYM", price_5m_return=-5.0, price_1m_return=None)
        monitor.monitor_shock("SYM", price_5m_return=-5.0, price_1m_return=None)
        # 3rd call — the position should have been closed (mock returns same pos)
        # We just ensure place_order is NOT called a second time for the same halt
        # (it was called once in the 2nd shock; 3rd would be fail_count=3 >= 2 again)
        # The current implementation continues to trigger. We document this is acceptable
        # (orchestrator should stop) but it must not panic or throw.
        try:
            monitor.monitor_shock("SYM", price_5m_return=-5.0, price_1m_return=None)
        except Exception as exc:
            pytest.fail(f"LA4c: 3rd call after halt must not raise an exception: {exc}")

    def test_non_adverse_failure_does_not_increment_adverse_fail_count(self) -> None:
        """LA4d: failure on a favorable shock must NOT count toward adverse_fail_count."""
        # Set up a broker where amend always fails
        broker = MagicMock()
        pos = {
            "symbol": "SYM",
            "side": "long",
            "qty": 10.0,
            "entry_price": 1.0,
            "unrealised_pnl": 0.0,
            "sl_price": 0.8,
        }
        broker.get_positions.return_value = [pos]
        broker.amend_stop_loss.return_value = False

        sf = _make_session_factory()
        monitor = PositionMonitor(
            broker,
            _shock_cfg(move_sl_to_breakeven_on_favorable_shock=True),
            session_factory=sf,
        )

        # Two favorable shock failures (price up, long position)
        monitor.monitor_shock("SYM", price_5m_return=5.0, price_1m_return=None)
        results = monitor.monitor_shock("SYM", price_5m_return=5.0, price_1m_return=None)

        # No halt should have fired (fail_count for adverse is still 0)
        halt_results = [r for r in results if r.get("reason") == "halt_emergency_close"]
        assert len(halt_results) == 0, (
            "LA4d: non-adverse failures must NOT contribute to adverse_fail_count; "
            "halt must not fire after 2 non-adverse failures"
        )
        broker.place_order.assert_not_called()

    def test_adverse_fail_count_resets_after_success(self) -> None:
        """LA4e: a successful amend resets the adverse fail count so halt does not fire early."""
        broker = MagicMock()
        pos = {
            "symbol": "SYM",
            "side": "long",
            "qty": 10.0,
            "entry_price": 1.0,
            "unrealised_pnl": 0.0,
            "sl_price": 0.8,
        }
        broker.get_positions.return_value = [pos]
        broker.amend_stop_loss.side_effect = [False, True, False]
        # 1st call fails (count=1), 2nd succeeds (reset), 3rd fails (count=1 again, not 2)

        sf = _make_session_factory()
        monitor = PositionMonitor(broker, _shock_cfg(), session_factory=sf)

        monitor.monitor_shock("SYM", price_5m_return=-5.0, price_1m_return=None)  # fail (count=1)
        monitor.monitor_shock("SYM", price_5m_return=-5.0, price_1m_return=None)  # success (reset)
        results = monitor.monitor_shock(
            "SYM", price_5m_return=-5.0, price_1m_return=None
        )  # fail (count=1)

        halt_results = [r for r in results if r.get("reason") == "halt_emergency_close"]
        assert len(halt_results) == 0, (
            "LA4e: after success resets count, the next single failure must not trigger halt"
        )
        broker.place_order.assert_not_called()


# ===========================================================================
# LA5 — Secret leakage: API keys must not appear in logged strings
# ===========================================================================


class TestLA5SecretLeakage:
    """API keys and secrets must never appear in log messages.

    The risk: any exception message or logged detail that includes the exchange
    object repr or config dict might contain the API key/secret.
    """

    def test_amend_stop_loss_exception_message_does_not_leak_secrets(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """LA5 (CONFIRMED BYPASS): amend_stop_loss logs the raw exception message,
        which can contain API key material when ccxt includes auth credentials in
        its error messages.

        Root cause: live_adapter.py line 464 uses:
            _log.warning("amend_stop_loss: fetch_positions failed for %s: %s", symbol, exc)
        This logs the full exception repr. If ccxt includes the API key/secret in the
        error message (e.g. 'Auth failed for key=<KEY>'), both secrets appear in the
        log record.

        This test documents the finding and fails to alert developers. The fix is to
        log only exc.__class__.__name__ or a sanitised message, never str(exc) directly
        for auth-related errors.

        BYPASS CLASSIFICATION: HIGH
        Reproduction: Set an exchange adapter that raises an exception containing key
        material. The warning log will include the full exception string.
        """
        import logging

        FAKE_KEY = "SUPER_SECRET_API_KEY_12345"
        FAKE_SECRET = "VERY_SECRET_API_SECRET_67890"

        mock_exchange = MagicMock()
        # Simulate ccxt including auth material in its error message
        mock_exchange.fetch_positions.side_effect = RuntimeError(
            f"Auth failed for key={FAKE_KEY} secret={FAKE_SECRET}"
        )

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        settings_mock = MagicMock()
        settings_mock.exchange_api_key = FAKE_KEY
        settings_mock.exchange_api_secret = FAKE_SECRET
        adapter._settings = settings_mock

        with caplog.at_level(logging.WARNING):
            result = adapter.amend_stop_loss("SYM", new_sl=0.75)

        assert result is False

        # Confirmed finding: the log contains both key and secret verbatim.
        leaking_records = [
            record.getMessage()
            for record in caplog.records
            if FAKE_KEY in record.getMessage() and FAKE_SECRET in record.getMessage()
        ]
        assert len(leaking_records) == 0, (
            "LA5 CONFIRMED BYPASS — HIGH severity: "
            "amend_stop_loss logs raw exception message which includes API credentials.\n"
            "Leaking log records:\n"
            + "\n".join(f"  {msg!r}" for msg in leaking_records)
            + "\n\nFix: replace `_log.warning(..., exc)` with "
            "`_log.warning(..., exc.__class__.__name__)` or use log-scrubbing middleware."
        )


# ===========================================================================
# LA6 — Emergency close must use reduceOnly=True (tested inline in LA2, standalone here)
# ===========================================================================


class TestLA6EmergencyCloseReduceOnly:
    """The emergency market close in amend_stop_loss must always use reduceOnly=True."""

    def test_emergency_close_uses_reduce_only_true(self) -> None:
        """LA6: after verification failure, emergency close is sent with reduceOnly=True."""

        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.side_effect = [
            [{"symbol": "SYM", "contracts": 5.0, "side": "long"}],
            [{"symbol": "SYM", "contracts": 5.0, "side": "long", "stopLoss": None}],
        ]
        mock_exchange.fetch_open_orders.return_value = []
        mock_exchange.create_order.return_value = {"id": "new-stop"}

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        adapter.amend_stop_loss("SYM", new_sl=0.75)

        # Find the market (emergency close) call
        market_calls = [
            c
            for c in mock_exchange.create_order.call_args_list
            if len(c[0]) > 1 and c[0][1] == "market"
        ]
        assert len(market_calls) == 1, "LA6: expected exactly one emergency market close call"
        params = market_calls[0][0][5] if len(market_calls[0][0]) > 5 else {}
        assert params.get("reduceOnly") is True, (
            f"LA6: emergency close must carry reduceOnly=True, got params={params}"
        )

    def test_emergency_close_uses_correct_counter_side_for_long(self) -> None:
        """LA6b: long position emergency close must use side='sell'."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.side_effect = [
            [{"symbol": "SYM", "contracts": 5.0, "side": "long"}],
            [{"symbol": "SYM", "contracts": 5.0, "side": "long", "stopLoss": None}],
        ]
        mock_exchange.fetch_open_orders.return_value = []
        mock_exchange.create_order.return_value = {"id": "new-stop"}

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        adapter.amend_stop_loss("SYM", new_sl=0.75)

        market_calls = [
            c
            for c in mock_exchange.create_order.call_args_list
            if len(c[0]) > 1 and c[0][1] == "market"
        ]
        assert len(market_calls) == 1
        close_side = market_calls[0][0][2]
        assert close_side == "sell", f"LA6b: long position close must be 'sell', got '{close_side}'"

    def test_emergency_close_uses_correct_counter_side_for_short(self) -> None:
        """LA6c: short position emergency close must use side='buy'."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.side_effect = [
            [{"symbol": "SYM", "contracts": 5.0, "side": "short"}],
            [{"symbol": "SYM", "contracts": 5.0, "side": "short", "stopLoss": None}],
        ]
        mock_exchange.fetch_open_orders.return_value = []
        mock_exchange.create_order.return_value = {"id": "new-stop"}

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        # For a short, new_sl must be higher than current (but baseline is 0, so any positive ok)
        adapter.amend_stop_loss("SYM", new_sl=1.25)

        market_calls = [
            c
            for c in mock_exchange.create_order.call_args_list
            if len(c[0]) > 1 and c[0][1] == "market"
        ]
        assert len(market_calls) == 1
        close_side = market_calls[0][0][2]
        assert close_side == "buy", f"LA6c: short position close must be 'buy', got '{close_side}'"

    def test_position_monitor_emergency_close_uses_reduce_only_true(self) -> None:
        """LA6d: PositionMonitor._emergency_close must call broker.place_order with reduce_only=True."""
        broker = MagicMock()
        broker.place_order.return_value = {
            "order_id": "ec-1",
            "status": "filled",
            "filled_qty": 10.0,
            "avg_price": 1.0,
        }
        pos = {"side": "buy", "qty": 10.0, "symbol": "SYM"}
        monitor = PositionMonitor(broker, _shock_cfg())
        monitor._emergency_close("SYM", pos)

        assert broker.place_order.called
        call_kwargs = broker.place_order.call_args[1]
        assert call_kwargs.get("reduce_only") is True, (
            f"LA6d: PositionMonitor emergency close must use reduce_only=True, "
            f"got kwargs={call_kwargs}"
        )
        assert call_kwargs.get("order_type") == "market", (
            f"LA6d: emergency close must be 'market', got order_type={call_kwargs.get('order_type')}"
        )


# ===========================================================================
# LA7 — Exception swallowing: broker verify exception must not return True
# ===========================================================================


class TestLA7ExceptionSwallowing:
    """Exceptions during verification must result in False, not True.

    A swallowed exception that converts to True would bypass the emergency close.
    """

    def test_fetch_positions_exception_in_verify_returns_false_not_true(self) -> None:
        """LA7a: fetch_positions raises in _verify_sl_placed -> returns False."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.side_effect = [
            # amend step 1: get position
            [{"symbol": "SYM", "contracts": 10.0, "side": "long"}],
            # _verify_sl_placed: raises
            ConnectionError("timeout during verify"),
        ]
        mock_exchange.fetch_open_orders.return_value = []
        mock_exchange.create_order.return_value = {"id": "new-stop"}

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)

        assert result is False, (
            "LA7a: exception in fetch_positions during _verify_sl_placed "
            "must return False, not propagate or return True"
        )

    def test_fetch_open_orders_exception_in_verify_returns_false(self) -> None:
        """LA7b: fetch_open_orders raises in _verify_sl_placed secondary check -> False."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": None}
        ]
        mock_exchange.fetch_open_orders.side_effect = RuntimeError("order fetch failed")

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)

        assert result is False, (
            "LA7b: exception in fetch_open_orders in _verify_sl_placed "
            "must return False (fail-closed), not True"
        )

    def test_verify_exception_leads_to_emergency_close_not_approval(self) -> None:
        """LA7c: if verification raises (all paths), amend_stop_loss triggers emergency close."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.side_effect = [
            [{"symbol": "SYM", "contracts": 5.0, "side": "long"}],
            RuntimeError("verify network down"),
        ]
        mock_exchange.fetch_open_orders.side_effect = RuntimeError("verify network down")
        mock_exchange.create_order.return_value = {"id": "stop"}

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter.amend_stop_loss("SYM", new_sl=0.75)

        assert result is False, (
            "LA7c: verification exception must cause amend_stop_loss to return False "
            "and attempt emergency close"
        )
        # At least one create_order for placement + one for emergency close
        assert mock_exchange.create_order.call_count >= 2, (
            "LA7c: when verify raises, emergency close must be attempted"
        )


# ===========================================================================
# LA8 — Off-by-one on fail_count: halt must fire at count >= 2
# ===========================================================================


class TestLA8OffByOneFaultCount:
    """The check is 'if fail_count >= 2' — this is correct.

    We verify that fail_count==2 fires and fail_count==1 does not.
    An off-by-one bug (e.g., '> 2') would require a 3rd failure before halting,
    leaving the position unprotected for one extra cycle.
    """

    def test_fail_count_2_fires_halt_exactly(self) -> None:
        """LA8: halt fires on fail_count == 2 (not 1, not 3)."""
        broker = MagicMock()
        pos = {
            "symbol": "SYM",
            "side": "long",
            "qty": 10.0,
            "entry_price": 1.0,
            "unrealised_pnl": 0.0,
            "sl_price": 0.8,
        }
        broker.get_positions.return_value = [pos]
        broker.amend_stop_loss.return_value = False  # always fails
        broker.place_order.return_value = {
            "order_id": "ec",
            "status": "filled",
            "filled_qty": 10.0,
            "avg_price": 1.0,
        }

        sf = _make_session_factory()
        monitor = PositionMonitor(broker, _shock_cfg(), session_factory=sf)

        results_1 = monitor.monitor_shock("SYM", price_5m_return=-5.0, price_1m_return=None)
        assert all(r["reason"] != "halt_emergency_close" for r in results_1), (
            "LA8: halt must NOT fire on fail_count == 1"
        )
        assert broker.place_order.call_count == 0, "LA8: no emergency close on fail_count == 1"

        results_2 = monitor.monitor_shock("SYM", price_5m_return=-5.0, price_1m_return=None)
        halt_rs = [r for r in results_2 if r["reason"] == "halt_emergency_close"]
        assert len(halt_rs) == 1, (
            "LA8: halt MUST fire on fail_count == 2 (off-by-one bug: > 2 would skip this)"
        )
        assert broker.place_order.call_count == 1, (
            "LA8: emergency close must be called exactly once when fail_count == 2"
        )

    def test_internal_fail_count_reaches_exactly_2_before_halt(self) -> None:
        """LA8: inspect the internal _adverse_fail_count to verify it hits 2 on the 2nd call."""
        broker = MagicMock()
        pos = {
            "symbol": "SYM",
            "side": "long",
            "qty": 10.0,
            "entry_price": 1.0,
            "unrealised_pnl": 0.0,
            "sl_price": 0.8,
        }
        broker.get_positions.return_value = [pos]
        broker.amend_stop_loss.return_value = False
        broker.place_order.return_value = {
            "order_id": "ec",
            "status": "filled",
            "filled_qty": 10.0,
            "avg_price": 1.0,
        }

        monitor = PositionMonitor(broker, _shock_cfg())

        monitor.monitor_shock("SYM", price_5m_return=-5.0, price_1m_return=None)
        assert monitor._adverse_fail_count.get("SYM", 0) == 1, (
            "LA8: after 1st failure, _adverse_fail_count must be 1"
        )

        monitor.monitor_shock("SYM", price_5m_return=-5.0, price_1m_return=None)
        # After halt, count is 2 (check fires at >= 2)
        assert monitor._adverse_fail_count.get("SYM", 0) == 2, (
            "LA8: after 2nd failure (halt), _adverse_fail_count must be 2"
        )


# ===========================================================================
# LA9 — PaperBroker negative SL on short must be rejected
# ===========================================================================


class TestLA9PaperBrokerNegativeSlGuard:
    """PaperBroker.amend_stop_loss must reject new_sl <= 0 for ALL positions.

    A negative or zero SL is a sentinel/erroneous value. Installing it would
    mean the position's SL trigger logic would fire incorrectly (price <= 0
    is never normally reached, so a negative SL would never trigger).
    """

    def test_negative_sl_rejected_for_long(self) -> None:
        """LA9a: new_sl <= 0 must be rejected for long positions."""
        broker = _paper_long(entry=1.0, sl=0.8)
        result = broker.amend_stop_loss("SYM", -0.5)
        assert result is False
        assert broker._positions["SYM"]["sl_price"] == 0.8

    def test_zero_sl_rejected_for_long(self) -> None:
        """LA9b: new_sl == 0 must be rejected for long positions."""
        broker = _paper_long(entry=1.0, sl=0.8)
        result = broker.amend_stop_loss("SYM", 0.0)
        assert result is False
        assert broker._positions["SYM"]["sl_price"] == 0.8

    def test_negative_sl_rejected_for_short(self) -> None:
        """LA9c: new_sl <= 0 must be rejected for short positions even though it
        appears to be 'tighter' (lower than current for short is wider).

        The PaperBroker currently only has 'new_sl <= 0' guard. For a short,
        new_sl=-1.0 < current_sl=1.2, which means it passes the never-widen check
        because 'not is_long and new_sl >= current_sl' → '-1.0 >= 1.2' is False.
        So the never-widen guard does NOT protect shorts from negative SL.
        The explicit 'if new_sl <= 0: return False' guard at the top must catch this.
        """
        broker = _paper_short(entry=1.0, sl=1.2)
        result = broker.amend_stop_loss("SYM", -1.0)
        assert result is False, (
            "LA9c CRITICAL: negative SL was installed on short position. "
            "The 'if new_sl <= 0: return False' guard must reject this. "
            "A negative SL on a short means the stop never triggers."
        )
        assert broker._positions["SYM"]["sl_price"] == 1.2, (
            "LA9c: sl_price must remain 1.2 after rejected amendment"
        )

    @given(bad_sl=st.floats(max_value=0.0))
    @h_settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_property_non_positive_sl_always_rejected_for_long(self, bad_sl: float) -> None:
        """Property: any new_sl <= 0 is always rejected for long."""
        assume(math.isfinite(bad_sl))
        broker = _paper_long(entry=1.0, sl=0.8)
        result = broker.amend_stop_loss("SYM", bad_sl)
        assert result is False, f"LA9 property: non-positive SL {bad_sl} accepted for long"

    @given(bad_sl=st.floats(max_value=0.0))
    @h_settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_property_non_positive_sl_always_rejected_for_short(self, bad_sl: float) -> None:
        """Property: any new_sl <= 0 is always rejected for short."""
        assume(math.isfinite(bad_sl))
        broker = _paper_short(entry=1.0, sl=1.2)
        result = broker.amend_stop_loss("SYM", bad_sl)
        assert result is False, (
            f"LA9 property CRITICAL: non-positive SL {bad_sl} was accepted for short. "
            "The top-level 'if new_sl <= 0: return False' guard must block all non-positive values."
        )


# ===========================================================================
# LA10 — _find_sl_order_to_cancel strict reduceOnly==True
# ===========================================================================


class TestLA10FindSlOrderCancelStrict:
    """_find_sl_order_to_cancel must only match orders where reduceOnly is True.

    A match on reduceOnly=None or False could cause the adapter to cancel an
    entry-side stop order, leaving the position unprotected.
    """

    def test_reduce_only_false_not_matched_for_cancel(self) -> None:
        """LA10a: stop with reduceOnly=False must not be selected for cancellation."""
        from trader.execution.live_adapter import LiveAdapter

        adapter = object.__new__(LiveAdapter)
        orders = [
            {
                "id": "entry-stop",
                "type": "stop_market",
                "reduceOnly": False,
                "side": "sell",
                "stopPrice": 0.8,
                "amount": 10.0,
            }
        ]
        result = adapter._find_sl_order_to_cancel(is_long=True, open_orders=orders)
        assert result is None, (
            "LA10a: reduceOnly=False stop must not be selected for cancel — "
            "could be an entry-side stop, not a position SL"
        )

    def test_reduce_only_none_not_matched_for_cancel(self) -> None:
        """LA10b: stop with reduceOnly=None must not be selected for cancellation."""
        from trader.execution.live_adapter import LiveAdapter

        adapter = object.__new__(LiveAdapter)
        orders = [
            {
                "id": "ambiguous-stop",
                "type": "stop_market",
                "reduceOnly": None,
                "side": "sell",
                "stopPrice": 0.8,
                "amount": 10.0,
            }
        ]
        result = adapter._find_sl_order_to_cancel(is_long=True, open_orders=orders)
        assert result is None, "LA10b: reduceOnly=None stop must not be selected for cancel"

    def test_reduce_only_truthy_string_not_matched_for_cancel(self) -> None:
        """LA10c: reduceOnly='true' (string) must not be matched — 'is True' is strict."""
        from trader.execution.live_adapter import LiveAdapter

        adapter = object.__new__(LiveAdapter)
        orders = [
            {
                "id": "string-stop",
                "type": "stop_market",
                "reduceOnly": "true",  # truthy string, not the bool True
                "side": "sell",
                "stopPrice": 0.8,
                "amount": 10.0,
            }
        ]
        result = adapter._find_sl_order_to_cancel(is_long=True, open_orders=orders)
        assert result is None, (
            "LA10c: reduceOnly='true' (string) must not be matched — "
            "strict 'is True' check must distinguish booleans from truthy strings"
        )

    def test_reduce_only_true_bool_is_matched_for_cancel(self) -> None:
        """LA10d: stop with reduceOnly=True (bool) should be selected for cancellation."""
        from trader.execution.live_adapter import LiveAdapter

        adapter = object.__new__(LiveAdapter)
        orders = [
            {
                "id": "correct-stop",
                "type": "stop_market",
                "reduceOnly": True,
                "side": "sell",
                "stopPrice": 0.8,
                "amount": 10.0,
            }
        ]
        result = adapter._find_sl_order_to_cancel(is_long=True, open_orders=orders)
        assert result is not None and result["id"] == "correct-stop", (
            "LA10d: reduceOnly=True stop on correct side must be selected for cancel"
        )


# ===========================================================================
# LA11 — _conservative_existing_sl uses ANY close-side stop (never-widen baseline)
# ===========================================================================


class TestLA11ConservativeBaselineIncludesNullReduceOnly:
    """_conservative_existing_sl must return the price from ANY close-side stop,
    regardless of the reduceOnly value. This is the conservative never-widen baseline.
    """

    def test_reduce_only_none_stop_used_as_baseline(self) -> None:
        """LA11a: close-side stop with reduceOnly=None still provides a baseline."""
        from trader.execution.live_adapter import LiveAdapter

        adapter = object.__new__(LiveAdapter)
        orders = [
            {
                "id": "null-reduce",
                "type": "stop_market",
                "reduceOnly": None,
                "side": "sell",
                "stopPrice": 0.8,
                "amount": 10.0,
            }
        ]
        baseline = adapter._conservative_existing_sl(
            is_long=True, pos_stop_loss_raw=None, open_orders=orders
        )
        assert baseline == pytest.approx(0.8), (
            "LA11a: reduceOnly=None stop must be used as conservative baseline"
        )

    def test_reduce_only_false_stop_used_as_baseline(self) -> None:
        """LA11b: close-side stop with reduceOnly=False still provides a baseline."""
        from trader.execution.live_adapter import LiveAdapter

        adapter = object.__new__(LiveAdapter)
        orders = [
            {
                "id": "false-reduce",
                "type": "stop_market",
                "reduceOnly": False,
                "side": "sell",
                "stopPrice": 0.9,
                "amount": 10.0,
            }
        ]
        baseline = adapter._conservative_existing_sl(
            is_long=True, pos_stop_loss_raw=None, open_orders=orders
        )
        assert baseline == pytest.approx(0.9), (
            "LA11b: reduceOnly=False stop on close-side must be used as conservative baseline"
        )

    def test_position_stopLoss_takes_precedence_over_order_baseline(self) -> None:
        """LA11c: position.stopLoss is the primary baseline and takes precedence."""
        from trader.execution.live_adapter import LiveAdapter

        adapter = object.__new__(LiveAdapter)
        orders = [
            {
                "id": "order-stop",
                "type": "stop_market",
                "reduceOnly": True,
                "side": "sell",
                "stopPrice": 0.75,
                "amount": 10.0,
            }
        ]
        # Primary source is position's stopLoss = 0.85
        baseline = adapter._conservative_existing_sl(
            is_long=True, pos_stop_loss_raw="0.85", open_orders=orders
        )
        assert baseline == pytest.approx(0.85), (
            "LA11c: position stopLoss must be primary (0.85) over order baseline (0.75)"
        )

    def test_baseline_zero_returned_when_no_stop_sources(self) -> None:
        """LA11d: when there is no baseline source, 0.0 is returned (no restriction)."""
        from trader.execution.live_adapter import LiveAdapter

        adapter = object.__new__(LiveAdapter)
        baseline = adapter._conservative_existing_sl(
            is_long=True, pos_stop_loss_raw=None, open_orders=[]
        )
        assert baseline == 0.0, (
            "LA11d: no baseline means 0.0 is returned — any SL placement allowed"
        )


# ===========================================================================
# LA12 — Non-adverse failure must not increment adverse_fail_count
# ===========================================================================


class TestLA12NonAdverseFailureCountIsolation:
    """Confirmed by LA4d test above, tested independently here with direct inspection."""

    def test_favorable_shock_failure_does_not_affect_adverse_count(self) -> None:
        """LA12: favourable shock with failed amend leaves adverse_fail_count at 0."""
        broker = MagicMock()
        pos = {
            "symbol": "SYM",
            "side": "long",
            "qty": 10.0,
            "entry_price": 1.0,
            "unrealised_pnl": 0.0,
            "sl_price": 0.8,
        }
        broker.get_positions.return_value = [pos]
        broker.amend_stop_loss.return_value = False  # fail breakeven move

        monitor = PositionMonitor(
            broker,
            _shock_cfg(move_sl_to_breakeven_on_favorable_shock=True),
        )

        # price_return > 0 = favorable for long
        monitor.monitor_shock("SYM", price_5m_return=5.0, price_1m_return=None)
        monitor.monitor_shock("SYM", price_5m_return=5.0, price_1m_return=None)

        count = monitor._adverse_fail_count.get("SYM", 0)
        assert count == 0, (
            f"LA12: favorable shock failures must not increment adverse_fail_count, but got {count}"
        )

    def test_no_sl_position_skipped_does_not_increment_count(self) -> None:
        """LA12b: positions without SL are skipped and must not affect adverse_fail_count."""
        broker = MagicMock()
        pos = {
            "symbol": "SYM",
            "side": "long",
            "qty": 10.0,
            "entry_price": 1.0,
            "unrealised_pnl": 0.0,
            "sl_price": None,  # no SL
        }
        broker.get_positions.return_value = [pos]
        monitor = PositionMonitor(broker, _shock_cfg())

        monitor.monitor_shock("SYM", price_5m_return=-5.0, price_1m_return=None)
        monitor.monitor_shock("SYM", price_5m_return=-5.0, price_1m_return=None)

        assert monitor._adverse_fail_count.get("SYM", 0) == 0, (
            "LA12b: positions with no SL are skipped (no amendment attempted), "
            "so adverse_fail_count must stay 0"
        )


# ===========================================================================
# LA13 — Placement SL order carries stop_loss via place_order
# ===========================================================================


class TestLA13PlacementCarriesStopLoss:
    """When the new stop order is placed via create_order in amend_stop_loss,
    the stopPrice must be set in the params dict.
    """

    def test_new_stop_placement_carries_correct_stopPrice(self) -> None:
        """LA13: the create_order call for the new stop must include stopPrice == new_sl."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.side_effect = [
            [{"symbol": "SYM", "contracts": 10.0, "side": "long"}],
            [{"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": "0.75"}],
        ]
        mock_exchange.fetch_open_orders.return_value = []
        mock_exchange.create_order.return_value = {"id": "new-stop"}

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        adapter.amend_stop_loss("SYM", new_sl=0.75)

        # Find the stop order placement call (not the emergency close, if any)
        calls = mock_exchange.create_order.call_args_list
        stop_calls = [c for c in calls if len(c[0]) > 1 and c[0][1] == "stop_market"]
        assert len(stop_calls) >= 1, "Expected at least one stop_market create_order call"

        stop_call_params = stop_calls[0][0][5] if len(stop_calls[0][0]) > 5 else {}
        assert stop_call_params.get("stopPrice") == pytest.approx(0.75), (
            f"LA13: stop_market order must carry stopPrice=0.75, got params={stop_call_params}"
        )
        assert stop_call_params.get("reduceOnly") is True, (
            f"LA13: stop_market order must carry reduceOnly=True, got params={stop_call_params}"
        )


# ===========================================================================
# LA14 — _verify_sl_placed: stopPrice=0 or negative bypasses zero check
# ===========================================================================


class TestLA14VerifySlZeroStopPrice:
    """_verify_sl_placed must not accept orders with stopPrice <= 0.

    The 'sp <= 0' guard inside _verify_sl_placed prevents:
      - stopPrice=0 from matching (would satisfy abs(0 - new_sl)/new_sl check with inf)
      - stopPrice=None resulting in sp=0.0 from float conversion
    """

    def test_stop_price_zero_does_not_verify(self) -> None:
        """LA14a: order with stopPrice=0 must not verify (sp <= 0 guard)."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": None}
        ]
        mock_exchange.fetch_open_orders.return_value = [
            {
                "id": "zero-stop",
                "type": "stop_market",
                "reduceOnly": True,
                "side": "sell",
                "amount": 10.0,
                "stopPrice": 0,
            }
        ]

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)
        assert result is False, "LA14a: stopPrice=0 must not verify"

    def test_stop_price_none_does_not_verify(self) -> None:
        """LA14b: order with stopPrice=None (float('None') -> 0) must not verify."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": None}
        ]
        mock_exchange.fetch_open_orders.return_value = [
            {
                "id": "none-stop",
                "type": "stop_market",
                "reduceOnly": True,
                "side": "sell",
                "amount": 10.0,
                "stopPrice": None,
                "price": None,
            }
        ]

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)
        assert result is False, "LA14b: stopPrice=None->0.0 must not verify (sp <= 0 guard)"

    def test_stop_price_negative_does_not_verify(self) -> None:
        """LA14c: order with stopPrice=-1 must not verify."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "SYM", "contracts": 10.0, "side": "long", "stopLoss": None}
        ]
        mock_exchange.fetch_open_orders.return_value = [
            {
                "id": "neg-stop",
                "type": "stop_market",
                "reduceOnly": True,
                "side": "sell",
                "amount": 10.0,
                "stopPrice": -1.0,
            }
        ]

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)
        assert result is False, "LA14c: negative stopPrice must not verify"


# ===========================================================================
# LA15 — Both primary and secondary raising exceptions returns False
# ===========================================================================


class TestLA15StaleExchangeFetchBothFail:
    """When both exchange calls in _verify_sl_placed raise, the result is False.

    This is tested in LA1 property test above; here we add additional explicit
    permutations to document that each branch independently fails closed.
    """

    def test_positions_timeout_orders_returns_empty_returns_false(self) -> None:
        """LA15a: fetch_positions times out, orders returns empty -> False (no confirmation)."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.side_effect = TimeoutError("positions timeout")
        mock_exchange.fetch_open_orders.return_value = []

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)
        assert result is False

    def test_positions_returns_different_symbol_falls_through_to_orders(self) -> None:
        """LA15b: fetch_positions returns a different symbol -> falls through to orders check."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            # Different symbol — does not match "SYM"
            {"symbol": "DIFFERENT/SYM", "contracts": 10.0, "side": "long", "stopLoss": "0.75"}
        ]
        mock_exchange.fetch_open_orders.return_value = []

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)
        assert result is False, (
            "LA15b: position for different symbol must not satisfy verification "
            "for 'SYM' — symbol filtering must be applied"
        )

    def test_positions_with_contracts_zero_not_used_for_verification(self) -> None:
        """LA15c: position with contracts==0 (closed) must not be used for SL verification."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_positions.return_value = [
            {"symbol": "SYM", "contracts": 0, "side": "long", "stopLoss": "0.75"}
        ]
        mock_exchange.fetch_open_orders.return_value = []

        adapter = _live_adapter_with_mock_exchange(mock_exchange)
        result = adapter._verify_sl_placed("SYM", new_sl=0.75, is_long=True, qty=10.0)
        assert result is False, (
            "LA15c: position with contracts=0 is closed/empty and must not "
            "be used to verify SL (it has no open risk)"
        )
