"""Phase pre-8: PositionMonitor — SL amendment on price shock for open positions."""

from __future__ import annotations

import datetime
import logging
from typing import Any

from trader.config.settings import PriceShockSettings
from trader.execution.adapter import ExchangeAdapter
from trader.risk.price_shock import compute_shock_sl_amendment

logger = logging.getLogger(__name__)

_SessionFactory = Any  # sessionmaker or similar callable returning a Session


class PositionMonitor:
    """Monitors open positions and amends SL during price-shock events.

    Called by TradeCycleOrchestrator once per cycle when price_shock_active is True.
    Amends SL for each open position:
      - Adverse shock  → cut open risk by open_position_risk_cut_pct (move SL toward entry)
      - Favorable shock → move SL to breakeven (if move_sl_to_breakeven=True)
      - Never widens.

    Failure handling:
      - Each adverse-shock SL amend failure writes a SL_AMEND_FAILURE risk_event.
      - On the 2nd consecutive failure for the same symbol during an adverse shock,
        an emergency market close (reduceOnly) is sent and the kill switch is set.
      - A SL_AMEND_HALT risk_event is written before the emergency close.
    """

    def __init__(
        self,
        broker: ExchangeAdapter,
        shock_cfg: PriceShockSettings,
        session_factory: _SessionFactory | None = None,
    ) -> None:
        self._broker = broker
        self._shock_cfg = shock_cfg
        self._session_factory = session_factory
        # Tracks consecutive adverse SL amend failures per symbol
        self._adverse_fail_count: dict[str, int] = {}

    # ------------------------------------------------------------------
    # DSOR helpers
    # ------------------------------------------------------------------

    def _write_risk_event(self, event_type: str, details: str) -> None:
        if self._session_factory is None:
            logger.warning(
                "position_monitor: no session_factory; cannot write risk_event '%s'",
                event_type,
            )
            return
        try:
            from trader.dsor.models import RiskEvent  # noqa: PLC0415

            with self._session_factory() as session:
                session.add(
                    RiskEvent(
                        ts=datetime.datetime.now(datetime.UTC),
                        type=event_type,
                        details=details,
                    )
                )
                session.commit()
        except Exception as exc:
            logger.error("position_monitor: failed to write risk_event '%s': %s", event_type, exc)

    def _set_kill_switch(self) -> None:
        if self._session_factory is None:
            logger.warning("position_monitor: no session_factory; cannot set kill switch")
            return
        try:
            from trader.dsor.system_state import SystemStateHelper  # noqa: PLC0415

            with self._session_factory() as session:
                helper = SystemStateHelper(session)
                helper.set_kill_switch(on=True)
                session.commit()
        except Exception as exc:
            logger.error("position_monitor: failed to set kill switch: %s", exc)

    def _emergency_close(self, symbol: str, pos: dict[str, Any]) -> None:
        side_raw = pos.get("side", "").lower()
        is_long = side_raw in {"buy", "long"}
        qty = float(pos.get("qty", 0))
        if qty <= 0:
            logger.error("position_monitor: cannot emergency close %s — qty=0", symbol)
            return
        close_side = "sell" if is_long else "buy"
        try:
            self._broker.place_order(
                symbol=symbol,
                side=close_side,
                qty=qty,
                order_type="market",
                reduce_only=True,
            )
            logger.critical(
                "position_monitor: emergency close SENT for %s %s qty=%.6f",
                symbol,
                side_raw,
                qty,
            )
        except Exception as exc:
            logger.critical("position_monitor: emergency close FAILED for %s: %s", symbol, exc)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def monitor_shock(
        self,
        symbol: str,
        price_5m_return: float | None,
        price_1m_return: float | None,
    ) -> list[dict[str, Any]]:
        """Check open positions for *symbol* and amend SL if shock warrants it.

        Parameters
        ----------
        symbol:
            Trading pair.
        price_5m_return:
            Signed percentage return over 5 minutes (positive = price up).
        price_1m_return:
            Signed 1m return — used when 5m is None.

        Returns
        -------
        List of result dicts: {"symbol", "side", "old_sl", "new_sl", "amended", "reason"}.
        Reason "halt_emergency_close" means kill switch was set.
        """
        positions = [p for p in self._broker.get_positions() if p.get("symbol") == symbol]
        if not positions:
            return []

        price_return = price_5m_return if price_5m_return is not None else price_1m_return
        if price_return is None:
            logger.warning(
                "position_monitor: no price return data; skipping SL amendment for %s", symbol
            )
            return []

        if price_return == 0.0:
            return []

        # price_return > 0 means market went UP; adverse for shorts, favorable for longs
        price_went_up = price_return > 0

        results: list[dict[str, Any]] = []
        for pos in positions:
            side_raw = pos.get("side", "").lower()
            is_long = side_raw in {"buy", "long"}
            entry_price = float(pos.get("entry_price", 0.0))

            # Get current SL from position dict
            current_sl = pos.get("sl_price") or pos.get("stopLoss") or pos.get("stop_loss")
            if current_sl is None:
                logger.debug("position_monitor: no SL on %s %s; skipping", symbol, side_raw)
                continue
            current_sl = float(current_sl)
            if current_sl <= 0:
                continue

            shock_is_adverse = (is_long and not price_went_up) or (not is_long and price_went_up)

            current_price = (
                float(pos.get("unrealised_pnl", 0.0)) / max(float(pos.get("qty", 1.0)), 1e-8)
                + entry_price
            )

            new_sl = compute_shock_sl_amendment(
                side="long" if is_long else "short",
                entry_price=entry_price,
                current_sl=current_sl,
                current_price=current_price,
                open_position_risk_cut_pct=self._shock_cfg.open_position_risk_cut_pct,
                move_sl_to_breakeven_on_favorable_shock=(
                    self._shock_cfg.move_sl_to_breakeven_on_favorable_shock
                ),
                shock_is_adverse=shock_is_adverse,
            )

            if new_sl is None:
                results.append(
                    {
                        "symbol": symbol,
                        "side": side_raw,
                        "old_sl": current_sl,
                        "new_sl": None,
                        "amended": False,
                        "reason": "no_amendment_needed",
                    }
                )
                continue

            try:
                amended = self._broker.amend_stop_loss(symbol, new_sl)
            except Exception as exc:
                logger.warning("position_monitor: amend_stop_loss raised for %s: %s", symbol, exc)
                amended = False

            if not amended:
                if shock_is_adverse:
                    fail_count = self._adverse_fail_count.get(symbol, 0) + 1
                    self._adverse_fail_count[symbol] = fail_count

                    self._write_risk_event(
                        "SL_AMEND_FAILURE",
                        (
                            f"symbol={symbol} side={side_raw} "
                            f"old_sl={current_sl:.6f} new_sl={new_sl:.6f} "
                            f"adverse_fail_count={fail_count}"
                        ),
                    )
                    logger.critical(
                        "position_monitor: adverse shock SL amend failed #%d for %s %s",
                        fail_count,
                        symbol,
                        side_raw,
                    )

                    if fail_count >= 2:
                        self._write_risk_event(
                            "SL_AMEND_HALT",
                            (
                                f"symbol={symbol} — {fail_count} consecutive SL amend "
                                "failures during adverse shock → emergency close + halt"
                            ),
                        )
                        self._emergency_close(symbol, pos)
                        self._set_kill_switch()
                        results.append(
                            {
                                "symbol": symbol,
                                "side": side_raw,
                                "old_sl": current_sl,
                                "new_sl": new_sl,
                                "amended": False,
                                "reason": "halt_emergency_close",
                            }
                        )
                        continue
                else:
                    logger.warning(
                        "position_monitor: amend SL failed (non-adverse) for %s %s: %.6f -> %.6f",
                        symbol,
                        side_raw,
                        current_sl,
                        new_sl,
                    )

                results.append(
                    {
                        "symbol": symbol,
                        "side": side_raw,
                        "old_sl": current_sl,
                        "new_sl": new_sl,
                        "amended": False,
                        "reason": "amendment_rejected_by_broker",
                    }
                )
            else:
                self._adverse_fail_count.pop(symbol, None)
                logger.info(
                    "position_monitor: amended SL for %s %s: %.6f -> %.6f (shock_adverse=%s)",
                    symbol,
                    side_raw,
                    current_sl,
                    new_sl,
                    shock_is_adverse,
                )
                results.append(
                    {
                        "symbol": symbol,
                        "side": side_raw,
                        "old_sl": current_sl,
                        "new_sl": new_sl,
                        "amended": True,
                        "reason": "amended",
                    }
                )

        return results
