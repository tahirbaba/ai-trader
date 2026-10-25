"""Phase 6: Prompt regression tests (mandatory per BUILD_PLAN §6 DoD).

22 scenarios covering every NO_TRADE gate and positive trade paths.
All LLM calls are mocked. Each scenario asserts the expected cycle outcome.
"""

from __future__ import annotations

import asyncio
import datetime
from unittest.mock import MagicMock

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
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(
    mode: str = "paper",
    allowed_symbols: list[str] | None = None,
    min_confluence: int = 3,
    min_confidence: float = 0.7,
    daily_loss: float = 0.0,
    weekly_loss: float = 0.0,
    equity: float = 100.0,
    consecutive_losses: int = 0,
    trades_today: int = 0,
    llm_cost: float = 0.0,
    budget: float = 5.0,
) -> Settings:
    return Settings(
        trading_mode=mode,
        app=AppSettings(
            models=ModelNames(
                screening="test-haiku",
                decision="test-sonnet",
                review="test-sonnet",
            ),
            trading=TradingConfig(),
        ),
        risk=RiskSettings(
            max_margin_per_trade_usd=20.0,
            max_loss_per_trade_usd=10.0,
            daily_loss_limit_usd=20.0,
            allowed_symbols=allowed_symbols or ["BTC/USDT"],
            min_confluence_score=min_confluence,
            min_confidence=min_confidence,
            total_llm_budget_usd_per_day=budget,
        ),
    )


def _lesson_result(blocked: bool = False, penalty: float = 0.0):
    from trader.risk.lesson_filter import LessonFilterResult

    return LessonFilterResult(
        blocked=blocked,
        confidence_penalty=penalty,
        matched_lessons=[],
        require_extra_confirmation=False,
        details=[],
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


def build_orchestrator(
    settings: Settings,
    decision: BrainDecision | None = None,
    risk_verdict: RiskVerdict | None = None,
    lesson_blocked: bool = False,
    lesson_penalty: float = 0.0,
    kill_switch: bool = False,
    position_review: PositionReview | None = None,
    budget_exhausted: bool = False,
) -> TradeCycleOrchestrator:
    macro_analyst = MagicMock()
    macro_analyst.analyze.return_value = MacroNewsReport(
        agent="MacroNewsAnalyst", bias="neutral", confidence=0.5
    )

    technical_analyst = MagicMock()
    technical_analyst.analyze.return_value = TechnicalReport(
        agent="TechnicalAnalyst", bias="neutral", confidence=0.5
    )

    derivatives_analyst = MagicMock()
    derivatives_analyst.analyze.return_value = DerivativesReport(
        agent="DerivativesAnalyst", bias="neutral", confidence=0.5
    )

    decision_agent = MagicMock()
    decision_agent.decide.return_value = decision or BrainDecision(action="NO_TRADE")

    pos_reviewer = MagicMock()
    pos_reviewer.review.return_value = position_review or PositionReview(action="HOLD")

    lesson_filter = MagicMock()
    lesson_filter.evaluate.return_value = _lesson_result(
        blocked=lesson_blocked, penalty=lesson_penalty
    )

    risk_gate = MagicMock()
    risk_gate._kill_switch_on = kill_switch
    risk_gate.evaluate.return_value = risk_verdict or _approved_verdict()

    # Budget exhaustion: inject via session_factory that returns pre-loaded cost
    session_factory = None
    if budget_exhausted:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session as SASession

        from trader.dsor.base import Base
        from trader.dsor.models import LlmCall

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with SASession(engine) as s:
            s.add(
                LlmCall(
                    ts=datetime.datetime.utcnow(),
                    purpose="prev",
                    model="x",
                    input_tokens=1,
                    output_tokens=1,
                    cost_usd=999.0,  # exceeds any budget
                    cache_hit=False,
                )
            )
            s.commit()

        def session_factory():
            return SASession(engine)

    return TradeCycleOrchestrator(
        settings=settings,
        macro_analyst=macro_analyst,
        technical_analyst=technical_analyst,
        derivatives_analyst=derivatives_analyst,
        decision_agent=decision_agent,
        position_reviewer=pos_reviewer,
        lesson_filter=lesson_filter,
        risk_gate=risk_gate,
        session_factory=session_factory,
        analyst_timeout_seconds=5.0,
        cycle_timeout_seconds=30.0,
    )


def run(orch: TradeCycleOrchestrator, fp: dict | None = None, open_pos: dict | None = None):
    fp = fp or {"confluence_score": 3}
    return asyncio.get_event_loop().run_until_complete(
        orch.run_cycle("BTC/USDT", "candle_close", feature_pack=fp, open_position=open_pos)
    )


# ---------------------------------------------------------------------------
# 22 regression scenarios
# ---------------------------------------------------------------------------


class TestPromptRegression:
    # 1. FOMC blackout → NO_TRADE
    def test_01_fomc_blackout(self):
        settings = _make_settings()
        orch = build_orchestrator(
            settings,
            risk_verdict=_rejected_verdict("MACRO_BLACKOUT"),
            decision=BrainDecision(
                action="LONG",
                symbol="BTC/USDT",
                entry_price=50000,
                stop_loss=48000,
                take_profit=55000,
                confidence=0.75,
            ),
        )
        result = run(orch, {"confluence_score": 3, "is_macro_blackout": True})
        assert result.action == "NO_TRADE"

    # 2. Stale data → NO_TRADE
    def test_02_stale_data(self):
        settings = _make_settings()
        orch = build_orchestrator(settings)
        result = run(orch, {"confluence_score": 3, "data_age_seconds": 9999})
        assert result.action == "NO_TRADE"
        assert result.stopped_at == "data_quality"

    # 3. Kill switch on → NO_TRADE
    def test_03_kill_switch_on(self):
        settings = _make_settings()
        orch = build_orchestrator(settings, kill_switch=True)
        result = run(orch)
        assert result.action == "NO_TRADE"
        assert result.stopped_at == "kill_switch"

    # 4. Spread too wide → NO_TRADE (RiskGate rejects)
    def test_04_spread_too_wide(self):
        settings = _make_settings()
        orch = build_orchestrator(
            settings,
            decision=BrainDecision(
                action="LONG",
                symbol="BTC/USDT",
                entry_price=50000,
                stop_loss=48000,
                take_profit=55000,
                confidence=0.75,
            ),
            risk_verdict=_rejected_verdict("SPREAD_TOO_WIDE"),
        )
        result = run(orch, {"confluence_score": 3, "spread_pct": 0.5})
        assert result.action == "NO_TRADE"
        assert result.approved is False

    # 5. Equity below floor → NO_TRADE
    def test_05_equity_below_floor(self):
        settings = _make_settings()
        orch = build_orchestrator(
            settings,
            decision=BrainDecision(
                action="LONG",
                symbol="BTC/USDT",
                entry_price=50000,
                stop_loss=48000,
                take_profit=55000,
                confidence=0.75,
            ),
            risk_verdict=_rejected_verdict("EQUITY_FLOOR"),
        )
        result = run(
            orch,
            {
                "confluence_score": 3,
                "account_state": {
                    "equity_usd": 1.0,
                    "daily_loss_usd": 0,
                    "weekly_loss_usd": 0,
                    "consecutive_losses": 0,
                    "last_loss_ts": None,
                    "open_positions": 0,
                    "trades_today": 0,
                    "llm_cost_today_usd": 0,
                },
            },
        )
        assert result.action == "NO_TRADE"

    # 6. Daily loss limit hit → NO_TRADE
    def test_06_daily_loss_limit(self):
        settings = _make_settings()
        orch = build_orchestrator(
            settings,
            decision=BrainDecision(
                action="LONG",
                symbol="BTC/USDT",
                entry_price=50000,
                stop_loss=48000,
                take_profit=55000,
                confidence=0.75,
            ),
            risk_verdict=_rejected_verdict("DAILY_LOSS_LIMIT"),
        )
        result = run(orch, {"confluence_score": 3})
        assert result.action == "NO_TRADE"

    # 7. Confidence < min after lesson penalty → NO_TRADE
    def test_07_low_confidence_after_penalty(self):
        settings = _make_settings(min_confidence=0.7)
        orch = build_orchestrator(
            settings,
            decision=BrainDecision(
                action="LONG",
                symbol="BTC/USDT",
                entry_price=50000,
                stop_loss=48000,
                take_profit=55000,
                confidence=0.75,
            ),
            lesson_penalty=0.2,  # 0.75 - 0.20 = 0.55 < 0.70
            risk_verdict=_rejected_verdict("LOW_CONFIDENCE"),
        )
        result = run(orch, {"confluence_score": 3})
        assert result.action == "NO_TRADE"

    # 8. Confluence < min_confluence_score → NO_TRADE (screening stops)
    def test_08_low_confluence_screening(self):
        settings = _make_settings(min_confluence=3)
        orch = build_orchestrator(settings)
        result = run(orch, {"confluence_score": 1})
        assert result.action == "NO_TRADE"
        assert result.stopped_at == "screening"
        orch._macro_analyst.analyze.assert_not_called()

    # 9. LLM returns garbage → NO_TRADE
    def test_09_llm_garbage_response(self):
        settings = _make_settings()
        orch = build_orchestrator(
            settings,
            decision=BrainDecision(action="NO_TRADE", thesis="parse_error: ..."),
        )
        result = run(orch, {"confluence_score": 3})
        assert result.action == "NO_TRADE"

    # 10. Valid LONG signal, all checks pass → approved=True
    def test_10_valid_long_approved(self):
        settings = _make_settings()
        orch = build_orchestrator(
            settings,
            decision=BrainDecision(
                action="LONG",
                symbol="BTC/USDT",
                entry_price=50000.0,
                stop_loss=48000.0,
                take_profit=55000.0,
                confidence=0.78,
            ),
            risk_verdict=_approved_verdict(),
        )
        result = run(orch, {"confluence_score": 3})
        assert result.action == "LONG"
        assert result.approved is True

    # 11. Valid SHORT signal, all checks pass → approved=True
    def test_11_valid_short_approved(self):
        settings = _make_settings()
        orch = build_orchestrator(
            settings,
            decision=BrainDecision(
                action="SHORT",
                symbol="BTC/USDT",
                entry_price=50000.0,
                stop_loss=52000.0,
                take_profit=45000.0,
                confidence=0.74,
            ),
            risk_verdict=_approved_verdict(),
        )
        result = run(orch, {"confluence_score": 4})
        assert result.action == "SHORT"
        assert result.approved is True

    # 12. LessonFilter blocks → NO_TRADE
    def test_12_lesson_filter_blocks(self):
        settings = _make_settings()
        orch = build_orchestrator(
            settings,
            decision=BrainDecision(
                action="LONG",
                symbol="BTC/USDT",
                entry_price=50000,
                stop_loss=48000,
                take_profit=55000,
                confidence=0.75,
            ),
            lesson_blocked=True,
        )
        result = run(orch, {"confluence_score": 3})
        assert result.action == "NO_TRADE"
        assert result.stopped_at == "lesson_filter"

    # 13. Symbol not in allowed_symbols → NO_TRADE (RiskGate)
    def test_13_symbol_not_allowed(self):
        settings = _make_settings(allowed_symbols=["ETH/USDT"])  # BTC not allowed
        orch = build_orchestrator(
            settings,
            decision=BrainDecision(
                action="LONG",
                symbol="BTC/USDT",
                entry_price=50000,
                stop_loss=48000,
                take_profit=55000,
                confidence=0.75,
            ),
            risk_verdict=_rejected_verdict("SYMBOL_NOT_ALLOWED"),
        )
        result = run(orch, {"confluence_score": 3})
        assert result.action == "NO_TRADE"

    # 14. Stop loss on wrong side → NO_TRADE (RiskGate)
    def test_14_stop_loss_wrong_side(self):
        settings = _make_settings()
        orch = build_orchestrator(
            settings,
            decision=BrainDecision(
                action="LONG",
                symbol="BTC/USDT",
                entry_price=50000,
                stop_loss=52000,
                take_profit=55000,
                confidence=0.75,
            ),
            risk_verdict=_rejected_verdict("MISSING_STOP_LOSS"),
        )
        result = run(orch, {"confluence_score": 3})
        assert result.action == "NO_TRADE"

    # 15. Open position → PositionReviewAgent called
    def test_15_open_position_routes_to_reviewer(self):
        settings = _make_settings()
        orch = build_orchestrator(
            settings,
            position_review=PositionReview(action="HOLD", reason="stable"),
        )
        result = run(
            orch,
            fp={"confluence_score": 3},
            open_pos={"side": "long", "entry": 50000, "stop_loss": 48000, "symbol": "BTC/USDT"},
        )
        orch._position_reviewer.review.assert_called_once()
        orch._decision_agent.decide.assert_not_called()
        assert result.action == "HOLD"

    # 16. Entry drift too large → NO_TRADE (RiskGate)
    def test_16_entry_drift_too_large(self):
        settings = _make_settings()
        orch = build_orchestrator(
            settings,
            decision=BrainDecision(
                action="LONG",
                symbol="BTC/USDT",
                entry_price=50000,
                stop_loss=48000,
                take_profit=55000,
                confidence=0.75,
            ),
            risk_verdict=_rejected_verdict("ENTRY_DRIFT"),
        )
        result = run(orch, {"confluence_score": 3, "snapshot_price": 50000, "live_price": 60000})
        assert result.action == "NO_TRADE"

    # 17. Consecutive losses = max → NO_TRADE
    def test_17_consecutive_losses_max(self):
        settings = _make_settings()
        orch = build_orchestrator(
            settings,
            decision=BrainDecision(
                action="LONG",
                symbol="BTC/USDT",
                entry_price=50000,
                stop_loss=48000,
                take_profit=55000,
                confidence=0.75,
            ),
            risk_verdict=_rejected_verdict("CONSECUTIVE_LOSSES"),
        )
        result = run(orch, {"confluence_score": 3})
        assert result.action == "NO_TRADE"

    # 18. Weekly loss limit hit → NO_TRADE
    def test_18_weekly_loss_limit(self):
        settings = _make_settings()
        orch = build_orchestrator(
            settings,
            decision=BrainDecision(
                action="LONG",
                symbol="BTC/USDT",
                entry_price=50000,
                stop_loss=48000,
                take_profit=55000,
                confidence=0.75,
            ),
            risk_verdict=_rejected_verdict("WEEKLY_LOSS_LIMIT"),
        )
        result = run(orch, {"confluence_score": 3})
        assert result.action == "NO_TRADE"

    # 19. LLM budget exhausted → NO_TRADE
    def test_19_llm_budget_exhausted(self):
        settings = _make_settings(budget=0.01)
        orch = build_orchestrator(settings, budget_exhausted=True)
        result = run(orch, {"confluence_score": 3})
        assert result.action == "NO_TRADE"
        assert result.stopped_at == "llm_budget"

    # 20. Analyst timeout → NO_TRADE
    def test_20_analyst_timeout(self):
        settings = _make_settings()

        macro_analyst = MagicMock()

        def slow_analyze(*args, **kwargs):
            import time

            time.sleep(100)

        macro_analyst.analyze.side_effect = slow_analyze
        technical_analyst = MagicMock()
        technical_analyst.analyze.return_value = TechnicalReport(
            agent="TechnicalAnalyst", bias="neutral", confidence=0.5
        )
        derivatives_analyst = MagicMock()
        derivatives_analyst.analyze.return_value = DerivativesReport(
            agent="DerivativesAnalyst", bias="neutral", confidence=0.5
        )
        decision_agent = MagicMock()
        decision_agent.decide.return_value = BrainDecision(action="NO_TRADE")
        lesson_filter = MagicMock()
        lesson_filter.evaluate.return_value = _lesson_result()
        risk_gate = MagicMock()
        risk_gate._kill_switch_on = False
        risk_gate.evaluate.return_value = _approved_verdict()
        pos_reviewer = MagicMock()

        orch = TradeCycleOrchestrator(
            settings=settings,
            macro_analyst=macro_analyst,
            technical_analyst=technical_analyst,
            derivatives_analyst=derivatives_analyst,
            decision_agent=decision_agent,
            position_reviewer=pos_reviewer,
            lesson_filter=lesson_filter,
            risk_gate=risk_gate,
            analyst_timeout_seconds=0.05,
            cycle_timeout_seconds=5.0,
        )
        result = asyncio.get_event_loop().run_until_complete(
            orch.run_cycle("BTC/USDT", "candle_close", feature_pack={"confluence_score": 3})
        )
        assert result.action == "NO_TRADE"
        assert result.stopped_at == "analyst_timeout"

    # 21. Low confluence (below threshold-1) → stopped at screening, no LLM call
    def test_21_low_confluence_below_threshold_minus_one(self):
        settings = _make_settings(min_confluence=3)  # threshold-1 = 2
        orch = build_orchestrator(settings)
        result = run(orch, {"confluence_score": 0})  # 0 < 2
        assert result.action == "NO_TRADE"
        assert result.stopped_at == "screening"
        orch._macro_analyst.analyze.assert_not_called()
        orch._decision_agent.decide.assert_not_called()

    # 22. Position review TIGHTEN_STOP with tighter stop → approved
    def test_22_position_review_tighten_stop(self):
        settings = _make_settings()
        orch = build_orchestrator(
            settings,
            position_review=PositionReview(
                action="TIGHTEN_STOP", new_stop_loss=49000.0, reason="lock profits"
            ),
        )
        result = run(
            orch,
            fp={"confluence_score": 3},
            open_pos={"side": "long", "entry": 50000, "stop_loss": 48000, "symbol": "BTC/USDT"},
        )
        assert result.action == "TIGHTEN_STOP"
        assert result.approved is True
