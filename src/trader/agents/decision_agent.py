"""Phase 6: DecisionAgent — calls the strong LLM model and validates the BrainDecision."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from trader.agents.prompts import DECISION_SYSTEM
from trader.llm.schemas import BrainDecision, DerivativesReport, MacroNewsReport, TechnicalReport

if TYPE_CHECKING:
    from trader.llm.client import LlmClient

logger = logging.getLogger(__name__)

_PURPOSE = "decision_agent"


def _no_trade(thesis: str) -> BrainDecision:
    return BrainDecision(action="NO_TRADE", thesis=thesis)


def _extract_json(raw: str) -> str:
    """Extract JSON from a response that may be wrapped in markdown code fences."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:-1] if len(lines) > 2 else lines[1:]
        text = "\n".join(inner).strip()
    return text


def _compute_prompt_hash(prompt: str) -> str:
    """SHA-256 hash of the prompt (first 16 hex chars for compactness)."""
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


class DecisionAgent:
    """Calls the strong LLM model and validates the BrainDecision output.

    Parameters
    ----------
    llm:
        LlmClient instance.
    model:
        Optional model override. Defaults to settings.models.decision (strong model).
    """

    def __init__(self, llm: LlmClient, model: str | None = None) -> None:
        self._llm = llm
        # Use settings decision model (strong model) if not overridden
        self._model = model or llm._settings.app.models.decision

    def decide(
        self,
        macro_report: MacroNewsReport,
        technical_report: TechnicalReport,
        derivatives_report: DerivativesReport,
        feature_pack: dict[str, Any],
        confluence_score: int,
        ksor_lessons: list[dict[str, Any]],
        account_state: dict[str, Any],
        symbol: str,
        cycle_id: str,
    ) -> BrainDecision:
        """Call strong LLM model. Parse response as BrainDecision.

        On parse failure → return BrainDecision(action='NO_TRADE', ...) with parse error noted.
        """
        prompt = self._build_prompt(
            macro_report=macro_report,
            technical_report=technical_report,
            derivatives_report=derivatives_report,
            feature_pack=feature_pack,
            confluence_score=confluence_score,
            ksor_lessons=ksor_lessons,
            account_state=account_state,
            symbol=symbol,
            cycle_id=cycle_id,
        )

        prompt_hash = _compute_prompt_hash(prompt)
        raw_response = ""

        try:
            raw_response = self._llm.complete(
                purpose=_PURPOSE,
                system=DECISION_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                model=self._model,
                max_tokens=1024,
            )
            json_str = _extract_json(raw_response)
            decision = BrainDecision.model_validate_json(json_str)

            # Inject prompt_hash into thesis for traceability
            logger.info(
                "DecisionAgent: cycle=%s symbol=%s action=%s confidence=%.2f prompt_hash=%s",
                cycle_id,
                symbol,
                decision.action,
                decision.confidence,
                prompt_hash,
            )

            return decision

        except (ValidationError, ValueError, TypeError) as exc:
            logger.warning(
                "DecisionAgent: parse error cycle=%s: %s | raw=%r",
                cycle_id,
                exc,
                raw_response[:200],
            )
            return _no_trade(thesis=f"parse_error: {exc}")
        except Exception as exc:
            logger.warning("DecisionAgent: unexpected error cycle=%s: %s", cycle_id, exc)
            return _no_trade(thesis=f"agent_error: {exc}")

    def _build_prompt(
        self,
        macro_report: MacroNewsReport,
        technical_report: TechnicalReport,
        derivatives_report: DerivativesReport,
        feature_pack: dict[str, Any],
        confluence_score: int,
        ksor_lessons: list[dict[str, Any]],
        account_state: dict[str, Any],
        symbol: str,
        cycle_id: str,
    ) -> str:
        parts: list[str] = []

        parts.append(f"Cycle ID: {cycle_id}")
        parts.append(f"Symbol: {symbol}")
        parts.append(f"Confluence score: {confluence_score}")

        parts.append(f"Macro report:\n{json.dumps(macro_report.model_dump(), default=str)}")
        parts.append(f"Technical report:\n{json.dumps(technical_report.model_dump(), default=str)}")
        parts.append(
            f"Derivatives report:\n{json.dumps(derivatives_report.model_dump(), default=str)}"
        )

        # Feature pack is structured numeric data — safe to include directly
        parts.append(f"Feature pack (summary):\n{json.dumps(feature_pack, default=str)}")

        # KSOR lessons — structured data from our own DB
        parts.append(f"KSOR lessons to consider:\n{json.dumps(ksor_lessons, default=str)}")

        # Account state — structured numeric data
        parts.append(f"Account state:\n{json.dumps(account_state, default=str)}")

        return "\n\n".join(parts)
