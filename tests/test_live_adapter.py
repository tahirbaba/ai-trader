"""Phase 7: Tests for LiveAdapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trader.execution.exceptions import LiveNotApprovedError, SlPlacementError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_settings() -> object:
    """Create a minimal Settings-like object for LiveAdapter."""
    settings = MagicMock()
    settings.exchange_api_key = "test-key"
    settings.exchange_api_secret = "test-secret"
    settings.app.trading.symbols = []
    return settings


def make_mock_exchange() -> MagicMock:
    """Return a mock ccxt exchange."""
    exchange = MagicMock()
    exchange.load_markets.return_value = {}
    exchange.markets = {
        "BTC/USDT:USDT": {
            "precision": {"amount": 0.001, "price": 0.01},
            "limits": {
                "amount": {"min": 0.001},
                "leverage": {"max": 100},
            },
            "fees": {"maker": 0.0002, "taker": 0.00055},
        }
    }
    exchange.fetch_time.return_value = 1_700_000_000_000
    exchange.fetch_balance.return_value = {
        "USDT": {"total": 10_000.0, "free": 9_000.0}
    }
    exchange.fetch_positions.return_value = []
    exchange.fetch_open_orders.return_value = []
    exchange.create_order.return_value = {
        "id": "exchange-order-123",
        "clientOrderId": "test-coid",
        "status": "closed",
        "filled": 1.0,
        "average": 30_000.0,
    }
    exchange.cancel_order.return_value = {}
    exchange.set_leverage.return_value = {}
    exchange.set_margin_mode.return_value = {}
    exchange.set_position_mode.return_value = {}
    return exchange


# ---------------------------------------------------------------------------
# Construction tests
# ---------------------------------------------------------------------------


class TestLiveAdapterConstruction:
    def test_construction_without_approval_file_raises(self, tmp_path: Path) -> None:
        """LiveAdapter raises LiveNotApprovedError when approval file is missing."""
        from trader.execution.live_adapter import LiveAdapter

        settings = make_settings()
        non_existent = tmp_path / "live_approval.yaml"
        # Ensure it doesn't exist
        assert not non_existent.exists()

        with pytest.raises(LiveNotApprovedError):
            LiveAdapter(settings, approval_path=non_existent)  # type: ignore[arg-type]

    def test_construction_with_approval_file_and_mocked_ccxt_succeeds(
        self, tmp_path: Path
    ) -> None:
        """LiveAdapter succeeds when approval file exists and ccxt is mocked."""
        from trader.execution.live_adapter import LiveAdapter

        approval = tmp_path / "live_approval.yaml"
        approval.write_text("approved: true\n")

        settings = make_settings()
        mock_exchange = make_mock_exchange()

        mock_ccxt = MagicMock()
        mock_ccxt.bybit.return_value = mock_exchange

        with patch.dict("sys.modules", {"ccxt": mock_ccxt}):
            adapter = LiveAdapter(settings, approval_path=approval)  # type: ignore[arg-type]

        assert adapter is not None


# ---------------------------------------------------------------------------
# Method tests (all with mocked ccxt)
# ---------------------------------------------------------------------------


class TestLiveAdapterMethods:
    @pytest.fixture()
    def adapter(self, tmp_path: Path):  # type: ignore[return]
        from trader.execution.live_adapter import LiveAdapter

        approval = tmp_path / "live_approval.yaml"
        approval.write_text("approved: true\n")

        settings = make_settings()
        self.mock_exchange = make_mock_exchange()

        mock_ccxt = MagicMock()
        mock_ccxt.bybit.return_value = self.mock_exchange

        with patch.dict("sys.modules", {"ccxt": mock_ccxt}):
            return LiveAdapter(settings, approval_path=approval)  # type: ignore[arg-type]

    def test_place_order_calls_ccxt_create_order(self, adapter: object) -> None:
        from trader.execution.live_adapter import LiveAdapter

        assert isinstance(adapter, LiveAdapter)
        result = adapter.place_order(
            symbol="BTC/USDT:USDT",
            side="buy",
            qty=1.0,
            order_type="limit",
            price=30_000.0,
            client_order_id="test-coid",
        )
        assert result["order_id"] == "exchange-order-123"
        self.mock_exchange.create_order.assert_called_once()

    def test_place_order_sl_failure_raises_sl_placement_error(
        self, tmp_path: Path
    ) -> None:
        """When SL order fails, SlPlacementError is raised."""
        from trader.execution.live_adapter import LiveAdapter

        approval = tmp_path / "live_approval.yaml"
        approval.write_text("approved: true\n")

        settings = make_settings()
        mock_exchange = make_mock_exchange()

        # Entry order succeeds, but SL (stop_market) fails
        mock_exchange.create_order.side_effect = [
            {
                "id": "entry-123",
                "clientOrderId": "test",
                "status": "open",
                "filled": 0.0,
                "average": 30_000.0,
            },
            Exception("SL rejected by exchange"),
        ]

        mock_ccxt = MagicMock()
        mock_ccxt.bybit.return_value = mock_exchange

        with patch.dict("sys.modules", {"ccxt": mock_ccxt}):
            adapter = LiveAdapter(settings, approval_path=approval)  # type: ignore[arg-type]

        with pytest.raises(SlPlacementError):
            adapter.place_order(
                symbol="BTC/USDT:USDT",
                side="buy",
                qty=1.0,
                order_type="limit",
                price=30_000.0,
                stop_loss=29_000.0,
                client_order_id="test-sl-fail",
            )

    def test_get_balance_callable(self, adapter: object) -> None:
        from trader.execution.live_adapter import LiveAdapter

        assert isinstance(adapter, LiveAdapter)
        bal = adapter.get_balance()
        assert "equity_usd" in bal
        assert "available_usd" in bal

    def test_get_positions_callable(self, adapter: object) -> None:
        from trader.execution.live_adapter import LiveAdapter

        assert isinstance(adapter, LiveAdapter)
        positions = adapter.get_positions()
        assert isinstance(positions, list)

    def test_get_open_orders_callable(self, adapter: object) -> None:
        from trader.execution.live_adapter import LiveAdapter

        assert isinstance(adapter, LiveAdapter)
        orders = adapter.get_open_orders("BTC/USDT:USDT")
        assert isinstance(orders, list)

    def test_cancel_order_callable(self, adapter: object) -> None:
        from trader.execution.live_adapter import LiveAdapter

        assert isinstance(adapter, LiveAdapter)
        result = adapter.cancel_order("BTC/USDT:USDT", "order-123")
        assert result is True

    def test_set_leverage_callable(self, adapter: object) -> None:
        from trader.execution.live_adapter import LiveAdapter

        assert isinstance(adapter, LiveAdapter)
        adapter.set_leverage("BTC/USDT:USDT", 3)

    def test_set_margin_mode_callable(self, adapter: object) -> None:
        from trader.execution.live_adapter import LiveAdapter

        assert isinstance(adapter, LiveAdapter)
        adapter.set_margin_mode("BTC/USDT:USDT", "isolated")

    def test_set_position_mode_callable(self, adapter: object) -> None:
        from trader.execution.live_adapter import LiveAdapter

        assert isinstance(adapter, LiveAdapter)
        adapter.set_position_mode("one_way")

    def test_get_market_meta_callable(self, adapter: object) -> None:
        from trader.execution.live_adapter import LiveAdapter

        assert isinstance(adapter, LiveAdapter)
        meta = adapter.get_market_meta("BTC/USDT:USDT")
        assert "lot_size" in meta
        assert "taker_fee" in meta

    def test_get_server_time_callable(self, adapter: object) -> None:
        from trader.execution.live_adapter import LiveAdapter

        assert isinstance(adapter, LiveAdapter)
        t = adapter.get_server_time()
        assert isinstance(t, int)

    def test_ping_callable(self, adapter: object) -> None:
        from trader.execution.live_adapter import LiveAdapter

        assert isinstance(adapter, LiveAdapter)
        result = adapter.ping()
        assert result is True
