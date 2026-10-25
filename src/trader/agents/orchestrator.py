"""Phase 6: TradeCycleOrchestrator — pure Python coordinator.

No LLM calls here — only calls agents.
Coordinates: MacroNewsAnalyst, TechnicalAnalyst, DerivativesAnalyst, DecisionAgent,
LessonFilter, RiskGate in a fixed, validated order.

Path: Decision -> LessonFilter -> RiskGate -> Executor
The LLM never talks to the exchange.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from trader.llm.schemas import BrainDecision, MacroNewsReport, PositionReview
from trader.risk.models import RiskVerdict

if TYPE_CHECKING:
    from trader.agents.decision_agent import DecisionAgent
    from trader.agents.derivatives_analyst import DerivativesAnalyst
    from trader.agents.macro_analyst import MacroNewsAnalyst
    from trader.agents.position_reviewer import PositionReviewAgent
    from trader.agents.technical_analyst import TechnicalAnalyst
    from trader.config.settings import Settings
    from trader.ksor.retriever import KsorRetriever
    from trader.risk.gate import RiskGate
    from trader.risk.lesson_filter import LessonFilter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CycleResult
# ---------------------------------------------------------------------------


class CycleResult(BaseModel):
    """Result of a single trade cycle."""

    cycle_id: str
    symbol: str
    trigger: str
    action: str  # final action (from BrainDecision or NO_TRADE)
    approved: bool  # True if RiskGate approved
    stopped_at: str  # which step stopped the cycle
    decision: BrainDecision | None = None
    risk_verdict: RiskVerdict | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _no_trade_result(
    cycle_id: str,
    symbol: str,
    trigger: str,
    stopped_at: str,
    error: str | None = None,
) -> CycleResult:
    return CycleResult(
        cycle_id=cycle_id,
        symbol=symbol,
        trigger=trigger,
        action="NO_TRADE",
        approved=False,
        stopped_at=stopped_at,
        error=error,
    )


def _neutral_macro_report() -> MacroNewsReport:
    return MacroNewsReport(
        agent="MacroNewsAnalyst",
        bias="neutral",
        confidence=0.0,
        data_missing=["macro_analyst_failed"],
    )


# ---------------------------------------------------------------------------
# TradeCycleOrchestrator
# ---------------------------------------------------------------------------


class TradeCycleOrchestrator:
    """Pure Python coordinator. No LLM calls here — only calls agents.

    run_cycle(symbol, trigger) fixed order:
    1. Check kill switch + mode + LLM budget → stop if blocked (no LLM call)
    2. Build/load FeaturePack + check data quality → stale/bad = NO_TRADE, no LLM
    3. Screening trigger: confluence_score >= (min_confluence_score - 1) → else stop
    4. Run MacroNewsAnalyst, TechnicalAnalyst, DerivativesAnalyst IN PARALLEL (asyncio.gather)
    5. Retrieve KSOR lessons
    6. Call DecisionAgent
    7. Apply LessonFilter
    8. Apply RiskGate
    9. Store everything under cycle_id

    If open position exists: run PositionReviewAgent instead (step 4-8 replaced)
    Concurrency: one cycle per symbol at a time (asyncio.Lock per symbol)
    """

    def __init__(
        self,
        settings: Settings,
        macro_analyst: MacroNewsAnalyst,
        technical_analyst: TechnicalAnalyst,
        derivatives_analyst: DerivativesAnalyst,
        decision_agent: DecisionAgent,
        position_reviewer: PositionReviewAgent,
        lesson_filter: LessonFilter,
        risk_gate: RiskGate,
        ksor_retriever: KsorRetriever | None = None,
        session_factory: Any = None,
        analyst_timeout_seconds: float = 20.0,
        cycle_timeout_seconds: float = 60.0,
    ) -> None:
        self._settings = settings
        self._macro_analyst = macro_analyst
        self._technical_analyst = technical_analyst
        self._derivatives_analyst = derivatives_analyst
        self._decision_agent = decision_agent
        self._position_reviewer = position_reviewer
        self._lesson_filter = lesson_filter
        self._risk_gate = risk_gate
        self._ksor_retriever = ksor_retriever
        self._session_factory = session_factory
        self._analyst_timeout = analyst_timeout_seconds
        self._cycle_timeout = cycle_timeout_seconds

        # Per-symbol locks for concurrency safety
        self._symbol_locks: dict[str, asyncio.Lock] = {}
        # Cycle store for replay: cycle_id → (inputs_dict, result)
        self._cycle_store: dict[str, dict[str, Any]] = {}

    def _get_symbol_lock(self, symbol: str) -> asyncio.Lock:
        if symbol not in self._symbol_locks:
            self._symbol_locks[symbol] = asyncio.Lock()
        return self._symbol_locks[symbol]

    async def run_cycle(
        self,
        symbol: str,
        trigger: str,
        feature_pack: dict[str, Any] | None = None,
        open_position: dict[str, Any] | None = None,
    ) -> CycleResult:
        """Run a single trade evaluation cycle for a symbol.

        Parameters
        ----------
        symbol:         Trading symbol (e.g. "BTC/USDT").
        trigger:        What triggered this cycle (e.g. "candle_close").
        feature_pack:   Pre-built feature pack dict (for testing / replay).
        open_position:  If set, routes to PositionReviewAgent instead of full cycle.
        """
        lock = self._get_symbol_lock(symbol)

        # Try to acquire lock without waiting (non-blocking)
        if lock.locked():
            cycle_id = str(uuid.uuid4())
            logger.warning(
                "TradeCycleOrchestrator: cycle for %s already running, rejecting", symbol
            )
            return _no_trade_result(
                cycle_id=cycle_id,
                symbol=symbol,
                trigger=trigger,
                stopped_at="concurrent_cycle",
                error="symbol_cycle_already_running",
            )

        async with lock:
            cycle_id = str(uuid.uuid4())
            try:
                result = await asyncio.wait_for(
                    self._run_cycle_inner(
                        cycle_id=cycle_id,
                        symbol=symbol,
                        trigger=trigger,
                        feature_pack=feature_pack,
                        open_position=open_position,
                    ),
                    timeout=self._cycle_timeout,
                )
            except TimeoutError:
                logger.error(
                    "TradeCycleOrchestrator: cycle %s timed out after %.0fs",
                    cycle_id,
                    self._cycle_timeout,
                )
                result = _no_trade_result(
                    cycle_id=cycle_id,
                    symbol=symbol,
                    trigger=trigger,
                    stopped_at="cycle_timeout",
                    error=f"cycle timed out after {self._cycle_timeout}s",
                )

            return result

    async def _run_cycle_inner(
        self,
        cycle_id: str,
        symbol: str,
        trigger: str,
        feature_pack: dict[str, Any] | None,
        open_position: dict[str, Any] | None,
    ) -> CycleResult:
        """Inner cycle logic — called within the symbol lock and overall timeout."""

        # ------------------------------------------------------------------ #
        # Step 1: Kill switch + mode + LLM budget checks
        # ------------------------------------------------------------------ #
        kill_switch_on = self._check_kill_switch()
        if kill_switch_on:
            logger.info("TradeCycleOrchestrator: kill switch ON, stopping cycle %s", cycle_id)
            return _no_trade_result(cycle_id, symbol, trigger, stopped_at="kill_switch")

        if self._settings.trading_mode == "live":
            logger.warning("TradeCycleOrchestrator: live mode not approved, stopping")
            return _no_trade_result(cycle_id, symbol, trigger, stopped_at="mode_check")

        # Budget check — query before any LLM call
        if self._is_budget_exhausted():
            return _no_trade_result(cycle_id, symbol, trigger, stopped_at="llm_budget")

        # ------------------------------------------------------------------ #
        # Step 2: FeaturePack data quality check
        # ------------------------------------------------------------------ #
        if feature_pack is None:
            feature_pack = {}

        # Check data age if present
        data_age = float(feature_pack.get("data_age_seconds", 0))
        if data_age > self._settings.risk.max_data_age_seconds:
            logger.info(
                "TradeCycleOrchestrator: stale data (age=%.1fs > max=%ds) for %s",
                data_age,
                self._settings.risk.max_data_age_seconds,
                symbol,
            )
            return _no_trade_result(cycle_id, symbol, trigger, stopped_at="data_quality")

        # Check required data flag if present
        if feature_pack.get("data_quality_failed", False):
            return _no_trade_result(cycle_id, symbol, trigger, stopped_at="data_quality")

        # Block on macro calendar staleness — fail-closed when no high-impact events
        # cover the next 14 days (calendar source may be misconfigured or expired).
        dq_flags: list[str] = list(feature_pack.get("data_quality_flags", []))
        if "macro_calendar_stale" in dq_flags:
            logger.warning(
                "TradeCycleOrchestrator: macro_calendar_stale for %s — blocking new entry",
                symbol,
            )
            return _no_trade_result(cycle_id, symbol, trigger, stopped_at="data_quality")

        # ------------------------------------------------------------------ #
        # Open position branch → PositionReviewAgent
        # ------------------------------------------------------------------ #
        if open_position is not None:
            return await self._run_position_review(
                cycle_id=cycle_id,
                symbol=symbol,
                trigger=trigger,
                position=open_position,
                feature_pack=feature_pack,
            )

        # ------------------------------------------------------------------ #
        # Step 3: Screening — confluence check
        # ------------------------------------------------------------------ #
        confluence_score = int(feature_pack.get("confluence_score", 0))
        min_confluence = self._settings.risk.min_confluence_score
        # Allow cycles to proceed if score >= (min_confluence - 1)
        if confluence_score < (min_confluence - 1):
            logger.info(
                "TradeCycleOrchestrator: low confluence=%d (threshold=%d) for %s, stopping",
                confluence_score,
                min_confluence,
                symbol,
            )
            return _no_trade_result(cycle_id, symbol, trigger, stopped_at="screening")

        # ------------------------------------------------------------------ #
        # Step 4: Run all three analysts in parallel
        # ------------------------------------------------------------------ #
        news_items: list[dict[str, Any]] = feature_pack.get("news_items", [])
        macro_events: list[dict[str, Any]] = feature_pack.get("macro_events", [])
        cross_asset: dict[str, Any] = feature_pack.get("cross_asset", {})
        is_macro_blackout = bool(feature_pack.get("is_macro_blackout", False))
        derivatives: dict[str, Any] = feature_pack.get("derivatives", {})
        orderflow: dict[str, Any] = feature_pack.get("orderflow", {})

        macro_report, technical_report, derivatives_report = await self._run_analysts_parallel(
            symbol=symbol,
            news_items=news_items,
            macro_events=macro_events,
            cross_asset=cross_asset,
            is_macro_blackout=is_macro_blackout,
            feature_pack_dict=feature_pack,
            derivatives=derivatives,
            orderflow=orderflow,
        )

        # If any analyst returned a timeout indicator, return NO_TRADE
        if "analyst_timeout" in macro_report.data_missing:
            return _no_trade_result(cycle_id, symbol, trigger, stopped_at="analyst_timeout")

        # ------------------------------------------------------------------ #
        # Step 5: Retrieve KSOR lessons
        # ------------------------------------------------------------------ #
        ksor_lessons = self._retrieve_ksor_lessons(symbol=symbol, feature_pack=feature_pack)

        # ------------------------------------------------------------------ #
        # Step 6: DecisionAgent
        # ------------------------------------------------------------------ #
        account_state = feature_pack.get("account_state", {})
        brain_decision = self._decision_agent.decide(
            macro_report=macro_report,
            technical_report=technical_report,
            derivatives_report=derivatives_report,
            feature_pack=feature_pack,
            confluence_score=confluence_score,
            ksor_lessons=ksor_lessons,
            account_state=account_state,
            symbol=symbol,
            cycle_id=cycle_id,
        )

        # ------------------------------------------------------------------ #
        # Step 7: LessonFilter
        # ------------------------------------------------------------------ #
        decision_dict = {
            "action": brain_decision.action,
            "side": brain_decision.action.lower()
            if brain_decision.action in ("LONG", "SHORT")
            else "none",
            "symbol": symbol,
            "confidence": brain_decision.confidence,
        }
        lesson_result = self._lesson_filter.evaluate(
            decision_dict=decision_dict,
            feature_dict=feature_pack,
        )

        if lesson_result.blocked:
            blocked_decision = BrainDecision(
                action="NO_TRADE",
                symbol=symbol,
                thesis="lesson_filter_blocked",
            )
            return CycleResult(
                cycle_id=cycle_id,
                symbol=symbol,
                trigger=trigger,
                action="NO_TRADE",
                approved=False,
                stopped_at="lesson_filter",
                decision=blocked_decision,
            )

        # ------------------------------------------------------------------ #
        # Step 8: RiskGate
        # ------------------------------------------------------------------ #
        risk_verdict = self._evaluate_risk_gate(
            brain_decision=brain_decision,
            lesson_result=lesson_result,
            feature_pack=feature_pack,
            symbol=symbol,
        )

        final_action = brain_decision.action if risk_verdict.approved else "NO_TRADE"
        stopped_at = (
            "completed" if risk_verdict.approved else f"risk_gate:{risk_verdict.reject_reason}"
        )

        result = CycleResult(
            cycle_id=cycle_id,
            symbol=symbol,
            trigger=trigger,
            action=final_action,
            approved=risk_verdict.approved,
            stopped_at=stopped_at,
            decision=brain_decision,
            risk_verdict=risk_verdict,
        )

        # ------------------------------------------------------------------ #
        # Step 9: Store cycle result
        # ------------------------------------------------------------------ #
        self._store_cycle(cycle_id=cycle_id, inputs=feature_pack, result=result)

        return result

    async def _run_analysts_parallel(
        self,
        symbol: str,
        news_items: list[dict[str, Any]],
        macro_events: list[dict[str, Any]],
        cross_asset: dict[str, Any],
        is_macro_blackout: bool,
        feature_pack_dict: dict[str, Any],
        derivatives: dict[str, Any],
        orderflow: dict[str, Any],
    ) -> tuple[MacroNewsReport, Any, Any]:
        """Run all three analysts in parallel using asyncio.gather with timeouts."""
        from trader.llm.schemas import DerivativesReport, TechnicalReport  # noqa: PLC0415

        async def run_macro() -> MacroNewsReport:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        self._macro_analyst.analyze,
                        news_items,
                        macro_events,
                        cross_asset,
                        is_macro_blackout,
                        symbol,
                    ),
                    timeout=self._analyst_timeout,
                )
            except TimeoutError:
                logger.warning("TradeCycleOrchestrator: MacroNewsAnalyst timed out")
                r = _neutral_macro_report()
                r.data_missing = ["analyst_timeout"]
                return r
            except Exception as exc:
                logger.warning("TradeCycleOrchestrator: MacroNewsAnalyst error: %s", exc)
                return _neutral_macro_report()

        async def run_technical() -> TechnicalReport:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        self._technical_analyst.analyze,
                        feature_pack_dict,
                        symbol,
                    ),
                    timeout=self._analyst_timeout,
                )
            except TimeoutError:
                logger.warning("TradeCycleOrchestrator: TechnicalAnalyst timed out")
                return TechnicalReport(
                    agent="TechnicalAnalyst",
                    bias="neutral",
                    confidence=0.0,
                    data_missing=["analyst_timeout"],
                )
            except Exception as exc:
                logger.warning("TradeCycleOrchestrator: TechnicalAnalyst error: %s", exc)
                return TechnicalReport(
                    agent="TechnicalAnalyst",
                    bias="neutral",
                    confidence=0.0,
                    data_missing=["analyst_error"],
                )

        async def run_derivatives() -> DerivativesReport:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        self._derivatives_analyst.analyze,
                        derivatives,
                        orderflow,
                        symbol,
                    ),
                    timeout=self._analyst_timeout,
                )
            except TimeoutError:
                logger.warning("TradeCycleOrchestrator: DerivativesAnalyst timed out")
                return DerivativesReport(
                    agent="DerivativesAnalyst",
                    bias="neutral",
                    confidence=0.0,
                    data_missing=["analyst_timeout"],
                )
            except Exception as exc:
                logger.warning("TradeCycleOrchestrator: DerivativesAnalyst error: %s", exc)
                return DerivativesReport(
                    agent="DerivativesAnalyst",
                    bias="neutral",
                    confidence=0.0,
                    data_missing=["analyst_error"],
                )

        macro, technical, derivatives_result = await asyncio.gather(
            run_macro(), run_technical(), run_derivatives()
        )
        return macro, technical, derivatives_result

    async def _run_position_review(
        self,
        cycle_id: str,
        symbol: str,
        trigger: str,
        position: dict[str, Any],
        feature_pack: dict[str, Any],
    ) -> CycleResult:
        """Run position review cycle."""
        try:
            review: PositionReview = await asyncio.wait_for(
                asyncio.to_thread(
                    self._position_reviewer.review,
                    position,
                    feature_pack,
                    symbol,
                ),
                timeout=self._analyst_timeout,
            )
        except TimeoutError:
            return _no_trade_result(
                cycle_id,
                symbol,
                trigger,
                stopped_at="analyst_timeout",
                error="position_reviewer_timeout",
            )
        except Exception as exc:
            return _no_trade_result(
                cycle_id, symbol, trigger, stopped_at="position_reviewer_error", error=str(exc)
            )

        # Position review does not go through RiskGate
        return CycleResult(
            cycle_id=cycle_id,
            symbol=symbol,
            trigger=trigger,
            action=review.action,
            approved=True,  # position reviews don't need risk gate approval
            stopped_at="completed",
            decision=None,
        )

    def _evaluate_risk_gate(
        self,
        brain_decision: BrainDecision,
        lesson_result: Any,
        feature_pack: dict[str, Any],
        symbol: str,
    ) -> RiskVerdict:
        """Evaluate risk gate. Returns a RiskVerdict."""
        from trader.risk.models import (  # noqa: PLC0415
            AccountState,
            DecisionInput,
            LessonCheckInput,
            MarketState,
        )

        # Build DecisionInput from BrainDecision.
        # For non-trading actions (NO_TRADE/HOLD/CLOSE) entry_price may be 0;
        # use a sentinel of 1.0 so Pydantic's gt=0 passes — the gate's action_filter
        # (check 3) will reject these before reaching any price-dependent check.
        _entry = brain_decision.entry_price if brain_decision.entry_price > 0 else 1.0
        _stop = brain_decision.stop_loss if brain_decision.stop_loss > 0 else 0.0
        _tp = brain_decision.take_profit if brain_decision.take_profit > 0 else 0.0
        decision_input = DecisionInput(
            action=brain_decision.action,
            symbol=symbol,
            side=brain_decision.action.lower()
            if brain_decision.action in ("LONG", "SHORT")
            else "none",
            entry_price=_entry,
            stop_loss=_stop,
            take_profit=_tp,
            confidence=brain_decision.confidence,
            confluence_score=int(feature_pack.get("confluence_score", 0)),
        )

        # Build AccountState from feature_pack
        account_raw = feature_pack.get("account_state", {})
        account_state = AccountState(
            equity_usd=float(account_raw.get("equity_usd", 100.0)),
            daily_loss_usd=float(account_raw.get("daily_loss_usd", 0.0)),
            weekly_loss_usd=float(account_raw.get("weekly_loss_usd", 0.0)),
            consecutive_losses=int(account_raw.get("consecutive_losses", 0)),
            last_loss_ts=account_raw.get("last_loss_ts"),
            open_positions=int(account_raw.get("open_positions", 0)),
            trades_today=int(account_raw.get("trades_today", 0)),
            llm_cost_today_usd=float(account_raw.get("llm_cost_today_usd", 0.0)),
        )

        # Build MarketState from feature_pack
        _live_price = float(feature_pack.get("live_price", _entry))
        _snap_price = float(feature_pack.get("snapshot_price", _entry))
        market_state = MarketState(
            symbol=symbol,
            live_price=_live_price if _live_price > 0 else _entry,
            snapshot_price=_snap_price if _snap_price > 0 else _entry,
            atr_14=feature_pack.get("atr_14"),
            spread_pct=feature_pack.get("spread_pct"),
            funding_rate=feature_pack.get("funding_rate"),
            next_funding_ts=feature_pack.get("next_funding_ts"),
            data_age_seconds=float(feature_pack.get("data_age_seconds", 0)),
            clock_drift_ms=float(feature_pack.get("clock_drift_ms", 0)),
            is_macro_blackout=bool(feature_pack.get("is_macro_blackout", False)),
            macro_blackout_name=str(feature_pack.get("macro_blackout_name", "")),
            is_exchange_blackout=bool(feature_pack.get("is_exchange_blackout", False)),
            exchange_blackout_reason=str(feature_pack.get("exchange_blackout_reason", "")),
        )

        lesson_check = LessonCheckInput(
            blocked=lesson_result.blocked,
            confidence_penalty=lesson_result.confidence_penalty,
        )

        return self._risk_gate.evaluate(
            decision=decision_input,
            lesson_check=lesson_check,
            account=account_state,
            market=market_state,
        )

    def _check_kill_switch(self) -> bool:
        """Check kill switch from system state or gate's internal state."""
        # Delegate to the risk gate's kill switch state
        return getattr(self._risk_gate, "_kill_switch_on", False)

    def _is_budget_exhausted(self) -> bool:
        """Check if LLM budget is exhausted."""
        if self._session_factory is None:
            return False
        try:
            from trader.dsor.repositories import LlmCallRepository  # noqa: PLC0415

            session = self._session_factory()
            try:
                today_start = datetime.datetime.now(datetime.UTC).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                repo = LlmCallRepository(session)
                spent = repo.total_cost_since(today_start)
                return spent >= self._settings.risk.total_llm_budget_usd_per_day
            finally:
                session.close()
        except Exception as exc:
            logger.warning("TradeCycleOrchestrator: budget check error: %s", exc)
            return False

    def _retrieve_ksor_lessons(
        self, symbol: str, feature_pack: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Retrieve relevant KSOR lessons as plain dicts."""
        if self._ksor_retriever is None:
            return []
        try:
            entries = self._ksor_retriever.retrieve(symbol=symbol, k=10)
            return [
                {
                    "id": e.id,
                    "effect": e.effect,
                    "status": e.status,
                    "tags": e.tags,
                }
                for e in entries
            ]
        except Exception as exc:
            logger.warning("TradeCycleOrchestrator: KSOR retrieval error: %s", exc)
            return []

    def _store_cycle(self, cycle_id: str, inputs: dict[str, Any], result: CycleResult) -> None:
        """Store cycle inputs and result for replay."""
        self._cycle_store[cycle_id] = {
            "inputs": inputs,
            "result": result.model_dump(mode="json"),
        }
        logger.debug("TradeCycleOrchestrator: stored cycle %s", cycle_id)

        # Optionally persist to DSOR decisions table
        if self._session_factory is not None:
            self._persist_cycle_to_dsor(cycle_id=cycle_id, result=result)

    def _persist_cycle_to_dsor(self, cycle_id: str, result: CycleResult) -> None:
        """Persist cycle result to DSOR decisions table."""
        try:
            from trader.dsor.models import Decision  # noqa: PLC0415

            session = self._session_factory()
            try:
                decision = result.decision
                row = Decision(
                    ts=datetime.datetime.now(datetime.UTC),
                    action=result.action,
                    symbol=result.symbol,
                    side=(
                        decision.action.lower()
                        if decision and decision.action in ("LONG", "SHORT")
                        else None
                    ),
                    entry=decision.entry_price if decision else None,
                    stop_loss=decision.stop_loss if decision else None,
                    take_profit=decision.take_profit if decision else None,
                    confidence=decision.confidence if decision else 0.0,
                    confluence_score=int(
                        self._cycle_store.get(cycle_id, {})
                        .get("inputs", {})
                        .get("confluence_score", 0)
                    ),
                    thesis=decision.thesis if decision else result.stopped_at,
                    lessons_considered=json.dumps(decision.lessons_considered if decision else []),
                    prompt_hash=cycle_id[:16],
                    parse_ok=decision is not None,
                )
                session.add(row)
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        except Exception as exc:
            logger.warning("TradeCycleOrchestrator: DSOR persist error: %s", exc)

    def replay(self, cycle_id: str) -> CycleResult:
        """Reproduce a stored cycle result from its stored inputs.

        Note: In Phase 6 this returns the stored result directly.
        Full replay (re-running agents with stored inputs) is Phase 8.
        """
        stored = self._cycle_store.get(cycle_id)
        if stored is None:
            raise KeyError(f"cycle_id {cycle_id!r} not found in store")

        result_dict = stored["result"]
        return CycleResult.model_validate(result_dict)
