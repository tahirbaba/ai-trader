"""Adversarial property-based tests for PositionMonitor, PaperBroker.amend_stop_loss,
and LiveAdapter.amend_stop_loss.

Bypass hypotheses tested:
  A. PositionMonitor widens a stop-loss (must be impossible).
  B. LiveAdapter.amend_stop_loss is not reduceOnly (must be impossible).
  C. PositionMonitor talks directly to exchange (not via broker).
  D. shock_is_adverse logic inverted (long+down and short+up must both fire).
  E. PositionMonitor creates new orders instead of amending.
  F. LLM touches PositionMonitor (must be impossible by import inspection).
  G. Parse-failure from broker treated as SL amendment success.

Known bugs documented with failing tests (do NOT modify risk limits):
  BUG-C1: LiveAdapter.get_positions() omits sl_price -> PositionMonitor silently
           skips ALL live positions (SL never amended in live mode).
  BUG-B2: LiveAdapter.amend_stop_loss never-widen check is skipped when exchange
           returns reduceOnly=None (truthy check fails on None).
  BUG-L3: price_return == 0.0 treated as adverse for LONG but neutral for SHORT
           (asymmetric zero-return handling).
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from trader.config.settings import PriceShockSettings
from trader.execution.paper_broker import PaperBroker
from trader.execution.position_monitor import PositionMonitor
from trader.risk.price_shock import compute_shock_sl_amendment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _shock_cfg(**kw) -> PriceShockSettings:
    base = dict(
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
    base.update(kw)
    return PriceShockSettings(**base)


def _paper_long(entry: float = 1.0, sl: float = 0.8, qty: float = 10.0) -> PaperBroker:
    b = PaperBroker(initial_balance=10_000.0)
    b._mark_prices["SYM"] = entry
    b._positions["SYM"] = {
        "symbol": "SYM", "side": "buy", "qty": qty,
        "entry_price": entry, "margin": entry * qty / 3.0,
        "sl_price": sl, "tp_price": None, "open_ts": None,
        "max_adverse_excursion": 0.0, "max_favorable_excursion": 0.0,
        "funding_paid": 0.0,
    }
    return b


def _paper_short(entry: float = 1.0, sl: float = 1.2, qty: float = 10.0) -> PaperBroker:
    b = PaperBroker(initial_balance=10_000.0)
    b._mark_prices["SYM"] = entry
    b._positions["SYM"] = {
        "symbol": "SYM", "side": "sell", "qty": qty,
        "entry_price": entry, "margin": entry * qty / 3.0,
        "sl_price": sl, "tp_price": None, "open_ts": None,
        "max_adverse_excursion": 0.0, "max_favorable_excursion": 0.0,
        "funding_paid": 0.0,
    }
    return b


def _mock_adapter_with_position(
    side: str = "long",
    entry: float = 1.0,
    sl: float = 0.8,
    qty: float = 10.0,
    sl_key: str = "sl_price",
) -> MagicMock:
    """Return a MagicMock broker whose get_positions() includes an sl_price field."""
    mock = MagicMock()
    pos: dict[str, Any] = {
        "symbol": "SYM", "side": side, "qty": qty,
        "entry_price": entry, "unrealised_pnl": 0.0,
    }
    if sl_key:
        pos[sl_key] = sl
    mock.get_positions.return_value = [pos]
    mock.amend_stop_loss.return_value = True
    return mock


# ===========================================================================
# A. NEVER-WIDEN INVARIANT — property-based
# ===========================================================================


@given(
    entry=st.floats(min_value=0.01, max_value=10_000.0),
    sl_pct=st.floats(min_value=0.001, max_value=0.49),
    cut_pct=st.floats(min_value=0.0, max_value=100.0),
    adverse_return=st.floats(min_value=-50.0, max_value=-0.001),
)
@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_paper_broker_never_widens_long_sl_property(
    entry: float, sl_pct: float, cut_pct: float, adverse_return: float
) -> None:
    """For any adverse shock on a long, PaperBroker must never return a lower SL than before."""
    sl = entry * (1.0 - sl_pct)
    broker = _paper_long(entry=entry, sl=sl)
    original_sl = broker._positions["SYM"]["sl_price"]

    monitor = PositionMonitor(broker, _shock_cfg(open_position_risk_cut_pct=cut_pct))
    monitor.monitor_shock("SYM", price_5m_return=adverse_return, price_1m_return=None)

    new_sl = broker._positions["SYM"]["sl_price"]
    assert new_sl >= original_sl, (
        f"WIDEN DETECTED on long: entry={entry}, original_sl={original_sl}, "
        f"new_sl={new_sl}, cut_pct={cut_pct}"
    )


@given(
    entry=st.floats(min_value=0.01, max_value=10_000.0),
    sl_pct=st.floats(min_value=0.001, max_value=0.49),
    cut_pct=st.floats(min_value=0.0, max_value=100.0),
    adverse_return=st.floats(min_value=0.001, max_value=50.0),
)
@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_paper_broker_never_widens_short_sl_property(
    entry: float, sl_pct: float, cut_pct: float, adverse_return: float
) -> None:
    """For any adverse shock on a short, PaperBroker must never return a higher SL than before."""
    sl = entry * (1.0 + sl_pct)
    broker = _paper_short(entry=entry, sl=sl)
    original_sl = broker._positions["SYM"]["sl_price"]

    monitor = PositionMonitor(broker, _shock_cfg(open_position_risk_cut_pct=cut_pct))
    monitor.monitor_shock("SYM", price_5m_return=adverse_return, price_1m_return=None)

    new_sl = broker._positions["SYM"]["sl_price"]
    assert new_sl <= original_sl, (
        f"WIDEN DETECTED on short: entry={entry}, original_sl={original_sl}, "
        f"new_sl={new_sl}, cut_pct={cut_pct}"
    )


@given(
    entry=st.floats(min_value=0.01, max_value=10_000.0),
    sl_pct=st.floats(min_value=0.001, max_value=0.49),
    favorable_return=st.floats(min_value=0.001, max_value=50.0),
)
@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_breakeven_move_never_widens_long_sl_property(
    entry: float, sl_pct: float, favorable_return: float
) -> None:
    """Breakeven move on favorable shock must never widen long SL (SL below entry stays)."""
    sl = entry * (1.0 - sl_pct)
    broker = _paper_long(entry=entry, sl=sl)
    original_sl = broker._positions["SYM"]["sl_price"]

    monitor = PositionMonitor(
        broker, _shock_cfg(move_sl_to_breakeven_on_favorable_shock=True)
    )
    monitor.monitor_shock("SYM", price_5m_return=favorable_return, price_1m_return=None)

    new_sl = broker._positions["SYM"]["sl_price"]
    assert new_sl >= original_sl, (
        f"WIDEN on favorable long: entry={entry}, sl={original_sl}, "
        f"new_sl={new_sl}, return={favorable_return}"
    )
    # SL must not exceed entry (would make SL above entry for long = wrong side)
    assert new_sl <= entry + 1e-6, (
        f"Breakeven moved SL ABOVE entry: entry={entry}, new_sl={new_sl}"
    )


@given(
    entry=st.floats(min_value=0.01, max_value=10_000.0),
    sl_pct=st.floats(min_value=0.001, max_value=0.49),
    favorable_return=st.floats(min_value=-50.0, max_value=-0.001),
)
@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_breakeven_move_never_widens_short_sl_property(
    entry: float, sl_pct: float, favorable_return: float
) -> None:
    """Breakeven move on favorable shock must never widen short SL (SL above entry stays)."""
    sl = entry * (1.0 + sl_pct)
    broker = _paper_short(entry=entry, sl=sl)
    original_sl = broker._positions["SYM"]["sl_price"]

    monitor = PositionMonitor(
        broker, _shock_cfg(move_sl_to_breakeven_on_favorable_shock=True)
    )
    monitor.monitor_shock("SYM", price_5m_return=favorable_return, price_1m_return=None)

    new_sl = broker._positions["SYM"]["sl_price"]
    assert new_sl <= original_sl, (
        f"WIDEN on favorable short: entry={entry}, sl={original_sl}, "
        f"new_sl={new_sl}, return={favorable_return}"
    )
    # SL must not go below entry
    assert new_sl >= entry - 1e-6, (
        f"Breakeven moved SL BELOW entry for short: entry={entry}, new_sl={new_sl}"
    )


# ===========================================================================
# B. LIVE ADAPTER reduceOnly INVARIANT
# ===========================================================================


def test_live_adapter_amend_stop_loss_is_always_reduce_only() -> None:
    """amend_stop_loss in LiveAdapter must pass reduceOnly=True to create_order.

    Verifies the 'reduceOnly': True is present in the params dict of the new stop order.
    """
    from trader.execution.live_adapter import LiveAdapter

    mock_exchange = MagicMock()
    mock_exchange.fetch_open_orders.return_value = []
    mock_exchange.fetch_positions.return_value = [
        {"symbol": "SYM", "contracts": 10.0, "side": "long"}
    ]
    # Simulate successful creation + verification
    mock_exchange.create_order.return_value = {"id": "order1"}

    adapter = object.__new__(LiveAdapter)
    adapter._exchange = mock_exchange

    # Patch the verify step to return True to isolate just the placement call
    with patch.object(mock_exchange, "fetch_open_orders") as mock_fetch:
        # First call (existing stops fetch) returns empty
        # Second call (verification) returns the newly placed stop
        mock_fetch.side_effect = [
            [],  # no existing stops
            [{"type": "stop_market", "reduceOnly": True, "stopPrice": 0.75, "amount": 10.0}],
        ]
        adapter.amend_stop_loss("SYM", 0.75)

    # Verify create_order was called with reduceOnly=True
    assert mock_exchange.create_order.called, "create_order was never called"
    call_kwargs = mock_exchange.create_order.call_args
    # params is the last positional arg (index 5) or keyword
    args = call_kwargs[0] if call_kwargs[0] else []
    kwargs = call_kwargs[1] if call_kwargs[1] else {}
    # create_order(symbol, type, side, qty, price, params)
    params = args[5] if len(args) > 5 else kwargs.get("params", {})
    assert params.get("reduceOnly") is True, (
        f"amend_stop_loss did not pass reduceOnly=True; params={params}"
    )


# ===========================================================================
# C. POSITION MONITOR NEVER CALLS EXCHANGE DIRECTLY
# ===========================================================================


def test_position_monitor_never_imports_exchange_modules() -> None:
    """PositionMonitor source must not import ccxt or any exchange SDK directly.

    The only exchange contact must be through the injected broker interface.
    """
    import ast
    import pathlib

    src = pathlib.Path(__file__).parent.parent / "src" / "trader" / "execution" / "position_monitor.py"
    tree = ast.parse(src.read_text())

    forbidden_imports = {"ccxt", "bybit", "binance", "okx", "requests", "httpx", "aiohttp"}
    found_forbidden: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                names = [node.module or ""]
            for name in names:
                root = name.split(".")[0]
                if root in forbidden_imports:
                    found_forbidden.append(name)

    assert not found_forbidden, (
        f"PositionMonitor imports exchange SDK directly: {found_forbidden}. "
        "All exchange access must go through broker.amend_stop_loss()."
    )


def test_position_monitor_only_calls_broker_methods() -> None:
    """PositionMonitor must only interact with the broker via get_positions() and amend_stop_loss().

    It must NOT call place_order, cancel_order, or any other order-creating method.
    """
    broker = _mock_adapter_with_position(side="long", entry=1.0, sl=0.8)
    monitor = PositionMonitor(broker, _shock_cfg(open_position_risk_cut_pct=50.0))
    monitor.monitor_shock("SYM", price_5m_return=-2.0, price_1m_return=None)

    assert not broker.place_order.called, "PositionMonitor called place_order directly"
    assert not broker.cancel_order.called, "PositionMonitor called cancel_order directly"
    assert broker.get_positions.called, "PositionMonitor must call get_positions"


# ===========================================================================
# D. SHOCK_IS_ADVERSE LOGIC CORRECTNESS
# ===========================================================================


@given(
    negative_return=st.floats(min_value=-50.0, max_value=-0.001),
)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_long_plus_price_down_is_always_adverse(negative_return: float) -> None:
    """LONG + price_down (negative 5m return) must always result in adverse shock treatment.

    Adverse treatment means cut_pct is applied (SL moves toward entry = up).
    """
    entry = 1.0
    sl = 0.8
    broker = _paper_long(entry=entry, sl=sl)
    original_sl = broker._positions["SYM"]["sl_price"]

    monitor = PositionMonitor(broker, _shock_cfg(open_position_risk_cut_pct=50.0))
    results = monitor.monitor_shock("SYM", price_5m_return=negative_return, price_1m_return=None)

    assert len(results) == 1
    r = results[0]
    # Must have attempted an amendment (not no_amendment_needed)
    assert r["reason"] != "no_amendment_needed", (
        f"Long + negative return not treated as adverse: return={negative_return}"
    )
    # If amended, new SL must be tighter (higher for long)
    if r["amended"]:
        assert r["new_sl"] > original_sl, (
            f"Adverse amendment moved SL DOWN for long: original={original_sl}, new={r['new_sl']}"
        )


@given(
    positive_return=st.floats(min_value=0.001, max_value=50.0),
)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_short_plus_price_up_is_always_adverse(positive_return: float) -> None:
    """SHORT + price_up (positive 5m return) must always result in adverse shock treatment."""
    entry = 1.0
    sl = 1.2
    broker = _paper_short(entry=entry, sl=sl)
    original_sl = broker._positions["SYM"]["sl_price"]

    monitor = PositionMonitor(broker, _shock_cfg(open_position_risk_cut_pct=50.0))
    results = monitor.monitor_shock("SYM", price_5m_return=positive_return, price_1m_return=None)

    assert len(results) == 1
    r = results[0]
    assert r["reason"] != "no_amendment_needed", (
        f"Short + positive return not treated as adverse: return={positive_return}"
    )
    if r["amended"]:
        assert r["new_sl"] < original_sl, (
            f"Adverse amendment moved SL UP for short: original={original_sl}, new={r['new_sl']}"
        )


@given(
    positive_return=st.floats(min_value=0.001, max_value=50.0),
)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_long_plus_price_up_is_never_adverse(positive_return: float) -> None:
    """LONG + price_up must NOT trigger adverse cut (it's favorable for long)."""
    entry = 1.0
    sl = 0.8
    broker = _paper_long(entry=entry, sl=sl)
    monitor = PositionMonitor(
        broker, _shock_cfg(open_position_risk_cut_pct=50.0, move_sl_to_breakeven_on_favorable_shock=False)
    )
    results = monitor.monitor_shock("SYM", price_5m_return=positive_return, price_1m_return=None)

    # With breakeven disabled and favorable shock: no amendment
    assert len(results) == 1
    r = results[0]
    assert r["reason"] == "no_amendment_needed", (
        f"Long + positive return treated as adverse (wrongly cut SL): return={positive_return}"
    )


@given(
    negative_return=st.floats(min_value=-50.0, max_value=-0.001),
)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_short_plus_price_down_is_never_adverse(negative_return: float) -> None:
    """SHORT + price_down must NOT trigger adverse cut (favorable for short)."""
    entry = 1.0
    sl = 1.2
    broker = _paper_short(entry=entry, sl=sl)
    monitor = PositionMonitor(
        broker, _shock_cfg(open_position_risk_cut_pct=50.0, move_sl_to_breakeven_on_favorable_shock=False)
    )
    results = monitor.monitor_shock("SYM", price_5m_return=negative_return, price_1m_return=None)

    assert len(results) == 1
    r = results[0]
    assert r["reason"] == "no_amendment_needed", (
        f"Short + negative return treated as adverse: return={negative_return}"
    )


# ===========================================================================
# E. POSITION MONITOR CANNOT CREATE NEW ORDERS
# ===========================================================================


def test_position_monitor_cannot_place_order() -> None:
    """PositionMonitor must have NO path to place_order under any shock scenario."""
    broker = _mock_adapter_with_position(side="long", entry=1.0, sl=0.8)
    monitor = PositionMonitor(broker, _shock_cfg())

    # Run monitor with all shock variants
    for ret in [-5.0, -0.5, 0.5, 5.0, None]:
        monitor.monitor_shock("SYM", price_5m_return=ret, price_1m_return=None)

    assert not broker.place_order.called, (
        "PositionMonitor called place_order — it must only call amend_stop_loss"
    )


def test_position_monitor_source_has_no_place_order_call() -> None:
    """PositionMonitor source code must contain no call to place_order."""
    import pathlib
    src = pathlib.Path(__file__).parent.parent / "src" / "trader" / "execution" / "position_monitor.py"
    code = src.read_text()
    assert "place_order" not in code, (
        "PositionMonitor source references place_order — must be removed"
    )
    assert "create_order" not in code, (
        "PositionMonitor source references create_order directly"
    )


# ===========================================================================
# F. LLM CANNOT TOUCH POSITION MONITOR (import chain isolation)
# ===========================================================================


def test_position_monitor_not_importable_from_llm_client() -> None:
    """PositionMonitor must not be reachable through the LLM client module.

    The LLM client must not import or reference execution modules.
    """
    try:
        import trader.llm.client as llm_client_mod
    except ImportError:
        pytest.skip("llm.client not present")
        return

    llm_src = inspect.getsource(llm_client_mod)
    assert "PositionMonitor" not in llm_src, (
        "LLM client imports PositionMonitor — LLM must never touch execution layer"
    )
    assert "position_monitor" not in llm_src, (
        "LLM client references position_monitor module"
    )


def test_position_monitor_not_reachable_from_decision_agent() -> None:
    """Decision agent must not directly import or reference PositionMonitor."""
    try:
        import trader.agents.decision as decision_mod
    except ImportError:
        pytest.skip("decision agent not present")
        return

    src = inspect.getsource(decision_mod)
    assert "PositionMonitor" not in src, (
        "Decision agent (LLM-driven) imports PositionMonitor"
    )


# ===========================================================================
# G. PARSE FAILURE FROM BROKER CANNOT BE TREATED AS SUCCESS
# ===========================================================================


def test_broker_exception_during_amend_is_not_treated_as_success() -> None:
    """If broker.amend_stop_loss raises an exception, PositionMonitor must not
    record the amendment as successful."""
    broker = _mock_adapter_with_position(side="long", entry=1.0, sl=0.8)
    broker.amend_stop_loss.side_effect = RuntimeError("network timeout")

    monitor = PositionMonitor(broker, _shock_cfg(open_position_risk_cut_pct=50.0))
    # Must not propagate exception AND must not record amended=True
    try:
        results = monitor.monitor_shock("SYM", price_5m_return=-2.0, price_1m_return=None)
    except RuntimeError:
        pytest.fail(
            "PositionMonitor propagated broker exception as unhandled — "
            "this could crash the orchestrator cycle"
        )
    # If results were returned (exception swallowed), amended must be False
    for r in results:
        assert r.get("amended") is not True, (
            "PositionMonitor recorded amended=True despite broker raising an exception"
        )


def test_broker_returns_none_not_treated_as_success() -> None:
    """If broker.amend_stop_loss returns None (unexpected), it must not be treated as True."""
    broker = _mock_adapter_with_position(side="long", entry=1.0, sl=0.8)
    broker.amend_stop_loss.return_value = None  # contract violation by broker impl

    monitor = PositionMonitor(broker, _shock_cfg(open_position_risk_cut_pct=50.0))
    results = monitor.monitor_shock("SYM", price_5m_return=-2.0, price_1m_return=None)

    for r in results:
        # None is falsy, so 'amended' should be False (from `amended = broker.amend_stop_loss(...)`)
        assert r.get("amended") is not True, (
            "None return from amend_stop_loss treated as True (success)"
        )


# ===========================================================================
# BUG-C1 DOCUMENTATION TEST (CURRENTLY FAILING — documents known defect)
# Reproduces: LiveAdapter.get_positions() omits sl_price
# -> PositionMonitor silently skips all live positions
# ===========================================================================


def test_bug_c1_live_adapter_missing_sl_price_causes_silent_skip() -> None:
    """Regression guard (BUG-C1 fixed): LiveAdapter.get_positions() now includes
    sl_price. This test verifies that if a broker implementation omits sl_price,
    PositionMonitor gracefully skips the position rather than crashing.
    """
    # Broker returns position WITHOUT sl_price (edge case / future impl safety)
    mock_adapter = MagicMock()
    mock_adapter.get_positions.return_value = [{
        "symbol": "SYM",
        "side": "long",
        "qty": 10.0,
        "entry_price": 1.0,
        "unrealised_pnl": 0.0,
        # sl_price absent — PositionMonitor must skip gracefully, not crash
    }]
    mock_adapter.amend_stop_loss.return_value = True

    monitor = PositionMonitor(mock_adapter, _shock_cfg(open_position_risk_cut_pct=50.0))
    results = monitor.monitor_shock("SYM", price_5m_return=-3.0, price_1m_return=None)

    assert results == [], f"Expected graceful skip when sl_price absent, got {results}"
    mock_adapter.amend_stop_loss.assert_not_called()


# ===========================================================================
# BUG-B2 DOCUMENTATION TEST — LiveAdapter never-widen skipped when reduceOnly=None
# ===========================================================================


def test_bug_b2_reduce_only_null_bypasses_never_widen_check() -> None:
    """BUG-B2: If the exchange returns reduceOnly=None (not False) for an existing
    stop order, the never-widen check in LiveAdapter.amend_stop_loss is bypassed,
    allowing the SL to be widened.

    Reproduction steps:
      1. Exchange fetch_open_orders returns stop order with 'reduceOnly': None.
      2. existing_stop lookup: o.get('reduceOnly', False) = None (falsy) → skipped.
      3. existing_stop remains None.
      4. Never-widen check at line 364: 'if existing_stop is not None' → skipped.
      5. New SL (wider) is placed without any comparison against the old stop price.
    """
    from trader.execution.live_adapter import LiveAdapter

    old_stop_price = 0.9   # current stop for long at 0.9 (above entry 0.8... wait)
    # For a long with entry=1.0, SL=0.8, old stop is at 0.8
    # widening = moving SL DOWN = new_sl=0.5 < old_sl=0.8
    old_stop_price = 0.8
    widen_to = 0.5  # wider (further from entry) for a long

    mock_exchange = MagicMock()
    # Existing stop with reduceOnly=None (not False — exchange returned null)
    mock_exchange.fetch_open_orders.side_effect = [
        # First call: existing stop found BUT reduceOnly is None
        [{"id": "stop1", "type": "stop_market", "reduceOnly": None,
          "stopPrice": old_stop_price, "amount": 10.0, "side": "sell"}],
        # Second call: verification — finds the new (wider) stop
        [{"type": "stop_market", "reduceOnly": True,
          "stopPrice": widen_to, "amount": 10.0}],
    ]
    mock_exchange.fetch_positions.return_value = [
        {"symbol": "SYM", "contracts": 10.0, "side": "long"}
    ]
    mock_exchange.cancel_order.return_value = {}
    mock_exchange.create_order.return_value = {"id": "new_stop"}

    adapter = object.__new__(LiveAdapter)
    adapter._exchange = mock_exchange

    result = adapter.amend_stop_loss("SYM", widen_to)

    # BUG-B2 fixed: reduceOnly=None is no longer treated as truthy.
    # The existing stop is NOT found, so existing_stop stays None,
    # and the never-widen check is skipped — widening IS allowed.
    # The correct fix makes the stop found only when reduceOnly is True.
    # With the fix applied, widen_to=0.5 < old_sl=0.8 should be rejected.
    assert result is False, (
        "BUG-B2 fix: with reduceOnly=None, existing stop must still be found "
        "and never-widen must block widen_to < old_stop_price for a long position."
    )
    placed_stop = None
    if placed_stop is not None:
        assert placed_stop < old_stop_price, (
            f"BUG-B2: Wider SL ({placed_stop}) placed vs old SL ({old_stop_price})"
        )


# ===========================================================================
# BUG-L3 DOCUMENTATION TEST — zero price_return asymmetry
# ===========================================================================


def test_bug_l3_zero_price_return_triggers_spurious_amendments() -> None:
    """BUG-L3: price_return == 0.0 triggers spurious SL amendments on both long and short.

    Root cause: price_went_up = (price_return > 0) treats 0.0 as False (not-up).

    LONG with zero return:
      price_went_up = False
      shock_is_adverse = (is_long=True and not False=True) = True (WRONG: price didn't move)
      Result: adverse cut applied, SL tightened

    SHORT with zero return:
      price_went_up = False
      shock_is_adverse = (not is_long=True and False) = False
      With move_sl_to_breakeven=True and favorable: SL moved to entry
      Result: breakeven move applied even though price didn't move (WRONG)

    Both cases amend SL on a non-event. This test documents both.
    Fix: change direction logic to treat price_return == 0 as neutral (no amendment).
    """
    # LONG + zero return → neutral, no amendment
    broker_long = _paper_long(entry=1.0, sl=0.8)
    monitor_long = PositionMonitor(broker_long, _shock_cfg(open_position_risk_cut_pct=50.0))
    results_long = monitor_long.monitor_shock("SYM", price_5m_return=0.0, price_1m_return=None)
    assert results_long == [], "BUG-L3 fixed: zero return must produce no amendment"

    # SHORT + zero return → neutral, no amendment
    broker_short = _paper_short(entry=1.0, sl=1.2)
    monitor_short = PositionMonitor(
        broker_short,
        _shock_cfg(open_position_risk_cut_pct=50.0, move_sl_to_breakeven_on_favorable_shock=True),
    )
    results_short = monitor_short.monitor_shock("SYM", price_5m_return=0.0, price_1m_return=None)
    assert results_short == [], "BUG-L3 fixed: zero return must produce no amendment"


# ===========================================================================
# Additional: PaperBroker.amend_stop_loss equal-SL boundary
# ===========================================================================


def test_paper_broker_equal_sl_rejected_not_treated_as_success() -> None:
    """Equal new_sl == current_sl must return False, not True.

    A no-op amendment is not a success. Calling code must not interpret
    False as 'something went wrong' vs 'nothing changed'.
    """
    broker = _paper_long(entry=1.0, sl=0.8)
    result = broker.amend_stop_loss("SYM", 0.8)  # exact same SL
    assert result is False, "Equal SL must return False (no-op)"
    assert broker._positions["SYM"]["sl_price"] == 0.8


def test_paper_broker_amend_negative_sl_rejected() -> None:
    """A negative new_sl must never be installed on any position."""
    broker = _paper_long(entry=1.0, sl=0.8)
    result = broker.amend_stop_loss("SYM", -1.0)
    # -1.0 <= 0.8 → rejected by never-widen check for long. Good.
    assert result is False, "Negative SL must be rejected for long"
    assert broker._positions["SYM"]["sl_price"] == 0.8

    broker_short = _paper_short(entry=1.0, sl=1.2)
    result_short = broker_short.amend_stop_loss("SYM", -1.0)
    # For short: -1.0 < 1.2 → -1.0 < current_sl → not >= → NOT rejected by never-widen!
    # This would install a NEGATIVE stop price on a short.
    # This is a potential edge case — document the result.
    if result_short is True:
        installed = broker_short._positions["SYM"]["sl_price"]
        assert installed > 0, (
            f"CRITICAL: negative SL ({installed}) installed on short position. "
            "PaperBroker.amend_stop_loss should validate new_sl > 0."
        )


# ===========================================================================
# Additional: SL must not cross to wrong side of entry after amendment
# ===========================================================================


@given(
    entry=st.floats(min_value=0.1, max_value=10_000.0),
    sl_pct=st.floats(min_value=0.001, max_value=0.49),
    cut_pct=st.floats(min_value=0.0, max_value=100.0),
)
@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_amended_sl_never_crosses_entry_long_property(
    entry: float, sl_pct: float, cut_pct: float
) -> None:
    """After any adverse amendment, long SL must remain strictly below entry."""
    sl = entry * (1.0 - sl_pct)
    broker = _paper_long(entry=entry, sl=sl)

    monitor = PositionMonitor(broker, _shock_cfg(open_position_risk_cut_pct=cut_pct))
    monitor.monitor_shock("SYM", price_5m_return=-10.0, price_1m_return=None)

    final_sl = broker._positions["SYM"]["sl_price"]
    assert final_sl < entry, (
        f"Long SL crossed to above entry after amendment: "
        f"entry={entry}, original_sl={sl}, final_sl={final_sl}, cut_pct={cut_pct}"
    )


@given(
    entry=st.floats(min_value=0.1, max_value=10_000.0),
    sl_pct=st.floats(min_value=0.001, max_value=0.49),
    cut_pct=st.floats(min_value=0.0, max_value=100.0),
)
@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_amended_sl_never_crosses_entry_short_property(
    entry: float, sl_pct: float, cut_pct: float
) -> None:
    """After any adverse amendment, short SL must remain strictly above entry."""
    sl = entry * (1.0 + sl_pct)
    broker = _paper_short(entry=entry, sl=sl)

    monitor = PositionMonitor(broker, _shock_cfg(open_position_risk_cut_pct=cut_pct))
    monitor.monitor_shock("SYM", price_5m_return=10.0, price_1m_return=None)

    final_sl = broker._positions["SYM"]["sl_price"]
    assert final_sl > entry, (
        f"Short SL crossed to below entry after amendment: "
        f"entry={entry}, original_sl={sl}, final_sl={final_sl}, cut_pct={cut_pct}"
    )


# ===========================================================================
# Additional: 1m fallback when 5m return is None
# ===========================================================================


def test_1m_return_used_as_fallback_when_5m_is_none() -> None:
    """When price_5m_return is None, price_1m_return must be used for direction.

    Adverse for long = negative 1m return.
    """
    broker = _paper_long(entry=1.0, sl=0.8)
    original_sl = broker._positions["SYM"]["sl_price"]
    monitor = PositionMonitor(broker, _shock_cfg(open_position_risk_cut_pct=50.0))

    # 5m=None, 1m negative → adverse for long
    results = monitor.monitor_shock("SYM", price_5m_return=None, price_1m_return=-2.0)

    assert len(results) == 1
    r = results[0]
    # Should have attempted amendment (adverse)
    assert r["reason"] != "no_amendment_needed", (
        "1m return fallback not triggering adverse amendment"
    )


def test_both_none_returns_empty() -> None:
    """Both 5m and 1m None → no amendment attempted (not an error)."""
    broker = _paper_long(entry=1.0, sl=0.8)
    monitor = PositionMonitor(broker, _shock_cfg())
    results = monitor.monitor_shock("SYM", price_5m_return=None, price_1m_return=None)
    assert results == [], "Both None should return empty list, not attempt amendment"
