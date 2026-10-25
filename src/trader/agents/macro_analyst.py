"""Phase 6: MacroNewsAnalyst — LLM-powered macro/news analysis."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from trader.agents.prompts import MACRO_NEWS_SYSTEM
from trader.llm.sanitizer import sanitize_for_prompt
from trader.llm.schemas import MacroNewsReport

if TYPE_CHECKING:
    from trader.llm.client import LlmClient

logger = logging.getLogger(__name__)

_PURPOSE = "macro_analyst"

# Neutral fallback report
_NEUTRAL_BIAS = "neutral"


def _neutral_report(data_missing: list[str]) -> MacroNewsReport:
    return MacroNewsReport(
        agent="MacroNewsAnalyst",
        bias=_NEUTRAL_BIAS,
        confidence=0.0,
        data_missing=data_missing,
    )


def _build_prompt(
    news_items: list[dict[str, Any]],
    macro_events: list[dict[str, Any]],
    cross_asset: dict[str, Any],
    is_macro_blackout: bool,
    symbol: str,
) -> str:
    """Build the user prompt, sanitizing all external text."""
    parts: list[str] = []

    parts.append(f"Symbol: {symbol}")
    parts.append(f"Macro blackout active: {is_macro_blackout}")

    # Sanitize news items (external text — untrusted data)
    if news_items:
        sanitized_news = []
        for item in news_items[:10]:  # cap at 10 items
            title = sanitize_for_prompt(str(item.get("title", "")), max_length=200, label="title")
            summary = sanitize_for_prompt(
                str(item.get("summary", "")), max_length=300, label="summary"
            )
            sanitized_news.append(f"{title}\n{summary}")
        parts.append("News items:\n" + "\n---\n".join(sanitized_news))

    # Sanitize macro events (external text — untrusted data)
    if macro_events:
        sanitized_events = []
        for event in macro_events[:5]:  # cap at 5 events
            name = sanitize_for_prompt(str(event.get("name", "")), max_length=100, label="event")
            sanitized_events.append(
                f"{name} importance={event.get('importance', 'unknown')} ts={event.get('ts', '')}"
            )
        parts.append("Macro events:\n" + "\n".join(sanitized_events))

    # Cross-asset data is structured numeric data — safe to include directly
    if cross_asset:
        parts.append(f"Cross-asset snapshot: {json.dumps(cross_asset)}")

    return "\n\n".join(parts)


class MacroNewsAnalyst:
    """LLM-powered macro and news analyst.

    Parameters
    ----------
    llm:
        LlmClient instance for making API calls.
    model:
        Optional model override (defaults to settings.models.screening).
    """

    def __init__(self, llm: LlmClient, model: str | None = None) -> None:
        self._llm = llm
        self._model = model  # None → LlmClient uses its default

    def analyze(
        self,
        news_items: list[dict[str, Any]],
        macro_events: list[dict[str, Any]],
        cross_asset: dict[str, Any],
        is_macro_blackout: bool,
        symbol: str,
    ) -> MacroNewsReport:
        """Call LLM, parse response as MacroNewsReport.

        On any parse failure → return neutral MacroNewsReport with data_missing=['parse_error'].
        """
        prompt = _build_prompt(
            news_items=news_items,
            macro_events=macro_events,
            cross_asset=cross_asset,
            is_macro_blackout=is_macro_blackout,
            symbol=symbol,
        )

        try:
            raw = self._llm.complete(
                purpose=_PURPOSE,
                system=MACRO_NEWS_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                model=self._model,
                max_tokens=512,
            )
            json_str = _extract_json(raw)
            return MacroNewsReport.model_validate_json(json_str)
        except (ValidationError, ValueError, TypeError) as exc:
            logger.warning("MacroNewsAnalyst: parse error: %s", exc)
            return _neutral_report(data_missing=["parse_error"])
        except Exception as exc:
            logger.warning("MacroNewsAnalyst: unexpected error: %s", exc)
            return _neutral_report(data_missing=["analyst_error"])


def _extract_json(raw: str) -> str:
    """Extract JSON from a response that may be wrapped in markdown code fences."""
    text = raw.strip()
    # Strip ```json ... ``` or ``` ... ``` fences
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove first line (fence) and last line (closing fence)
        inner = lines[1:-1] if len(lines) > 2 else lines[1:]
        text = "\n".join(inner).strip()
    return text
