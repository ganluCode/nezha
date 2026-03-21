"""Tests for FileFeatureQueue implementation."""

import json
import time
from pathlib import Path

import pytest

from nezha.feature_queue import (
    FileFeatureQueue,
    Feature,
    FeatureStatus,
    split_task_list,
    _slugify,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def queue(tmp_path) -> FileFeatureQueue:
    """A fresh FileFeatureQueue backed by a temp directory."""
    return FileFeatureQueue(tmp_path)


@pytest.fixture
def queue2(tmp_path) -> FileFeatureQueue:
    """A second queue instance pointing to the same directory (for isolation test)."""
    return FileFeatureQueue(tmp_path)


AGENT = "test-agent"
OTHER_AGENT = "other-agent"


# ---------------------------------------------------------------------------
# Slugify helper
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_basic(self):
        assert _slugify("User Auth") == "user-auth"

    def test_lowercase(self):
        assert _slugify("HELLO WORLD") == "hello-world"

    def test_multiple_spaces(self):
        assert _slugify("a  b   c") == "a-b-c"

    def test_special_chars_stripped(self):
        assert _slugify("hello! world?") == "hello-world"

    def test_empty_string(self):
        assert _slugify("") == ""

    def test_underscores_become_dashes(self):
        assert _slugify("user_auth_feature") == "user-auth-feature"

    def test_already_slug(self):
        assert _slugify("user-auth") == "user-auth"


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestFileFeatureQueueInit:
    def test_creates_features_dir(self, tmp_path):
        """Constructor creates <base_dir>/features/ directory."""
        queue = FileFeatureQueue(tmp_path)
        assert (tmp_path / "features").is_dir()

    def test_features_dir_exists_already(self, tmp_path):
        """Constructor succeeds even if features/ already exists."""
        (tmp_path / "features").mkdir()
        queue = FileFeatureQueue(tmp_path)  # should not raise
        assert (tmp_path / "features").is_dir()


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------

class TestCreate:
    def test_returns_feature(self, queue):
        feature = queue.create()
        assert isinstance(feature, Feature)

    def test_status_is_pending(self, queue):
        feature = queue.create()
        assert feature.status == FeatureStatus.PENDING

    def test_no_title(self, queue):
        feature = queue.create()
        assert feature.title == ""

    def test_title_preserved(self, queue):
        feature = queue.create("User Auth Feature")
        assert feature.title == "User Auth Feature"

    def test_id_contains_timestamp(self, queue):
        feature = queue.create()
        # ID format: YYYY-MM-DD-HH-MM-SS or YYYY-MM-DD-HH-MM-SS_slug
        parts = feature.id.split("-")
        assert len(parts) >= 6, f"Unexpected ID format: {feature.id}"

    def test_id_contains_slug_when_title_given(self, queue):
        feature = queue.create("user auth")
        assert "user-auth" in feature.id

    def test_created_at_is_set(self, queue):
        feature = queue.create()
        assert feature.created_at is not None
        assert len(feature.created_at) > 0

    def test_creates_feature_directory(self, tmp_path, queue):
        feature = queue.create()
        assert (tmp_path / "features" / feature.id).is_dir()

    def test_creates_input_subdirectory(self, tmp_path, queue):
        feature = queue.create()
        assert (tmp_path / "features" / feature.id / "input").is_dir()

    def test_creates_feature_yaml(self, tmp_path, queue):
        feature = queue.create()
        assert (tmp_path / "features" / feature.id / "feature.yaml").exists()

    def test_optional_fields_none(self, queue):
        feature = queue.create()
        assert feature.started_at is None
        assert feature.completed_at is None
        assert feature.error is None
        assert "branch" in feature.metadata
        assert feature.metadata["branch"].startswith("feat/")

    def test_unique_ids_for_multiple_features(self, queue):
        f1 = queue.create()
        time.sleep(1.1)  # ensure different second
        f2 = queue.create()
        assert f1.id != f2.id

    def test_default_priority_is_50(self, queue):
        feature = queue.create()
        assert feature.priority == 50

    def test_custom_priority_stored(self, queue):
        feature = queue.create(priority=80)
        assert feature.priority == 80

    def test_priority_clamped_to_0_100(self, queue):
        f_low = queue.create(priority=-10)
        f_high = queue.create(priority=200)
        assert f_low.priority == 0
        assert f_high.priority == 100

    def test_priority_persisted_in_yaml(self, tmp_path, queue):
        feature = queue.create("test", priority=75)
        yaml_path = tmp_path / "features" / feature.id / "feature.yaml"
        import yaml as _yaml
        data = _yaml.safe_load(yaml_path.read_text())
        assert data["priority"] == 75


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------

class TestGet:
    def test_returns_feature_by_id(self, queue):
        created = queue.create()
        fetched = queue.get(created.id)
        assert fetched is not None
        assert fetched.id == created.id

    def test_returns_none_for_unknown_id(self, queue):
        result = queue.get("nonexistent-id")
        assert result is None

    def test_preserves_fields(self, queue):
        created = queue.create("my feature")
        fetched = queue.get(created.id)
        assert fetched.title == "my feature"
        assert fetched.status == FeatureStatus.PENDING
        assert fetched.created_at == created.created_at


# ---------------------------------------------------------------------------
# update_status()
# ---------------------------------------------------------------------------

class TestUpdateStatus:
    def test_pending_to_running(self, queue):
        feature = queue.create()
        queue.update_status(feature.id, FeatureStatus.RUNNING)
        updated = queue.get(feature.id)
        assert updated.status == FeatureStatus.RUNNING

    def test_running_sets_started_at(self, queue):
        feature = queue.create()
        queue.update_status(feature.id, FeatureStatus.RUNNING)
        updated = queue.get(feature.id)
        assert updated.started_at is not None

    def test_running_to_completed(self, queue):
        feature = queue.create()
        queue.update_status(feature.id, FeatureStatus.RUNNING)
        queue.update_status(feature.id, FeatureStatus.COMPLETED)
        updated = queue.get(feature.id)
        assert updated.status == FeatureStatus.COMPLETED

    def test_completed_sets_completed_at(self, queue):
        feature = queue.create()
        queue.update_status(feature.id, FeatureStatus.COMPLETED)
        updated = queue.get(feature.id)
        assert updated.completed_at is not None

    def test_failed_sets_completed_at(self, queue):
        feature = queue.create()
        queue.update_status(feature.id, FeatureStatus.FAILED)
        updated = queue.get(feature.id)
        assert updated.completed_at is not None

    def test_failed_with_error(self, queue):
        feature = queue.create()
        queue.update_status(feature.id, FeatureStatus.FAILED, error="Something went wrong")
        updated = queue.get(feature.id)
        assert updated.error == "Something went wrong"

    def test_raises_for_unknown_id(self, queue):
        with pytest.raises(ValueError, match="not found"):
            queue.update_status("bad-id", FeatureStatus.RUNNING)


# ---------------------------------------------------------------------------
# update_metadata()
# ---------------------------------------------------------------------------

class TestUpdateMetadata:
    def test_sets_metadata(self, queue):
        feature = queue.create()
        queue.update_metadata(feature.id, {"branch": "feat/my-feature", "base_branch": "main"})
        updated = queue.get(feature.id)
        assert updated.metadata["branch"] == "feat/my-feature"
        assert updated.metadata["base_branch"] == "main"

    def test_merges_metadata(self, queue):
        feature = queue.create()
        queue.update_metadata(feature.id, {"a": 1})
        queue.update_metadata(feature.id, {"b": 2})
        updated = queue.get(feature.id)
        assert updated.metadata["a"] == 1
        assert updated.metadata["b"] == 2

    def test_raises_for_unknown_id(self, queue):
        with pytest.raises(ValueError, match="not found"):
            queue.update_metadata("bad-id", {"key": "val"})


# ---------------------------------------------------------------------------
# get_next()
# ---------------------------------------------------------------------------

class TestGetNext:
    def test_returns_none_when_empty(self, queue):
        result = queue.get_next()
        assert result is None

    def test_returns_pending_feature_no_filter(self, queue):
        feature = queue.create()
        result = queue.get_next()
        assert result is not None
        assert result.id == feature.id

    def test_returns_earliest_feature(self, queue):
        f1 = queue.create()
        time.sleep(1.1)
        f2 = queue.create()
        result = queue.get_next()
        assert result.id == f1.id

    def test_skips_non_pending(self, queue):
        feature = queue.create()
        queue.update_status(feature.id, FeatureStatus.RUNNING)
        result = queue.get_next()
        assert result is None

    def test_skips_completed(self, queue):
        feature = queue.create()
        queue.update_status(feature.id, FeatureStatus.COMPLETED)
        result = queue.get_next()
        assert result is None

    def test_agent_filter_returns_feature_with_task_list(self, tmp_path, queue):
        """get_next(agent) returns features that have task_list.<agent>.json."""
        feature = queue.create()
        # Create per-agent task_list file
        (tmp_path / "features" / feature.id / f"task_list.{AGENT}.json").write_text("[]")
        result = queue.get_next(AGENT)
        assert result is not None
        assert result.id == feature.id

    def test_agent_filter_returns_none_when_no_task_list(self, queue):
        """get_next(agent) returns None when no task_list.<agent>.json exists."""
        queue.create()
        result = queue.get_next(AGENT)
        assert result is None

    def test_agent_filter_ignores_other_agent_files(self, tmp_path, queue):
        """get_next(OTHER_AGENT) returns None when only AGENT has a file."""
        feature = queue.create()
        (tmp_path / "features" / feature.id / f"task_list.{AGENT}.json").write_text("[]")
        result = queue.get_next(OTHER_AGENT)
        assert result is None

    def test_no_agent_filter_returns_any_pending(self, queue):
        queue.create()
        result = queue.get_next()
        assert result is not None

    def test_returns_next_after_first_completed(self, queue):
        f1 = queue.create()
        time.sleep(1.1)
        f2 = queue.create()
        queue.update_status(f1.id, FeatureStatus.COMPLETED)
        result = queue.get_next()
        assert result is not None
        assert result.id == f2.id

    def test_higher_priority_feature_returned_first(self, queue):
        """High-priority feature should be returned before older lower-priority feature."""
        f_low = queue.create("low priority feature", priority=10)
        time.sleep(1.1)
        f_high = queue.create("high priority feature", priority=90)
        result = queue.get_next()
        assert result is not None
        assert result.id == f_high.id

    def test_same_priority_returns_earliest(self, queue):
        """Features with equal priority: earliest created_at wins."""
        f1 = queue.create(priority=50)
        time.sleep(1.1)
        f2 = queue.create(priority=50)
        result = queue.get_next()
        assert result.id == f1.id


# ---------------------------------------------------------------------------
# list_features()
# ---------------------------------------------------------------------------

class TestListFeatures:
    def test_empty_queue(self, queue):
        assert queue.list_features() == []

    def test_lists_all_features(self, queue):
        queue.create()
        time.sleep(1.1)
        queue.create()
        features = queue.list_features()
        assert len(features) == 2

    def test_filter_by_agent(self, tmp_path, queue):
        """Filter by agent_name uses task_list.<agent>.json existence."""
        f1 = queue.create("user-auth")
        time.sleep(1.1)
        f2 = queue.create("payment")
        # Only f1 has AGENT's task_list
        (tmp_path / "features" / f1.id / f"task_list.{AGENT}.json").write_text("[]")
        features = queue.list_features(agent_name=AGENT)
        assert len(features) == 1
        assert features[0].id == f1.id

    def test_filter_by_status(self, queue):
        f1 = queue.create()
        queue.update_status(f1.id, FeatureStatus.COMPLETED)
        queue.create()  # pending
        completed = queue.list_features(status=FeatureStatus.COMPLETED)
        assert len(completed) == 1
        assert completed[0].status == FeatureStatus.COMPLETED

    def test_filter_by_agent_and_status(self, tmp_path, queue):
        f1 = queue.create()
        queue.update_status(f1.id, FeatureStatus.COMPLETED)
        (tmp_path / "features" / f1.id / f"task_list.{AGENT}.json").write_text("[]")

        f2 = queue.create()  # pending, no agent file
        time.sleep(1.1)
        f3 = queue.create()
        (tmp_path / "features" / f3.id / f"task_list.{AGENT}.json").write_text("[]")

        results = queue.list_features(agent_name=AGENT, status=FeatureStatus.COMPLETED)
        assert len(results) == 1
        assert results[0].id == f1.id

    def test_features_sorted_by_id(self, queue):
        queue.create()
        time.sleep(1.1)
        queue.create()
        time.sleep(1.1)
        queue.create()
        features = queue.list_features()
        ids = [f.id for f in features]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# feature_workspace()
# ---------------------------------------------------------------------------

class TestFeatureWorkspace:
    def test_returns_feature_directory(self, tmp_path, queue):
        feature = queue.create()
        ws = queue.feature_workspace(feature.id)
        assert ws == tmp_path / "features" / feature.id

    def test_path_exists(self, queue):
        feature = queue.create()
        ws = queue.feature_workspace(feature.id)
        assert ws.is_dir()


# ---------------------------------------------------------------------------
# split_task_list()
# ---------------------------------------------------------------------------

class TestSplitTaskList:
    def test_splits_by_assigned_to(self, tmp_path):
        tasks = [
            {"id": "F-001", "assigned_to": "backend-agent", "description": "API"},
            {"id": "F-002", "assigned_to": "backend-agent", "description": "DB"},
            {"id": "F-003", "assigned_to": "frontend-agent", "description": "UI"},
        ]
        master = tmp_path / "task_list.json"
        master.write_text(json.dumps(tasks))

        result = split_task_list(tmp_path)

        assert set(result.keys()) == {"backend-agent", "frontend-agent"}

        backend_path = tmp_path / "task_list.backend-agent.json"
        assert backend_path.exists()
        backend = json.loads(backend_path.read_text())
        assert len(backend) == 2
        assert all(f["assigned_to"] == "backend-agent" for f in backend)

        frontend_path = tmp_path / "task_list.frontend-agent.json"
        assert frontend_path.exists()
        frontend = json.loads(frontend_path.read_text())
        assert len(frontend) == 1
        assert frontend[0]["id"] == "F-003"

    def test_skips_tasks_without_assigned_to(self, tmp_path):
        tasks = [
            {"id": "F-001", "description": "No owner"},
            {"id": "F-002", "assigned_to": "backend-agent", "description": "API"},
        ]
        (tmp_path / "task_list.json").write_text(json.dumps(tasks))

        result = split_task_list(tmp_path)
        assert set(result.keys()) == {"backend-agent"}

    def test_returns_empty_when_no_assigned_to(self, tmp_path):
        tasks = [{"id": "F-001", "description": "No owner"}]
        (tmp_path / "task_list.json").write_text(json.dumps(tasks))
        result = split_task_list(tmp_path)
        assert result == {}

    def test_returns_empty_when_no_task_list(self, tmp_path):
        result = split_task_list(tmp_path)
        assert result == {}

    def test_returned_paths_exist(self, tmp_path):
        tasks = [{"id": "F-001", "assigned_to": "agent-a", "description": "X"}]
        (tmp_path / "task_list.json").write_text(json.dumps(tasks))
        result = split_task_list(tmp_path)
        for path in result.values():
            assert path.exists()

    def test_legacy_feature_list_fallback(self, tmp_path):
        """split_task_list() falls back to feature_list.json if task_list.json absent."""
        tasks = [{"id": "F-001", "assigned_to": "backend-agent", "description": "API"}]
        (tmp_path / "feature_list.json").write_text(json.dumps(tasks))

        result = split_task_list(tmp_path)
        assert set(result.keys()) == {"backend-agent"}


# ---------------------------------------------------------------------------
# Persistence: data survives across queue instances
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_feature_survives_new_instance(self, tmp_path):
        q1 = FileFeatureQueue(tmp_path)
        feature = q1.create("user auth")

        q2 = FileFeatureQueue(tmp_path)
        fetched = q2.get(feature.id)
        assert fetched is not None
        assert fetched.id == feature.id
        assert fetched.title == "user auth"

    def test_status_update_persists(self, tmp_path):
        q1 = FileFeatureQueue(tmp_path)
        feature = q1.create()
        q1.update_status(feature.id, FeatureStatus.RUNNING)

        q2 = FileFeatureQueue(tmp_path)
        fetched = q2.get(feature.id)
        assert fetched.status == FeatureStatus.RUNNING


# ---------------------------------------------------------------------------
# FeatureStatus enum
# ---------------------------------------------------------------------------

class TestFeatureStatus:
    def test_string_values(self):
        assert FeatureStatus.PENDING.value == "pending"
        assert FeatureStatus.RUNNING.value == "running"
        assert FeatureStatus.PAUSED.value == "paused"
        assert FeatureStatus.COMPLETED.value == "completed"
        assert FeatureStatus.FAILED.value == "failed"

    def test_from_string(self):
        assert FeatureStatus("pending") == FeatureStatus.PENDING
        assert FeatureStatus("completed") == FeatureStatus.COMPLETED
