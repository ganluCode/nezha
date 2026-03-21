"""Tests for FileTaskQueue implementation."""

import json
import time
from pathlib import Path

import pytest

from nezha.task_queue import (
    FileTaskQueue,
    Task,
    TaskStatus,
    split_feature_list,
    _slugify,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def queue(tmp_path) -> FileTaskQueue:
    """A fresh FileTaskQueue backed by a temp directory."""
    return FileTaskQueue(tmp_path)


@pytest.fixture
def queue2(tmp_path) -> FileTaskQueue:
    """A second queue instance pointing to the same directory (for isolation test)."""
    return FileTaskQueue(tmp_path)


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

class TestFileTaskQueueInit:
    def test_creates_tasks_dir(self, tmp_path):
        """Constructor creates <base_dir>/tasks/ directory."""
        queue = FileTaskQueue(tmp_path)
        assert (tmp_path / "tasks").is_dir()

    def test_tasks_dir_exists_already(self, tmp_path):
        """Constructor succeeds even if tasks/ already exists."""
        (tmp_path / "tasks").mkdir()
        queue = FileTaskQueue(tmp_path)  # should not raise
        assert (tmp_path / "tasks").is_dir()


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------

class TestCreate:
    def test_returns_task(self, queue):
        task = queue.create()
        assert isinstance(task, Task)

    def test_status_is_pending(self, queue):
        task = queue.create()
        assert task.status == TaskStatus.PENDING

    def test_no_title(self, queue):
        task = queue.create()
        assert task.title == ""

    def test_title_preserved(self, queue):
        task = queue.create("User Auth Feature")
        assert task.title == "User Auth Feature"

    def test_id_contains_timestamp(self, queue):
        task = queue.create()
        # ID format: YYYY-MM-DD-HH-MM-SS or YYYY-MM-DD-HH-MM-SS_slug
        parts = task.id.split("-")
        assert len(parts) >= 6, f"Unexpected ID format: {task.id}"

    def test_id_contains_slug_when_title_given(self, queue):
        task = queue.create("user auth")
        assert "user-auth" in task.id

    def test_created_at_is_set(self, queue):
        task = queue.create()
        assert task.created_at is not None
        assert len(task.created_at) > 0

    def test_creates_task_directory(self, tmp_path, queue):
        task = queue.create()
        assert (tmp_path / "tasks" / task.id).is_dir()

    def test_creates_input_subdirectory(self, tmp_path, queue):
        task = queue.create()
        assert (tmp_path / "tasks" / task.id / "input").is_dir()

    def test_creates_task_yaml(self, tmp_path, queue):
        task = queue.create()
        assert (tmp_path / "tasks" / task.id / "task.yaml").exists()

    def test_optional_fields_none(self, queue):
        task = queue.create()
        assert task.started_at is None
        assert task.completed_at is None
        assert task.error is None
        assert "branch" in task.metadata
        assert task.metadata["branch"].startswith("feat/")

    def test_unique_ids_for_multiple_tasks(self, queue):
        t1 = queue.create()
        time.sleep(1.1)  # ensure different second
        t2 = queue.create()
        assert t1.id != t2.id

    def test_default_priority_is_50(self, queue):
        task = queue.create()
        assert task.priority == 50

    def test_custom_priority_stored(self, queue):
        task = queue.create(priority=80)
        assert task.priority == 80

    def test_priority_clamped_to_0_100(self, queue):
        t_low = queue.create(priority=-10)
        t_high = queue.create(priority=200)
        assert t_low.priority == 0
        assert t_high.priority == 100

    def test_priority_persisted_in_yaml(self, tmp_path, queue):
        task = queue.create("test", priority=75)
        yaml_path = tmp_path / "tasks" / task.id / "task.yaml"
        import yaml as _yaml
        data = _yaml.safe_load(yaml_path.read_text())
        assert data["priority"] == 75


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------

class TestGet:
    def test_returns_task_by_id(self, queue):
        created = queue.create()
        fetched = queue.get(created.id)
        assert fetched is not None
        assert fetched.id == created.id

    def test_returns_none_for_unknown_id(self, queue):
        result = queue.get("nonexistent-id")
        assert result is None

    def test_preserves_fields(self, queue):
        created = queue.create("my task")
        fetched = queue.get(created.id)
        assert fetched.title == "my task"
        assert fetched.status == TaskStatus.PENDING
        assert fetched.created_at == created.created_at


# ---------------------------------------------------------------------------
# update_status()
# ---------------------------------------------------------------------------

class TestUpdateStatus:
    def test_pending_to_running(self, queue):
        task = queue.create()
        queue.update_status(task.id, TaskStatus.RUNNING)
        updated = queue.get(task.id)
        assert updated.status == TaskStatus.RUNNING

    def test_running_sets_started_at(self, queue):
        task = queue.create()
        queue.update_status(task.id, TaskStatus.RUNNING)
        updated = queue.get(task.id)
        assert updated.started_at is not None

    def test_running_to_completed(self, queue):
        task = queue.create()
        queue.update_status(task.id, TaskStatus.RUNNING)
        queue.update_status(task.id, TaskStatus.COMPLETED)
        updated = queue.get(task.id)
        assert updated.status == TaskStatus.COMPLETED

    def test_completed_sets_completed_at(self, queue):
        task = queue.create()
        queue.update_status(task.id, TaskStatus.COMPLETED)
        updated = queue.get(task.id)
        assert updated.completed_at is not None

    def test_failed_sets_completed_at(self, queue):
        task = queue.create()
        queue.update_status(task.id, TaskStatus.FAILED)
        updated = queue.get(task.id)
        assert updated.completed_at is not None

    def test_failed_with_error(self, queue):
        task = queue.create()
        queue.update_status(task.id, TaskStatus.FAILED, error="Something went wrong")
        updated = queue.get(task.id)
        assert updated.error == "Something went wrong"

    def test_raises_for_unknown_id(self, queue):
        with pytest.raises(ValueError, match="not found"):
            queue.update_status("bad-id", TaskStatus.RUNNING)


# ---------------------------------------------------------------------------
# update_metadata()
# ---------------------------------------------------------------------------

class TestUpdateMetadata:
    def test_sets_metadata(self, queue):
        task = queue.create()
        queue.update_metadata(task.id, {"branch": "feat/my-feature", "base_branch": "main"})
        updated = queue.get(task.id)
        assert updated.metadata["branch"] == "feat/my-feature"
        assert updated.metadata["base_branch"] == "main"

    def test_merges_metadata(self, queue):
        task = queue.create()
        queue.update_metadata(task.id, {"a": 1})
        queue.update_metadata(task.id, {"b": 2})
        updated = queue.get(task.id)
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

    def test_returns_pending_task_no_filter(self, queue):
        task = queue.create()
        result = queue.get_next()
        assert result is not None
        assert result.id == task.id

    def test_returns_earliest_task(self, queue):
        t1 = queue.create()
        time.sleep(1.1)
        t2 = queue.create()
        result = queue.get_next()
        assert result.id == t1.id

    def test_skips_non_pending(self, queue):
        task = queue.create()
        queue.update_status(task.id, TaskStatus.RUNNING)
        result = queue.get_next()
        assert result is None

    def test_skips_completed(self, queue):
        task = queue.create()
        queue.update_status(task.id, TaskStatus.COMPLETED)
        result = queue.get_next()
        assert result is None

    def test_agent_filter_returns_task_with_feature_list(self, tmp_path, queue):
        """get_next(agent) returns tasks that have feature_list.<agent>.json."""
        task = queue.create()
        # Create per-agent feature_list file
        (tmp_path / "tasks" / task.id / f"feature_list.{AGENT}.json").write_text("[]")
        result = queue.get_next(AGENT)
        assert result is not None
        assert result.id == task.id

    def test_agent_filter_returns_none_when_no_feature_list(self, queue):
        """get_next(agent) returns None when no feature_list.<agent>.json exists."""
        queue.create()
        result = queue.get_next(AGENT)
        assert result is None

    def test_agent_filter_ignores_other_agent_files(self, tmp_path, queue):
        """get_next(OTHER_AGENT) returns None when only AGENT has a file."""
        task = queue.create()
        (tmp_path / "tasks" / task.id / f"feature_list.{AGENT}.json").write_text("[]")
        result = queue.get_next(OTHER_AGENT)
        assert result is None

    def test_no_agent_filter_returns_any_pending(self, queue):
        queue.create()
        result = queue.get_next()
        assert result is not None

    def test_returns_next_after_first_completed(self, queue):
        t1 = queue.create()
        time.sleep(1.1)
        t2 = queue.create()
        queue.update_status(t1.id, TaskStatus.COMPLETED)
        result = queue.get_next()
        assert result is not None
        assert result.id == t2.id

    def test_higher_priority_task_returned_first(self, queue):
        """High-priority task should be returned before older lower-priority task."""
        t_low = queue.create("low priority task", priority=10)
        time.sleep(1.1)
        t_high = queue.create("high priority task", priority=90)
        result = queue.get_next()
        assert result is not None
        assert result.id == t_high.id

    def test_same_priority_returns_earliest(self, queue):
        """Tasks with equal priority: earliest created_at wins."""
        t1 = queue.create(priority=50)
        time.sleep(1.1)
        t2 = queue.create(priority=50)
        result = queue.get_next()
        assert result.id == t1.id


# ---------------------------------------------------------------------------
# list_tasks()
# ---------------------------------------------------------------------------

class TestListTasks:
    def test_empty_queue(self, queue):
        assert queue.list_tasks() == []

    def test_lists_all_tasks(self, queue):
        queue.create()
        time.sleep(1.1)
        queue.create()
        tasks = queue.list_tasks()
        assert len(tasks) == 2

    def test_filter_by_agent(self, tmp_path, queue):
        """Filter by agent_name uses feature_list.<agent>.json existence."""
        t1 = queue.create("user-auth")
        time.sleep(1.1)
        t2 = queue.create("payment")
        # Only t1 has AGENT's feature_list
        (tmp_path / "tasks" / t1.id / f"feature_list.{AGENT}.json").write_text("[]")
        tasks = queue.list_tasks(agent_name=AGENT)
        assert len(tasks) == 1
        assert tasks[0].id == t1.id

    def test_filter_by_status(self, queue):
        t1 = queue.create()
        queue.update_status(t1.id, TaskStatus.COMPLETED)
        queue.create()  # pending
        completed = queue.list_tasks(status=TaskStatus.COMPLETED)
        assert len(completed) == 1
        assert completed[0].status == TaskStatus.COMPLETED

    def test_filter_by_agent_and_status(self, tmp_path, queue):
        t1 = queue.create()
        queue.update_status(t1.id, TaskStatus.COMPLETED)
        (tmp_path / "tasks" / t1.id / f"feature_list.{AGENT}.json").write_text("[]")

        t2 = queue.create()  # pending, no agent file
        time.sleep(1.1)
        t3 = queue.create()
        (tmp_path / "tasks" / t3.id / f"feature_list.{AGENT}.json").write_text("[]")

        results = queue.list_tasks(agent_name=AGENT, status=TaskStatus.COMPLETED)
        assert len(results) == 1
        assert results[0].id == t1.id

    def test_tasks_sorted_by_id(self, queue):
        queue.create()
        time.sleep(1.1)
        queue.create()
        time.sleep(1.1)
        queue.create()
        tasks = queue.list_tasks()
        ids = [t.id for t in tasks]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# task_workspace()
# ---------------------------------------------------------------------------

class TestTaskWorkspace:
    def test_returns_task_directory(self, tmp_path, queue):
        task = queue.create()
        ws = queue.task_workspace(task.id)
        assert ws == tmp_path / "tasks" / task.id

    def test_path_exists(self, queue):
        task = queue.create()
        ws = queue.task_workspace(task.id)
        assert ws.is_dir()


# ---------------------------------------------------------------------------
# split_feature_list()
# ---------------------------------------------------------------------------

class TestSplitFeatureList:
    def test_splits_by_assigned_to(self, tmp_path):
        features = [
            {"id": "F-001", "assigned_to": "backend-agent", "description": "API"},
            {"id": "F-002", "assigned_to": "backend-agent", "description": "DB"},
            {"id": "F-003", "assigned_to": "frontend-agent", "description": "UI"},
        ]
        master = tmp_path / "task_list.json"
        master.write_text(json.dumps(features))

        result = split_feature_list(tmp_path)

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

    def test_skips_features_without_assigned_to(self, tmp_path):
        features = [
            {"id": "F-001", "description": "No owner"},
            {"id": "F-002", "assigned_to": "backend-agent", "description": "API"},
        ]
        (tmp_path / "task_list.json").write_text(json.dumps(features))

        result = split_feature_list(tmp_path)
        assert set(result.keys()) == {"backend-agent"}

    def test_returns_empty_when_no_assigned_to(self, tmp_path):
        features = [{"id": "F-001", "description": "No owner"}]
        (tmp_path / "task_list.json").write_text(json.dumps(features))
        result = split_feature_list(tmp_path)
        assert result == {}

    def test_returns_empty_when_no_feature_list(self, tmp_path):
        result = split_feature_list(tmp_path)
        assert result == {}

    def test_returned_paths_exist(self, tmp_path):
        features = [{"id": "F-001", "assigned_to": "agent-a", "description": "X"}]
        (tmp_path / "task_list.json").write_text(json.dumps(features))
        result = split_feature_list(tmp_path)
        for path in result.values():
            assert path.exists()


# ---------------------------------------------------------------------------
# Persistence: data survives across queue instances
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_task_survives_new_instance(self, tmp_path):
        q1 = FileTaskQueue(tmp_path)
        task = q1.create("user auth")

        q2 = FileTaskQueue(tmp_path)
        fetched = q2.get(task.id)
        assert fetched is not None
        assert fetched.id == task.id
        assert fetched.title == "user auth"

    def test_status_update_persists(self, tmp_path):
        q1 = FileTaskQueue(tmp_path)
        task = q1.create()
        q1.update_status(task.id, TaskStatus.RUNNING)

        q2 = FileTaskQueue(tmp_path)
        fetched = q2.get(task.id)
        assert fetched.status == TaskStatus.RUNNING


# ---------------------------------------------------------------------------
# TaskStatus enum
# ---------------------------------------------------------------------------

class TestTaskStatus:
    def test_string_values(self):
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.PAUSED.value == "paused"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"

    def test_from_string(self):
        assert TaskStatus("pending") == TaskStatus.PENDING
        assert TaskStatus("completed") == TaskStatus.COMPLETED
