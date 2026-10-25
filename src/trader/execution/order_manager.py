"""Phase 7: OrderManager — coordinates RiskGate verdict with exchange broker."""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any

from pydantic import BaseModel

from trader.config.settings import Settings
from trader.dsor.models import Fill as FillRow
from trader.dsor.models import Order as OrderRow
from trader.dsor.models import RiskEvent
from trader.execution.adapter import ExchangeAdapter
from trader.execution.exceptions import AdapterError, DuplicateOrderError, SlPlacementError
from trader.execution.layer2_check import layer2_cap_check
from trader.risk.models import DecisionInput, RiskVerdict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class OrderResult(BaseModel):
    client_order_id: str
    exchange_order_id: str | None
    status: str  # "filled" | "open" | "cancelled" | "failed"
    filled_qty: float
    avg_price: float
    error: str | None


class ReconcileResult(BaseModel):
    ok: bool
    broker_positions: list[dict[str, Any]]
    dsor_open_orders: list[dict[str, Any]]
    mismatches: list[str]


# ---------------------------------------------------------------------------
# OrderManager
# ---------------------------------------------------------------------------


class OrderManager:
    """Coordinates between RiskGate verdict and the broker.

    Parameters
    ----------
    broker:
        Exchange adapter (PaperBroker or LiveAdapter).
    settings:
        Loaded configuration.
    session_factory:
        SQLAlchemy session factory (callable returning a Session).
    """

    def __init__(
        self,
        broker: ExchangeAdapter,
        settings: Settings,
        session_factory: Any,
    ) -> None:
        self._broker = broker
        self._settings = settings
        self._session_factory = session_factory

    def submit(
        self,
        verdict: RiskVerdict,
        decision: DecisionInput,
        cycle_id: str,
        decision_db_id: int | None = None,
    ) -> OrderResult:
        """Submit an order following RiskGate approval.

        Steps:
        1. Layer 2 cap check — raises CapBreachError on violation
        2. Generate client_order_id
        3. Call broker.place_order() with attached SL/TP
        4. Handle SlPlacementError (cancel + close + record risk_event + raise)
        5. Write Order row + Fill row to DSOR
        6. Return OrderResult
        """
        if not verdict.approved:
            return OrderResult(
                client_order_id="",
                exchange_order_id=None,
                status="failed",
                filled_qty=0.0,
                avg_price=0.0,
                error=f"RiskGate rejected: {verdict.reject_reason}",
            )

        qty = verdict.adjusted_size
        if qty is None or qty <= 0:
            return OrderResult(
                client_order_id="",
                exchange_order_id=None,
                status="failed",
                filled_qty=0.0,
                avg_price=0.0,
                error="adjusted_size is None or zero",
            )

        # Step 1: Layer 2 cap check
        market_meta = self._broker.get_market_meta(decision.symbol)
        layer2_cap_check(
            symbol=decision.symbol,
            side=decision.side,
            qty=qty,
            entry_price=decision.entry_price,
            stop_price=decision.stop_loss,
            lot_size=float(market_meta.get("lot_size", 1.0)),
            max_leverage=float(market_meta.get("max_leverage", self._settings.risk.max_leverage)),
            max_margin_usd=self._settings.risk.max_margin_per_trade_usd,
            max_loss_usd=self._settings.risk.max_loss_per_trade_usd,
            slippage_buffer_pct=self._settings.risk.slippage_buffer_pct,
            taker_fee_rate=float(market_meta.get("taker_fee", 0.00055)),
        )

        # Step 2: Generate client_order_id
        client_order_id = f"{cycle_id[:8]}-{uuid.uuid4().hex[:8]}"

        # Step 3: Place order
        broker_side = "buy" if decision.side.lower() == "long" else "sell"
        order_type = decision.entry_type  # "market" | "limit"
        limit_price: float | None = decision.entry_price if order_type == "limit" else None

        try:
            result = self._broker.place_order(
                symbol=decision.symbol,
                side=broker_side,
                qty=qty,
                order_type=order_type,
                price=limit_price,
                reduce_only=False,
                client_order_id=client_order_id,
                stop_loss=decision.stop_loss if decision.stop_loss > 0 else None,
                take_profit=decision.take_profit if decision.take_profit > 0 else None,
            )
        except DuplicateOrderError:
            raise
        except SlPlacementError as sl_exc:
            # Step 4: SL placement failure — cancel/close is already handled inside
            # LiveAdapter. OrderManager only records events and activates the kill switch.
            if getattr(sl_exc, "close_failed", False):
                self._record_risk_event(
                    "HALT_CLOSE_FAILED",
                    f"SL placement failed AND market close failed: {sl_exc} "
                    f"(entry order_id={getattr(sl_exc, 'order_id', '')})",
                )
            self._record_risk_event("HALT", f"SL placement failed: {sl_exc}")
            self._set_kill_switch()
            raise

        except AdapterError as exc:
            return OrderResult(
                client_order_id=client_order_id,
                exchange_order_id=None,
                status="failed",
                filled_qty=0.0,
                avg_price=0.0,
                error=str(exc),
            )

        # Step 5: Write DSOR rows
        exchange_order_id: str = result.get("order_id", "")
        status: str = result.get("status", "open")
        filled_qty: float = float(result.get("filled_qty", 0.0))
        avg_price: float = float(result.get("avg_price", 0.0))

        try:
            session = self._session_factory()
            now = datetime.datetime.now(datetime.UTC)

            order_row = OrderRow(
                decision_id=decision_db_id,
                client_order_id=client_order_id,
                exchange_order_id=exchange_order_id or None,
                mode=self._settings.trading_mode,
                type=order_type,
                side=broker_side,
                symbol=decision.symbol,
                reduce_only=False,
                qty=qty,
                price=limit_price,
                status=status,
                ts=now,
            )
            session.add(order_row)
            session.flush()

            if filled_qty > 0 and avg_price > 0:
                fee = avg_price * filled_qty * float(market_meta.get("taker_fee", 0.00055))
                fill_row = FillRow(
                    order_id=order_row.id,
                    price=avg_price,
                    qty=filled_qty,
                    fee=fee,
                    ts=now,
                )
                session.add(fill_row)

            session.commit()
        except Exception as db_exc:
            logger.error("DSOR write failed: %s", db_exc)
            # Don't fail the order result — the order is placed

        # Step 6: Return OrderResult
        return OrderResult(
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id or None,
            status=status,
            filled_qty=filled_qty,
            avg_price=avg_price,
            error=None,
        )

    def cancel_expired_orders(self, symbol: str) -> int:
        """Cancel all open limit orders past their TTL. Returns count cancelled."""
        open_orders = self._broker.get_open_orders(symbol)
        count = 0
        now = datetime.datetime.now(datetime.UTC)
        for order in open_orders:
            ttl_ts = order.get("ttl_ts")
            if ttl_ts is not None and ttl_ts.tzinfo is None:
                ttl_ts = ttl_ts.replace(tzinfo=datetime.UTC)
            if ttl_ts is not None and now > ttl_ts:
                try:
                    self._broker.cancel_order(symbol, order["order_id"])
                    count += 1
                except Exception as exc:
                    logger.warning("Failed to cancel order %s: %s", order["order_id"], exc)
        return count

    def reconcile(self, symbol: str) -> ReconcileResult:
        """Compare broker positions vs DSOR open orders. Mismatch → log risk_event."""
        broker_positions = [p for p in self._broker.get_positions() if p.get("symbol") == symbol]
        broker_open_orders = self._broker.get_open_orders(symbol)

        # Query DSOR
        dsor_open_orders: list[dict[str, Any]] = []
        try:
            session = self._session_factory()
            rows = (
                session.query(OrderRow)
                .filter(
                    OrderRow.symbol == symbol,
                    OrderRow.status == "open",
                )
                .all()
            )
            dsor_open_orders = [
                {
                    "client_order_id": r.client_order_id,
                    "exchange_order_id": r.exchange_order_id,
                    "symbol": r.symbol,
                    "status": r.status,
                }
                for r in rows
            ]
        except Exception as exc:
            logger.error("DSOR query failed during reconcile: %s", exc)

        mismatches: list[str] = []

        # Check: broker has position, DSOR has no open order
        if broker_positions and not dsor_open_orders and not broker_open_orders:
            mismatch = f"Broker has position for {symbol} but DSOR has no open orders"
            mismatches.append(mismatch)

        # Check: DSOR has open orders, broker has no position and no open orders
        if dsor_open_orders and not broker_positions and not broker_open_orders:
            mismatch = (
                f"DSOR has {len(dsor_open_orders)} open orders for {symbol} "
                "but broker has no positions/orders"
            )
            mismatches.append(mismatch)

        if mismatches:
            for mismatch in mismatches:
                self._record_risk_event("RECONCILE_MISMATCH", mismatch)
            # Reconciliation mismatch is a critical integrity failure — halt immediately
            self._set_kill_switch()
            logger.critical(
                "RECONCILE_MISMATCH: kill switch activated — %d mismatch(es) for %s: %s",
                len(mismatches),
                symbol,
                mismatches,
            )

        ok = len(mismatches) == 0
        return ReconcileResult(
            ok=ok,
            broker_positions=broker_positions,
            dsor_open_orders=dsor_open_orders,
            mismatches=mismatches,
        )

    def force_close(self, symbol: str, reason: str) -> OrderResult:
        """Close any open position at market immediately (kill switch, max_holding_minutes)."""
        positions = [p for p in self._broker.get_positions() if p.get("symbol") == symbol]

        if not positions:
            return OrderResult(
                client_order_id="",
                exchange_order_id=None,
                status="cancelled",
                filled_qty=0.0,
                avg_price=0.0,
                error=f"No open position for {symbol}",
            )

        pos = positions[0]
        # Normalise side: ccxt returns "long"/"short" for live, PaperBroker uses "buy"/"sell"
        raw_side = pos["side"].lower()
        is_long = raw_side in {"buy", "long"}
        broker_side = "sell" if is_long else "buy"
        qty = pos["qty"]
        client_order_id = f"force-close-{uuid.uuid4().hex[:8]}"

        try:
            result = self._broker.place_order(
                symbol=symbol,
                side=broker_side,
                qty=qty,
                order_type="market",
                reduce_only=True,
                client_order_id=client_order_id,
            )
        except Exception as exc:
            # Best-effort: log and return failed regardless of exception type
            logger.error("force_close broker call failed: %s", exc)
            self._record_risk_event("FORCE_CLOSE_FAILED", f"{reason}: {exc}")
            return OrderResult(
                client_order_id=client_order_id,
                exchange_order_id=None,
                status="failed",
                filled_qty=0.0,
                avg_price=0.0,
                error=str(exc),
            )

        exchange_order_id = result.get("order_id", "")
        self._record_risk_event("FORCE_CLOSE", reason)

        return OrderResult(
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id or None,
            status=result.get("status", "filled"),
            filled_qty=float(result.get("filled_qty", 0.0)),
            avg_price=float(result.get("avg_price", 0.0)),
            error=None,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_kill_switch(self) -> None:
        """Activate the system kill switch in DSOR."""
        try:
            from trader.dsor.system_state import SystemStateHelper  # noqa: PLC0415

            session = self._session_factory()
            SystemStateHelper(session).set_kill_switch(True)
            session.commit()
        except Exception as exc:
            # CRITICAL: kill switch not persisted — system may resume trading after restart
            logger.critical("KILL_SWITCH_PERSIST_FAILED — DB unavailable: %s", exc)

    def _record_risk_event(self, event_type: str, details: str) -> None:
        """Write a RiskEvent row to DSOR."""
        try:
            session = self._session_factory()
            event = RiskEvent(
                ts=datetime.datetime.now(datetime.UTC),
                type=event_type,
                details=details,
            )
            session.add(event)
            session.commit()
        except Exception as exc:
            # CRITICAL: audit trail lost — emit at CRITICAL level so structured logging captures it
            logger.critical(
                "RISK_EVENT_PERSIST_FAILED event_type=%s details=%r db_error=%s",
                event_type,
                details,
                exc,
            )
