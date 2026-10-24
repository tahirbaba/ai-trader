"""Phase 2B: Tests for SentimentCollector."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

from trader.data.collector import CollectorResult


def _mock_fng_response(value: str = "42", label: str = "Fear") -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": [
            {
                "value": value,
                "value_classification": label,
                "timestamp": "1234567890",
            }
        ]
    }
    mock_resp.raise_for_status.return_value = None
    return mock_resp


class TestSentimentCollector:
    def test_disabled_returns_disabled_flag(self):
        """SentimentCollector(enabled=False).collect() → quality_flags=['disabled']."""
        from trader.data.sentiment import SentimentCollector

        collector = SentimentCollector(enabled=False)
        result = collector.collect()

        assert isinstance(result, CollectorResult)
        assert "disabled" in result.quality_flags
        assert result.data == {}

    def test_collects_fear_greed(self):
        """Mock API → fear_greed_value == 42."""
        from trader.data.sentiment import SentimentCollector

        with patch(
            "trader.data.sentiment.requests.get", return_value=_mock_fng_response("42", "Fear")
        ):
            collector = SentimentCollector(enabled=True)
            result = collector.collect()

        assert result.data["fear_greed_value"] == 42
        assert result.data["fear_greed_label"] == "Fear"
        assert isinstance(result.data["ts"], datetime.datetime)

    def test_api_failure_returns_error_flag(self):
        """requests.get raises → quality_flags=['sentiment_error']."""
        from trader.data.sentiment import SentimentCollector

        with patch("trader.data.sentiment.requests.get", side_effect=Exception("API down")):
            collector = SentimentCollector(enabled=True)
            result = collector.collect()

        assert "sentiment_error" in result.quality_flags
        assert isinstance(result, CollectorResult)

    def test_collect_returns_collector_result(self):
        """Return type is CollectorResult."""
        from trader.data.sentiment import SentimentCollector

        with patch("trader.data.sentiment.requests.get", return_value=_mock_fng_response()):
            collector = SentimentCollector(enabled=True)
            result = collector.collect()

        assert isinstance(result, CollectorResult)

    def test_greed_classification(self):
        """Greed classification is parsed correctly."""
        from trader.data.sentiment import SentimentCollector

        with patch(
            "trader.data.sentiment.requests.get",
            return_value=_mock_fng_response("78", "Greed"),
        ):
            collector = SentimentCollector(enabled=True)
            result = collector.collect()

        assert result.data["fear_greed_value"] == 78
        assert result.data["fear_greed_label"] == "Greed"

    def test_symbols_none_allowed(self):
        """SentimentCollector(symbols=None) works fine."""
        from trader.data.sentiment import SentimentCollector

        with patch("trader.data.sentiment.requests.get", return_value=_mock_fng_response()):
            collector = SentimentCollector(symbols=None, enabled=True)
            result = collector.collect()

        assert isinstance(result, CollectorResult)
