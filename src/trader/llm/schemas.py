"""Phase 6: Pydantic models for all LLM I/O."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class AnalystReport(BaseModel):
    """Base report returned by every specialist analyst."""

    agent: str
    bias: str  # "bullish" | "bearish" | "neutral"
    confidence: float  # 0.0–1.0
    key_points: list[str] = []
    risks: list[str] = []
    data_used: list[str] = []
    data_missing: list[str] = []


class MacroNewsReport(AnalystReport):
    """Report from MacroNewsAnalyst."""

    is_blackout: bool = False
    blackout_reason: str = ""
    regime_shift_detected: bool = False
    news_shock: bool = False


class TechnicalReport(AnalystReport):
    """Report from TechnicalAnalyst."""


class DerivativesReport(AnalystReport):
    """Report from DerivativesAnalyst."""


class BrainDecision(BaseModel):
    """Validated output schema for the DecisionAgent."""

    action: Literal["LONG", "SHORT", "CLOSE", "HOLD", "NO_TRADE"]
    symbol: str = ""
    entry_type: str = "limit"
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0  # single TP
    invalidation: str = ""
    confidence: float = 0.0
    thesis: str = ""
    signals_used: list[str] = []
    lessons_considered: list[dict[str, Any]] = []
    data_missing: list[str] = []
    why_not_no_trade: str = ""
    expected_risk_reward: float = 0.0


class PositionReview(BaseModel):
    """Validated output schema for PositionReviewAgent."""

    action: Literal["HOLD", "CLOSE", "TIGHTEN_STOP"]
    new_stop_loss: float | None = None  # only for TIGHTEN_STOP; must be tighter
    reason: str = ""


class LessonCandidate(BaseModel):
    """A single lesson candidate proposed by the LLM post-trade reviewer."""

    condition: str = ""
    rule: dict[str, Any] = {}
    effect: str = ""
    tags: list[str] = []

    def model_post_init(self, __context: Any) -> None:
        from trader.learning.constants import TIGHTEN_ONLY_FORBIDDEN_KEYS  # noqa: PLC0415

        for key in TIGHTEN_ONLY_FORBIDDEN_KEYS:
            if key in self.rule:
                raise ValueError(
                    f"LessonCandidate rule contains forbidden key {key!r} "
                    "that would loosen safety constraints (tighten-only policy)"
                )


class PostTradeReviewOutput(BaseModel):
    """Validated output schema for the PostTradeReview LLM call.

    Parse failure (ValidationError) must be treated as NO corrective lesson.
    """

    outcome_class: Literal[
        "good_decision_good_outcome",
        "good_decision_bad_outcome",
        "bad_decision_good_outcome",
        "bad_decision_bad_outcome",
    ]
    what_went_right: str = ""
    what_went_wrong: str = ""
    lesson_candidates: list[LessonCandidate] = []

    def model_post_init(self, __context: Any) -> None:
        if len(self.lesson_candidates) > 2:
            object.__setattr__(self, "lesson_candidates", self.lesson_candidates[:2])
