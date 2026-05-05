"""Tests for phase.py — Phase data structures, DAG validation, and batch creation."""

import json
import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from nezha.feature_queue import FeatureStatus
from nezha.phase import (
    Phase,
    PhaseFeatureRef,
    PhaseStatus,
    FilePhaseStore,
    compute_branch_chain,
    load_phase_input,
    topo_sort,
    validate_phase_dag,
    _generate_phase_id,
)


# ---------------------------------------------------------------------------
# load_phase_input
# ---------------------------------------------------------------------------

class TestLoadPhaseInput:
    def test_valid_input(self, tmp_path):
        p = tmp_path / "phase.yaml"
        p.write_text(yaml.dump({
            "title": "MVP",
            "features": [
                {"id": "f1", "title": "Feature 1"},
                {"id": "f2", "title": "Feature 2", "depends_on": ["f1"]},
            ],
        }))
        data = load_phase_input(p)
        assert data["title"] == "MVP"
        assert len(data["features"]) == 2

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_phase_input(tmp_path / "nonexistent.yaml")

    def test_missing_title(self, tmp_path):
        p = tmp_path / "phase.yaml"
        p.write_text(yaml.dump({"features": [{"id": "f1", "title": "F"}]}))
        with pytest.raises(ValueError, match="title"):
            load_phase_input(p)

    def test_missing_features(self, tmp_path):
        p = tmp_path / "phase.yaml"
        p.write_text(yaml.dump({"title": "MVP"}))
        with pytest.raises(ValueError, match="features"):
            load_phase_input(p)

    def test_empty_features(self, tmp_path):
        p = tmp_path / "phase.yaml"
        p.write_text(yaml.dump({"title": "MVP", "features": []}))
        with pytest.raises(ValueError, match="features"):
            load_phase_input(p)

    def test_feature_missing_id(self, tmp_path):
        p = tmp_path / "phase.yaml"
        p.write_text(yaml.dump({
            "title": "MVP",
            "features": [{"title": "No ID"}],
        }))
        with pytest.raises(ValueError, match="id"):
            load_phase_input(p)

    def test_feature_missing_title(self, tmp_path):
        p = tmp_path / "phase.yaml"
        p.write_text(yaml.dump({
            "title": "MVP",
            "features": [{"id": "f1"}],
        }))
        with pytest.raises(ValueError, match="title"):
            load_phase_input(p)


# ---------------------------------------------------------------------------
# validate_phase_dag
# ---------------------------------------------------------------------------

class TestValidatePhaseDag:
    def test_linear_chain(self):
        features = [
            {"id": "a", "depends_on": []},
            {"id": "b", "depends_on": ["a"]},
            {"id": "c", "depends_on": ["b"]},
        ]
        validate_phase_dag(features)  # should not raise

    def test_diamond(self):
        features = [
            {"id": "a"},
            {"id": "b", "depends_on": ["a"]},
            {"id": "c", "depends_on": ["a"]},
            {"id": "d", "depends_on": ["b", "c"]},
        ]
        validate_phase_dag(features)  # should not raise

    def test_single_feature(self):
        validate_phase_dag([{"id": "solo"}])  # should not raise

    def test_duplicate_id(self):
        features = [{"id": "a"}, {"id": "a"}]
        with pytest.raises(ValueError, match="Duplicate"):
            validate_phase_dag(features)

    def test_missing_dependency(self):
        features = [{"id": "a", "depends_on": ["nonexistent"]}]
        with pytest.raises(ValueError, match="does not exist"):
            validate_phase_dag(features)

    def test_self_reference(self):
        features = [{"id": "a", "depends_on": ["a"]}]
        with pytest.raises(ValueError, match="depends on itself"):
            validate_phase_dag(features)

    def test_cycle_two_nodes(self):
        features = [
            {"id": "a", "depends_on": ["b"]},
            {"id": "b", "depends_on": ["a"]},
        ]
        with pytest.raises(ValueError, match="cycle"):
            validate_phase_dag(features)

    def test_cycle_three_nodes(self):
        features = [
            {"id": "a", "depends_on": ["c"]},
            {"id": "b", "depends_on": ["a"]},
            {"id": "c", "depends_on": ["b"]},
        ]
        with pytest.raises(ValueError, match="cycle"):
            validate_phase_dag(features)


# ---------------------------------------------------------------------------
# topo_sort
# ---------------------------------------------------------------------------

class TestTopoSort:
    def test_linear_chain(self):
        features = [
            {"id": "c", "depends_on": ["b"]},
            {"id": "a"},
            {"id": "b", "depends_on": ["a"]},
        ]
        result = topo_sort(features)
        ids = [f["id"] for f in result]
        assert ids.index("a") < ids.index("b")
        assert ids.index("b") < ids.index("c")

    def test_diamond(self):
        features = [
            {"id": "d", "depends_on": ["b", "c"]},
            {"id": "b", "depends_on": ["a"]},
            {"id": "c", "depends_on": ["a"]},
            {"id": "a"},
        ]
        result = topo_sort(features)
        ids = [f["id"] for f in result]
        assert ids.index("a") < ids.index("b")
        assert ids.index("a") < ids.index("c")
        assert ids.index("b") < ids.index("d")
        assert ids.index("c") < ids.index("d")

    def test_independent_features_by_priority(self):
        features = [
            {"id": "low", "priority": 30},
            {"id": "high", "priority": 90},
            {"id": "mid", "priority": 50},
        ]
        result = topo_sort(features)
        ids = [f["id"] for f in result]
        assert ids == ["high", "mid", "low"]

    def test_single_feature(self):
        result = topo_sort([{"id": "solo"}])
        assert len(result) == 1
        assert result[0]["id"] == "solo"


# ---------------------------------------------------------------------------
# compute_branch_chain
# ---------------------------------------------------------------------------

class TestComputeBranchChain:
    def test_root_gets_base_branch(self):
        features = [{"id": "root"}]
        fids = {"root": "2026-04-17_root"}
        result = compute_branch_chain(features, fids, base_branch="main")
        assert result["root"] == "main"

    def test_single_dep_chains(self):
        features = [
            {"id": "a"},
            {"id": "b", "depends_on": ["a"]},
        ]
        fids = {"a": "2026-04-17_a", "b": "2026-04-17_b"}
        result = compute_branch_chain(features, fids, base_branch="main")
        assert result["a"] == "main"
        assert result["b"] == "feat/2026-04-17_a"

    def test_multi_dep_linear_chain(self):
        """Even with diamond DAG, branch chain is linear."""
        features = [
            {"id": "a"},
            {"id": "b"},
            {"id": "c", "depends_on": ["a", "b"]},
        ]
        fids = {"a": "id_a", "b": "id_b", "c": "id_c"}
        result = compute_branch_chain(features, fids, base_branch="dev")
        assert result["a"] == "dev"
        assert result["b"] == "feat/id_a"     # linear: b bases on a
        assert result["c"] == "feat/id_b"     # linear: c bases on b

    def test_linear_chain_three(self):
        features = [
            {"id": "a"},
            {"id": "b", "depends_on": ["a"]},
            {"id": "c", "depends_on": ["b"]},
        ]
        fids = {"a": "id_a", "b": "id_b", "c": "id_c"}
        result = compute_branch_chain(features, fids)
        assert result["a"] == "main"
        assert result["b"] == "feat/id_a"
        assert result["c"] == "feat/id_b"


# ---------------------------------------------------------------------------
# FilePhaseStore
# ---------------------------------------------------------------------------

class TestFilePhaseStore:
    def _make_phase(self) -> Phase:
        return Phase(
            id="test-phase",
            title="Test Phase",
            status=PhaseStatus.PLANNED,
            created_at="2026-04-17T10:00:00+08:00",
            base_branch="main",
            agent="python-agent",
            features=[
                PhaseFeatureRef(
                    step_id="f1",
                    feature_id="2026-04-17_f1",
                    title="Feature 1",
                    depends_on=[],
                    priority=90,
                ),
                PhaseFeatureRef(
                    step_id="f2",
                    feature_id="2026-04-17_f2",
                    title="Feature 2",
                    depends_on=["f1"],
                    priority=80,
                ),
            ],
        )

    def test_save_and_get(self, tmp_path):
        store = FilePhaseStore(tmp_path)
        phase = self._make_phase()
        store.save(phase)

        loaded = store.get("test-phase")
        assert loaded is not None
        assert loaded.id == "test-phase"
        assert loaded.title == "Test Phase"
        assert loaded.status == PhaseStatus.PLANNED
        assert loaded.base_branch == "main"
        assert loaded.agent == "python-agent"
        assert len(loaded.features) == 2
        assert loaded.features[0].step_id == "f1"
        assert loaded.features[1].depends_on == ["f1"]

    def test_get_missing_returns_none(self, tmp_path):
        store = FilePhaseStore(tmp_path)
        assert store.get("nonexistent") is None

    def test_list_phases(self, tmp_path):
        store = FilePhaseStore(tmp_path)
        p1 = self._make_phase()
        p1.id = "phase-a"
        p2 = self._make_phase()
        p2.id = "phase-b"
        store.save(p1)
        store.save(p2)

        phases = store.list_phases()
        assert len(phases) == 2
        ids = [p.id for p in phases]
        assert "phase-a" in ids
        assert "phase-b" in ids

    def test_list_phases_empty(self, tmp_path):
        store = FilePhaseStore(tmp_path)
        assert store.list_phases() == []

    def test_update_status(self, tmp_path):
        store = FilePhaseStore(tmp_path)
        phase = self._make_phase()
        store.save(phase)

        store.update_status("test-phase", PhaseStatus.RUNNING)
        loaded = store.get("test-phase")
        assert loaded.status == PhaseStatus.RUNNING

    def test_update_status_missing_raises(self, tmp_path):
        store = FilePhaseStore(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            store.update_status("missing", PhaseStatus.RUNNING)


# ---------------------------------------------------------------------------
# _generate_phase_id
# ---------------------------------------------------------------------------

class TestGeneratePhaseId:
    def test_with_title(self):
        pid = _generate_phase_id("User System MVP")
        assert "user-system-mvp" in pid
        # Should start with timestamp
        assert pid[:4].isdigit()

    def test_with_chinese_title(self):
        pid = _generate_phase_id("用户系统")
        # Chinese chars stripped, but timestamp remains
        assert pid[:4].isdigit()

    def test_empty_title(self):
        pid = _generate_phase_id("")
        assert pid[:4].isdigit()


# ---------------------------------------------------------------------------
# Phase-aware get_next() in FileFeatureQueue
# ---------------------------------------------------------------------------

class TestPhaseAwareGetNext:
    """Test that get_next() respects phase dependencies."""

    def _setup_queue_with_phase(self, tmp_path):
        """Create a queue with two features in a phase (A → B)."""
        from nezha.feature_queue import FileFeatureQueue

        ws = tmp_path / "workspace"
        ws.mkdir()
        queue = FileFeatureQueue(ws)

        # Create two features
        feat_a = queue.create(title="Feature A", priority=90)
        feat_b = queue.create(title="Feature B", priority=80)

        phase_id = "test-phase"

        # Set phase metadata
        queue.update_metadata(feat_a.id, {
            "phase_id": phase_id,
            "step_id": "step-a",
            "phase_depends_on": [],
        })
        queue.update_metadata(feat_b.id, {
            "phase_id": phase_id,
            "step_id": "step-b",
            "phase_depends_on": ["step-a"],
        })

        # Save phase manifest
        store = FilePhaseStore(ws)
        phase = Phase(
            id=phase_id,
            title="Test Phase",
            status=PhaseStatus.PLANNED,
            created_at="2026-04-17T10:00:00+08:00",
            features=[
                PhaseFeatureRef(
                    step_id="step-a",
                    feature_id=feat_a.id,
                    title="Feature A",
                    depends_on=[],
                    priority=90,
                ),
                PhaseFeatureRef(
                    step_id="step-b",
                    feature_id=feat_b.id,
                    title="Feature B",
                    depends_on=["step-a"],
                    priority=80,
                ),
            ],
        )
        store.save(phase)

        return queue, feat_a, feat_b

    def test_root_feature_returned(self, tmp_path):
        """Phase root (no deps) should be returned immediately."""
        queue, feat_a, feat_b = self._setup_queue_with_phase(tmp_path)
        next_feat = queue.get_next()
        assert next_feat is not None
        assert next_feat.id == feat_a.id

    def test_downstream_blocked_until_upstream_complete(self, tmp_path):
        """Downstream feature should be blocked when upstream is pending."""
        queue, feat_a, feat_b = self._setup_queue_with_phase(tmp_path)

        # Complete feature A
        queue.update_status(feat_a.id, FeatureStatus.RUNNING)
        # B should still be blocked (A is running, not completed)
        next_feat = queue.get_next()
        assert next_feat is None  # A is running, B is blocked

    def test_downstream_unblocked_after_upstream_complete(self, tmp_path):
        """Downstream feature should be returned after upstream completes."""
        queue, feat_a, feat_b = self._setup_queue_with_phase(tmp_path)

        # Complete feature A
        queue.update_status(feat_a.id, FeatureStatus.COMPLETED)

        next_feat = queue.get_next()
        assert next_feat is not None
        assert next_feat.id == feat_b.id

    def test_non_phase_feature_not_affected(self, tmp_path):
        """Feature without phase_id should always be returned (backward compat)."""
        from nezha.feature_queue import FileFeatureQueue

        ws = tmp_path / "workspace"
        ws.mkdir()
        queue = FileFeatureQueue(ws)
        feat = queue.create(title="Standalone")

        next_feat = queue.get_next()
        assert next_feat is not None
        assert next_feat.id == feat.id

    def test_missing_phase_manifest_does_not_block(self, tmp_path):
        """Feature with phase_id but no manifest should not be blocked."""
        from nezha.feature_queue import FileFeatureQueue

        ws = tmp_path / "workspace"
        ws.mkdir()
        queue = FileFeatureQueue(ws)
        feat = queue.create(title="Orphan")
        queue.update_metadata(feat.id, {
            "phase_id": "nonexistent-phase",
            "step_id": "s1",
            "phase_depends_on": ["s0"],
        })

        next_feat = queue.get_next()
        assert next_feat is not None
        assert next_feat.id == feat.id

    def test_multiple_phases_independent(self, tmp_path):
        """Features in different phases don't block each other."""
        from nezha.feature_queue import FileFeatureQueue

        ws = tmp_path / "workspace"
        ws.mkdir()
        queue = FileFeatureQueue(ws)

        # Phase 1: A (pending)
        feat_a = queue.create(title="Phase1 A", priority=50)
        queue.update_metadata(feat_a.id, {
            "phase_id": "phase-1",
            "step_id": "a",
            "phase_depends_on": [],
        })

        # Phase 2: B depends on C (C not done)
        feat_b = queue.create(title="Phase2 B", priority=90)
        queue.update_metadata(feat_b.id, {
            "phase_id": "phase-2",
            "step_id": "b",
            "phase_depends_on": ["c"],
        })
        # Phase 2 manifest missing → B won't be blocked by missing manifest

        # A should be picked (phase-1 root, lower priority but unblocked)
        # B should also be picked (missing manifest = not blocked)
        # B has higher priority so it should be first
        next_feat = queue.get_next()
        assert next_feat is not None
        assert next_feat.id == feat_b.id  # higher priority, manifest missing = ok
