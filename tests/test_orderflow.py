"""Phase 2A: Unit tests for order-flow helpers (CVD, book imbalance)."""

from __future__ import annotations

import pytest

from trader.data.orderflow import book_imbalance, cvd_from_trades


class TestCvdFromTrades:
    def test_all_buys(self):
        trades = [
            {"side": "Buy", "size": "100"},
            {"side": "Buy", "size": "200"},
        ]
        assert cvd_from_trades(trades) == pytest.approx(300.0)

    def test_all_sells(self):
        trades = [
            {"side": "Sell", "size": "150"},
            {"side": "Sell", "size": "50"},
        ]
        assert cvd_from_trades(trades) == pytest.approx(-200.0)

    def test_mixed(self):
        trades = [
            {"side": "Buy", "size": "500"},
            {"side": "Sell", "size": "200"},
            {"side": "Buy", "size": "100"},
            {"side": "Sell", "size": "50"},
        ]
        # +500 - 200 + 100 - 50 = +350
        assert cvd_from_trades(trades) == pytest.approx(350.0)

    def test_empty_trades(self):
        assert cvd_from_trades([]) == pytest.approx(0.0)

    def test_unknown_side_ignored(self):
        trades = [{"side": "Unknown", "size": "1000"}]
        assert cvd_from_trades(trades) == pytest.approx(0.0)

    def test_case_insensitive_side(self):
        trades = [
            {"side": "buy", "size": "100"},
            {"side": "sell", "size": "50"},
        ]
        assert cvd_from_trades(trades) == pytest.approx(50.0)

    def test_numeric_size(self):
        trades = [{"side": "Buy", "size": 250.5}]
        assert cvd_from_trades(trades) == pytest.approx(250.5)

    def test_zero_size_trade(self):
        trades = [
            {"side": "Buy", "size": "0"},
            {"side": "Sell", "size": "100"},
        ]
        assert cvd_from_trades(trades) == pytest.approx(-100.0)


class TestBookImbalance:
    def test_equal_sides(self):
        bids = [["0.60", "1000"]]
        asks = [["0.61", "1000"]]
        assert book_imbalance(bids, asks) == pytest.approx(0.0)

    def test_all_bids(self):
        bids = [["0.60", "1000"]]
        asks: list = []
        assert book_imbalance(bids, asks) == pytest.approx(1.0)

    def test_all_asks(self):
        bids: list = []
        asks = [["0.61", "1000"]]
        assert book_imbalance(bids, asks) == pytest.approx(-1.0)

    def test_bid_dominated(self):
        bids = [["0.60", "750"]]
        asks = [["0.61", "250"]]
        # (750 - 250) / (750 + 250) = 500 / 1000 = 0.5
        assert book_imbalance(bids, asks) == pytest.approx(0.5)

    def test_ask_dominated(self):
        bids = [["0.60", "200"]]
        asks = [["0.61", "800"]]
        # (200 - 800) / (200 + 800) = -600 / 1000 = -0.6
        assert book_imbalance(bids, asks) == pytest.approx(-0.6)

    def test_empty_both_sides(self):
        assert book_imbalance([], []) == pytest.approx(0.0)

    def test_depth_limits_levels(self):
        # 3 bid levels, 3 ask levels; depth=2 means only first 2 are summed
        bids = [["0.60", "100"], ["0.59", "100"], ["0.58", "100"]]  # 200 in top 2
        asks = [["0.61", "100"], ["0.62", "100"], ["0.63", "100"]]  # 200 in top 2
        assert book_imbalance(bids, asks, depth=2) == pytest.approx(0.0)

    def test_depth_default_is_ten(self):
        # Feed 15 levels, only 10 should be counted
        bids = [[str(0.60 - i * 0.001), "100"] for i in range(15)]
        asks = [[str(0.61 + i * 0.001), "100"] for i in range(15)]
        # Top 10 bid vol = 1000, top 10 ask vol = 1000 → imbalance = 0
        assert book_imbalance(bids, asks) == pytest.approx(0.0)

    def test_multi_level_imbalance(self):
        bids = [["0.60", "300"], ["0.59", "200"]]  # total 500
        asks = [["0.61", "100"]]  # total 100
        # (500 - 100) / 600 = 400/600 ≈ 0.6667
        assert book_imbalance(bids, asks) == pytest.approx(400 / 600)
