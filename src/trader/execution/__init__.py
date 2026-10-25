"""Phase 7: Execution layer — adapter interface, paper broker, live adapter, order manager."""

from trader.execution.adapter import ExchangeAdapter
from trader.execution.exceptions import (
    AdapterError,
    DuplicateOrderError,
    LiveNotApprovedError,
    SlPlacementError,
)
from trader.execution.order_manager import OrderManager, OrderResult, ReconcileResult
from trader.execution.paper_broker import PaperBroker

__all__ = [
    "ExchangeAdapter",
    "PaperBroker",
    "OrderManager",
    "OrderResult",
    "ReconcileResult",
    "AdapterError",
    "DuplicateOrderError",
    "LiveNotApprovedError",
    "SlPlacementError",
]
