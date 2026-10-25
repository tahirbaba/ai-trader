"""Phase pre-8: PositionMonitor — SL amendment on price shock for open positions."""

from __future__ import annotations

import logging
from typing import Any

from trader.config.settings import PriceShockSettings
from trader.execution.adapter import ExchangeAdapter
from trader.risk.price_shock import compute_shock_sl_amendment

logger = logging.getLogger(__name__)


class PositionMonitor:
    """Monitors open positions and amends SL during price-shock events.

    Called by TradeCycleOrchestrator once per cycle when price_shock_active is True.
    Amends SL for each open position:
      - Adverse shock  → cut open risk by open_position_risk_cut_pct (move SL toward entry)
      - Favorable shock → move SL to breakeven (if move_sl_to_breakeven=True)
      - Never widens.

    Verification is delegated to broker.amend_stop_loss(), which guarantees the
    new stop order exists on the exchange before returning True.
    """

    def __init__(self, broker: ExchangeAdapter, shock_cfg: PriceShockSettings) -> None:
        self._broker = broker
        self._shock_cfg = shock_cfg

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
            Used to determine shock direction.
        price_1m_return:
            Signed 1m return for secondary direction signal. Used when 5m is None.

        Returns
        -------
        List of amendment result dicts: {"symbol", "side", "old_sl", "new_sl",
        "amended", "reason"}.
        """
        positions = [p for p in self._broker.get_positions() if p.get("symbol") == symbol]
        if not positions:
            return []

        # Determine shock direction from available return data
        price_return = price_5m_return if price_5m_return is not None else price_1m_return
        if price_return is None:
            logger.warning("position_monitor: no price return data; skipping SL amendment for %s", symbol)
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

            # Get current SL from position (broker stores it)
            current_sl = pos.get("sl_price") or pos.get("stopLoss") or pos.get("stop_loss")
            if current_sl is None:
                logger.debug("position_monitor: no SL on %s %s; skipping", symbol, side_raw)
                continue
            current_sl = float(current_sl)
            if current_sl <= 0:
                continue

            # Shock direction relative to position
            shock_is_adverse = (is_long and not price_went_up) or (not is_long and price_went_up)

            current_price = float(pos.get("unrealised_pnl", 0.0)) / max(float(pos.get("qty", 1.0)), 1e-8) + entry_price

            new_sl = compute_shock_sl_amendment(
                side="long" if is_long else "short",
                entry_price=entry_price,
                current_sl=current_sl,
                current_price=current_price,
                open_position_risk_cut_pct=self._shock_cfg.open_position_risk_cut_pct,
                move_sl_to_breakeven_on_favorable_shock=self._shock_cfg.move_sl_to_breakeven_on_favorable_shock,
                shock_is_adverse=shock_is_adverse,
            )

            if new_sl is None:
                results.append({
                    "symbol": symbol,
                    "side": side_raw,
                    "old_sl": current_sl,
                    "new_sl": None,
                    "amended": False,
                    "reason": "no_amendment_needed",
                })
                continue

            try:
                amended = self._broker.amend_stop_loss(symbol, new_sl)
            except Exception as exc:
                logger.warning(
                    "position_monitor: amend_stop_loss raised for %s: %s", symbol, exc
                )
                amended = False
            reason = "amended" if amended else "amendment_rejected_by_broker"
            if amended:
                logger.info(
                    "position_monitor: amended SL for %s %s: %.6f -> %.6f (shock_adverse=%s)",
                    symbol, side_raw, current_sl, new_sl, shock_is_adverse,
                )
            else:
                logger.warning(
                    "position_monitor: amend SL failed for %s %s: %.6f -> %.6f",
                    symbol, side_raw, current_sl, new_sl,
                )

            results.append({
                "symbol": symbol,
                "side": side_raw,
                "old_sl": current_sl,
                "new_sl": new_sl,
                "amended": amended,
                "reason": reason,
            })

        return results
