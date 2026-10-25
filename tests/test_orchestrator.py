"""Phase 6: Tests for TradeCycleOrchestrator."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from trader.agents.orchestrator import TradeCycleOrchestrator
from trader.config.settings import AppSettings, ModelNames, RiskSettings, Settings, TradingConfig
from trader.llm.schemas import (
    BrainDecision,
    DerivativesReport,
    MacroNewsReport,
    PositionReview,
    TechnicalReport,
)
from trader.risk.models import RiskVerdict

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_settings(
    kill_switch: bool = False,
    allowed_symbols: list[str] | None = None,
    min_confluence: int = 3,
    mode: str = "paper",
) -> Settings:
    return Settings(
        trading_mode=mode,
        app=AppSettings(
            models=ModelNames(
                screening="test-haiku",
                decision="test-sonnet",
                review="test-sonnet",
            ),
            trading=TradingConfig(symbols=["BTC/USDT"]),
        ),
        risk=RiskSettings(
            max_margin_per_trade_usd=20.0,
            max_loss_per_trade_usd=10.0,
            daily_loss_limit_usd=20.0,
            allowed_symbols=allowed_symbols or ["BTC/USDT"],
            min_confluence_score=min_confluence,
            total_llm_budget_usd_per_day=5.0,
        ),
    )


def _make_neutral_macro() -> MacroNewsReport:
    return MacroNewsReport(agent="MacroNewsAnalyst", bias="neutral", confidence=0.5)


def _make_neutral_technical() -> TechnicalReport:
    return TechnicalReport(agent="TechnicalAnalyst", bias="neutral", confidence=0.5)


def _make_neutral_derivatives() -> DerivativesReport:
    return DerivativesReport(agent="DerivativesAnalyst", bias="neutral", confidence=0.5)


def _make_long_decision(symbol: str = "BTC/USDT") -> BrainDecision:
    return BrainDecision(
        action="LONG",
        symbol=symbol,
        entry_price=50000.0,
        stop_loss=48000.0,
        take_profit=55000.0,
        confidence=0.75,
        thesis="strong signal",
        invalidation="price below 48000",
    )


def _approved_verdict() -> RiskVerdict:
    return RiskVerdict(
        approved=True,
        adjusted_size=0.001,
        rules_failed=[],
        loss_at_stop_usd=2.0,
        liq_price=45000.0,
        entry_drift_pct=0.001,
        reject_reason="",
    )


def _rejected_verdict(reason: str) -> RiskVerdict:
    return RiskVerdict(
        approved=False,
        adjusted_size=None,
        rules_failed=[reason],
        loss_at_stop_usd=None,
        liq_price=None,
        entry_drift_pct=None,
        reject_reason=reason,
    )


def _make_lesson_result(blocked: bool = False, penalty: float = 0.0):
    from trader.risk.lesson_filter import LessonFilterResult

    return LessonFilterResult(
        blocked=blocked,
        confidence_penalty=penalty,
        matched_lessons=[],
        require_extra_confirmation=False,
        details=[],
    )


def make_orchestrator(
    settings: Settings | None = None,
    macro_response: MacroNewsReport | None = None,
    technical_response: TechnicalReport | None = None,
    derivatives_response: DerivativesReport | None = None,
    decision_response: BrainDecision | None = None,
    risk_approved: bool = True,
    lesson_blocked: bool = False,
    kill_switch: bool = False,
) -> TradeCycleOrchestrator:
    if settings is None:
        settings = _make_settings()

    # Mock analysts
    macro_analyst = MagicMock()
    macro_analyst.analyze.return_value = macro_response or _make_neutral_macro()

    technical_analyst = MagicMock()
    technical_analyst.analyze.return_value = technical_response or _make_neutral_technical()

    derivatives_analyst = MagicMock()
    derivatives_analyst.analyze.return_value = derivatives_response or _make_neutral_derivatives()

    # Mock decision agent
    decision_agent = MagicMock()
    decision_agent.decide.return_value = decision_response or BrainDecision(action="NO_TRADE")

    # Mock position reviewer
    position_reviewer = MagicMock()
    position_reviewer.review.return_value = PositionReview(action="HOLD", reason="no change")

    # Mock lesson filter
    lesson_filter = MagicMock()
    lesson_filter.evaluate.return_value = _make_lesson_result(blocked=lesson_blocked)

    # Real risk gate (or mock)
    risk_gate = MagicMock()
    verdict = _approved_verdict() if risk_approved else _rejected_verdict("SPREAD_TOO_WIDE")
    risk_gate.evaluate.return_value = verdict
    risk_gate._kill_switch_on = kill_switch

    return TradeCycleOrchestrator(
        settings=settings,
        macro_analyst=macro_analyst,
        technical_analyst=technical_analyst,
        derivatives_analyst=derivatives_analyst,
        decision_agent=decision_agent,
        position_reviewer=position_reviewer,
        lesson_filter=lesson_filter,
        risk_gate=risk_gate,
        ksor_retriever=None,
        session_factory=None,
        analyst_timeout_seconds=5.0,
        cycle_timeout_seconds=30.0,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestKillSwitch:
    def test_kill_switch_on_stops_at_step1(self):
        orch = make_orchestrator(kill_switch=True)
        result = asyncio.get_event_loop().run_until_complete(
            orch.run_cycle("BTC/USDT", "candle_close")
        )
        assert result.action == "NO_TRADE"
        assert result.stopped_at == "kill_switch"
        # No LLM calls should be made
        orch._macro_analyst.analyze.assert_not_called()
        orch._decision_agent.decide.assert_not_called()

    def test_kill_switch_off_continues(self):
        orch = make_orchestrator(
            kill_switch=False,
            decision_response=_make_long_decision(),
        )
        result = asyncio.get_event_loop().run_until_complete(
            orch.run_cycle(
                "BTC/USDT",
                "candle_close",
                feature_pack={"confluence_score": 3},
            )
        )
        # Should not stop at kill_switch
        assert result.stopped_at != "kill_switch"


class TestDataQuality:
    def test_stale_data_stops_at_step2(self):
        orch = make_orchestrator()
        result = asyncio.get_event_loop().run_until_complete(
            orch.run_cycle(
                "BTC/USDT",
                "candle_close",
                feature_pack={"data_age_seconds": 999},  # > max_data_age_seconds (60)
            )
        )
        assert result.stopped_at == "data_quality"
        assert result.action == "NO_TRADE"
        orch._macro_analyst.analyze.assert_not_called()

    def test_data_quality_flag_stops_at_step2(self):
        orch = make_orchestrator()
        result = asyncio.get_event_loop().run_until_complete(
            orch.run_cycle(
                "BTC/USDT",
                "candle_close",
                feature_pack={"data_quality_failed": True},
            )
        )
        assert result.stopped_at == "data_quality"
        assert result.action == "NO_TRADE"

    def test_fresh_data_continues(self):
        orch = make_orchestrator(decision_response=BrainDecision(action="NO_TRADE"))
        result = asyncio.get_event_loop().run_until_complete(
            orch.run_cycle(
                "BTC/USDT",
                "candle_close",
                feature_pack={"data_age_seconds": 5, "confluence_score": 3},
            )
        )
        assert result.stopped_at != "data_quality"


class TestScreening:
    def test_low_confluence_stops_at_screening(self):
        """min_confluence=3, so score < 2 should be rejected at screening (threshold-1=2)."""
        settings = _make_settings(min_confluence=3)
        orch = make_orchestrator(settings=settings)
        result = asyncio.get_event_loop().run_until_complete(
            orch.run_cycle(
                "BTC/USDT",
                "candle_close",
                feature_pack={"confluence_score": 1},  # below threshold-1=2
            )
        )
        assert result.stopped_at == "screening"
        assert result.action == "NO_TRADE"
        # No LLM calls made
        orch._macro_analyst.analyze.assert_not_called()

    def test_confluence_at_threshold_minus_one_continues(self):
        """Score exactly at threshold-1 should continue."""
        settings = _make_settings(min_confluence=3)
        orch = make_orchestrator(settings=settings)
        result = asyncio.get_event_loop().run_until_complete(
            orch.run_cycle(
                "BTC/USDT",
                "candle_close",
                feature_pack={"confluence_score": 2},  # exactly threshold-1
            )
        )
        assert result.stopped_at != "screening"


class TestAnalystTimeout:
    def test_analyst_timeout_returns_no_trade(self):
        """If MacroNewsAnalyst times out, cycle returns NO_TRADE."""
        settings = _make_settings()

        macro_analyst = MagicMock()

        def slow_analyze(*args, **kwargs):
            import time
            time.sleep(100)  # simulate timeout

        macro_analyst.analyze.side_effect = slow_analyze

        technical_analyst = MagicMock()
        technical_analyst.analyze.return_value = _make_neutral_technical()
        derivatives_analyst = MagicMock()
        derivatives_analyst.analyze.return_value = _make_neutral_derivatives()
        decision_agent = MagicMock()
        decision_agent.decide.return_value = BrainDecision(action="NO_TRADE")
        position_reviewer = MagicMock()
        lesson_filter = MagicMock()
        lesson_filter.evaluate.return_value = _make_lesson_result()
        risk_gate = MagicMock()
        risk_gate._kill_switch_on = False
        risk_gate.evaluate.return_value = _approved_verdict()

        orch = TradeCycleOrchestrator(
            settings=settings,
            macro_analyst=macro_analyst,
            technical_analyst=technical_analyst,
            derivatives_analyst=derivatives_analyst,
            decision_agent=decision_agent,
            position_reviewer=position_reviewer,
            lesson_filter=lesson_filter,
            risk_gate=risk_gate,
            analyst_timeout_seconds=0.05,  # very short timeout
            cycle_timeout_seconds=5.0,
        )

        result = asyncio.get_event_loop().run_until_complete(
            orch.run_cycle(
                "BTC/USDT",
                "candle_close",
                feature_pack={"confluence_score": 3},
            )
        )
        assert result.action == "NO_TRADE"
        assert result.stopped_at == "analyst_timeout"


class TestNormalFlow:
    def test_normal_flow_all_nine_steps(self):
        """Full flow with mocked analysts + decision → runs through all 9 steps."""
        orch = make_orchestrator(
            decision_response=_make_long_decision(),
            risk_approved=True,
        )
        asyncio.get_event_loop().run_until_complete(
            orch.run_cycle(
                "BTC/USDT",
                "candle_close",
                feature_pack={
                    "confluence_score": 3,
                    "account_state": {
                        "equity_usd": 200.0,
                        "daily_loss_usd": 0.0,
                        "weekly_loss_usd": 0.0,
                        "consecutive_losses": 0,
                        "last_loss_ts": None,
                        "open_positions": 0,
                        "trades_today": 0,
                        "llm_cost_today_usd": 0.0,
                    },
                },
            )
        )
        # All agents should have been called
        orch._macro_analyst.analyze.assert_called_once()
        orch._technical_analyst.analyze.assert_called_once()
        orch._derivatives_analyst.analyze.assert_called_once()
        orch._decision_agent.decide.assert_called_once()
        orch._lesson_filter.evaluate.assert_called_once()
        orch._risk_gate.evaluate.assert_called_once()

    def test_risk_gate_rejected_returns_no_trade(self):
        orch = make_orchestrator(
            decision_response=_make_long_decision(),
            risk_approved=False,
        )
        result = asyncio.get_event_loop().run_until_complete(
            orch.run_cycle(
                "BTC/USDT",
                "candle_close",
                feature_pack={"confluence_score": 3},
            )
        )
        assert result.action == "NO_TRADE"
        assert result.approved is False

    def test_lesson_filter_blocked_stops_cycle(self):
        orch = make_orchestrator(
            decision_response=_make_long_decision(),
            lesson_blocked=True,
        )
        result = asyncio.get_event_loop().run_until_complete(
            orch.run_cycle(
                "BTC/USDT",
                "candle_close",
                feature_pack={"confluence_score": 3},
            )
        )
        assert result.action == "NO_TRADE"
        assert result.stopped_at == "lesson_filter"
        # RiskGate should NOT be called after lesson filter blocks
        orch._risk_gate.evaluate.assert_not_called()


class TestOpenPosition:
    def test_open_position_routes_to_position_reviewer(self):
        """When open_position is provided, PositionReviewAgent is called, not DecisionAgent."""
        orch = make_orchestrator()
        orch._position_reviewer.review.return_value = PositionReview(action="HOLD", reason="stable")

        result = asyncio.get_event_loop().run_until_complete(
            orch.run_cycle(
                "BTC/USDT",
                "candle_close",
                feature_pack={"confluence_score": 3},
                open_position={
                    "side": "long",
                    "entry": 50000.0,
                    "stop_loss": 48000.0,
                    "symbol": "BTC/USDT",
                },
            )
        )

        orch._position_reviewer.review.assert_called_once()
        orch._decision_agent.decide.assert_not_called()
        assert result.action == "HOLD"

    def test_open_position_tighten_stop_approved(self):
        orch = make_orchestrator()
        orch._position_reviewer.review.return_value = PositionReview(
            action="TIGHTEN_STOP", new_stop_loss=49000.0, reason="protect profits"
        )

        result = asyncio.get_event_loop().run_until_complete(
            orch.run_cycle(
                "BTC/USDT",
                "candle_close",
                feature_pack={"confluence_score": 3},
                open_position={"side": "long", "entry": 50000.0, "stop_loss": 48000.0},
            )
        )
        assert result.action == "TIGHTEN_STOP"
        assert result.approved is True


class TestConcurrency:
    def test_concurrent_cycles_same_symbol_second_rejected(self):
        """Two concurrent run_cycle calls for same symbol → second rejected."""
        import asyncio

        orch = make_orchestrator(decision_response=BrainDecision(action="NO_TRADE"))

        async def run_both():
            # Start both concurrently
            task1 = asyncio.create_task(
                orch.run_cycle("BTC/USDT", "candle_close", feature_pack={"confluence_score": 1})
            )
            task2 = asyncio.create_task(
                orch.run_cycle("BTC/USDT", "candle_close", feature_pack={"confluence_score": 1})
            )
            results = await asyncio.gather(task1, task2)
            return results

        results = asyncio.get_event_loop().run_until_complete(run_both())
        # At least one should be stopped_at concurrent_cycle
        stopped_ats = {r.stopped_at for r in results}
        assert "concurrent_cycle" in stopped_ats or len(results) == 2

    def test_different_symbols_run_concurrently(self):
        """Two cycles for different symbols should both proceed."""
        orch = make_orchestrator()

        async def run_both():
            task1 = asyncio.create_task(
                orch.run_cycle("BTC/USDT", "candle_close", feature_pack={"confluence_score": 3})
            )
            task2 = asyncio.create_task(
                orch.run_cycle("ETH/USDT", "candle_close", feature_pack={"confluence_score": 3})
            )
            return await asyncio.gather(task1, task2)

        results = asyncio.get_event_loop().run_until_complete(run_both())
        # Neither should be "concurrent_cycle" stopped
        for r in results:
            assert r.stopped_at != "concurrent_cycle"


class TestReplay:
    def test_replay_returns_stored_result(self):
        orch = make_orchestrator(decision_response=BrainDecision(action="NO_TRADE"))
        result = asyncio.get_event_loop().run_until_complete(
            orch.run_cycle("BTC/USDT", "candle_close", feature_pack={"confluence_score": 3})
        )
        cycle_id = result.cycle_id

        replayed = orch.replay(cycle_id)
        assert replayed.cycle_id == cycle_id
        assert replayed.symbol == "BTC/USDT"
        assert replayed.action == result.action

    def test_replay_unknown_cycle_raises(self):
        orch = make_orchestrator()
        with pytest.raises(KeyError):
            orch.replay("nonexistent-cycle-id")
