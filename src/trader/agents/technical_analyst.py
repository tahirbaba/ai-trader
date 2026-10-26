"""Phase 6: TechnicalAnalyst — LLM-powered technical analysis."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from trader.agents.prompts import TECHNICAL_SYSTEM
from trader.llm.schemas import TechnicalReport

if TYPE_CHECKING:
    from trader.llm.client import LlmClient

logger = logging.getLogger(__name__)

_PURPOSE = "technical_analyst"


def _neutral_report(data_missing: list[str]) -> TechnicalReport:
    return TechnicalReport(
        agent="TechnicalAnalyst",
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


class TechnicalAnalyst:
    """LLM-powered technical analyst.

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

    def analyze(self, feature_pack_dict: dict[str, Any], symbol: str) -> TechnicalReport:
        """Analyze technical indicators from feature pack.

        On parse failure → return neutral TechnicalReport.
        """
        # Feature data is structured numeric data — safe to include as JSON
        prompt = f"Symbol: {symbol}\n\nFeature pack:\n{json.dumps(feature_pack_dict, default=str)}"

        try:
            raw = self._llm.complete(
                purpose=_PURPOSE,
                system=TECHNICAL_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                model=self._model,
                max_tokens=512,
            )
            json_str = _extract_json(raw)
            return TechnicalReport.model_validate_json(json_str)
        except (ValidationError, ValueError, TypeError) as exc:
            logger.warning("TechnicalAnalyst: parse error: %s", exc)
            return _neutral_report(data_missing=["parse_error"])
        except Exception as exc:
            logger.warning("TechnicalAnalyst: unexpected error: %s", exc)
            return _neutral_report(data_missing=["analyst_error"])
