"""Phase 6: DerivativesAnalyst — LLM-powered derivatives/orderflow analysis."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from trader.agents.prompts import DERIVATIVES_SYSTEM
from trader.llm.schemas import DerivativesReport

if TYPE_CHECKING:
    from trader.llm.client import LlmClient

logger = logging.getLogger(__name__)

_PURPOSE = "derivatives_analyst"


def _neutral_report(data_missing: list[str]) -> DerivativesReport:
    return DerivativesReport(
        agent="DerivativesAnalyst",
        bias="neutral",
        confidence=0.0,
        data_missing=data_missing,
    )


def _extract_json(raw: str) -> str:
    """Extract JSON from a response that may be wrapped in markdown code fences."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:-1] if len(lines) > 2 else lines[1:]
        text = "\n".join(inner).strip()
    return text


class DerivativesAnalyst:
    """LLM-powered derivatives and orderflow analyst.

    Parameters
    ----------
    llm:
        LlmClient instance for making API calls.
    model:
        Optional model override (defaults to settings.models.screening).
    """

    def __init__(self, llm: LlmClient, model: str | None = None) -> None:
        self._llm = llm
        self._model = model

    def analyze(
        self, derivatives: dict[str, Any], orderflow: dict[str, Any], symbol: str
    ) -> DerivativesReport:
        """Analyze derivatives and orderflow data.

        On parse failure → return neutral DerivativesReport.
        """
        prompt = (
            f"Symbol: {symbol}\n\n"
            f"Derivatives data:\n{json.dumps(derivatives, default=str)}\n\n"
            f"Orderflow data:\n{json.dumps(orderflow, default=str)}"
        )

        try:
            raw = self._llm.complete(
                purpose=_PURPOSE,
                system=DERIVATIVES_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                model=self._model,
                max_tokens=512,
            )
            json_str = _extract_json(raw)
            return DerivativesReport.model_validate_json(json_str)
        except (ValidationError, ValueError, TypeError) as exc:
            logger.warning("DerivativesAnalyst: parse error: %s", exc)
            return _neutral_report(data_missing=["parse_error"])
        except Exception as exc:
            logger.warning("DerivativesAnalyst: unexpected error: %s", exc)
            return _neutral_report(data_missing=["analyst_error"])
