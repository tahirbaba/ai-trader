"""Phase 2B: Tests for CrossAssetCollector."""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trader.data.collector import CollectorResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_coingecko_response(btc_pct: float = 45.3, eth_pct: float = 18.7) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "market_cap_percentage": {
                "btc": btc_pct,
                "eth": eth_pct,
            }
        }
    }
    mock_resp.raise_for_status.return_value = None
    return mock_resp


def _fake_yf_ticker(price: float = 100.0) -> MagicMock:
    mock_ticker = MagicMock()
    mock_ticker.fast_info.last_price = price
    return mock_ticker


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCrossAssetCollector:
    def test_stale_outside_trading_hours(self):
        """Sunday 10:00 UTC — traditional markets closed → stale True."""
        from trader.data.cross_asset import CrossAssetCollector

        sunday_10am = datetime.datetime(2026, 10, 25, 10, 0, 0)  # Sunday

        with (
            patch("trader.data.cross_asset.requests.get", return_value=_fake_coingecko_response()),
            patch("trader.data.cross_asset.yf.Ticker", return_value=_fake_yf_ticker(100.0)),
            patch("trader.data.cross_asset._utcnow", return_value=sunday_10am),
        ):
            from trader.data.cross_asset import CrossAssetCollector

            result = CrossAssetCollector().collect()

        stale = result.data["stale_mask"]
        assert stale["DXY"] is True
        assert stale["SPX"] is True
        assert stale["GOLD"] is True
        # crypto never stale
        assert stale["BTC_DOM"] is False
        assert stale["ETH_BTC"] is False

    def test_not_stale_during_trading_hours(self):
        """Weekday 15:00 UTC — traditional markets open → stale False."""
        from trader.data.cross_asset import CrossAssetCollector

        wednesday_3pm = datetime.datetime(2026, 10, 21, 15, 0, 0)  # Wednesday

        with (
            patch("trader.data.cross_asset.requests.get", return_value=_fake_coingecko_response()),
            patch("trader.data.cross_asset.yf.Ticker", return_value=_fake_yf_ticker(100.0)),
            patch("trader.data.cross_asset._utcnow", return_value=wednesday_3pm),
        ):
            result = CrossAssetCollector().collect()

        stale = result.data["stale_mask"]
        assert stale["DXY"] is False
        assert stale["SPX"] is False
        assert stale["GOLD"] is False

    def test_partial_failure_returns_partial_result(self):
        """If yfinance raises for DXY, SPX should still appear in data."""
        from trader.data.cross_asset import CrossAssetCollector

        call_count = 0

        def _yf_side_effect(ticker_sym: str) -> Any:
            nonlocal call_count
            call_count += 1
            if ticker_sym == "DX-Y.NYB":
                raise RuntimeError("yfinance DXY error")
            return _fake_yf_ticker(4500.0)

        weekday_3pm = datetime.datetime(2026, 10, 21, 15, 0, 0)

        with (
            patch("trader.data.cross_asset.requests.get", return_value=_fake_coingecko_response()),
            patch("trader.data.cross_asset.yf.Ticker", side_effect=_yf_side_effect),
            patch("trader.data.cross_asset._utcnow", return_value=weekday_3pm),
        ):
            result = CrossAssetCollector().collect()

        # DXY failed → None or absent
        assert result.data.get("DXY") is None
        # SPX succeeded
        assert result.data.get("SPX") == pytest.approx(4500.0)

    def test_coingecko_btc_dom(self):
        """Mock CoinGecko response; verify BTC_DOM extracted correctly."""
        from trader.data.cross_asset import CrossAssetCollector

        weekday_15 = datetime.datetime(2026, 10, 21, 15, 0, 0)

        with (
            patch(
                "trader.data.cross_asset.requests.get",
                return_value=_fake_coingecko_response(btc_pct=52.1, eth_pct=17.5),
            ),
            patch("trader.data.cross_asset.yf.Ticker", return_value=_fake_yf_ticker(100.0)),
            patch("trader.data.cross_asset._utcnow", return_value=weekday_15),
        ):
            result = CrossAssetCollector().collect()

        assert result.data["BTC_DOM"] == pytest.approx(52.1)

    def test_collect_returns_collector_result(self):
        """Verify the return type is CollectorResult."""
        from trader.data.cross_asset import CrossAssetCollector

        weekday_15 = datetime.datetime(2026, 10, 21, 15, 0, 0)

        with (
            patch("trader.data.cross_asset.requests.get", return_value=_fake_coingecko_response()),
            patch("trader.data.cross_asset.yf.Ticker", return_value=_fake_yf_ticker(100.0)),
            patch("trader.data.cross_asset._utcnow", return_value=weekday_15),
        ):
            result = CrossAssetCollector().collect()

        assert isinstance(result, CollectorResult)
        assert "stale_mask" in result.data
