"""Phase 6: Tests for DecisionAgent."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from trader.agents.decision_agent import DecisionAgent, _compute_prompt_hash
from trader.llm.schemas import (
    BrainDecision,
    DerivativesReport,
    MacroNewsReport,
    TechnicalReport,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _macro_report() -> MacroNewsReport:
    return MacroNewsReport(agent="MacroNewsAnalyst", bias="neutral", confidence=0.5)


def _technical_report() -> TechnicalReport:
    return TechnicalReport(agent="TechnicalAnalyst", bias="bullish", confidence=0.7)


def _derivatives_report() -> DerivativesReport:
    return DerivativesReport(agent="DerivativesAnalyst", bias="neutral", confidence=0.5)


def _make_settings():
    from trader.config.settings import (
        AppSettings,
        ModelNames,
        RiskSettings,
        Settings,
        TradingConfig,
    )

    return Settings(
        trading_mode="paper",
        app=AppSettings(
            models=ModelNames(
                screening="claude-haiku-test",
                decision="claude-sonnet-test",
                review="claude-sonnet-test",
            ),
            trading=TradingConfig(),
        ),
        risk=RiskSettings(
            max_margin_per_trade_usd=10.0,
            max_loss_per_trade_usd=5.0,
            daily_loss_limit_usd=10.0,
        ),
    )


def make_llm(response_text: str, settings=None) -> MagicMock:
    mock = MagicMock()
    mock.complete.return_value = response_text
    mock._settings = settings or _make_settings()
    return mock


VALID_LONG_JSON = json.dumps(
    {
        "action": "LONG",
        "symbol": "BTC/USDT",
        "entry_type": "limit",
        "entry_price": 50000.0,
        "stop_loss": 48000.0,
        "take_profit": 55000.0,
        "invalidation": "price closes below 48000",
        "confidence": 0.78,
        "thesis": "strong breakout with high volume",
        "signals_used": ["ema_crossover", "rsi_recovery", "funding_neutral"],
        "lessons_considered": [{"id": "L001", "applied": True, "note": "avoid high funding"}],
        "data_missing": [],
        "why_not_no_trade": "3 independent signals align with R:R of 2.5",
        "expected_risk_reward": 2.5,
    }
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDecisionAgent:
    def test_valid_long_decision(self):
        settings = _make_settings()
        llm = make_llm(VALID_LONG_JSON, settings)
        agent = DecisionAgent(llm=llm)
        decision = agent.decide(
            macro_report=_macro_report(),
            technical_report=_technical_report(),
            derivatives_report=_derivatives_report(),
            feature_pack={"confluence_score": 4},
            confluence_score=4,
            ksor_lessons=[],
            account_state={"equity_usd": 100.0},
            symbol="BTC/USDT",
            cycle_id="test-001",
        )

        assert isinstance(decision, BrainDecision)
        assert decision.action == "LONG"
        assert decision.symbol == "BTC/USDT"
        assert decision.stop_loss == 48000.0
        assert decision.confidence == 0.78

    def test_garbage_response_returns_no_trade(self):
        settings = _make_settings()
        llm = make_llm("sorry I cannot help with that today", settings)
        agent = DecisionAgent(llm=llm)
        decision = agent.decide(
            macro_report=_macro_report(),
            technical_report=_technical_report(),
            derivatives_report=_derivatives_report(),
            feature_pack={},
            confluence_score=0,
            ksor_lessons=[],
            account_state={},
            symbol="BTC/USDT",
            cycle_id="test-002",
        )

        assert decision.action == "NO_TRADE"
        assert "parse_error" in decision.thesis

    def test_high_confidence_accepted_by_schema(self):
        """Schema allows confidence > 0.9; RiskGate may filter it."""
        high_conf = json.loads(VALID_LONG_JSON)
        high_conf["confidence"] = 0.95
        settings = _make_settings()
        llm = make_llm(json.dumps(high_conf), settings)
        agent = DecisionAgent(llm=llm)
        decision = agent.decide(
            macro_report=_macro_report(),
            technical_report=_technical_report(),
            derivatives_report=_derivatives_report(),
            feature_pack={},
            confluence_score=4,
            ksor_lessons=[],
            account_state={},
            symbol="BTC/USDT",
            cycle_id="test-003",
        )

        assert decision.action == "LONG"
        assert decision.confidence == 0.95

    def test_prompt_hash_stable_for_same_input(self):
        """Same inputs should produce the same prompt hash."""
        settings = _make_settings()
        llm = make_llm(VALID_LONG_JSON, settings)
        agent = DecisionAgent(llm=llm)

        prompt1 = agent._build_prompt(
            macro_report=_macro_report(),
            technical_report=_technical_report(),
            derivatives_report=_derivatives_report(),
            feature_pack={"confluence_score": 4},
            confluence_score=4,
            ksor_lessons=[],
            account_state={},
            symbol="BTC/USDT",
            cycle_id="fixed-cycle-id",
        )
        prompt2 = agent._build_prompt(
            macro_report=_macro_report(),
            technical_report=_technical_report(),
            derivatives_report=_derivatives_report(),
            feature_pack={"confluence_score": 4},
            confluence_score=4,
            ksor_lessons=[],
            account_state={},
            symbol="BTC/USDT",
            cycle_id="fixed-cycle-id",
        )

        assert _compute_prompt_hash(prompt1) == _compute_prompt_hash(prompt2)

    def test_uses_decision_model_from_settings(self):
        """DecisionAgent uses settings.models.decision, not screening."""
        settings = _make_settings()
        llm = make_llm(VALID_LONG_JSON, settings)
        agent = DecisionAgent(llm=llm)

        assert agent._model == "claude-sonnet-test"

    def test_invalid_action_returns_no_trade(self):
        """LLM returns action not in Literal — schema validation fails → NO_TRADE."""
        bad_json = '{"action": "YOLO", "symbol": "BTC/USDT", "confidence": 0.9}'
        settings = _make_settings()
        llm = make_llm(bad_json, settings)
        agent = DecisionAgent(llm=llm)
        decision = agent.decide(
            macro_report=_macro_report(),
            technical_report=_technical_report(),
            derivatives_report=_derivatives_report(),
            feature_pack={},
            confluence_score=0,
            ksor_lessons=[],
            account_state={},
            symbol="BTC/USDT",
            cycle_id="test-004",
        )
        assert decision.action == "NO_TRADE"

    def test_markdown_wrapped_json_parsed(self):
        wrapped = f"```json\n{VALID_LONG_JSON}\n```"
        settings = _make_settings()
        llm = make_llm(wrapped, settings)
        agent = DecisionAgent(llm=llm)
        decision = agent.decide(
            macro_report=_macro_report(),
            technical_report=_technical_report(),
            derivatives_report=_derivatives_report(),
            feature_pack={},
            confluence_score=4,
            ksor_lessons=[],
            account_state={},
            symbol="BTC/USDT",
            cycle_id="test-005",
        )
        assert decision.action == "LONG"
