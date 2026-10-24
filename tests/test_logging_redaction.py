"""Tests for Phase 1: structured JSON logging and secret redaction."""

import json
import logging

import pytest

from trader.ops.logging import (
    SecretRedactionFilter,
    build_json_formatter,
    redact_dict,
)


class TestRedactDict:
    def test_api_key_redacted(self) -> None:
        result = redact_dict({"ANTHROPIC_API_KEY": "sk-real-secret"})
        assert result["ANTHROPIC_API_KEY"] == "***REDACTED***"

    def test_secret_redacted(self) -> None:
        result = redact_dict({"exchange_api_secret": "my-secret"})
        assert result["exchange_api_secret"] == "***REDACTED***"

    def test_token_redacted(self) -> None:
        result = redact_dict({"TELEGRAM_BOT_TOKEN": "12345:abc"})
        assert result["TELEGRAM_BOT_TOKEN"] == "***REDACTED***"

    def test_password_redacted(self) -> None:
        result = redact_dict({"db_password": "hunter2"})
        assert result["db_password"] == "***REDACTED***"

    def test_webhook_secret_redacted(self) -> None:
        result = redact_dict({"TRADINGVIEW_WEBHOOK_SECRET": "wh-secret"})
        assert result["TRADINGVIEW_WEBHOOK_SECRET"] == "***REDACTED***"

    def test_safe_field_unchanged(self) -> None:
        result = redact_dict({"symbol": "XRP/USDT:USDT", "price": 0.5})
        assert result["symbol"] == "XRP/USDT:USDT"
        assert result["price"] == 0.5

    def test_case_insensitive(self) -> None:
        result = redact_dict({"Api_Key": "secret"})
        assert result["Api_Key"] == "***REDACTED***"

    def test_nested_dict_redacted(self) -> None:
        result = redact_dict({"outer": {"inner_api_key": "secret", "price": 0.5}})
        assert result["outer"]["inner_api_key"] == "***REDACTED***"
        assert result["outer"]["price"] == 0.5

    def test_non_string_value_safe_field(self) -> None:
        result = redact_dict({"count": 42, "price": 1.23})
        assert result["count"] == 42


class TestSecretRedactionFilter:
    def test_filter_applied_to_log_record(self) -> None:
        filt = SecretRedactionFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        record.__dict__["extra"] = {"API_KEY": "real-secret", "symbol": "XRP"}
        filt.filter(record)
        assert record.__dict__["extra"]["API_KEY"] == "***REDACTED***"
        assert record.__dict__["extra"]["symbol"] == "XRP"


class TestJsonFormatter:
    def test_log_line_is_valid_json(self, caplog: pytest.LogCaptureFixture) -> None:
        import io

        formatter = build_json_formatter()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(formatter)
        logger = logging.getLogger("test_json_fmt")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.info("hello world")
        output = stream.getvalue().strip()
        parsed = json.loads(output)
        assert parsed["message"] == "hello world"
        assert "timestamp" in parsed
        assert "level" in parsed
        logger.removeHandler(handler)
