"""Phase 7: Execution layer exceptions."""

from __future__ import annotations


class AdapterError(Exception):
    """Generic exchange adapter error."""


class LiveNotApprovedError(AdapterError):
    """Raised when LiveAdapter is constructed without config/live_approval.yaml."""


class SlPlacementError(AdapterError):
    """Raised when SL order placement fails after an entry order was placed."""


class DuplicateOrderError(AdapterError):
    """Raised when a client_order_id is reused."""
