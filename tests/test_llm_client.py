"""Phase 6: Tests for LlmClient — all Anthropic calls are mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trader.config.settings import AppSettings, ModelNames, RiskSettings, Settings, TradingConfig
from trader.dsor.base import Base
from trader.llm.exceptions import BudgetExceededError, LlmError, LlmTimeoutError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session_factory(engine):
    def factory():
        return Session(engine)

    return factory


@pytest.fixture()
def settings():
    return Settings(
        trading_mode="paper",
        app=AppSettings(
            models=ModelNames(
                screening="claude-haiku-test",
                decision="claude-sonnet-test",
                review="claude-sonnet-test",
            ),
            trading=TradingConfig(symbols=["BTC/USDT"]),
        ),
        risk=RiskSettings(
            max_margin_per_trade_usd=10.0,
            max_loss_per_trade_usd=5.0,
            daily_loss_limit_usd=10.0,
            total_llm_budget_usd_per_day=1.0,
            allowed_symbols=["BTC/USDT"],
        ),
    )


def _make_mock_response(text: str, input_tokens: int = 100, output_tokens: int = 50) -> MagicMock:
    """Build a mock Anthropic response object."""
    mock_usage = MagicMock()
    mock_usage.input_tokens = input_tokens
    mock_usage.output_tokens = output_tokens
    mock_usage.cache_read_input_tokens = 0

    mock_content = MagicMock()
    mock_content.text = text

    mock_response = MagicMock()
    mock_response.usage = mock_usage
    mock_response.content = [mock_content]
    return mock_response


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLlmClientSuccess:
    def test_successful_call_returns_text(self, settings, session_factory):
        from trader.llm.client import LlmClient

        mock_response = _make_mock_response("Hello, world!")

        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = MagicMock()
            mock_anthropic_cls.return_value = mock_client
            mock_client.messages.create.return_value = mock_response

            client = LlmClient(settings=settings, session_factory=session_factory)
            result = client.complete(
                purpose="test",
                system="You are a test assistant.",
                messages=[{"role": "user", "content": "Say hello"}],
            )

        assert result == "Hello, world!"

    def test_successful_call_logs_to_llm_calls(self, settings, session_factory, engine):
        from trader.dsor.models import LlmCall
        from trader.llm.client import LlmClient

        mock_response = _make_mock_response("test response", input_tokens=200, output_tokens=100)

        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = MagicMock()
            mock_anthropic_cls.return_value = mock_client
            mock_client.messages.create.return_value = mock_response

            client = LlmClient(settings=settings, session_factory=session_factory)
            client.complete(
                purpose="test_purpose",
                system="system prompt",
                messages=[{"role": "user", "content": "test"}],
            )

        # Verify logged to DB
        with Session(engine) as session:
            rows = session.query(LlmCall).all()
            assert len(rows) == 1
            row = rows[0]
            assert row.purpose == "test_purpose"
            assert row.input_tokens == 200
            assert row.output_tokens == 100
            assert row.cost_usd > 0

    def test_cost_accumulates_across_calls(self, settings, session_factory, engine):
        from trader.dsor.models import LlmCall
        from trader.llm.client import LlmClient

        mock_response = _make_mock_response("resp", input_tokens=100, output_tokens=50)

        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = MagicMock()
            mock_anthropic_cls.return_value = mock_client
            mock_client.messages.create.return_value = mock_response

            client = LlmClient(settings=settings, session_factory=session_factory)
            for _ in range(3):
                client.complete(
                    purpose="loop",
                    system="sys",
                    messages=[{"role": "user", "content": "msg"}],
                )

        with Session(engine) as session:
            rows = session.query(LlmCall).all()
            assert len(rows) == 3
            total_cost = sum(r.cost_usd for r in rows)
            assert total_cost > 0

    def test_model_override_used(self, settings, session_factory, engine):
        from trader.dsor.models import LlmCall
        from trader.llm.client import LlmClient

        mock_response = _make_mock_response("resp")

        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = MagicMock()
            mock_anthropic_cls.return_value = mock_client
            mock_client.messages.create.return_value = mock_response

            client = LlmClient(settings=settings, session_factory=session_factory)
            client.complete(
                purpose="test",
                system="sys",
                messages=[{"role": "user", "content": "msg"}],
                model="custom-model",
            )

        with Session(engine) as session:
            rows = session.query(LlmCall).all()
            assert rows[0].model == "custom-model"

    def test_cache_hit_detected(self, settings, session_factory, engine):
        from trader.dsor.models import LlmCall
        from trader.llm.client import LlmClient

        mock_response = _make_mock_response("resp")
        mock_response.usage.cache_read_input_tokens = 80  # cache hit

        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = MagicMock()
            mock_anthropic_cls.return_value = mock_client
            mock_client.messages.create.return_value = mock_response

            client = LlmClient(settings=settings, session_factory=session_factory)
            client.complete(
                purpose="cached",
                system="sys",
                messages=[{"role": "user", "content": "msg"}],
            )

        with Session(engine) as session:
            row = session.query(LlmCall).first()
            assert row.cache_hit is True


class TestLlmClientBudget:
    def test_budget_exceeded_raises_before_api_call(self, settings, session_factory, engine):
        """Budget check happens BEFORE any Anthropic API call."""
        from trader.dsor.models import LlmCall
        from trader.llm.client import LlmClient

        # Pre-populate llm_calls with cost exceeding budget
        with Session(engine) as session:
            import datetime

            row = LlmCall(
                ts=datetime.datetime.utcnow(),
                purpose="previous",
                model="claude-sonnet",
                input_tokens=1000,
                output_tokens=500,
                cost_usd=2.0,  # exceeds budget of 1.0
                cache_hit=False,
            )
            session.add(row)
            session.commit()

        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = MagicMock()
            mock_anthropic_cls.return_value = mock_client

            client = LlmClient(settings=settings, session_factory=session_factory)
            with pytest.raises(BudgetExceededError) as exc_info:
                client.complete(
                    purpose="test",
                    system="sys",
                    messages=[{"role": "user", "content": "msg"}],
                )

            # API should NOT have been called
            mock_client.messages.create.assert_not_called()

        assert exc_info.value.limit == 1.0
        assert exc_info.value.spent >= 1.0

    def test_budget_exactly_at_limit_raises(self, settings, session_factory, engine):
        from trader.dsor.models import LlmCall
        from trader.llm.client import LlmClient

        with Session(engine) as session:
            import datetime

            row = LlmCall(
                ts=datetime.datetime.utcnow(),
                purpose="prev",
                model="claude",
                input_tokens=100,
                output_tokens=50,
                cost_usd=1.0,  # exactly at limit
                cache_hit=False,
            )
            session.add(row)
            session.commit()

        with patch("anthropic.Anthropic"):
            client = LlmClient(settings=settings, session_factory=session_factory)
            with pytest.raises(BudgetExceededError):
                client.complete(
                    purpose="test",
                    system="sys",
                    messages=[{"role": "user", "content": "msg"}],
                )


class TestLlmClientErrors:
    def test_api_timeout_raises_llm_timeout_error(self, settings, session_factory):
        import anthropic

        from trader.llm.client import LlmClient

        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = MagicMock()
            mock_anthropic_cls.return_value = mock_client
            mock_client.messages.create.side_effect = anthropic.APITimeoutError(request=MagicMock())

            client = LlmClient(settings=settings, session_factory=session_factory)
            with pytest.raises(LlmTimeoutError):
                client.complete(
                    purpose="test",
                    system="sys",
                    messages=[{"role": "user", "content": "msg"}],
                    timeout=5.0,
                )

    def test_api_error_raises_llm_error(self, settings, session_factory):
        import anthropic

        from trader.llm.client import LlmClient

        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = MagicMock()
            mock_anthropic_cls.return_value = mock_client
            mock_client.messages.create.side_effect = anthropic.APIStatusError(
                "server error", response=MagicMock(status_code=500), body={}
            )

            client = LlmClient(settings=settings, session_factory=session_factory)
            with pytest.raises(LlmError):
                client.complete(
                    purpose="test",
                    system="sys",
                    messages=[{"role": "user", "content": "msg"}],
                )
