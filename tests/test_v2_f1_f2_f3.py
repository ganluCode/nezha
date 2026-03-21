"""Tests for V2.0 features: F1 (chain branches), F2 (per-task model), F3 (integration verification)."""

import json
from pathlib import Path

import pytest

from nezha.dag.graph import Task, TaskDAG
from nezha.engine import SessionResult
from nezha.feature_queue import FileFeatureQueue


# ---------------------------------------------------------------------------
# F1: Chain branches (per-feature base_branch)
# ---------------------------------------------------------------------------

class TestF1ChainBranches:

    def test_create_feature_with_base_branch(self, tmp_path):
        """base_branch is saved in feature metadata."""
        queue = FileFeatureQueue(tmp_path)
        feature = queue.create(
            title="api-v2",
            base_branch="feat/2026-01-01_api-v1",
        )
        assert feature.metadata.get("base_branch") == "feat/2026-01-01_api-v1"

    def test_create_feature_without_base_branch(self, tmp_path):
        """base_branch absent from metadata when not provided."""
        queue = FileFeatureQueue(tmp_path)
        feature = queue.create(title="standalone")
        assert "base_branch" not in feature.metadata

    def test_base_branch_persisted_on_reload(self, tmp_path):
        """base_branch survives write + read round-trip."""
        queue = FileFeatureQueue(tmp_path)
        created = queue.create(title="feature", base_branch="feat/prior")
        reloaded = queue.get(created.id)
        assert reloaded is not None
        assert reloaded.metadata.get("base_branch") == "feat/prior"

    def test_feature_branch_defaults_when_no_base_branch(self, tmp_path):
        """Feature branch name uses feat/<id> when no explicit branch given."""
        queue = FileFeatureQueue(tmp_path)
        feature = queue.create(title="my-feature")
        branch = feature.metadata.get("branch", "")
        assert branch.startswith("feat/")
        assert feature.id in branch

    def test_base_branch_does_not_override_feature_branch(self, tmp_path):
        """base_branch is separate from the feature's own branch."""
        queue = FileFeatureQueue(tmp_path)
        feature = queue.create(
            title="feature",
            branch="feat/custom-branch",
            base_branch="main",
        )
        assert feature.metadata["branch"] == "feat/custom-branch"
        assert feature.metadata["base_branch"] == "main"


# ---------------------------------------------------------------------------
# F2: per-Task model override
# ---------------------------------------------------------------------------

class TestF2PerTaskModel:

    def _write_tasks(self, path: Path, tasks: list[dict]) -> Path:
        p = path / "task_list.json"
        p.write_text(json.dumps(tasks, indent=2))
        return p

    def test_task_model_field_parsed(self, tmp_path):
        """Task.model is loaded from JSON."""
        tasks = [
            {"id": "F-001", "description": "setup", "model": "claude-haiku-4-5"},
        ]
        tl = self._write_tasks(tmp_path, tasks)
        dag = TaskDAG.load(tl)
        task = dag.get_task("F-001")
        assert task is not None
        assert task.model == "claude-haiku-4-5"

    def test_task_model_defaults_to_empty(self, tmp_path):
        """Task.model defaults to empty string when not in JSON."""
        tasks = [
            {"id": "F-001", "description": "setup"},
        ]
        tl = self._write_tasks(tmp_path, tasks)
        dag = TaskDAG.load(tl)
        task = dag.get_task("F-001")
        assert task is not None
        assert task.model == ""

    @pytest.mark.asyncio
    async def test_dag_engine_passes_model_to_session_fn(self, tmp_path):
        """DAGEngine passes task.model as second arg to run_session_fn."""
        from nezha.dag.engine import DAGEngine

        tasks = [
            {"id": "F-001", "description": "haiku task", "model": "claude-haiku-4-5"},
        ]
        tl = self._write_tasks(tmp_path, tasks)

        captured_models = []

        def mock_session(prompt_path, model_override="", env_override=None):
            captured_models.append(model_override)
            data = json.loads(tl.read_text())
            data[0]["passes"] = True
            tl.write_text(json.dumps(data))
            return SessionResult(status="completed", num_turns=1, cost_usd=0.01, duration_ms=100)

        engine = DAGEngine(
            task_list_path=tl,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
        )
        await engine.run("worker.md")

        assert captured_models == ["claude-haiku-4-5"]

    @pytest.mark.asyncio
    async def test_dag_engine_passes_empty_model_when_not_set(self, tmp_path):
        """DAGEngine passes empty string as model when task has no model."""
        from nezha.dag.engine import DAGEngine

        tasks = [{"id": "F-001", "description": "normal task"}]
        tl = self._write_tasks(tmp_path, tasks)

        captured_models = []

        def mock_session(prompt_path, model_override="", env_override=None):
            captured_models.append(model_override)
            data = json.loads(tl.read_text())
            data[0]["passes"] = True
            tl.write_text(json.dumps(data))
            return SessionResult(status="completed", num_turns=1, cost_usd=0.01, duration_ms=100)

        engine = DAGEngine(
            task_list_path=tl,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
        )
        await engine.run("worker.md")

        assert captured_models == [""]

    def test_task_dataclass_model_field(self):
        """Task dataclass has model field with empty default."""
        task = Task(id="F-001")
        assert hasattr(task, "model")
        assert task.model == ""

    def test_task_dataclass_model_set(self):
        """Task dataclass model field can be set."""
        task = Task(id="F-001", model="claude-haiku-4-5")
        assert task.model == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# F3: Integration verification (post-DAG session)
# ---------------------------------------------------------------------------

class TestF3IntegrationVerification:

    def _write_tasks(self, path: Path, tasks: list[dict]) -> Path:
        p = path / "task_list.json"
        p.write_text(json.dumps(tasks, indent=2))
        return p

    @pytest.mark.asyncio
    async def test_integration_prompt_runs_after_all_done(self, tmp_path):
        """DAGEngine runs integration session after all tasks complete."""
        from nezha.dag.engine import DAGEngine

        tasks = [{"id": "F-001", "description": "setup"}]
        tl = self._write_tasks(tmp_path, tasks)
        integration_prompt = tmp_path / "integration.md"
        integration_prompt.write_text("# Integration check")

        call_log = []

        def mock_session(prompt_path, model_override="", env_override=None):
            call_log.append(prompt_path)
            if prompt_path != str(integration_prompt):
                data = json.loads(tl.read_text())
                data[0]["passes"] = True
                tl.write_text(json.dumps(data))
            return SessionResult(status="completed", num_turns=1, cost_usd=0.01, duration_ms=100)

        engine = DAGEngine(
            task_list_path=tl,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            integration_prompt_path=str(integration_prompt),
        )
        await engine.run("worker.md")

        assert call_log[-1] == str(integration_prompt), "Integration session should be last"
        assert len(call_log) == 2  # 1 task session + 1 integration session

    @pytest.mark.asyncio
    async def test_no_integration_session_when_not_configured(self, tmp_path):
        """No extra session when integration_prompt_path is None."""
        from nezha.dag.engine import DAGEngine

        tasks = [{"id": "F-001", "description": "setup"}]
        tl = self._write_tasks(tmp_path, tasks)

        call_count = [0]

        def mock_session(prompt_path, model_override="", env_override=None):
            call_count[0] += 1
            data = json.loads(tl.read_text())
            data[0]["passes"] = True
            tl.write_text(json.dumps(data))
            return SessionResult(status="completed", num_turns=1, cost_usd=0.01, duration_ms=100)

        engine = DAGEngine(
            task_list_path=tl,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            integration_prompt_path=None,
        )
        await engine.run("worker.md")

        assert call_count[0] == 1  # Only the task session, no integration

    @pytest.mark.asyncio
    async def test_integration_session_cost_counted(self, tmp_path):
        """Integration session cost is included in total_cost_usd."""
        from nezha.dag.engine import DAGEngine

        tasks = [{"id": "F-001", "description": "setup"}]
        tl = self._write_tasks(tmp_path, tasks)
        integration_prompt = tmp_path / "integration.md"
        integration_prompt.write_text("# Integration check")

        def mock_session(prompt_path, model_override="", env_override=None):
            if prompt_path != str(integration_prompt):
                data = json.loads(tl.read_text())
                data[0]["passes"] = True
                tl.write_text(json.dumps(data))
            return SessionResult(status="completed", num_turns=1, cost_usd=0.10, duration_ms=100)

        engine = DAGEngine(
            task_list_path=tl,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            integration_prompt_path=str(integration_prompt),
        )
        result = await engine.run("worker.md")

        assert result.total_cost_usd == pytest.approx(0.20)  # task + integration
        assert result.sessions_run == 2

    @pytest.mark.asyncio
    async def test_integration_skipped_when_not_all_done(self, tmp_path):
        """Integration session does NOT run if DAG didn't complete (deadlocked)."""
        from nezha.dag.engine import DAGEngine

        # Task F-002 depends on F-001 but F-001 never passes → deadlock
        tasks = [
            {"id": "F-001", "description": "blocker"},
            {"id": "F-002", "description": "depends", "depends_on": ["F-001"]},
        ]
        tl = self._write_tasks(tmp_path, tasks)
        integration_prompt = tmp_path / "integration.md"
        integration_prompt.write_text("# Integration check")

        integration_called = [False]

        def mock_session(prompt_path, model_override="", env_override=None):
            if prompt_path == str(integration_prompt):
                integration_called[0] = True
            # Never mark F-001 as passes → deadlock after MAX_CONSECUTIVE
            return SessionResult(status="completed", num_turns=1, cost_usd=0.01, duration_ms=100)

        engine = DAGEngine(
            task_list_path=tl,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            integration_prompt_path=str(integration_prompt),
        )
        result = await engine.run("worker.md")

        assert result.exit_reason != "all_done"
        assert not integration_called[0]
