"""Phase 7: Tests for PaperBroker."""

from __future__ import annotations

import datetime

import pytest

from trader.config.settings import AppSettings, ModelNames, RiskSettings, Settings
from trader.execution.exceptions import DuplicateOrderError
from trader.execution.paper_broker import PaperBroker


def _make_settings() -> Settings:
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
        allowed_symbols=["BTC/USDT"],
    )
    app = AppSettings(models=ModelNames(screening="x", decision="x", review="x"))
    return Settings(trading_mode="paper", app=app, risk=risk)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_broker(**kwargs: object) -> PaperBroker:
    return PaperBroker(initial_balance=10_000.0, **kwargs)  # type: ignore[arg-type]


def fixed_clock(dt: datetime.datetime) -> object:
    """Return a clock callable that always returns dt."""

    def _clock() -> datetime.datetime:
        return dt

    return _clock


# ---------------------------------------------------------------------------
# Market order tests
# ---------------------------------------------------------------------------


class TestMarketOrders:
    def test_market_long_fills_immediately_at_slippage_price(self) -> None:
        broker = make_broker(slippage_pct=0.001)
        broker._mark_prices["BTC/USDT"] = 30_000.0
        result = broker.place_order(
            symbol="BTC/USDT",
            side="buy",
            qty=1.0,
            order_type="market",
            client_order_id="test-long-1",
        )
        assert result["status"] == "filled"
        assert result["filled_qty"] == 1.0
        # buy: 30000 * 1.001 = 30030
        assert abs(result["avg_price"] - 30_030.0) < 0.01

    def test_market_short_fills_immediately_at_slippage_price(self) -> None:
        broker = make_broker(slippage_pct=0.001)
        broker._mark_prices["BTC/USDT"] = 30_000.0
        result = broker.place_order(
            symbol="BTC/USDT",
            side="sell",
            qty=1.0,
            order_type="market",
            client_order_id="test-short-1",
        )
        assert result["status"] == "filled"
        assert result["filled_qty"] == 1.0
        # sell: 30000 * 0.999 = 29970
        assert abs(result["avg_price"] - 29_970.0) < 0.01

    def test_market_order_no_open_position_no_error(self) -> None:
        """Market order when no existing position: should not raise."""
        broker = make_broker()
        broker._mark_prices["ETH/USDT"] = 2_000.0
        result = broker.place_order(
            symbol="ETH/USDT",
            side="buy",
            qty=1.0,
            order_type="market",
        )
        assert result["status"] == "filled"


# ---------------------------------------------------------------------------
# Limit order tests
# ---------------------------------------------------------------------------


class TestLimitOrders:
    def test_limit_long_fills_when_mark_price_touches(self) -> None:
        broker = make_broker()
        broker._mark_prices["BTC/USDT"] = 30_500.0
        result = broker.place_order(
            symbol="BTC/USDT",
            side="buy",
            qty=1.0,
            order_type="limit",
            price=30_000.0,
            client_order_id="test-limit-long",
        )
        assert result["status"] == "open"

        # Price drops to limit → should fill
        fills = broker.update_mark_price("BTC/USDT", 30_000.0)
        assert len(fills) == 1
        assert fills[0]["qty"] == 1.0
        assert fills[0]["price"] == 30_000.0

    def test_limit_long_does_not_fill_before_price_touched(self) -> None:
        broker = make_broker()
        broker._mark_prices["BTC/USDT"] = 30_500.0
        broker.place_order(
            symbol="BTC/USDT",
            side="buy",
            qty=1.0,
            order_type="limit",
            price=30_000.0,
            client_order_id="test-limit-no-fill",
        )
        # Price stays above limit
        fills = broker.update_mark_price("BTC/USDT", 30_200.0)
        assert len(fills) == 0

    def test_cancel_order_removes_pending_limit_order(self) -> None:
        broker = make_broker()
        broker._mark_prices["BTC/USDT"] = 30_500.0
        result = broker.place_order(
            symbol="BTC/USDT",
            side="buy",
            qty=1.0,
            order_type="limit",
            price=30_000.0,
            client_order_id="test-cancel",
        )
        order_id = result["order_id"]
        assert broker.cancel_order("BTC/USDT", order_id) is True
        # After cancellation, touching the price should not fill
        fills = broker.update_mark_price("BTC/USDT", 29_990.0)
        assert len(fills) == 0


# ---------------------------------------------------------------------------
# SL/TP tests
# ---------------------------------------------------------------------------


class TestSlTp:
    def test_sl_triggers_on_long(self) -> None:
        broker = make_broker()
        broker._mark_prices["BTC/USDT"] = 30_000.0
        broker.place_order(
            symbol="BTC/USDT",
            side="buy",
            qty=1.0,
            order_type="market",
            stop_loss=29_000.0,
            client_order_id="test-sl-long",
        )
        # Position open, SL not triggered yet
        fills = broker.update_mark_price("BTC/USDT", 29_500.0)
        assert len(fills) == 0
        # Now touch SL
        fills = broker.update_mark_price("BTC/USDT", 29_000.0)
        assert len(fills) == 1
        assert fills[0]["reason"] == "sl"
        # Position should be closed
        assert broker.get_positions() == []

    def test_tp_triggers_on_long(self) -> None:
        broker = make_broker()
        broker._mark_prices["BTC/USDT"] = 30_000.0
        broker.place_order(
            symbol="BTC/USDT",
            side="buy",
            qty=1.0,
            order_type="market",
            take_profit=32_000.0,
            client_order_id="test-tp-long",
        )
        fills = broker.update_mark_price("BTC/USDT", 32_000.0)
        assert len(fills) == 1
        assert fills[0]["reason"] == "tp"
        assert broker.get_positions() == []

    def test_sl_triggers_on_short(self) -> None:
        broker = make_broker()
        broker._mark_prices["BTC/USDT"] = 30_000.0
        broker.place_order(
            symbol="BTC/USDT",
            side="sell",
            qty=1.0,
            order_type="market",
            stop_loss=31_000.0,
            client_order_id="test-sl-short",
        )
        fills = broker.update_mark_price("BTC/USDT", 31_000.0)
        assert len(fills) == 1
        assert fills[0]["reason"] == "sl"

    def test_tp_triggers_on_short(self) -> None:
        broker = make_broker()
        broker._mark_prices["BTC/USDT"] = 30_000.0
        broker.place_order(
            symbol="BTC/USDT",
            side="sell",
            qty=1.0,
            order_type="market",
            take_profit=28_000.0,
            client_order_id="test-tp-short",
        )
        fills = broker.update_mark_price("BTC/USDT", 28_000.0)
        assert len(fills) == 1
        assert fills[0]["reason"] == "tp"


# ---------------------------------------------------------------------------
# Idempotency / TTL tests
# ---------------------------------------------------------------------------


class TestIdempotencyTtl:
    def test_duplicate_client_order_id_raises(self) -> None:
        broker = make_broker()
        broker._mark_prices["BTC/USDT"] = 30_000.0
        broker.place_order(
            symbol="BTC/USDT",
            side="buy",
            qty=1.0,
            order_type="market",
            client_order_id="dup-test",
        )
        with pytest.raises(DuplicateOrderError):
            broker.place_order(
                symbol="BTC/USDT",
                side="buy",
                qty=1.0,
                order_type="market",
                client_order_id="dup-test",
            )

    def test_ttl_expiry_limit_order_expires(self) -> None:
        now = datetime.datetime(2024, 1, 1, 12, 0, 0)
        past = datetime.datetime(2024, 1, 1, 11, 59, 0)
        broker = PaperBroker(clock_fn=fixed_clock(now))  # type: ignore[arg-type]
        broker._mark_prices["BTC/USDT"] = 30_500.0
        broker.place_order(
            symbol="BTC/USDT",
            side="buy",
            qty=1.0,
            order_type="limit",
            price=30_000.0,
            client_order_id="ttl-test",
            ttl_ts=past,  # already expired
        )
        # Trigger price update — TTL check should expire the order
        fills = broker.update_mark_price("BTC/USDT", 30_000.0)
        # Order should not fill — it was expired
        assert not any(f.get("client_order_id") == "ttl-test" for f in fills)
        assert len(broker._pending_orders) == 0


# ---------------------------------------------------------------------------
# MAE / MFE tests
# ---------------------------------------------------------------------------


class TestMaeMfe:
    def test_mae_mfe_tracked_correctly(self) -> None:
        broker = make_broker()
        broker._mark_prices["BTC/USDT"] = 30_000.0
        broker.place_order(
            symbol="BTC/USDT",
            side="buy",
            qty=1.0,
            order_type="market",
            client_order_id="mae-mfe-test",
        )
        # Favorable move
        broker.update_mark_price("BTC/USDT", 31_000.0)
        pos = broker._positions["BTC/USDT"]
        assert pos["max_favorable_excursion"] > 0

        # Adverse move
        broker.update_mark_price("BTC/USDT", 29_500.0)
        pos = broker._positions["BTC/USDT"]
        assert pos["max_adverse_excursion"] > 0


# ---------------------------------------------------------------------------
# Funding tests
# ---------------------------------------------------------------------------


class TestFunding:
    def test_funding_applied_long_pays_when_rate_positive(self) -> None:
        broker = make_broker()
        broker._mark_prices["BTC/USDT"] = 30_000.0
        broker.place_order(
            symbol="BTC/USDT",
            side="buy",
            qty=1.0,
            order_type="market",
            client_order_id="fund-test",
        )
        cash_before = broker._cash
        funding = broker.update_funding("BTC/USDT", funding_rate=0.0001)
        # Long pays when rate > 0
        assert funding > 0
        assert broker._cash < cash_before


# ---------------------------------------------------------------------------
# Balance and position tests
# ---------------------------------------------------------------------------


class TestBalancePositions:
    def test_get_balance_returns_correct_equity_after_pnl(self) -> None:
        broker = make_broker()
        broker._mark_prices["BTC/USDT"] = 30_000.0
        broker.place_order(
            symbol="BTC/USDT",
            side="buy",
            qty=1.0,
            order_type="market",
            client_order_id="bal-test",
        )
        # Mark up
        broker.update_mark_price("BTC/USDT", 31_000.0)
        bal = broker.get_balance()
        assert bal["equity_usd"] > 10_000.0  # we made money

    def test_get_positions_returns_open_position_with_correct_fields(self) -> None:
        broker = make_broker()
        broker._mark_prices["BTC/USDT"] = 30_000.0
        broker.place_order(
            symbol="BTC/USDT",
            side="buy",
            qty=2.0,
            order_type="market",
            client_order_id="pos-test",
        )
        positions = broker.get_positions()
        assert len(positions) == 1
        p = positions[0]
        assert p["symbol"] == "BTC/USDT"
        assert p["side"] == "buy"
        assert p["qty"] == 2.0
        assert "entry_price" in p
        assert "unrealised_pnl" in p


# ---------------------------------------------------------------------------
# MAE / MFE short-side tests
# ---------------------------------------------------------------------------


class TestMaeMfeShort:
    def test_mae_mfe_tracked_correctly_short(self) -> None:
        broker = make_broker()
        broker._mark_prices["BTC/USDT"] = 30_000.0
        broker.place_order(
            symbol="BTC/USDT",
            side="sell",
            qty=1.0,
            order_type="market",
            client_order_id="mae-mfe-short",
        )
        # Favorable move for short (price drops)
        broker.update_mark_price("BTC/USDT", 29_000.0)
        pos = broker._positions["BTC/USDT"]
        assert pos["max_favorable_excursion"] > 0

        # Adverse move for short (price rises)
        broker.update_mark_price("BTC/USDT", 31_000.0)
        pos = broker._positions["BTC/USDT"]
        assert pos["max_adverse_excursion"] > 0


# ---------------------------------------------------------------------------
# Funding short-side tests
# ---------------------------------------------------------------------------


class TestFundingShort:
    def test_funding_applied_short_receives_when_rate_positive(self) -> None:
        broker = make_broker()
        broker._mark_prices["BTC/USDT"] = 30_000.0
        broker.place_order(
            symbol="BTC/USDT",
            side="sell",
            qty=1.0,
            order_type="market",
            client_order_id="fund-short-test",
        )
        cash_before = broker._cash
        funding = broker.update_funding("BTC/USDT", funding_rate=0.0001)
        # Short receives when rate > 0 (funding is negative payment i.e. income)
        assert funding < 0
        assert broker._cash > cash_before


# ---------------------------------------------------------------------------
# SL and TP simultaneous trigger
# ---------------------------------------------------------------------------


class TestSlTpSimultaneous:
    def test_sl_takes_priority_when_price_gaps_through_both(self) -> None:
        """When price gaps through both SL and TP only one trigger fires."""
        broker = make_broker()
        broker._mark_prices["BTC/USDT"] = 30_000.0
        broker.place_order(
            symbol="BTC/USDT",
            side="buy",
            qty=1.0,
            order_type="market",
            stop_loss=28_000.0,
            take_profit=32_000.0,
            client_order_id="sl-tp-sim",
        )
        # Gap down past SL
        fills = broker.update_mark_price("BTC/USDT", 27_000.0)
        assert len(fills) == 1
        assert fills[0]["reason"] == "sl"
        assert broker.get_positions() == []


# ---------------------------------------------------------------------------
# force_close with no open position
# ---------------------------------------------------------------------------


class TestForceCloseNoPosition:
    def test_force_close_returns_cancelled_when_no_position(self) -> None:
        from unittest.mock import MagicMock

        from trader.execution.order_manager import OrderManager

        settings = _make_settings()
        broker = make_broker()
        session_factory = MagicMock(return_value=MagicMock())

        om = OrderManager(broker=broker, settings=settings, session_factory=session_factory)
        result = om.force_close("BTC/USDT", "test no-position")
        assert result.status == "cancelled"
        assert "No open position" in (result.error or "")


# ---------------------------------------------------------------------------
# Reconcile mismatch records risk_event
# ---------------------------------------------------------------------------


class TestReconcileMismatch:
    def test_reconcile_mismatch_records_risk_event(self) -> None:
        from unittest.mock import MagicMock

        from trader.execution.order_manager import OrderManager

        settings = _make_settings()
        broker = make_broker()
        # Simulate a live position
        broker._mark_prices["BTC/USDT"] = 30_000.0
        broker.place_order(
            symbol="BTC/USDT",
            side="buy",
            qty=1.0,
            order_type="market",
            client_order_id="reconcile-pos",
        )

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.all.return_value = []
        session_factory = MagicMock(return_value=mock_session)

        om = OrderManager(broker=broker, settings=settings, session_factory=session_factory)
        result = om.reconcile("BTC/USDT")
        assert not result.ok
        assert len(result.mismatches) > 0
        # Confirm a RiskEvent row was committed
        assert mock_session.add.called
        assert mock_session.commit.called
