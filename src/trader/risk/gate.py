"""Phase 5: RiskGate — evaluates all risk checks before an order is created.

The gate is pure code (no I/O during evaluate). All state is passed in as arguments.
Checks run in a fixed order; each check returns a verdict on failure.

Path: Decision -> LessonFilter -> RiskGate -> Executor
The LLM never calls the exchange. The RiskGate never makes network calls.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

from trader.config.settings import Settings
from trader.risk.models import (
    AccountState,
    DecisionInput,
    LessonCheckInput,
    MarketState,
    RiskVerdict,
)
from trader.risk.sizing import (
    SizingError,
    check_risk_reward,
    compute_liquidation_price,
    compute_size,
)

logger = logging.getLogger(__name__)

# Path to live approval sentinel file (relative to repo root)
_LIVE_APPROVAL_PATH = Path(__file__).parent.parent.parent.parent / "config" / "live_approval.yaml"

# Actions that mean "no trade" — not a risk rejection, just nothing to do
_NO_TRADE_ACTIONS = frozenset({"HOLD", "NO_TRADE"})


class RiskGate:
    """Evaluates all risk checks for a proposed trade.

    Parameters
    ----------
    settings : Settings
        Loaded configuration (app + risk).
    live_approval_path : Path | None
        Override for the live_approval.yaml path (for testing).
    kill_switch_on : bool
        Whether the system kill switch is active. Caller reads this from system_state.
    """

    def __init__(
        self,
        settings: Settings,
        live_approval_path: Path | None = None,
        kill_switch_on: bool = False,
        exchange_sl_capable: bool = True,
    ) -> None:
        self._settings = settings
        self._risk = settings.risk
        self._trading_mode = settings.trading_mode
        self._live_approval_path = live_approval_path or _LIVE_APPROVAL_PATH
        self._kill_switch_on = kill_switch_on
        self._exchange_sl_capable = exchange_sl_capable

    def evaluate(
        self,
        decision: DecisionInput,
        lesson_check: LessonCheckInput,
        account: AccountState,
        market: MarketState,
        lot_size: float = 1.0,
        min_qty: float = 1.0,
        maker_fee_rate: float = 0.0002,
        taker_fee_rate: float = 0.00055,
    ) -> RiskVerdict:
        """Run all 27 risk checks in order. Return a RiskVerdict.

        Checks are fail-fast: return immediately on first failure.
        The rules_failed list contains the name of the rule(s) that fired.
        """
        rules_failed: list[str] = []

        # ------------------------------------------------------------------ #
        # 1. kill_switch
        # ------------------------------------------------------------------ #
        if self._kill_switch_on:
            return self._reject("KILL_SWITCH", rules_failed)

        # ------------------------------------------------------------------ #
        # 2. mode_check — LIVE requires live_approval.yaml
        # ------------------------------------------------------------------ #
        if self._trading_mode == "live" and not self._live_approval_path.exists():
            return self._reject("LIVE_NOT_APPROVED", rules_failed)

        # ------------------------------------------------------------------ #
        # 3. action_filter — HOLD / NO_TRADE → no order needed
        # ------------------------------------------------------------------ #
        if decision.action.upper() in _NO_TRADE_ACTIONS:
            return RiskVerdict(
                approved=False,
                adjusted_size=None,
                rules_failed=["NO_TRADE"],
                loss_at_stop_usd=None,
                liq_price=None,
                entry_drift_pct=None,
                reject_reason="NO_TRADE",
            )

        # ------------------------------------------------------------------ #
        # 4. symbol_allowed
        # ------------------------------------------------------------------ #
        if decision.symbol not in self._risk.allowed_symbols:
            return self._reject("SYMBOL_NOT_ALLOWED", rules_failed)

        # ------------------------------------------------------------------ #
        # 5. lesson_filter_blocked
        # ------------------------------------------------------------------ #
        if lesson_check.blocked:
            return self._reject("LESSON_FILTER_BLOCK", rules_failed)

        # ------------------------------------------------------------------ #
        # 6. clock_drift
        # ------------------------------------------------------------------ #
        if market.clock_drift_ms > self._risk.max_clock_drift_ms:
            return self._reject("CLOCK_DRIFT", rules_failed)

        # ------------------------------------------------------------------ #
        # 7. data_freshness
        # ------------------------------------------------------------------ #
        if market.data_age_seconds > self._risk.max_data_age_seconds:
            return self._reject("STALE_DATA", rules_failed)

        # ------------------------------------------------------------------ #
        # 8. spread_check
        # ------------------------------------------------------------------ #
        if market.spread_pct is not None and market.spread_pct > self._risk.max_spread_pct:
            return self._reject("SPREAD_TOO_WIDE", rules_failed)

        # ------------------------------------------------------------------ #
        # 9. macro_blackout
        # ------------------------------------------------------------------ #
        if market.is_macro_blackout:
            return self._reject("MACRO_BLACKOUT", rules_failed)

        # ------------------------------------------------------------------ #
        # 10. exchange_blackout
        # ------------------------------------------------------------------ #
        if market.is_exchange_blackout:
            return self._reject("EXCHANGE_BLACKOUT", rules_failed)

        # ------------------------------------------------------------------ #
        # 10b. news_no_chase — block when price already moved post-high-impact news
        # ------------------------------------------------------------------ #
        if market.news_chase_block:
            entry_side = decision.side.lower()
            chase_side = market.news_chase_direction.lower()
            if chase_side == "" or entry_side == chase_side:
                return self._reject("NEWS_NO_CHASE", rules_failed)

        # ------------------------------------------------------------------ #
        # 10c. price_shock — block while shock_until window is active
        # ------------------------------------------------------------------ #
        if market.price_shock_active:
            block_until = market.price_shock_block_until
            if block_until is not None:
                if block_until.tzinfo is None:
                    block_until = block_until.replace(tzinfo=datetime.UTC)
                if datetime.datetime.now(datetime.UTC) < block_until:
                    return self._reject("PRICE_SHOCK", rules_failed)
            else:
                return self._reject("PRICE_SHOCK", rules_failed)

        # ------------------------------------------------------------------ #
        # 10d. exchange_sl_required — reject if adapter cannot attach/verify exchange SL
        # ------------------------------------------------------------------ #
        if self._risk.require_exchange_side_sl_tp and not self._exchange_sl_capable:
            return self._reject("EXCHANGE_SL_NOT_CAPABLE", rules_failed)

        # ------------------------------------------------------------------ #
        # 11. equity_floor
        # ------------------------------------------------------------------ #
        if account.equity_usd < self._risk.equity_floor_usd:
            return self._reject("EQUITY_FLOOR", rules_failed)

        # ------------------------------------------------------------------ #
        # 12. daily_loss_limit
        # ------------------------------------------------------------------ #
        if account.daily_loss_usd >= self._risk.daily_loss_limit_usd:
            return self._reject("DAILY_LOSS_LIMIT", rules_failed)

        # ------------------------------------------------------------------ #
        # 13. weekly_loss_limit
        # ------------------------------------------------------------------ #
        if account.weekly_loss_usd >= self._risk.weekly_loss_limit_usd:
            return self._reject("WEEKLY_LOSS_LIMIT", rules_failed)

        # ------------------------------------------------------------------ #
        # 14. consecutive_losses
        # ------------------------------------------------------------------ #
        if account.consecutive_losses >= self._risk.max_consecutive_losses:
            return self._reject("CONSECUTIVE_LOSSES", rules_failed)

        # ------------------------------------------------------------------ #
        # 15. cooldown — last_loss_ts within cooldown window
        # ------------------------------------------------------------------ #
        if account.last_loss_ts is not None:
            cooldown_delta = datetime.timedelta(minutes=self._risk.cooldown_after_loss_minutes)
            now_utc = datetime.datetime.now(datetime.UTC).replace(tzinfo=datetime.UTC)
            last_loss = account.last_loss_ts
            # Normalise to UTC-aware if naive
            if last_loss.tzinfo is None:
                last_loss = last_loss.replace(tzinfo=datetime.UTC)
            if (now_utc - last_loss) < cooldown_delta:
                return self._reject("COOLDOWN", rules_failed)

        # ------------------------------------------------------------------ #
        # 16. max_open_positions
        # ------------------------------------------------------------------ #
        if account.open_positions >= self._risk.max_open_positions:
            return self._reject("MAX_OPEN_POSITIONS", rules_failed)

        # ------------------------------------------------------------------ #
        # 17. max_trades_today
        # ------------------------------------------------------------------ #
        if account.trades_today >= self._risk.max_trades_per_day:
            return self._reject("MAX_TRADES_TODAY", rules_failed)

        # ------------------------------------------------------------------ #
        # 18. llm_budget
        # ------------------------------------------------------------------ #
        if account.llm_cost_today_usd >= self._risk.total_llm_budget_usd_per_day:
            return self._reject("LLM_BUDGET_EXHAUSTED", rules_failed)

        # ------------------------------------------------------------------ #
        # 19. confidence — effective confidence after lesson penalty
        # ------------------------------------------------------------------ #
        effective_confidence = decision.confidence - lesson_check.confidence_penalty
        if effective_confidence < self._risk.min_confidence:
            return self._reject("LOW_CONFIDENCE", rules_failed)

        # ------------------------------------------------------------------ #
        # 20. confluence
        # ------------------------------------------------------------------ #
        if decision.confluence_score < self._risk.min_confluence_score:
            return self._reject("LOW_CONFLUENCE", rules_failed)

        # ------------------------------------------------------------------ #
        # 21. require_sl — stop_loss must be on the correct side of entry
        # ------------------------------------------------------------------ #
        side_lower = decision.side.lower()
        entry = decision.entry_price
        stop = decision.stop_loss

        if stop <= 0:
            return self._reject("MISSING_STOP_LOSS", rules_failed)
        if side_lower == "long" and stop >= entry:
            return self._reject("MISSING_STOP_LOSS", rules_failed)
        if side_lower == "short" and stop <= entry:
            return self._reject("MISSING_STOP_LOSS", rules_failed)

        # ------------------------------------------------------------------ #
        # 22. stop_atr_range
        # ------------------------------------------------------------------ #
        if market.atr_14 is not None and market.atr_14 > 0:
            stop_distance = abs(entry - stop)
            min_stop = self._risk.min_stop_atr_multiple * market.atr_14
            max_stop = self._risk.max_stop_atr_multiple * market.atr_14
            if stop_distance < min_stop or stop_distance > max_stop:
                return self._reject("STOP_DISTANCE_ATR", rules_failed)

        # ------------------------------------------------------------------ #
        # 23. funding_window
        # ------------------------------------------------------------------ #
        if market.next_funding_ts is not None and market.funding_rate is not None:
            next_ts = market.next_funding_ts
            if next_ts.tzinfo is None:
                next_ts = next_ts.replace(tzinfo=datetime.UTC)
            now_utc = datetime.datetime.now(datetime.UTC).replace(tzinfo=datetime.UTC)
            funding_window = datetime.timedelta(minutes=self._risk.avoid_funding_window_minutes)
            within_window = abs(next_ts - now_utc) <= funding_window
            # Funding is "against" the position:
            # Long position: negative funding means longs pay shorts — that's against longs
            # Short position: positive funding means longs pay shorts — that's against shorts
            funding_against_long = market.funding_rate < 0
            funding_against_short = market.funding_rate > 0
            funding_against = (side_lower == "long" and funding_against_long) or (
                side_lower == "short" and funding_against_short
            )
            if within_window and funding_against:
                return self._reject("FUNDING_WINDOW", rules_failed)

        # ------------------------------------------------------------------ #
        # 24. sizing — compute position size
        # ------------------------------------------------------------------ #
        try:
            qty, loss_at_stop, margin_used = compute_size(
                side=side_lower,
                entry=entry,
                stop=stop,
                take_profit=decision.take_profit,
                lot_size=lot_size,
                min_qty=min_qty,
                maker_fee_rate=maker_fee_rate,
                taker_fee_rate=taker_fee_rate,
                max_margin_usd=self._risk.max_margin_per_trade_usd,
                max_loss_usd=self._risk.max_loss_per_trade_usd,
                max_leverage=float(self._risk.max_leverage),
                slippage_buffer_pct=self._risk.slippage_buffer_pct,
            )
        except (SizingError, ValueError, ZeroDivisionError, OverflowError) as exc:
            logger.warning("RiskGate: sizing failed: %s", exc)
            return self._reject("SIZING_FAILED", rules_failed)

        # ------------------------------------------------------------------ #
        # 25. liq_check — liquidation must be beyond stop by liq_buffer
        # ------------------------------------------------------------------ #
        stop_distance = abs(entry - stop)
        liq_price = compute_liquidation_price(
            side=side_lower,
            entry=entry,
            qty=qty,
            margin=margin_used,
        )
        liq_distance = abs(entry - liq_price)
        if liq_distance < self._risk.liq_buffer_multiple * stop_distance:
            return self._reject_with_liq("LIQ_TOO_CLOSE", rules_failed, liq_price)

        # ------------------------------------------------------------------ #
        # 26. rr_check
        # ------------------------------------------------------------------ #
        rr_passes, _actual_rr = check_risk_reward(
            side=side_lower,
            entry=entry,
            stop=stop,
            take_profit=decision.take_profit,
            qty=qty,
            maker_fee_rate=maker_fee_rate,
            taker_fee_rate=taker_fee_rate,
            min_rr=self._risk.min_risk_reward,
        )
        if not rr_passes:
            return self._reject("RR_TOO_LOW", rules_failed)

        # ------------------------------------------------------------------ #
        # 27. entry_drift — price has moved too far since brain snapshot
        # ------------------------------------------------------------------ #
        drift = abs(market.live_price - market.snapshot_price) / market.snapshot_price
        entry_drift_pct = drift
        if drift > self._risk.max_entry_drift_pct:
            return RiskVerdict(
                approved=False,
                adjusted_size=None,
                rules_failed=["ENTRY_DRIFT"],
                loss_at_stop_usd=loss_at_stop,
                liq_price=liq_price,
                entry_drift_pct=entry_drift_pct,
                reject_reason="ENTRY_DRIFT",
            )

        # ------------------------------------------------------------------ #
        # All checks passed
        # ------------------------------------------------------------------ #
        logger.info(
            "RiskGate: approved %s %s qty=%.4f loss_at_stop=%.4f margin=%.4f",
            side_lower,
            decision.symbol,
            qty,
            loss_at_stop,
            margin_used,
        )
        return RiskVerdict(
            approved=True,
            adjusted_size=qty,
            rules_failed=[],
            loss_at_stop_usd=loss_at_stop,
            liq_price=liq_price,
            entry_drift_pct=entry_drift_pct,
            reject_reason="",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _reject(rule_name: str, rules_failed: list[str]) -> RiskVerdict:
        rules_failed.append(rule_name)
        return RiskVerdict(
            approved=False,
            adjusted_size=None,
            rules_failed=rules_failed[:],
            loss_at_stop_usd=None,
            liq_price=None,
            entry_drift_pct=None,
            reject_reason=rule_name,
        )

    @staticmethod
    def _reject_with_liq(rule_name: str, rules_failed: list[str], liq_price: float) -> RiskVerdict:
        rules_failed.append(rule_name)
        return RiskVerdict(
            approved=False,
            adjusted_size=None,
            rules_failed=rules_failed[:],
            loss_at_stop_usd=None,
            liq_price=liq_price,
            entry_drift_pct=None,
            reject_reason=rule_name,
        )
