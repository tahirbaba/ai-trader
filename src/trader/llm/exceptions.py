"""Phase 6: Typed exceptions for LLM client errors."""

from __future__ import annotations


class LlmError(Exception):
    """Base exception for all LLM client errors."""


class BudgetExceededError(LlmError):
    """Raised when daily LLM cost budget is exceeded (no API call is made)."""

    def __init__(self, spent: float, limit: float) -> None:
        self.spent = spent
        self.limit = limit
        super().__init__(f"LLM daily budget exceeded: spent ${spent:.4f}, limit ${limit:.4f}")


class LlmTimeoutError(LlmError):
    """Raised when the Anthropic API call times out."""

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        super().__init__(f"LLM call timed out after {timeout}s")
