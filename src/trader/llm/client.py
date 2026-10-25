"""Phase 6: LlmClient — thin wrapper around the Anthropic SDK with cost tracking and budget enforcement."""

from __future__ import annotations

import datetime
import logging
import time
from typing import TYPE_CHECKING, Any

from trader.llm.exceptions import BudgetExceededError, LlmError, LlmTimeoutError

if TYPE_CHECKING:
    from collections.abc import Callable

    from trader.config.settings import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing constants (USD per million tokens) — Claude Sonnet 4.6 defaults
# ---------------------------------------------------------------------------

_INPUT_COST_PER_M = 3.0  # $3/M input tokens
_OUTPUT_COST_PER_M = 15.0  # $15/M output tokens
_CACHED_INPUT_COST_PER_M = 0.30  # $0.30/M cached input tokens


def _estimate_cost(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
) -> float:
    """Estimate USD cost for a single Anthropic API call."""
    # Cached tokens are billed at the reduced rate; subtract from regular input
    billable_input = max(0, input_tokens - cache_read_tokens)
    cost = (
        billable_input * _INPUT_COST_PER_M / 1_000_000
        + cache_read_tokens * _CACHED_INPUT_COST_PER_M / 1_000_000
        + output_tokens * _OUTPUT_COST_PER_M / 1_000_000
    )
    return cost


class LlmClient:
    """Thin wrapper around the Anthropic SDK with cost tracking and budget enforcement.

    Parameters
    ----------
    settings:
        Application settings (used for default model and budget limit).
    session_factory:
        Callable that returns a SQLAlchemy Session for logging to llm_calls table.
        Signature: () -> Session
    """

    def __init__(self, settings: Settings, session_factory: Callable[[], Any]) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._default_model = settings.app.models.screening  # cheap model default
        self._budget_limit = settings.risk.total_llm_budget_usd_per_day

        # Lazy import to avoid hard dependency at module-load time
        import anthropic  # noqa: PLC0415

        self._client = anthropic.Anthropic()

    def complete(
        self,
        purpose: str,
        system: str,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int = 1024,
        timeout: float = 30.0,
    ) -> str:
        """Call Anthropic API. Returns raw text response.

        Raises BudgetExceededError if daily LLM cost >= total_llm_budget_usd_per_day.
        Raises LlmTimeoutError on timeout.
        Raises LlmError on other failures.
        Logs every call to llm_calls DSOR table (tokens, cost, latency, cache_hit).
        """
        resolved_model = model or self._default_model

        # ------------------------------------------------------------------ #
        # 1. Budget check (before any API call)
        # ------------------------------------------------------------------ #
        spent_today = self._get_today_cost()
        if spent_today >= self._budget_limit:
            raise BudgetExceededError(spent=spent_today, limit=self._budget_limit)

        # ------------------------------------------------------------------ #
        # 2. Make the API call
        # ------------------------------------------------------------------ #
        start_time = time.monotonic()
        cache_hit = False
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0

        try:
            import anthropic  # noqa: PLC0415

            response = self._client.messages.create(
                model=resolved_model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                timeout=timeout,
            )

            # Extract token counts
            usage = response.usage
            input_tokens = usage.input_tokens
            output_tokens = usage.output_tokens
            cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_hit = cache_read_tokens > 0

            # Extract text from response
            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text

        except anthropic.APITimeoutError as exc:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            self._log_call(
                purpose=purpose,
                model=resolved_model,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                latency_ms=latency_ms,
                cache_hit=False,
            )
            raise LlmTimeoutError(timeout=timeout) from exc
        except anthropic.APIError as exc:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            self._log_call(
                purpose=purpose,
                model=resolved_model,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                latency_ms=latency_ms,
                cache_hit=False,
            )
            raise LlmError(f"Anthropic API error: {exc}") from exc

        latency_ms = int((time.monotonic() - start_time) * 1000)

        # ------------------------------------------------------------------ #
        # 3. Cost estimation and logging
        # ------------------------------------------------------------------ #
        cost_usd = _estimate_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
        )

        self._log_call(
            purpose=purpose,
            model=resolved_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            cache_hit=cache_hit,
        )

        logger.info(
            "LlmClient: purpose=%s model=%s tokens_in=%d tokens_out=%d cost=%.6f latency=%dms cache=%s",
            purpose,
            resolved_model,
            input_tokens,
            output_tokens,
            cost_usd,
            latency_ms,
            cache_hit,
        )

        return text

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_today_cost(self) -> float:
        """Query llm_calls table for today's total cost."""
        try:
            from trader.dsor.repositories import LlmCallRepository  # noqa: PLC0415

            session = self._session_factory()
            try:
                today_start = datetime.datetime.utcnow().replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                repo = LlmCallRepository(session)
                return repo.total_cost_since(today_start)
            finally:
                session.close()
        except Exception as exc:
            logger.warning("LlmClient: failed to query today cost: %s", exc)
            return 0.0

    def _log_call(
        self,
        purpose: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        latency_ms: int,
        cache_hit: bool,
    ) -> None:
        """Log the LLM call to the llm_calls DSOR table."""
        try:
            from trader.dsor.models import LlmCall  # noqa: PLC0415

            session = self._session_factory()
            try:
                row = LlmCall(
                    ts=datetime.datetime.utcnow(),
                    purpose=purpose,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    cache_hit=cache_hit,
                )
                session.add(row)
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        except Exception as exc:
            logger.warning("LlmClient: failed to log call to DSOR: %s", exc)
