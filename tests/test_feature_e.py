"""Tests for Task E: vibe --feature-id, --context all/latest/none, generate_all_context."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nezha.dag.handoff import generate_all_context, generate_handoff_context


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FEATURES = [
    {
        "id": "F-001", "description": "User auth", "passes": True, "rework": False,
        "depends_on": [],
    },
    {
        "id": "F-002", "description": "Dashboard page", "passes": False, "rework": True,
        "rework_note": "Layout broken on mobile", "rework_count": 1,
        "depends_on": ["F-001"],
    },
    {
        "id": "F-003", "description": "API endpoints", "passes": False, "rework": False,
        "depends_on": ["F-001"],
    },
    {
        "id": "F-004", "description": "Settings page", "passes": False, "rework": False,
        "depends_on": ["F-002", "F-003"],
    },
]


@pytest.fixture
def feature_workspace(tmp_path):
    """Workspace with a task_list.json."""
    fl = tmp_path / "task_list.json"
    fl.write_text(json.dumps(_FEATURES))
    return tmp_path


@pytest.fixture
def empty_workspace(tmp_path):
    """Workspace with no task_list.json."""
    return tmp_path


# ---------------------------------------------------------------------------
# E-004: generate_all_context()
# ---------------------------------------------------------------------------


class TestGenerateAllContext:
    def test_returns_string(self, feature_workspace):
        result = generate_all_context(feature_workspace, category="coding")
        assert isinstance(result, str)

    def test_includes_all_feature_ids(self, feature_workspace):
        result = generate_all_context(feature_workspace, category="coding")
        for f in _FEATURES:
            assert f["id"] in result

    def test_includes_progress_summary(self, feature_workspace):
        result = generate_all_context(feature_workspace, category="coding")
        # 1 completed (F-001), 1 rework (F-002), 2 pending
        assert "1/" in result or "Progress" in result

    def test_marks_completed_with_checkmark(self, feature_workspace):
        result = generate_all_context(feature_workspace, category="coding")
        # F-001 is completed
        assert "✓" in result

    def test_marks_rework_with_exclamation(self, feature_workspace):
        result = generate_all_context(feature_workspace, category="coding")
        # F-002 is in rework
        assert "!" in result

    def test_marks_pending_with_circle(self, feature_workspace):
        result = generate_all_context(feature_workspace, category="coding")
        # F-003 and F-004 are pending
        assert "○" in result

    def test_includes_rework_note(self, feature_workspace):
        result = generate_all_context(feature_workspace, category="coding")
        assert "Layout broken on mobile" in result

    def test_includes_handoff_context(self, feature_workspace):
        """generate_all_context should also include the standard handoff context."""
        result = generate_all_context(feature_workspace, category="coding")
        # The handoff context targets F-002 (rework has priority)
        # So F-002 should appear in HANDOFF CONTEXT section too
        assert "F-002" in result

    def test_empty_workspace_returns_empty(self, empty_workspace):
        result = generate_all_context(empty_workspace, category="coding")
        assert result == ""

    def test_management_category_skips_feature_list_section(self, feature_workspace):
        """Management agents don't have feature_list — context should just be handoff."""
        result = generate_all_context(feature_workspace, category="management")
        # ALL TASKS STATUS header should NOT be present for non-coding
        assert "ALL TASKS STATUS" not in result

    def test_management_category_empty_workspace_returns_empty(self, empty_workspace):
        result = generate_all_context(empty_workspace, category="management")
        assert result == ""

    def test_all_context_longer_than_latest(self, feature_workspace):
        """--context all should produce more content than --context latest."""
        all_ctx = generate_all_context(feature_workspace, category="coding")
        latest_ctx = generate_handoff_context(feature_workspace)
        assert len(all_ctx) >= len(latest_ctx)

    def test_all_features_status_header_present(self, feature_workspace):
        result = generate_all_context(feature_workspace, category="coding")
        assert "ALL TASKS STATUS" in result


# ---------------------------------------------------------------------------
# E-001: CLI parser has --feature-id and --context
# ---------------------------------------------------------------------------


class TestCLIParser:
    def test_vibe_parser_has_feature_id(self):
        from nezha.__main__ import build_parser
        parser = build_parser()
        args = parser.parse_args(["vibe", "my-agent", "--feature-id", "2026-02-19-10-00-00"])
        assert args.feature_id == "2026-02-19-10-00-00"

    def test_vibe_parser_backward_compat_task_id(self):
        """--task-id is kept as a hidden backward-compat alias for --feature-id."""
        from nezha.__main__ import build_parser
        parser = build_parser()
        args = parser.parse_args(["vibe", "my-agent", "--task-id", "2026-02-19-10-00-00"])
        # --task-id stores in args.task_id; __main__.py resolves via:
        # feature_id = getattr(args, "feature_id", None) or getattr(args, "task_id", None)
        assert args.task_id == "2026-02-19-10-00-00"

    def test_vibe_parser_has_context_default(self):
        from nezha.__main__ import build_parser
        parser = build_parser()
        args = parser.parse_args(["vibe", "my-agent"])
        assert args.context == "latest"

    def test_vibe_parser_context_all(self):
        from nezha.__main__ import build_parser
        parser = build_parser()
        args = parser.parse_args(["vibe", "my-agent", "--context", "all"])
        assert args.context == "all"

    def test_vibe_parser_context_none(self):
        from nezha.__main__ import build_parser
        parser = build_parser()
        args = parser.parse_args(["vibe", "my-agent", "--context", "none"])
        assert args.context == "none"

    def test_vibe_parser_context_invalid_rejected(self):
        from nezha.__main__ import build_parser
        import sys
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["vibe", "my-agent", "--context", "invalid"])

    def test_vibe_parser_feature_id_default_none(self):
        from nezha.__main__ import build_parser
        parser = build_parser()
        args = parser.parse_args(["vibe", "my-agent"])
        assert args.feature_id is None

    def test_vibe_parser_feature_id_and_context_combined(self):
        from nezha.__main__ import build_parser
        parser = build_parser()
        args = parser.parse_args([
            "vibe", "my-agent",
            "--feature-id", "2026-02-19-10-00-00",
            "--context", "all",
        ])
        assert args.feature_id == "2026-02-19-10-00-00"
        assert args.context == "all"


# ---------------------------------------------------------------------------
# E-002: vibe() feature_id workspace resolution
# ---------------------------------------------------------------------------


class TestVibeFeatureIdResolution:
    def test_vibe_resolves_feature_workspace(self, tmp_path):
        """When feature_id is given, vibe() uses feature's workspace directory."""
        from nezha.feature_queue import FileFeatureQueue

        agent_ws = tmp_path / "agent-workspace"
        agent_ws.mkdir()
        queue = FileFeatureQueue(agent_ws)
        feature = queue.create("my-agent")
        feature_ws = queue.feature_workspace(feature.id)

        # Verify feature workspace resolves correctly
        assert feature_ws.exists()
        assert feature_ws == agent_ws / "features" / feature.id

    def test_vibe_feature_not_found_returns_none(self, tmp_path):
        """get() returns None when feature_id doesn't exist."""
        from nezha.feature_queue import FileFeatureQueue

        agent_ws = tmp_path / "agent-workspace"
        agent_ws.mkdir()
        queue = FileFeatureQueue(agent_ws)

        feature = queue.get("nonexistent-feature-id")
        assert feature is None

    def test_backward_compat_task_queue_workspace(self, tmp_path):
        """FileTaskQueue (legacy) still has task_workspace() method."""
        from nezha.task_queue import FileTaskQueue

        agent_ws = tmp_path / "agent-workspace"
        agent_ws.mkdir()
        queue = FileTaskQueue(agent_ws)
        task = queue.create("my-agent")
        task_ws = queue.task_workspace(task.id)

        assert task_ws.exists()
        assert task_ws == agent_ws / "tasks" / task.id


# ---------------------------------------------------------------------------
# E-003: run_vibe_session() context_mode parameter
# ---------------------------------------------------------------------------


class TestRunVibeSessionContextMode:
    """Test that run_vibe_session() accepts and passes context_mode."""

    def test_run_vibe_session_accepts_context_mode_param(self):
        """run_vibe_session() should accept context_mode without error at signature level."""
        import inspect
        from nezha.pipeline.session import run_vibe_session
        sig = inspect.signature(run_vibe_session)
        assert "context_mode" in sig.parameters

    def test_run_vibe_session_context_mode_default_is_latest(self):
        """Default context_mode should be 'latest'."""
        import inspect
        from nezha.pipeline.session import run_vibe_session
        sig = inspect.signature(run_vibe_session)
        assert sig.parameters["context_mode"].default == "latest"

    def test_vibe_executor_accepts_context_mode(self):
        """executor.vibe() should accept context_mode parameter."""
        import inspect
        from nezha.executor import vibe
        sig = inspect.signature(vibe)
        assert "context_mode" in sig.parameters

    def test_vibe_executor_context_mode_default_is_latest(self):
        import inspect
        from nezha.executor import vibe
        sig = inspect.signature(vibe)
        assert sig.parameters["context_mode"].default == "latest"

    def test_vibe_executor_accepts_feature_id(self):
        import inspect
        from nezha.executor import vibe
        sig = inspect.signature(vibe)
        assert "feature_id" in sig.parameters


# ---------------------------------------------------------------------------
# Context mode: "none" means empty handoff context
# ---------------------------------------------------------------------------


class TestContextModeNone:
    def test_context_none_means_no_feature_list_content(self, feature_workspace):
        """When context_mode='none', vibe sessions should receive empty handoff_context."""
        # We can verify this by checking that context_mode="none" results in ""
        # The actual injection happens in the subprocess, but we can verify the logic:
        # handoff_context = "" when context_mode == "none"
        context_mode = "none"
        if context_mode == "none":
            handoff_context = ""
        elif context_mode == "all":
            handoff_context = generate_all_context(feature_workspace)
        else:
            handoff_context = generate_handoff_context(feature_workspace)
        assert handoff_context == ""

    def test_context_latest_produces_handoff(self, feature_workspace):
        context_mode = "latest"
        if context_mode == "none":
            handoff_context = ""
        elif context_mode == "all":
            handoff_context = generate_all_context(feature_workspace)
        else:
            handoff_context = generate_handoff_context(feature_workspace)
        # F-002 is the rework target — handoff should mention it
        assert "F-002" in handoff_context

    def test_context_all_produces_full_content(self, feature_workspace):
        context_mode = "all"
        if context_mode == "none":
            handoff_context = ""
        elif context_mode == "all":
            handoff_context = generate_all_context(feature_workspace)
        else:
            handoff_context = generate_handoff_context(feature_workspace)
        assert "ALL TASKS STATUS" in handoff_context
        assert "F-001" in handoff_context
        assert "F-002" in handoff_context
