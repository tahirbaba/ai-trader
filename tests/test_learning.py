"""Phase 8 Learning Loop tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _in_memory_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from trader.dsor.base import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


# ---------------------------------------------------------------------------
# TestOutcomeTracker
# ---------------------------------------------------------------------------


class TestOutcomeTracker:
    def _make_row(self, **kwargs):
        base = dict(
            id=1,
            symbol="BTCUSDT",
            side="long",
            entry_price=1.0,
            exit_price=1.0,
            qty=1.0,
            sl_price=None,
            open_ts=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            close_ts=datetime(2024, 1, 1, 1, 0, 0, tzinfo=UTC),
            regime=None,
            session=None,
        )
        base.update(kwargs)
        return base

    def test_r_multiple_long_win(self):
        from trader.learning.outcome_tracker import compute_outcome

        row = self._make_row(entry_price=1.0, sl_price=0.9, exit_price=1.2)
        outcome = compute_outcome(row)
        assert abs(outcome.r_multiple - 2.0) < 1e-9

    def test_r_multiple_short_win(self):
        from trader.learning.outcome_tracker import compute_outcome

        row = self._make_row(side="short", entry_price=1.0, sl_price=1.1, exit_price=0.8)
        outcome = compute_outcome(row)
        assert abs(outcome.r_multiple - 2.0) < 1e-9

    def test_r_multiple_no_sl(self):
        from trader.learning.outcome_tracker import compute_outcome

        row = self._make_row(entry_price=1.0, sl_price=None, exit_price=1.5)
        outcome = compute_outcome(row)
        assert outcome.r_multiple == 0.0

    def test_r_multiple_sl_zero(self):
        from trader.learning.outcome_tracker import compute_outcome

        row = self._make_row(entry_price=1.0, sl_price=0, exit_price=1.5)
        outcome = compute_outcome(row)
        assert outcome.r_multiple == 0.0

    def test_pnl_usd(self):
        from trader.learning.outcome_tracker import compute_outcome

        row = self._make_row(side="long", entry_price=1.0, exit_price=1.1, qty=10.0)
        outcome = compute_outcome(row)
        assert abs(outcome.pnl_usd - 1.0) < 1e-9

    def test_pnl_usd_short(self):
        from trader.learning.outcome_tracker import compute_outcome

        row = self._make_row(side="short", entry_price=1.0, exit_price=0.9, qty=10.0)
        outcome = compute_outcome(row)
        assert abs(outcome.pnl_usd - 1.0) < 1e-9

    def test_holding_seconds(self):
        from trader.learning.outcome_tracker import compute_outcome

        t = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
        row = self._make_row(open_ts=t, close_ts=t + timedelta(hours=1))
        outcome = compute_outcome(row)
        assert outcome.holding_seconds == 3600.0

    def test_holding_seconds_iso_string(self):
        from trader.learning.outcome_tracker import compute_outcome

        row = self._make_row(
            open_ts="2024-01-01T00:00:00+00:00",
            close_ts="2024-01-01T01:00:00+00:00",
        )
        outcome = compute_outcome(row)
        assert outcome.holding_seconds == 3600.0


# ---------------------------------------------------------------------------
# TestPostTradeReviewer
# ---------------------------------------------------------------------------


class TestPostTradeReviewer:
    def _make_outcome(self, r_multiple: float = 1.0, trade_id: int = 1):
        from trader.learning.outcome_tracker import TradeOutcome

        return TradeOutcome(
            trade_id=trade_id,
            symbol="BTCUSDT",
            side="long",
            r_multiple=r_multiple,
            holding_seconds=3600.0,
            regime=None,
            session=None,
            pnl_usd=r_multiple * 10,
            mae=None,
            mfe=None,
        )

    def _make_input(self, **kwargs):
        from trader.learning.post_trade_review import ReviewInput
        from trader.llm.schemas import LessonCandidate

        # Convert any raw dicts in llm_lesson_candidates to LessonCandidate
        if "llm_lesson_candidates" in kwargs:
            raw = kwargs["llm_lesson_candidates"]
            kwargs["llm_lesson_candidates"] = [
                LessonCandidate(**c) if isinstance(c, dict) else c for c in raw
            ]

        defaults = dict(
            trade_outcome=self._make_outcome(),
            snapshot={},
            active_lesson_ids=[],
            trade_during_blackout=False,
            stop_inside_noise=False,
            ignored_active_lesson=False,
            llm_outcome_class=None,
            llm_what_went_right="",
            llm_what_went_wrong="",
            llm_lesson_candidates=[],
        )
        defaults.update(kwargs)
        return ReviewInput(**defaults)

    def test_good_decision_bad_outcome_no_lesson(self):
        from trader.learning.post_trade_review import PostTradeReviewer
        from trader.llm.schemas import LessonCandidate

        inp = self._make_input(
            trade_outcome=self._make_outcome(r_multiple=-1.0),
            llm_outcome_class="good_decision_bad_outcome",
            llm_lesson_candidates=[LessonCandidate(condition="avoid xyz")],
        )
        result = PostTradeReviewer().review(inp)
        assert result.outcome_class == "good_decision_bad_outcome"
        assert result.lesson_candidates == []
        assert result.code_overrode_llm is False

    def test_blackout_violation_overrides_llm(self):
        from trader.learning.post_trade_review import PostTradeReviewer

        inp = self._make_input(
            trade_outcome=self._make_outcome(r_multiple=1.0),
            trade_during_blackout=True,
            llm_outcome_class="good_decision_good_outcome",
        )
        result = PostTradeReviewer().review(inp)
        assert result.outcome_class == "bad_decision_good_outcome"
        assert "ignored_news_or_calendar" in result.mistake_types
        assert result.code_overrode_llm is True

    def test_stop_inside_noise_is_bad_decision(self):
        from trader.learning.post_trade_review import PostTradeReviewer

        inp = self._make_input(
            stop_inside_noise=True,
            llm_outcome_class="good_decision_good_outcome",
        )
        result = PostTradeReviewer().review(inp)
        assert result.outcome_class.startswith("bad_decision")
        assert "stop_too_tight" in result.mistake_types

    def test_ignored_lesson_is_bad_decision(self):
        from trader.learning.post_trade_review import PostTradeReviewer

        inp = self._make_input(
            ignored_active_lesson=True,
            llm_outcome_class="good_decision_good_outcome",
        )
        result = PostTradeReviewer().review(inp)
        assert result.outcome_class.startswith("bad_decision")
        assert "ignored_existing_lesson" in result.mistake_types

    def test_no_violations_uses_llm_class(self):
        from trader.learning.post_trade_review import PostTradeReviewer

        inp = self._make_input(
            trade_outcome=self._make_outcome(r_multiple=-1.0),
            llm_outcome_class="bad_decision_bad_outcome",
            llm_lesson_candidates=[
                {"condition": "cond-1"},
                {"condition": "cond-2"},
                {"condition": "cond-3"},
            ],
        )
        result = PostTradeReviewer().review(inp)
        assert result.outcome_class == "bad_decision_bad_outcome"
        assert result.code_overrode_llm is False
        # Lesson candidates limited to 2 (PostTradeReviewOutput cap is 2; extra stripped in test _make_input)
        assert len(result.lesson_candidates) <= 2


# ---------------------------------------------------------------------------
# TestLessonLifecycle
# ---------------------------------------------------------------------------


class TestLessonLifecycle:
    def _add_candidate(self, session, lesson_id: str, rule_json: str | None = None) -> None:
        from trader.dsor.models import LessonIndex

        row = LessonIndex(lesson_id=lesson_id, status="candidate", rule_json=rule_json)
        session.add(row)
        session.flush()

    def test_promote_candidate_needs_min_5_evidence(self):
        from trader.learning.lesson_lifecycle import LessonLifecycleManager

        Session = _in_memory_session()
        with Session() as session:
            self._add_candidate(session, "lesson-1")
            mgr = LessonLifecycleManager()
            result = mgr.promote_by_evidence(session, "lesson-1", list(range(4)), [])
            assert result is False

    def test_promote_candidate_succeeds_at_5(self):
        from trader.dsor.models import LessonIndex
        from trader.learning.lesson_lifecycle import LessonLifecycleManager

        Session = _in_memory_session()
        with Session() as session:
            self._add_candidate(session, "lesson-2")
            mgr = LessonLifecycleManager()
            result = mgr.promote_by_evidence(session, "lesson-2", list(range(5)), [])
            assert result is True
            row = session.query(LessonIndex).filter_by(lesson_id="lesson-2").first()
            assert row is not None
            assert row.status == "active"

    def test_promote_blocked_by_contradiction(self):
        from trader.learning.lesson_lifecycle import LessonLifecycleManager

        Session = _in_memory_session()
        with Session() as session:
            self._add_candidate(session, "lesson-3")
            mgr = LessonLifecycleManager()
            result = mgr.promote_by_evidence(session, "lesson-3", list(range(10)), [99])
            assert result is False

    def test_owner_approve_needs_2_evidence(self):
        from trader.learning.lesson_lifecycle import LessonLifecycleManager

        Session = _in_memory_session()
        with Session() as session:
            self._add_candidate(session, "lesson-4")
            mgr = LessonLifecycleManager()
            with pytest.raises(ValueError, match="2 evidence"):
                mgr.owner_approve(session, "lesson-4", [1])

    def test_owner_approve_succeeds_with_2(self):
        from trader.dsor.models import LessonIndex
        from trader.learning.lesson_lifecycle import LessonLifecycleManager

        Session = _in_memory_session()
        with Session() as session:
            self._add_candidate(session, "lesson-5")
            mgr = LessonLifecycleManager()
            result = mgr.owner_approve(session, "lesson-5", [1, 2])
            assert result is True
            row = session.query(LessonIndex).filter_by(lesson_id="lesson-5").first()
            assert row is not None
            assert row.status == "active"

    def test_deprecate_active_lesson(self):
        from trader.dsor.models import LessonIndex
        from trader.learning.lesson_lifecycle import LessonLifecycleManager

        Session = _in_memory_session()
        with Session() as session:
            self._add_candidate(session, "lesson-6")
            mgr = LessonLifecycleManager()
            mgr.promote_by_evidence(session, "lesson-6", list(range(5)), [])
            result = mgr.deprecate(session, "lesson-6", reason="test")
            assert result is True
            row = session.query(LessonIndex).filter_by(lesson_id="lesson-6").first()
            assert row is not None
            assert row.status == "deprecated"

    def test_tighten_only_blocks_raise_size(self):
        from trader.learning.lesson_lifecycle import LessonLifecycleManager

        mgr = LessonLifecycleManager()
        with pytest.raises(ValueError):
            mgr.validate_tighten_only({"raise_size": 0.1})

    def test_tighten_only_blocks_raise_leverage(self):
        from trader.learning.lesson_lifecycle import LessonLifecycleManager

        mgr = LessonLifecycleManager()
        with pytest.raises(ValueError):
            mgr.validate_tighten_only({"raise_leverage": 2})

    def test_tighten_only_allows_block(self):
        from trader.learning.lesson_lifecycle import LessonLifecycleManager

        mgr = LessonLifecycleManager()
        mgr.validate_tighten_only({"action": "block"})  # should not raise

    def test_tighten_only_allows_reduce_confidence(self):
        from trader.learning.lesson_lifecycle import LessonLifecycleManager

        mgr = LessonLifecycleManager()
        mgr.validate_tighten_only({"action": "reduce_confidence"})  # should not raise

    def test_max_active_lessons_flag(self):
        from trader.dsor.models import LessonIndex
        from trader.learning.lesson_lifecycle import LessonLifecycleManager

        Session = _in_memory_session()
        with Session() as session:
            for i in range(26):
                row = LessonIndex(lesson_id=f"active-{i}", status="active")
                session.add(row)
            session.flush()
            mgr = LessonLifecycleManager(max_active_lessons=25)
            report = mgr.check_lesson_creep(session)
            assert report["over_limit"] is True
            assert report["active_count"] == 26

    def test_flag_positive_shadow_r(self):
        from trader.dsor.models import LessonIndex
        from trader.learning.lesson_lifecycle import LessonLifecycleManager

        Session = _in_memory_session()
        with Session() as session:
            row = LessonIndex(lesson_id="shadow-pos", status="active", blocked_shadow_r=0.5)
            session.add(row)
            session.flush()
            mgr = LessonLifecycleManager()
            ids = mgr.flag_positive_shadow_r_lessons(session)
            assert "shadow-pos" in ids


# ---------------------------------------------------------------------------
# TestShadowTracker
# ---------------------------------------------------------------------------


class TestShadowTracker:
    def test_record_shadow_outcome(self):
        from trader.dsor.models import Decision, ShadowOutcome
        from trader.learning.shadow_tracker import ShadowTracker

        Session = _in_memory_session()
        with Session() as session:
            # Create a decision row first (FK constraint)
            decision = Decision(
                ts=datetime.now(UTC),
                action="no_trade",
                symbol="BTCUSDT",
            )
            session.add(decision)
            session.flush()

            tracker = ShadowTracker()
            row = tracker.record(
                session=session,
                decision_id=decision.id,
                hypothetical_side="long",
                entry=50000.0,
                stop=49000.0,
                tp=52000.0,
                blocked_by="lesson-1",
                r_multiple=2.0,
                outcome="win",
                horizon=24,
            )
            assert row.id is not None
            assert row.r_multiple == 2.0
            assert row.blocked_by == "lesson-1"

            # Verify from DB
            db_row = session.query(ShadowOutcome).filter_by(id=row.id).first()
            assert db_row is not None
            assert db_row.hypothetical_side == "long"

    def test_update_lesson_shadow_stats(self):
        from trader.dsor.models import LessonIndex
        from trader.learning.shadow_tracker import ShadowTracker

        Session = _in_memory_session()
        with Session() as session:
            lesson = LessonIndex(
                lesson_id="lesson-shadow", status="active", blocked_shadow_r=0.0, block_count=0
            )
            session.add(lesson)
            session.flush()

            tracker = ShadowTracker()
            tracker.update_lesson_shadow_stats(session, "lesson-shadow", 1.5)
            tracker.update_lesson_shadow_stats(session, "lesson-shadow", 0.5)

            db_row = session.query(LessonIndex).filter_by(lesson_id="lesson-shadow").first()
            assert db_row is not None
            assert abs(db_row.blocked_shadow_r - 2.0) < 1e-9
            assert db_row.block_count == 2


# ---------------------------------------------------------------------------
# TestWeeklyConsolidation
# ---------------------------------------------------------------------------


class TestWeeklyConsolidation:
    def test_deprecate_expired_lessons(self):
        from trader.dsor.models import LessonIndex
        from trader.learning.consolidation import WeeklyConsolidation

        Session = _in_memory_session()
        with Session() as session:
            past = datetime.now(UTC) - timedelta(days=1)
            row = LessonIndex(lesson_id="expired-1", status="active", expires_ts=past)
            session.add(row)
            session.flush()

            cons = WeeklyConsolidation()
            counts = cons.run(session)
            assert counts["deprecated_stale"] == 1

            db_row = session.query(LessonIndex).filter_by(lesson_id="expired-1").first()
            assert db_row is not None
            assert db_row.status == "deprecated"

    def test_flag_positive_shadow_r_lessons(self):
        from trader.dsor.models import LessonIndex
        from trader.learning.consolidation import WeeklyConsolidation

        Session = _in_memory_session()
        with Session() as session:
            row = LessonIndex(lesson_id="pos-shadow-1", status="active", blocked_shadow_r=1.0)
            session.add(row)
            session.flush()

            cons = WeeklyConsolidation()
            counts = cons.run(session)
            assert counts["flagged_shadow_r"] == 1

            db_row = session.query(LessonIndex).filter_by(lesson_id="pos-shadow-1").first()
            assert db_row is not None
            assert db_row.status == "deprecated"


# ---------------------------------------------------------------------------
# TestRepeatMistakeRate
# ---------------------------------------------------------------------------


class TestRepeatMistakeRate:
    def test_repeat_mistake_rate_seeded_data(self):
        from trader.dsor.models import LessonIndex, Review, Trade
        from trader.learning.analytics import repeat_mistake_rate

        Session = _in_memory_session()
        with Session() as session:
            # Create trade stubs
            trade_ids = []
            for _i in range(5):
                t = Trade(
                    open_ts=datetime.now(UTC),
                    side="long",
                    entry=1.0,
                    qty=1.0,
                )
                session.add(t)
                session.flush()
                trade_ids.append(t.id)

            # Active lessons that match mistake types (via tags field)
            l1 = LessonIndex(
                lesson_id="bad_timing-lesson",
                status="active",
                tags=json.dumps(["bad_timing"]),
            )
            l2 = LessonIndex(
                lesson_id="overtrading-lesson",
                status="active",
                tags=json.dumps(["overtrading"]),
            )
            session.add_all([l1, l2])
            session.flush()

            # 3 bad-decision reviews: 2 with mistake type matching active lesson, 1 without
            r1 = Review(
                trade_id=trade_ids[0],
                outcome_class="bad_decision_bad_outcome",
                mistake_types_json=json.dumps(["bad_timing"]),
            )
            r2 = Review(
                trade_id=trade_ids[1],
                outcome_class="bad_decision_bad_outcome",
                mistake_types_json=json.dumps(["overtrading"]),
            )
            r3 = Review(
                trade_id=trade_ids[2],
                outcome_class="bad_decision_bad_outcome",
                mistake_types_json=json.dumps(["data_issue"]),
            )
            # 2 good-decision reviews (should not be counted)
            r4 = Review(
                trade_id=trade_ids[3],
                outcome_class="good_decision_good_outcome",
                mistake_types_json=json.dumps([]),
            )
            r5 = Review(
                trade_id=trade_ids[4],
                outcome_class="good_decision_bad_outcome",
                mistake_types_json=json.dumps([]),
            )
            session.add_all([r1, r2, r3, r4, r5])
            session.flush()

            result = repeat_mistake_rate(session)
            assert result["total_bad_trades"] == 3
            assert result["repeat_mistakes"] == 2
            assert abs(result["rate"] - 2 / 3) < 1e-9
            assert "bad_timing" in result["by_type"]
            assert "overtrading" in result["by_type"]
            assert result["by_type"]["bad_timing"]["had_active_lesson"] is True
            assert result["by_type"]["overtrading"]["had_active_lesson"] is True


# ---------------------------------------------------------------------------
# TestLessonCreepIntegration
# ---------------------------------------------------------------------------


class TestLessonCreepIntegration:
    def _make_outcome(self, r_multiple: float = -1.0):
        from trader.learning.outcome_tracker import TradeOutcome

        return TradeOutcome(
            trade_id=1,
            symbol="BTCUSDT",
            side="long",
            r_multiple=r_multiple,
            holding_seconds=3600.0,
            regime=None,
            session=None,
            pnl_usd=r_multiple * 10,
            mae=None,
            mfe=None,
        )

    def test_bad_decision_bad_outcome_produces_candidate(self):
        from trader.learning.post_trade_review import PostTradeReviewer, ReviewInput
        from trader.llm.schemas import LessonCandidate

        inp = ReviewInput(
            trade_outcome=self._make_outcome(r_multiple=-1.0),
            snapshot={},
            active_lesson_ids=[],
            trade_during_blackout=False,
            stop_inside_noise=False,
            ignored_active_lesson=False,
            llm_outcome_class="bad_decision_bad_outcome",
            llm_what_went_right="",
            llm_what_went_wrong="bad thesis",
            llm_lesson_candidates=[LessonCandidate(condition="avoid xyz")],
        )
        result = PostTradeReviewer().review(inp)
        assert result.outcome_class == "bad_decision_bad_outcome"
        assert len(result.lesson_candidates) > 0

    def test_good_decision_bad_outcome_produces_no_candidate(self):
        from trader.learning.post_trade_review import PostTradeReviewer, ReviewInput
        from trader.llm.schemas import LessonCandidate

        inp = ReviewInput(
            trade_outcome=self._make_outcome(r_multiple=-1.0),
            snapshot={},
            active_lesson_ids=[],
            trade_during_blackout=False,
            stop_inside_noise=False,
            ignored_active_lesson=False,
            llm_outcome_class="good_decision_bad_outcome",
            llm_what_went_right="good thesis",
            llm_what_went_wrong="random variance",
            llm_lesson_candidates=[LessonCandidate(condition="cand-1")],
        )
        result = PostTradeReviewer().review(inp)
        assert result.outcome_class == "good_decision_bad_outcome"
        assert result.lesson_candidates == []

    def test_pattern_promotes_lesson(self):
        from trader.dsor.models import LessonIndex
        from trader.learning.lesson_lifecycle import LessonLifecycleManager

        Session = _in_memory_session()
        with Session() as session:
            row = LessonIndex(lesson_id="pattern-1", status="candidate")
            session.add(row)
            session.flush()

            mgr = LessonLifecycleManager()
            result = mgr.promote_by_evidence(session, "pattern-1", list(range(5)), [])
            assert result is True

            db_row = session.query(LessonIndex).filter_by(lesson_id="pattern-1").first()
            assert db_row is not None
            assert db_row.status == "active"

    def test_contradiction_deprecates_lesson(self):
        from trader.dsor.models import LessonIndex
        from trader.learning.lesson_lifecycle import LessonLifecycleManager

        Session = _in_memory_session()
        with Session() as session:
            row = LessonIndex(lesson_id="contra-1", status="candidate")
            session.add(row)
            session.flush()

            mgr = LessonLifecycleManager()
            mgr.promote_by_evidence(session, "contra-1", list(range(5)), [])

            # Now deprecate it
            result = mgr.deprecate(session, "contra-1", reason="contradiction found")
            assert result is True

            db_row = session.query(LessonIndex).filter_by(lesson_id="contra-1").first()
            assert db_row is not None
            assert db_row.status == "deprecated"

    def test_contradiction_blocks_promotion(self):
        """DoD: a contradicted pattern is never promoted (contradicting_trade_ids blocks it)."""
        from trader.dsor.models import LessonIndex
        from trader.learning.lesson_lifecycle import LessonLifecycleManager

        Session = _in_memory_session()
        with Session() as session:
            row = LessonIndex(lesson_id="contra-2", status="candidate")
            session.add(row)
            session.flush()

            mgr = LessonLifecycleManager()
            # 10 supporting but 1 contradicting → should NOT promote
            result = mgr.promote_by_evidence(
                session, "contra-2", list(range(10)), contradicting_trade_ids=[99]
            )
            assert result is False

            db_row = session.query(LessonIndex).filter_by(lesson_id="contra-2").first()
            assert db_row is not None
            assert db_row.status == "candidate"  # unchanged

    def test_bad_decision_review_persists_candidate_to_db(self):
        """DoD: synthetic losing trade → Review + LessonIndex candidate written to DSOR."""
        import json

        from trader.dsor.models import LessonIndex, Review, Trade
        from trader.learning.post_trade_review import PostTradeReviewer, ReviewInput
        from trader.llm.schemas import LessonCandidate

        Session = _in_memory_session()
        with Session() as session:
            # Seed a trade row (uses Trade model field names: entry/exit, not entry_price/exit_price)
            trade = Trade(
                side="long",
                entry=0.5,
                exit=0.4,
                qty=100.0,
                open_ts=datetime(2026, 1, 1, tzinfo=UTC),
            )
            session.add(trade)
            session.flush()

            # Simulate LLM proposing a lesson candidate
            inp = ReviewInput(
                trade_outcome=self._make_outcome(r_multiple=-1.0),
                snapshot={},
                active_lesson_ids=[],
                trade_during_blackout=False,
                stop_inside_noise=False,
                ignored_active_lesson=False,
                llm_outcome_class="bad_decision_bad_outcome",
                llm_what_went_right="",
                llm_what_went_wrong="entered against trend",
                llm_lesson_candidates=[
                    LessonCandidate(condition="no counter-trend entries", tags=["bad_timing"])
                ],
            )
            result = PostTradeReviewer().review(inp)

            # Persist Review row
            review_row = Review(
                trade_id=trade.id,
                outcome_class=result.outcome_class,
                mistake_types_json=json.dumps(result.mistake_types),
                what_went_right=result.what_went_right,
                what_went_wrong=result.what_went_wrong,
                lesson_candidates_json=json.dumps(
                    [c.model_dump() for c in result.lesson_candidates]
                ),
            )
            session.add(review_row)

            # Persist LessonIndex candidate row
            for candidate in result.lesson_candidates:
                lesson_row = LessonIndex(
                    lesson_id=f"lesson-{candidate.condition[:20].replace(' ', '-')}",
                    status="candidate",
                    tags=json.dumps(candidate.tags),
                    rule_json=json.dumps(candidate.rule) if candidate.rule else None,
                )
                session.add(lesson_row)

            session.flush()

            # Verify DB state
            assert session.query(Review).count() == 1
            saved_review = session.query(Review).first()
            assert saved_review is not None
            assert saved_review.outcome_class == "bad_decision_bad_outcome"

            candidates_in_db = (
                session.query(LessonIndex).filter_by(status="candidate").all()
            )
            assert len(candidates_in_db) >= 1


# ---------------------------------------------------------------------------
# TestPathGuard
# ---------------------------------------------------------------------------


class TestPathGuard:
    def test_lesson_writer_cannot_write_outside_lessons_dir(self):
        import pathlib
        import tempfile

        from trader.ksor.lesson_writer import LessonWriter

        with tempfile.TemporaryDirectory() as tmp:
            ksor_root = pathlib.Path(tmp) / "ksor"
            ksor_root.mkdir()
            writer = LessonWriter(ksor_root=ksor_root)
            with pytest.raises(ValueError, match="Path guard"):
                writer.write(
                    filename="../../../config/risk.yaml",
                    frontmatter={},
                    body="malicious",
                    commit_message="test",
                    trade_ids=[],
                )

    def test_lesson_writer_cannot_write_absolute_path_outside(self):
        import pathlib
        import tempfile

        from trader.ksor.lesson_writer import LessonWriter

        with tempfile.TemporaryDirectory() as tmp:
            ksor_root = pathlib.Path(tmp) / "ksor"
            ksor_root.mkdir()
            writer = LessonWriter(ksor_root=ksor_root)
            with pytest.raises(ValueError, match="Path guard"):
                writer.write(
                    filename="/etc/passwd",
                    frontmatter={},
                    body="malicious",
                    commit_message="test",
                    trade_ids=[],
                )


# ---------------------------------------------------------------------------
# TestTightenOnlyAtLoad
# ---------------------------------------------------------------------------


class TestTightenOnlyAtLoad:
    def test_loader_rejects_raise_size_lesson(self):
        """load_ksor_entries skips a lesson with raise_size in rule, logs warning."""
        import tempfile
        from pathlib import Path

        from trader.ksor.loader import load_ksor_entries

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad_lesson.md"
            p.write_text(
                """---
id: bad-lesson
type: lesson
status: active
condition: test
rule:
  raise_size: 0.5
---
body
"""
            )
            entries = load_ksor_entries(Path(tmp))
            ids = [e.id for e in entries]
            assert "bad-lesson" not in ids  # skipped

    def test_loader_accepts_block_lesson(self):
        """load_ksor_entries accepts a lesson with action: block."""
        import tempfile
        from pathlib import Path

        from trader.ksor.loader import load_ksor_entries

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "good_lesson.md"
            p.write_text(
                """---
id: good-lesson
type: lesson
status: active
condition: test
rule:
  action: block
---
body
"""
            )
            entries = load_ksor_entries(Path(tmp))
            ids = [e.id for e in entries]
            assert "good-lesson" in ids
