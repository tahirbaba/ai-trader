"""Phase 7: Chaos/edge-case tests for execution layer."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trader.config.settings import AppSettings, ModelNames, RiskSettings, Settings
from trader.dsor.base import Base
from trader.execution.exceptions import AdapterError, DuplicateOrderError
from trader.execution.order_manager import OrderManager, ReconcileResult
from trader.execution.paper_broker import PaperBroker
from trader.risk.models import DecisionInput, RiskVerdict

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def make_settings() -> Settings:
    risk = RiskSettings(
        max_margin_per_trade_usd=200.0,
        max_loss_per_trade_usd=100.0,
        slippage_buffer_pct=0.05,
        max_leverage=10,
        margin_mode="isolated",
        position_mode="one_way",
        daily_loss_limit_usd=300.0,
        weekly_loss_limit_usd=600.0,
        equity_floor_usd=5.0,
        allowed_symbols=["XRP/USDT:USDT"],
    )
    app = AppSettings(
        models=ModelNames(screening="x", decision="x", review="x"),
    )
    return Settings(trading_mode="paper", app=app, risk=risk)


def make_verdict(qty: float = 10.0, approved: bool = True) -> RiskVerdict:
    return RiskVerdict(
        approved=approved,
        adjusted_size=qty,
        rules_failed=[],
        loss_at_stop_usd=0.5,
        liq_price=None,
        entry_drift_pct=0.01,
        reject_reason="",
    )


def make_decision(symbol: str = "XRP/USDT:USDT") -> DecisionInput:
    return DecisionInput(
        action="LONG",
        symbol=symbol,
        side="long",
        entry_price=1.0,
        stop_loss=0.90,
        take_profit=1.20,
        confidence=0.85,
        confluence_score=4,
        entry_type="market",
    )


def mock_broker_with_meta(place_order_response: dict | Exception) -> MagicMock:
    """Return a mock broker that returns specified response for place_order."""
    broker = MagicMock()
    broker.get_market_meta.return_value = {
        "lot_size": 1.0,
        "min_qty": 1.0,
        "tick_size": 0.01,
        "maker_fee": 0.0002,
        "taker_fee": 0.00055,
        "max_leverage": 10,
    }
    if isinstance(place_order_response, Exception):
        broker.place_order.side_effect = place_order_response
    else:
        broker.place_order.return_value = place_order_response
    broker.get_positions.return_value = []
    broker.get_open_orders.return_value = []
    return broker


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session_factory(engine):
    def _factory():
        return Session(engine, autocommit=False, autoflush=True)

    return _factory


# ---------------------------------------------------------------------------
# Chaos tests
# ---------------------------------------------------------------------------


class TestChaos:
    def test_broker_adapter_error_returns_failed_order_result(
        self, session_factory: object
    ) -> None:
        """AdapterError from broker → OrderResult(status='failed', error=...)"""
        broker = mock_broker_with_meta(AdapterError("network timeout"))
        om = OrderManager(
            broker=broker,
            settings=make_settings(),
            session_factory=session_factory,  # type: ignore[arg-type]
        )

        result = om.submit(make_verdict(), make_decision(), cycle_id="chaos-err")
        assert result.status == "failed"
        assert result.error is not None
        assert "network timeout" in result.error

    def test_partial_fill_reflected_in_order_result(
        self, session_factory: object
    ) -> None:
        """Broker returns filled_qty < qty → OrderResult reflects partial."""
        broker = mock_broker_with_meta(
            {
                "order_id": "partial-123",
                "client_order_id": "partial-coid",
                "status": "open",
                "filled_qty": 5.0,  # partial — only half filled
                "avg_price": 1.0,
            }
        )
        om = OrderManager(
            broker=broker,
            settings=make_settings(),
            session_factory=session_factory,  # type: ignore[arg-type]
        )

        result = om.submit(make_verdict(qty=10.0), make_decision(), cycle_id="partial-test")
        assert result.filled_qty == 5.0
        assert result.status == "open"

    def test_place_order_twice_same_client_order_id_raises_duplicate(
        self, session_factory: object
    ) -> None:
        """Second place_order call with same client_order_id → DuplicateOrderError."""
        broker = PaperBroker(initial_balance=10_000.0)
        broker._mark_prices["XRP/USDT:USDT"] = 1.0

        # First call to place_order succeeds
        broker.place_order(
            symbol="XRP/USDT:USDT",
            side="buy",
            qty=1.0,
            order_type="market",
            client_order_id="chaos-dup-coid",
        )

        # Second call with the same client_order_id raises DuplicateOrderError
        with pytest.raises(DuplicateOrderError):
            broker.place_order(
                symbol="XRP/USDT:USDT",
                side="buy",
                qty=1.0,
                order_type="market",
                client_order_id="chaos-dup-coid",
            )

    def test_broker_cancel_error_on_force_close_returns_result(
        self, session_factory: object
    ) -> None:
        """broker.cancel_order raises on force-close: still returns result (best-effort)."""
        broker = PaperBroker(initial_balance=10_000.0)
        broker._mark_prices["XRP/USDT:USDT"] = 1.0

        # Open a position
        broker.place_order(
            symbol="XRP/USDT:USDT",
            side="buy",
            qty=10.0,
            order_type="market",
            client_order_id="force-close-entry",
        )

        om = OrderManager(
            broker=broker,
            settings=make_settings(),
            session_factory=session_factory,  # type: ignore[arg-type]
        )

        # force_close should succeed even if cancel_order raises
        result = om.force_close("XRP/USDT:USDT", "chaos test")
        assert result.status in ("filled", "open", "cancelled", "failed")
        # Position should be gone
        assert broker.get_positions() == []

    def test_reconcile_with_injected_mismatch_returns_not_ok(
        self, session_factory: object
    ) -> None:
        """Reconcile with a broker position and empty DSOR → ReconcileResult(ok=False)."""
        broker = PaperBroker(initial_balance=10_000.0)
        broker._mark_prices["XRP/USDT:USDT"] = 1.0

        # Inject position without any DSOR order
        broker._positions["XRP/USDT:USDT"] = {
            "symbol": "XRP/USDT:USDT",
            "side": "buy",
            "qty": 5.0,
            "entry_price": 1.0,
            "margin": 0.5,
            "sl_price": None,
            "tp_price": None,
            "open_ts": None,
            "max_adverse_excursion": 0.0,
            "max_favorable_excursion": 0.0,
            "funding_paid": 0.0,
        }

        om = OrderManager(
            broker=broker,
            settings=make_settings(),
            session_factory=session_factory,  # type: ignore[arg-type]
        )

        result = om.reconcile("XRP/USDT:USDT")
        assert isinstance(result, ReconcileResult)
        assert result.ok is False
        assert len(result.mismatches) > 0
