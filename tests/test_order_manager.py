"""Phase 7: Tests for OrderManager."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trader.config.settings import AppSettings, ModelNames, RiskSettings, Settings
from trader.dsor.base import Base
from trader.dsor.models import Order as OrderRow
from trader.dsor.models import RiskEvent
from trader.execution.exceptions import DuplicateOrderError, SlPlacementError
from trader.execution.layer2_check import CapBreachError
from trader.execution.order_manager import OrderManager
from trader.execution.paper_broker import PaperBroker
from trader.risk.models import DecisionInput, RiskVerdict

# ---------------------------------------------------------------------------
# Fixtures
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


def make_verdict(qty: float = 1.0, approved: bool = True) -> RiskVerdict:
    return RiskVerdict(
        approved=approved,
        adjusted_size=qty,
        rules_failed=[],
        loss_at_stop_usd=0.5,
        liq_price=None,
        entry_drift_pct=0.01,
        reject_reason="",
    )


def make_decision(
    symbol: str = "XRP/USDT:USDT",
    side: str = "long",
    entry_price: float = 1.0,
    stop_loss: float = 0.90,
    take_profit: float = 1.20,
    entry_type: str = "market",
) -> DecisionInput:
    return DecisionInput(
        action="LONG",
        symbol=symbol,
        side=side,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        confidence=0.85,
        confluence_score=4,
        entry_type=entry_type,
    )


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


@pytest.fixture()
def broker() -> PaperBroker:
    b = PaperBroker(initial_balance=10_000.0)
    b._mark_prices["XRP/USDT:USDT"] = 1.0
    return b


@pytest.fixture()
def om(broker, session_factory) -> OrderManager:
    return OrderManager(
        broker=broker,
        settings=make_settings(),
        session_factory=session_factory,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestOrderManagerHappyPath:
    def test_valid_verdict_returns_filled_order_result(self, om: OrderManager) -> None:
        verdict = make_verdict(qty=10.0)
        decision = make_decision()
        result = om.submit(verdict, decision, cycle_id="abc12345-test")
        assert result.status == "filled"
        assert result.filled_qty == 10.0
        assert result.error is None

    def test_submit_writes_order_row_to_dsor(
        self, om: OrderManager, session_factory: object
    ) -> None:
        verdict = make_verdict(qty=5.0)
        decision = make_decision()
        result = om.submit(verdict, decision, cycle_id="abc12345-write")

        session = session_factory()  # type: ignore[operator]
        rows = session.query(OrderRow).all()
        assert len(rows) >= 1
        assert rows[0].client_order_id == result.client_order_id


# ---------------------------------------------------------------------------
# Layer 2 cap breach
# ---------------------------------------------------------------------------


class TestLayer2CapBreach:
    def test_cap_breach_raises_before_broker_call(self, session_factory: object) -> None:
        """Layer 2 cap breach raises CapBreachError before any broker call."""
        # Use tiny limits so the order breaches
        risk = RiskSettings(
            max_margin_per_trade_usd=0.001,  # tiny limit
            max_loss_per_trade_usd=0.0005,
            slippage_buffer_pct=0.05,
            max_leverage=10,
            margin_mode="isolated",
            position_mode="one_way",
            daily_loss_limit_usd=0.01,
            weekly_loss_limit_usd=0.02,
            equity_floor_usd=0.001,
            allowed_symbols=["XRP/USDT:USDT"],
        )
        app = AppSettings(
            models=ModelNames(screening="x", decision="x", review="x"),
        )
        settings = Settings(trading_mode="paper", app=app, risk=risk)

        broker = PaperBroker(initial_balance=10_000.0)
        broker._mark_prices["XRP/USDT:USDT"] = 1.0

        om = OrderManager(broker=broker, settings=settings, session_factory=session_factory)  # type: ignore[arg-type]

        # qty=100 would breach max_margin_usd=0.001 at price=1.0
        verdict = make_verdict(qty=100.0)
        decision = make_decision()

        with pytest.raises(CapBreachError):
            om.submit(verdict, decision, cycle_id="breach-test")


# ---------------------------------------------------------------------------
# SL placement failure
# ---------------------------------------------------------------------------


class TestSlPlacementFailure:
    def test_sl_failure_cancels_entry_records_risk_event(self, session_factory: object) -> None:
        """SlPlacementError: entry cancelled, risk_event recorded, exception re-raised."""
        mock_broker = MagicMock()
        mock_broker.get_market_meta.return_value = {
            "lot_size": 1.0,
            "min_qty": 1.0,
            "tick_size": 0.01,
            "maker_fee": 0.0002,
            "taker_fee": 0.00055,
            "max_leverage": 10,
        }
        mock_broker.place_order.side_effect = SlPlacementError("SL failed")

        om = OrderManager(
            broker=mock_broker,
            settings=make_settings(),
            session_factory=session_factory,  # type: ignore[arg-type]
        )

        verdict = make_verdict(qty=10.0)
        decision = make_decision()

        with pytest.raises(SlPlacementError):
            om.submit(verdict, decision, cycle_id="sl-fail-test")

        # Check risk event was recorded
        session = session_factory()  # type: ignore[operator]
        events = session.query(RiskEvent).all()
        assert any(e.type == "HALT" for e in events)


# ---------------------------------------------------------------------------
# Duplicate order
# ---------------------------------------------------------------------------


class TestDuplicateOrder:
    def test_same_client_order_id_raises_duplicate_error(
        self, broker: PaperBroker, session_factory: object
    ) -> None:
        """Injecting same client_order_id into broker raises DuplicateOrderError."""
        om = OrderManager(
            broker=broker,
            settings=make_settings(),
            session_factory=session_factory,  # type: ignore[arg-type]
        )

        # Pre-seed the used IDs in broker
        fake_coid = "abc12345-deadbeef"
        broker._used_client_ids.add(fake_coid)

        # Patch uuid4 to return a predictable value
        with patch(
            "trader.execution.order_manager.uuid.uuid4",
            return_value=MagicMock(hex="deadbeef0123456789"),
        ):
            verdict = make_verdict(qty=1.0)
            decision = make_decision()
            with pytest.raises(DuplicateOrderError):
                om.submit(verdict, decision, cycle_id="abc12345")


# ---------------------------------------------------------------------------
# cancel_expired_orders
# ---------------------------------------------------------------------------


class TestCancelExpiredOrders:
    def test_cancel_expired_orders_removes_past_ttl_orders(
        self, om: OrderManager, broker: PaperBroker
    ) -> None:
        import datetime

        # Place a limit order that already expired
        past = datetime.datetime(2020, 1, 1)
        broker.place_order(
            symbol="XRP/USDT:USDT",
            side="buy",
            qty=5.0,
            order_type="limit",
            price=0.90,
            client_order_id="ttl-om-test",
            ttl_ts=past,
        )
        # Override now in broker to be in the future (default utcnow will be past)
        # The broker's _pending_orders should have the order
        assert len(broker._pending_orders) == 1

        # cancel_expired_orders uses datetime.datetime.utcnow() — it will be after past
        count = om.cancel_expired_orders("XRP/USDT:USDT")
        assert count == 1


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


class TestReconcile:
    def test_reconcile_broker_has_position_dsor_has_none_returns_mismatch(
        self, broker: PaperBroker, session_factory: object
    ) -> None:
        """Broker has position, DSOR has no open order → mismatch."""
        om = OrderManager(
            broker=broker,
            settings=make_settings(),
            session_factory=session_factory,  # type: ignore[arg-type]
        )

        # Manually inject position into broker
        broker._positions["XRP/USDT:USDT"] = {
            "symbol": "XRP/USDT:USDT",
            "side": "buy",
            "qty": 10.0,
            "entry_price": 1.0,
            "margin": 1.0,
            "sl_price": None,
            "tp_price": None,
            "open_ts": None,
            "max_adverse_excursion": 0.0,
            "max_favorable_excursion": 0.0,
            "funding_paid": 0.0,
        }

        result = om.reconcile("XRP/USDT:USDT")
        assert result.ok is False
        assert len(result.mismatches) > 0


# ---------------------------------------------------------------------------
# force_close
# ---------------------------------------------------------------------------


class TestForceClose:
    def test_force_close_places_market_reduce_only_order(
        self, broker: PaperBroker, om: OrderManager
    ) -> None:
        # Open a position first
        broker.place_order(
            symbol="XRP/USDT:USDT",
            side="buy",
            qty=10.0,
            order_type="market",
            client_order_id="fc-entry",
        )
        assert len(broker.get_positions()) == 1

        result = om.force_close("XRP/USDT:USDT", "test kill switch")
        assert result.status == "filled"
        assert broker.get_positions() == []
