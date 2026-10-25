"""Phase 6: Tests for MacroNewsAnalyst, TechnicalAnalyst, DerivativesAnalyst."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from trader.agents.derivatives_analyst import DerivativesAnalyst
from trader.agents.macro_analyst import MacroNewsAnalyst
from trader.agents.technical_analyst import TechnicalAnalyst
from trader.llm.schemas import DerivativesReport, MacroNewsReport, TechnicalReport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_llm(response_text: str) -> MagicMock:
    """Create a mock LlmClient that returns a fixed response text."""
    mock = MagicMock()
    mock.complete.return_value = response_text
    return mock


VALID_MACRO_JSON = json.dumps(
    {
        "agent": "MacroNewsAnalyst",
        "bias": "bearish",
        "confidence": 0.65,
        "key_points": ["FOMC meeting tomorrow", "DXY rising"],
        "risks": ["policy surprise"],
        "data_used": ["news", "macro_events"],
        "data_missing": [],
        "is_blackout": False,
        "blackout_reason": "",
        "regime_shift_detected": False,
        "news_shock": False,
    }
)

VALID_TECHNICAL_JSON = json.dumps(
    {
        "agent": "TechnicalAnalyst",
        "bias": "bullish",
        "confidence": 0.72,
        "key_points": ["above 200 EMA", "RSI recovering from oversold"],
        "risks": ["resistance at 52000"],
        "data_used": ["indicators"],
        "data_missing": [],
    }
)

VALID_DERIVATIVES_JSON = json.dumps(
    {
        "agent": "DerivativesAnalyst",
        "bias": "neutral",
        "confidence": 0.55,
        "key_points": ["funding rate near zero"],
        "risks": ["OI declining"],
        "data_used": ["derivatives", "orderflow"],
        "data_missing": [],
    }
)


# ---------------------------------------------------------------------------
# MacroNewsAnalyst tests
# ---------------------------------------------------------------------------


class TestMacroNewsAnalyst:
    def test_valid_json_response_returns_report(self):
        llm = make_llm(VALID_MACRO_JSON)
        analyst = MacroNewsAnalyst(llm=llm)
        report = analyst.analyze(
            news_items=[{"title": "Fed raises rates", "summary": "Fed hiked by 25bp"}],
            macro_events=[{"name": "FOMC", "importance": "high", "ts": "2026-10-25T18:00:00"}],
            cross_asset={"DXY": 103.5, "SPX": 4200},
            is_macro_blackout=False,
            symbol="BTC/USDT",
        )

        assert isinstance(report, MacroNewsReport)
        assert report.agent == "MacroNewsAnalyst"
        assert report.bias == "bearish"
        assert report.confidence == 0.65
        assert report.is_blackout is False

    def test_invalid_json_returns_neutral_report(self):
        llm = make_llm("this is not valid json at all!!")
        analyst = MacroNewsAnalyst(llm=llm)
        report = analyst.analyze(
            news_items=[],
            macro_events=[],
            cross_asset={},
            is_macro_blackout=False,
            symbol="BTC/USDT",
        )

        assert isinstance(report, MacroNewsReport)
        assert report.bias == "neutral"
        assert report.confidence == 0.0
        assert "parse_error" in report.data_missing

    def test_markdown_wrapped_json_parsed(self):
        wrapped = f"```json\n{VALID_MACRO_JSON}\n```"
        llm = make_llm(wrapped)
        analyst = MacroNewsAnalyst(llm=llm)
        report = analyst.analyze(
            news_items=[], macro_events=[], cross_asset={}, is_macro_blackout=False, symbol="BTC/USDT"
        )
        assert report.bias == "bearish"

    def test_llm_called_with_sanitized_news(self):
        """Verify LLM complete() is called (news is sanitized before passing)."""
        llm = make_llm(VALID_MACRO_JSON)
        analyst = MacroNewsAnalyst(llm=llm)
        analyst.analyze(
            news_items=[{"title": "<b>BTC</b> drops", "summary": "price fell 10%"}],
            macro_events=[],
            cross_asset={},
            is_macro_blackout=False,
            symbol="BTC/USDT",
        )
        llm.complete.assert_called_once()
        call_kwargs = llm.complete.call_args
        # Messages should contain sanitized text (wrapped in <title> tags)
        messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[2] if len(call_kwargs.args) > 2 else call_kwargs.kwargs.get("messages", [])
        content = str(messages)
        assert "<title>" in content  # sanitized

    def test_llm_error_returns_neutral_report(self):
        from trader.llm.exceptions import LlmError

        llm = MagicMock()
        llm.complete.side_effect = LlmError("API down")
        analyst = MacroNewsAnalyst(llm=llm)
        report = analyst.analyze(
            news_items=[], macro_events=[], cross_asset={}, is_macro_blackout=False, symbol="ETH/USDT"
        )
        assert report.bias == "neutral"
        assert "analyst_error" in report.data_missing


# ---------------------------------------------------------------------------
# TechnicalAnalyst tests
# ---------------------------------------------------------------------------


class TestTechnicalAnalyst:
    def test_valid_json_returns_report(self):
        llm = make_llm(VALID_TECHNICAL_JSON)
        analyst = TechnicalAnalyst(llm=llm)
        report = analyst.analyze(
            feature_pack_dict={"rsi_14": 42, "ema_200": 49000, "atr_14": 500},
            symbol="BTC/USDT",
        )

        assert isinstance(report, TechnicalReport)
        assert report.bias == "bullish"
        assert report.confidence == 0.72

    def test_invalid_json_returns_neutral(self):
        llm = make_llm("Not JSON")
        analyst = TechnicalAnalyst(llm=llm)
        report = analyst.analyze(feature_pack_dict={}, symbol="BTC/USDT")

        assert report.bias == "neutral"
        assert "parse_error" in report.data_missing

    def test_markdown_wrapped_json_parsed(self):
        wrapped = f"```\n{VALID_TECHNICAL_JSON}\n```"
        llm = make_llm(wrapped)
        analyst = TechnicalAnalyst(llm=llm)
        report = analyst.analyze(feature_pack_dict={}, symbol="ETH/USDT")
        assert report.bias == "bullish"

    def test_llm_error_returns_neutral(self):
        from trader.llm.exceptions import LlmError

        llm = MagicMock()
        llm.complete.side_effect = LlmError("timeout")
        analyst = TechnicalAnalyst(llm=llm)
        report = analyst.analyze(feature_pack_dict={}, symbol="BTC/USDT")
        assert report.bias == "neutral"


# ---------------------------------------------------------------------------
# DerivativesAnalyst tests
# ---------------------------------------------------------------------------


class TestDerivativesAnalyst:
    def test_valid_json_returns_report(self):
        llm = make_llm(VALID_DERIVATIVES_JSON)
        analyst = DerivativesAnalyst(llm=llm)
        report = analyst.analyze(
            derivatives={"funding_rate": 0.0001, "open_interest": 1_000_000},
            orderflow={"cvd": 500, "delta": 200},
            symbol="BTC/USDT",
        )

        assert isinstance(report, DerivativesReport)
        assert report.bias == "neutral"
        assert report.confidence == 0.55

    def test_invalid_json_returns_neutral(self):
        llm = make_llm("{broken json")
        analyst = DerivativesAnalyst(llm=llm)
        report = analyst.analyze(derivatives={}, orderflow={}, symbol="BTC/USDT")

        assert report.bias == "neutral"
        assert "parse_error" in report.data_missing

    def test_missing_agent_field_returns_neutral(self):
        # Missing required 'bias' field
        bad_json = '{"agent": "DerivativesAnalyst", "confidence": 0.5}'
        llm = make_llm(bad_json)
        analyst = DerivativesAnalyst(llm=llm)
        report = analyst.analyze(derivatives={}, orderflow={}, symbol="BTC/USDT")
        assert report.bias == "neutral"
