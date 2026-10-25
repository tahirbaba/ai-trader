"""Phase 6: Tests for LLM Pydantic schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trader.llm.schemas import BrainDecision, PositionReview


class TestBrainDecision:
    def test_valid_no_trade(self):
        d = BrainDecision(action="NO_TRADE")
        assert d.action == "NO_TRADE"
        assert d.confidence == 0.0
        assert d.stop_loss == 0.0

    def test_valid_long_decision(self):
        d = BrainDecision(
            action="LONG",
            symbol="BTC/USDT",
            entry_price=50000.0,
            stop_loss=48000.0,
            take_profit=55000.0,
            confidence=0.75,
            thesis="strong breakout",
            invalidation="price closes below 48000",
            expected_risk_reward=2.5,
        )
        assert d.action == "LONG"
        assert d.stop_loss == 48000.0

    def test_valid_short_decision(self):
        d = BrainDecision(
            action="SHORT",
            symbol="ETH/USDT",
            entry_price=3000.0,
            stop_loss=3200.0,
            take_profit=2500.0,
            confidence=0.70,
        )
        assert d.action == "SHORT"

    def test_valid_hold(self):
        d = BrainDecision(action="HOLD")
        assert d.action == "HOLD"

    def test_valid_close(self):
        d = BrainDecision(action="CLOSE")
        assert d.action == "CLOSE"

    def test_missing_action_raises(self):
        with pytest.raises(ValidationError):
            BrainDecision.model_validate({})

    def test_invalid_action_raises(self):
        with pytest.raises(ValidationError):
            BrainDecision(action="INVALID")

    def test_long_with_zero_stop_loss_valid(self):
        """RiskGate enforces stop_loss, not schema."""
        d = BrainDecision(action="LONG", symbol="BTC/USDT", stop_loss=0.0, confidence=0.8)
        assert d.stop_loss == 0.0

    def test_high_confidence_accepted(self):
        """Schema allows confidence > 0.9 (RiskGate may filter it)."""
        d = BrainDecision(action="LONG", confidence=0.95)
        assert d.confidence == 0.95

    def test_from_json(self):
        json_str = '{"action": "NO_TRADE", "symbol": "BTC/USDT", "thesis": "low confluence"}'
        d = BrainDecision.model_validate_json(json_str)
        assert d.action == "NO_TRADE"

    def test_signals_used_defaults_empty(self):
        d = BrainDecision(action="NO_TRADE")
        assert d.signals_used == []

    def test_lessons_considered_defaults_empty(self):
        d = BrainDecision(action="NO_TRADE")
        assert d.lessons_considered == []

    def test_data_missing_defaults_empty(self):
        d = BrainDecision(action="NO_TRADE")
        assert d.data_missing == []


class TestPositionReview:
    def test_valid_hold(self):
        r = PositionReview(action="HOLD", reason="no change needed")
        assert r.action == "HOLD"
        assert r.new_stop_loss is None

    def test_valid_close(self):
        r = PositionReview(action="CLOSE", reason="target reached")
        assert r.action == "CLOSE"

    def test_valid_tighten_stop(self):
        r = PositionReview(action="TIGHTEN_STOP", new_stop_loss=49000.0, reason="lock in profits")
        assert r.action == "TIGHTEN_STOP"
        assert r.new_stop_loss == 49000.0

    def test_invalid_action_raises(self):
        with pytest.raises(ValidationError):
            PositionReview(action="WIDEN_STOP")

    def test_long_action_raises(self):
        with pytest.raises(ValidationError):
            PositionReview(action="LONG")

    def test_tighten_stop_with_no_new_sl_is_valid(self):
        """Schema accepts this; caller validates the tightness logic."""
        r = PositionReview(action="TIGHTEN_STOP", new_stop_loss=None)
        assert r.new_stop_loss is None

    def test_missing_action_raises(self):
        with pytest.raises(ValidationError):
            PositionReview.model_validate({})

    def test_reason_defaults_empty(self):
        r = PositionReview(action="HOLD")
        assert r.reason == ""
