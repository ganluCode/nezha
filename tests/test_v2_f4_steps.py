"""Tests for V2.0 F4: Feature Steps (simple orchestration DAG)."""

import pytest

from nezha.feature_queue import (
    Feature,
    FeatureStatus,
    FeatureStep,
    FileFeatureQueue,
    STEP_COMPLETED,
    STEP_NEEDS_REVIEW,
    STEP_PENDING,
    STEP_READY,
    STEP_RUNNING,
    STEP_SKIPPED,
)


# ---------------------------------------------------------------------------
# FeatureStep dataclass basics
# ---------------------------------------------------------------------------

class TestFeatureStepDataclass:

    def test_defaults(self):
        step = FeatureStep(id="s1", agent="backend")
        assert step.id == "s1"
        assert step.agent == "backend"
        assert step.depends_on == []
        assert step.status == STEP_PENDING
        assert step.review_gate is False
        assert step.note == ""

    def test_all_fields(self):
        step = FeatureStep(
            id="s2", agent="frontend",
            depends_on=["s1"], status=STEP_RUNNING,
            review_gate=True, note="WIP",
        )
        assert step.depends_on == ["s1"]
        assert step.status == STEP_RUNNING
        assert step.review_gate is True
        assert step.note == "WIP"


# ---------------------------------------------------------------------------
# Steps serialization round-trip
# ---------------------------------------------------------------------------

class TestStepsRoundTrip:

    def _make_feature_with_steps(self, queue: FileFeatureQueue) -> Feature:
        feature = queue.create(title="multi-step")
        # Manually add steps and persist
        feature.steps = [
            FeatureStep(id="design", agent="planner"),
            FeatureStep(id="backend", agent="evolve-agent", depends_on=["design"]),
            FeatureStep(id="review", agent="evolve-agent", depends_on=["backend"], review_gate=True),
        ]
        queue._write_feature(feature)
        return feature

    def test_steps_persisted_and_reloaded(self, tmp_path):
        queue = FileFeatureQueue(tmp_path)
        created = self._make_feature_with_steps(queue)
        reloaded = queue.get(created.id)

        assert reloaded is not None
        assert len(reloaded.steps) == 3
        assert reloaded.steps[0].id == "design"
        assert reloaded.steps[1].depends_on == ["design"]
        assert reloaded.steps[2].review_gate is True

    def test_feature_without_steps(self, tmp_path):
        queue = FileFeatureQueue(tmp_path)
        feature = queue.create(title="no-steps")
        reloaded = queue.get(feature.id)
        assert reloaded is not None
        assert reloaded.steps == []

    def test_step_note_persisted(self, tmp_path):
        queue = FileFeatureQueue(tmp_path)
        feature = self._make_feature_with_steps(queue)
        queue.update_step_status(feature.id, "design", STEP_COMPLETED, note="LGTM")
        reloaded = queue.get(feature.id)
        step = next(s for s in reloaded.steps if s.id == "design")
        assert step.note == "LGTM"


# ---------------------------------------------------------------------------
# Step status computation (_get_step_status / get_next_ready_step)
# ---------------------------------------------------------------------------

class TestStepStatusComputation:

    def _setup_pipeline(self, tmp_path):
        """Create a 3-step pipeline: design → backend → review(gate)."""
        queue = FileFeatureQueue(tmp_path)
        feature = queue.create(title="pipeline")
        feature.steps = [
            FeatureStep(id="design", agent="planner"),
            FeatureStep(id="backend", agent="evolve-agent", depends_on=["design"]),
            FeatureStep(id="review", agent="evolve-agent", depends_on=["backend"], review_gate=True),
        ]
        queue._write_feature(feature)
        return queue, feature.id

    def test_first_step_is_ready(self, tmp_path):
        queue, fid = self._setup_pipeline(tmp_path)
        step = queue.get_next_ready_step(fid)
        assert step is not None
        assert step.id == "design"

    def test_blocked_step_not_ready(self, tmp_path):
        queue, fid = self._setup_pipeline(tmp_path)
        feature = queue.get(fid)
        # "backend" depends on "design" which is still pending
        status = queue._get_step_status(feature, "backend")
        assert status == STEP_PENDING

    def test_step_becomes_ready_after_dep_completed(self, tmp_path):
        queue, fid = self._setup_pipeline(tmp_path)
        queue.update_step_status(fid, "design", STEP_COMPLETED)
        step = queue.get_next_ready_step(fid)
        assert step is not None
        assert step.id == "backend"

    def test_completed_step_stays_completed(self, tmp_path):
        queue, fid = self._setup_pipeline(tmp_path)
        queue.update_step_status(fid, "design", STEP_COMPLETED)
        feature = queue.get(fid)
        assert queue._get_step_status(feature, "design") == STEP_COMPLETED

    def test_no_ready_step_when_all_blocked(self, tmp_path):
        """When first step is running, no other step is ready."""
        queue, fid = self._setup_pipeline(tmp_path)
        queue.update_step_status(fid, "design", STEP_RUNNING)
        step = queue.get_next_ready_step(fid)
        assert step is None  # design is running, backend/review blocked

    def test_nonexistent_feature_returns_none(self, tmp_path):
        queue = FileFeatureQueue(tmp_path)
        assert queue.get_next_ready_step("nonexistent") is None


# ---------------------------------------------------------------------------
# needs_review / all_steps_done
# ---------------------------------------------------------------------------

class TestStepReviewAndCompletion:

    def _setup(self, tmp_path):
        queue = FileFeatureQueue(tmp_path)
        feature = queue.create(title="review-test")
        feature.steps = [
            FeatureStep(id="s1", agent="a1"),
            FeatureStep(id="s2", agent="a2", depends_on=["s1"], review_gate=True),
        ]
        queue._write_feature(feature)
        return queue, feature.id

    def test_needs_review_empty_initially(self, tmp_path):
        queue, fid = self._setup(tmp_path)
        assert queue.needs_review(fid) == []

    def test_needs_review_returns_review_steps(self, tmp_path):
        queue, fid = self._setup(tmp_path)
        queue.update_step_status(fid, "s2", STEP_NEEDS_REVIEW)
        reviews = queue.needs_review(fid)
        assert len(reviews) == 1
        assert reviews[0].id == "s2"

    def test_all_steps_done_false_when_pending(self, tmp_path):
        queue, fid = self._setup(tmp_path)
        assert queue.all_steps_done(fid) is False

    def test_all_steps_done_true_when_all_completed(self, tmp_path):
        queue, fid = self._setup(tmp_path)
        queue.update_step_status(fid, "s1", STEP_COMPLETED)
        queue.update_step_status(fid, "s2", STEP_COMPLETED)
        assert queue.all_steps_done(fid) is True

    def test_all_steps_done_true_with_skipped(self, tmp_path):
        queue, fid = self._setup(tmp_path)
        queue.update_step_status(fid, "s1", STEP_COMPLETED)
        queue.update_step_status(fid, "s2", STEP_SKIPPED)
        assert queue.all_steps_done(fid) is True

    def test_all_steps_done_false_with_needs_review(self, tmp_path):
        queue, fid = self._setup(tmp_path)
        queue.update_step_status(fid, "s1", STEP_COMPLETED)
        queue.update_step_status(fid, "s2", STEP_NEEDS_REVIEW)
        assert queue.all_steps_done(fid) is False

    def test_all_steps_done_true_for_no_steps(self, tmp_path):
        queue = FileFeatureQueue(tmp_path)
        feature = queue.create(title="empty")
        assert queue.all_steps_done(feature.id) is True

    def test_needs_review_nonexistent_feature(self, tmp_path):
        queue = FileFeatureQueue(tmp_path)
        assert queue.needs_review("nonexistent") == []


# ---------------------------------------------------------------------------
# update_step_status
# ---------------------------------------------------------------------------

class TestUpdateStepStatus:

    def test_update_status(self, tmp_path):
        queue = FileFeatureQueue(tmp_path)
        feature = queue.create(title="update-test")
        feature.steps = [FeatureStep(id="s1", agent="a1")]
        queue._write_feature(feature)

        queue.update_step_status(feature.id, "s1", STEP_COMPLETED)
        reloaded = queue.get(feature.id)
        assert reloaded.steps[0].status == STEP_COMPLETED

    def test_update_status_with_note(self, tmp_path):
        queue = FileFeatureQueue(tmp_path)
        feature = queue.create(title="note-test")
        feature.steps = [FeatureStep(id="s1", agent="a1")]
        queue._write_feature(feature)

        queue.update_step_status(feature.id, "s1", STEP_PENDING, note="Rejected: needs refactor")
        reloaded = queue.get(feature.id)
        assert reloaded.steps[0].note == "Rejected: needs refactor"

    def test_update_nonexistent_feature_raises(self, tmp_path):
        queue = FileFeatureQueue(tmp_path)
        with pytest.raises(ValueError, match="Feature not found"):
            queue.update_step_status("ghost", "s1", STEP_COMPLETED)

    def test_update_nonexistent_step_raises(self, tmp_path):
        queue = FileFeatureQueue(tmp_path)
        feature = queue.create(title="test")
        feature.steps = [FeatureStep(id="s1", agent="a1")]
        queue._write_feature(feature)

        with pytest.raises(ValueError, match="Step not found"):
            queue.update_step_status(feature.id, "ghost-step", STEP_COMPLETED)


# ---------------------------------------------------------------------------
# CLI: approve / reject
# ---------------------------------------------------------------------------

class TestCLIApproveReject:

    def _setup_feature_with_review_step(self, tmp_path):
        """Create a feature with a step in needs_review state."""
        queue = FileFeatureQueue(tmp_path)
        feature = queue.create(title="cli-review")
        feature.steps = [
            FeatureStep(id="s1", agent="a1", status=STEP_COMPLETED),
            FeatureStep(id="s2", agent="a2", depends_on=["s1"], status=STEP_NEEDS_REVIEW, review_gate=True),
        ]
        queue._write_feature(feature)
        return queue, feature.id

    def test_approve_sets_completed(self, tmp_path):
        queue, fid = self._setup_feature_with_review_step(tmp_path)
        queue.update_step_status(fid, "s2", STEP_COMPLETED)  # simulate approve
        reloaded = queue.get(fid)
        step = next(s for s in reloaded.steps if s.id == "s2")
        assert step.status == STEP_COMPLETED

    def test_reject_resets_to_pending(self, tmp_path):
        queue, fid = self._setup_feature_with_review_step(tmp_path)
        queue.update_step_status(fid, "s2", STEP_PENDING, note="Rejected")
        reloaded = queue.get(fid)
        step = next(s for s in reloaded.steps if s.id == "s2")
        assert step.status == STEP_PENDING
        assert step.note == "Rejected"

    def test_approve_last_step_completes_feature(self, tmp_path):
        """Approving the last needs_review step + marking completed → all_steps_done."""
        queue, fid = self._setup_feature_with_review_step(tmp_path)
        queue.update_step_status(fid, "s2", STEP_COMPLETED)
        assert queue.all_steps_done(fid) is True


# ---------------------------------------------------------------------------
# Complex DAG scenarios
# ---------------------------------------------------------------------------

class TestComplexDAG:

    def test_diamond_dependency(self, tmp_path):
        """Diamond: A → B, A → C, B+C → D."""
        queue = FileFeatureQueue(tmp_path)
        feature = queue.create(title="diamond")
        feature.steps = [
            FeatureStep(id="A", agent="a"),
            FeatureStep(id="B", agent="a", depends_on=["A"]),
            FeatureStep(id="C", agent="a", depends_on=["A"]),
            FeatureStep(id="D", agent="a", depends_on=["B", "C"]),
        ]
        queue._write_feature(feature)

        # Only A is ready
        step = queue.get_next_ready_step(feature.id)
        assert step.id == "A"

        # Complete A → B and C are ready
        queue.update_step_status(feature.id, "A", STEP_COMPLETED)
        step = queue.get_next_ready_step(feature.id)
        assert step.id in ("B", "C")

        # Complete B only → D still blocked (C pending)
        queue.update_step_status(feature.id, "B", STEP_COMPLETED)
        feature_reloaded = queue.get(feature.id)
        d_status = queue._get_step_status(feature_reloaded, "D")
        assert d_status == STEP_PENDING  # C not done yet

        # Complete C → D ready
        queue.update_step_status(feature.id, "C", STEP_COMPLETED)
        step = queue.get_next_ready_step(feature.id)
        assert step.id == "D"

    def test_linear_chain(self, tmp_path):
        """Linear: s1 → s2 → s3."""
        queue = FileFeatureQueue(tmp_path)
        feature = queue.create(title="linear")
        feature.steps = [
            FeatureStep(id="s1", agent="a"),
            FeatureStep(id="s2", agent="a", depends_on=["s1"]),
            FeatureStep(id="s3", agent="a", depends_on=["s2"]),
        ]
        queue._write_feature(feature)

        # Walk through the chain
        for expected in ["s1", "s2", "s3"]:
            step = queue.get_next_ready_step(feature.id)
            assert step.id == expected
            queue.update_step_status(feature.id, expected, STEP_COMPLETED)

        assert queue.all_steps_done(feature.id) is True
        assert queue.get_next_ready_step(feature.id) is None
