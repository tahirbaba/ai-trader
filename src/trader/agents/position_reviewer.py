"""Phase 6: PositionReviewAgent — reviews open positions via LLM."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from trader.agents.prompts import POSITION_REVIEW_SYSTEM
from trader.llm.schemas import PositionReview

if TYPE_CHECKING:
    from trader.llm.client import LlmClient

logger = logging.getLogger(__name__)

_PURPOSE = "position_reviewer"


def _hold_review(reason: str) -> PositionReview:
    return PositionReview(action="HOLD", reason=reason)


def _extract_json(raw: str) -> str:
    """Extract JSON from a response that may be wrapped in markdown code fences."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:-1] if len(lines) > 2 else lines[1:]
        text = "\n".join(inner).strip()
    return text


class PositionReviewAgent:
    """Reviews an open position and recommends HOLD, CLOSE, or TIGHTEN_STOP.

    Validates that new_stop_loss (if provided) is tighter than current stop.
    On schema violation → returns PositionReview(action='HOLD', reason='parse_error').

    Parameters
    ----------
    llm:
        LlmClient instance.
    model:
        Optional model override.
    """

    def __init__(self, llm: LlmClient, model: str | None = None) -> None:
        self._llm = llm
        self._model = model or llm._settings.app.models.review

    def review(
        self,
        position: dict[str, Any],
        feature_pack: dict[str, Any],
        symbol: str,
    ) -> PositionReview:
        """Review an open position.

        May only output HOLD, CLOSE, or TIGHTEN_STOP.
        Validates that new_stop_loss (if provided) is tighter than current stop.
        On schema violation → return PositionReview(action='HOLD', reason='parse_error').
        """
        prompt = (
            f"Symbol: {symbol}\n\n"
            f"Open position:\n{json.dumps(position, default=str)}\n\n"
            f"Current market features:\n{json.dumps(feature_pack, default=str)}"
        )

        raw_response = ""
        try:
            raw_response = self._llm.complete(
                purpose=_PURPOSE,
                system=POSITION_REVIEW_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                model=self._model,
                max_tokens=512,
            )
            json_str = _extract_json(raw_response)
            review = PositionReview.model_validate_json(json_str)

            # Validate stop tightness for TIGHTEN_STOP
            if review.action == "TIGHTEN_STOP":
                if review.new_stop_loss is None:
                    logger.warning(
                        "PositionReviewAgent: TIGHTEN_STOP with no new_stop_loss — defaulting HOLD"
                    )
                    return _hold_review("tighten_stop_missing_value")

                current_stop = position.get("stop_loss", 0.0)
                side = str(position.get("side", "long")).lower()

                # For long: new SL must be > current SL (closer to entry from below)
                # For short: new SL must be < current SL (closer to entry from above)
                if side == "long" and review.new_stop_loss <= current_stop:
                    logger.warning(
                        "PositionReviewAgent: TIGHTEN_STOP for long with new_sl=%.4f <= current_sl=%.4f",
                        review.new_stop_loss,
                        current_stop,
                    )
                    return _hold_review("tighten_stop_not_tighter")
                if side == "short" and review.new_stop_loss >= current_stop:
                    logger.warning(
                        "PositionReviewAgent: TIGHTEN_STOP for short with new_sl=%.4f >= current_sl=%.4f",
                        review.new_stop_loss,
                        current_stop,
                    )
                    return _hold_review("tighten_stop_not_tighter")

            return review

        except (ValidationError, ValueError, TypeError) as exc:
            logger.warning("PositionReviewAgent: parse error: %s | raw=%r", exc, raw_response[:200])
            return _hold_review("parse_error")
        except Exception as exc:
            logger.warning("PositionReviewAgent: unexpected error: %s", exc)
            return _hold_review("agent_error")
