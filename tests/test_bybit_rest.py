"""Phase 2A: Unit tests for BybitRestClient (all HTTP mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from trader.data.bybit_rest import BybitRestClient, BybitRestError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(json_body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    return resp


def _kline_payload(rows: list | None = None) -> dict:
    _rows = rows if rows is not None else [
        ["1700000000000", "0.60", "0.62", "0.59", "0.61", "1000000", "610000"],
    ]
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": _rows,
        },
    }


def _orderbook_payload(bids=None, asks=None, ts="1700000000000") -> dict:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "s": "XRPUSDT",
            "b": bids or [["0.605", "5000"]],
            "a": asks or [["0.606", "3000"]],
            "ts": ts,
        },
    }


def _ticker_payload() -> dict:
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [
                {
                    "symbol": "XRPUSDT",
                    "lastPrice": "0.605",
                    "bid1Price": "0.604",
                    "ask1Price": "0.606",
                    "markPrice": "0.6049",
                    "indexPrice": "0.6045",
                    "openInterest": "250000000",
                    "fundingRate": "0.0001",
                    "nextFundingTime": "1700020000000",
                }
            ]
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetKline:
    def test_returns_parsed_list(self):
        row = ["1700000000000", "0.60", "0.62", "0.59", "0.61", "1000000", "610000"]
        payload = _kline_payload([row])
        with patch("requests.Session.get", return_value=_make_response(payload)):
            client = BybitRestClient()
            result = client.get_kline("XRPUSDT", "5")
        assert len(result) == 1
        assert result[0] == row

    def test_multiple_rows_returned_oldest_first(self):
        # Bybit returns newest first; our method reverses
        newest = ["1700000100000", "0.62", "0.63", "0.61", "0.62", "500", "310"]
        oldest = ["1700000000000", "0.60", "0.62", "0.59", "0.61", "1000", "610"]
        payload = _kline_payload([newest, oldest])  # newest first (bybit order)
        with patch("requests.Session.get", return_value=_make_response(payload)):
            client = BybitRestClient()
            result = client.get_kline("XRPUSDT", "5", limit=2)
        # After reversal: oldest first
        assert result[0] == oldest
        assert result[1] == newest

    def test_empty_result(self):
        payload = _kline_payload([])
        with patch("requests.Session.get", return_value=_make_response(payload)):
            client = BybitRestClient()
            result = client.get_kline("XRPUSDT", "5")
        assert result == []


class TestGetOrderbook:
    def test_returns_bids_and_asks(self):
        payload = _orderbook_payload(
            bids=[["0.605", "5000"], ["0.604", "2000"]],
            asks=[["0.606", "3000"]],
        )
        with patch("requests.Session.get", return_value=_make_response(payload)):
            client = BybitRestClient()
            result = client.get_orderbook("XRPUSDT")
        assert result["bids"] == [["0.605", "5000"], ["0.604", "2000"]]
        assert result["asks"] == [["0.606", "3000"]]
        assert result["symbol"] == "XRPUSDT"

    def test_ts_present(self):
        payload = _orderbook_payload(ts="1700000000123")
        with patch("requests.Session.get", return_value=_make_response(payload)):
            client = BybitRestClient()
            result = client.get_orderbook("XRPUSDT")
        assert result["ts"] == "1700000000123"


class TestGetTickers:
    def test_returns_ticker_dict(self):
        payload = _ticker_payload()
        with patch("requests.Session.get", return_value=_make_response(payload)):
            client = BybitRestClient()
            result = client.get_tickers("XRPUSDT")
        assert result["symbol"] == "XRPUSDT"
        assert result["fundingRate"] == "0.0001"

    def test_raises_when_empty_list(self):
        payload = {"retCode": 0, "retMsg": "OK", "result": {"list": []}}
        with patch("requests.Session.get", return_value=_make_response(payload)):
            client = BybitRestClient()
            with pytest.raises(BybitRestError):
                client.get_tickers("XRPUSDT")


class TestRetryBehavior:
    def test_retries_on_500_then_succeeds(self):
        """Second call succeeds after a 500 on the first."""
        fail_resp = _make_response({}, status_code=500)
        ok_resp = _make_response(_kline_payload())

        with patch("requests.Session.get", side_effect=[fail_resp, ok_resp]), patch("time.sleep"):
            client = BybitRestClient(max_retries=2)
            result = client.get_kline("XRPUSDT", "5")
        assert len(result) == 1

    def test_raises_after_max_retries(self):
        """Should raise BybitRestError once all retries are exhausted."""
        fail_resp = _make_response({}, status_code=500)

        with patch(
            "requests.Session.get",
            side_effect=[fail_resp, fail_resp, fail_resp],
        ), patch("time.sleep"):
            client = BybitRestClient(max_retries=3)
            with pytest.raises(BybitRestError):
                client.get_kline("XRPUSDT", "5")

    def test_retries_on_connection_error(self):
        """ConnectionError triggers a retry."""
        ok_resp = _make_response(_kline_payload())

        with patch(
            "requests.Session.get",
            side_effect=[requests.ConnectionError("timeout"), ok_resp],
        ), patch("time.sleep"):
            client = BybitRestClient(max_retries=2)
            result = client.get_kline("XRPUSDT", "5")
        assert len(result) == 1

    def test_raises_on_api_error_retcode(self):
        """Non-zero retCode should raise immediately (no retry)."""
        error_payload = {"retCode": 10001, "retMsg": "Parameter error", "result": {}}
        ok_resp = _make_response(error_payload)

        with patch("requests.Session.get", return_value=ok_resp):
            client = BybitRestClient(max_retries=3)
            with pytest.raises(BybitRestError, match="retCode=10001"):
                client.get("bogus/path")


class TestGetLongShortRatio:
    def test_parses_ratio_list(self):
        payload = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {"timestamp": "1700000000000", "buyRatio": "0.55", "sellRatio": "0.45"},
                ]
            },
        }
        with patch("requests.Session.get", return_value=_make_response(payload)):
            client = BybitRestClient()
            result = client.get_long_short_ratio("XRPUSDT")
        assert len(result) == 1
        assert result[0][1] == "0.55"
        assert result[0][2] == "0.45"


class TestGetRecentTrades:
    def test_returns_trade_list(self):
        payload = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {"price": "0.61", "size": "1000", "side": "Buy", "time": "1700000001000"},
                    {"price": "0.60", "size": "500", "side": "Sell", "time": "1700000000000"},
                ]
            },
        }
        with patch("requests.Session.get", return_value=_make_response(payload)):
            client = BybitRestClient()
            result = client.get_recent_trades("XRPUSDT")
        # Reversed (oldest first)
        assert result[0]["time"] == "1700000000000"
        assert result[1]["time"] == "1700000001000"
