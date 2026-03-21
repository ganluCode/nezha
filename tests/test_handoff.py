"""Tests for the VibeCoding handoff context generator (F-003)."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from nezha.dag.handoff import (
    generate_handoff_context,
    _load_tasks as _load_features,
    _find_target_task as _find_target_feature,
    _load_report,
    _build_target_section,
    _extract_attempt_history,
    _extract_last_error,
    _get_changed_files,
    _get_blocked_downstream,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path):
    """Workspace directory with feature_list.json."""
    return tmp_path


@pytest.fixture
def simple_features():
    """Feature list with mixed statuses."""
    return [
        {
            "id": "F-001",
            "description": "Feature one",
            "acceptance": ["criterion 1", "criterion 2"],
            "depends_on": [],
            "passes": True,
        },
        {
            "id": "F-002",
            "description": "Feature two",
            "acceptance": ["criterion A"],
            "depends_on": [],
            "passes": False,
        },
        {
            "id": "F-003",
            "description": "Feature three depends on F-001",
            "acceptance": ["criterion X", "criterion Y"],
            "depends_on": ["F-001"],
            "passes": False,
        },
    ]


@pytest.fixture
def rework_features():
    """Feature list with a rework feature."""
    return [
        {
            "id": "F-001",
            "description": "Feature one",
            "acceptance": ["criterion 1"],
            "depends_on": [],
            "passes": True,
        },
        {
            "id": "F-002",
            "description": "Feature two with rework",
            "acceptance": ["criterion A"],
            "depends_on": [],
            "passes": False,
            "rework": True,
            "rework_note": "Test failed: assertion error in test_foo",
            "rework_count": 1,
        },
    ]


@pytest.fixture
def feature_list_path(workspace, simple_features):
    """Write simple_features to workspace/feature_list.json."""
    path = workspace / "feature_list.json"
    path.write_text(json.dumps(simple_features), encoding="utf-8")
    return path


@pytest.fixture
def sample_report():
    """Sample execution-report.md content."""
    return """# Execution Report

Generated: 2025-01-15 10:00:00 UTC
Started: 2025-01-15 09:00:00 UTC
Exit reason: max_iterations

## Overview

| Status | Count |
|--------|-------|
| Completed | 1/3 |
| Failed/Rework | 1 |
| Blocked | 0 |
| Skipped | 0 |
| Ready | 1 |

Total sessions: 3
Total cost: $0.5000
Total time: 120000ms

## Session Timeline

| # | Feature | Type | Duration | Cost | Result |
|---|---------|------|----------|------|--------|
| 1 | F-001 | new | 30000ms | $0.1000 | completed |
| 2 | F-002 | new | 45000ms | $0.2000 | failed |
| 3 | F-002 | rework | 45000ms | $0.2000 | failed |

## Failure Records

### F-002

- **Status**: rework
- **Attempts**: 2
- **Last error**: Verification failed: test_foo assertion error

Session history:

- Session 2: failed — Verification failed: test_foo assertion error
- Session 3: failed — Verification failed: still broken

## Blocked Dependencies

No blocked features.
"""


# ---------------------------------------------------------------------------
# _load_features tests
# ---------------------------------------------------------------------------

class TestLoadFeatures:
    def test_load_valid_features(self, feature_list_path):
        features = _load_features(feature_list_path)
        assert len(features) == 3
        assert features[0]["id"] == "F-001"

    def test_load_missing_file(self, workspace):
        features = _load_features(workspace / "nonexistent.json")
        assert features == []

    def test_load_invalid_json(self, workspace):
        path = workspace / "bad.json"
        path.write_text("not valid json", encoding="utf-8")
        features = _load_features(path)
        assert features == []

    def test_load_empty_list(self, workspace):
        path = workspace / "feature_list.json"
        path.write_text("[]", encoding="utf-8")
        features = _load_features(path)
        assert features == []


# ---------------------------------------------------------------------------
# _find_target_feature tests
# ---------------------------------------------------------------------------

class TestFindTargetFeature:
    def test_find_rework_feature_first(self, rework_features):
        target = _find_target_feature(rework_features)
        assert target is not None
        assert target["id"] == "F-002"
        assert target["rework"] is True

    def test_find_ready_feature(self, simple_features):
        target = _find_target_feature(simple_features)
        assert target is not None
        # F-002 is ready (no deps, not passing), F-003 depends on F-001 (completed) so also ready
        # F-002 comes first alphabetically
        assert target["id"] == "F-002"

    def test_find_blocked_feature_skipped(self):
        """Feature with unmet deps should not be selected."""
        features = [
            {"id": "F-001", "description": "A", "depends_on": [], "passes": False},
            {"id": "F-002", "description": "B", "depends_on": ["F-001"], "passes": False},
        ]
        target = _find_target_feature(features)
        assert target is not None
        assert target["id"] == "F-001"  # F-002 blocked by F-001

    def test_all_completed(self):
        features = [
            {"id": "F-001", "description": "A", "depends_on": [], "passes": True},
            {"id": "F-002", "description": "B", "depends_on": [], "passes": True},
        ]
        target = _find_target_feature(features)
        assert target is None

    def test_empty_features(self):
        target = _find_target_feature([])
        assert target is None

    def test_rework_priority_over_ready(self):
        """Rework features should be picked before ready features."""
        features = [
            {"id": "F-001", "description": "Ready", "depends_on": [], "passes": False},
            {"id": "F-002", "description": "Rework", "depends_on": [], "passes": False,
             "rework": True, "rework_note": "broken"},
        ]
        target = _find_target_feature(features)
        assert target["id"] == "F-002"

    def test_only_blocked_features(self):
        """If all non-passing features are blocked, return None."""
        features = [
            {"id": "F-001", "description": "A", "depends_on": ["F-099"], "passes": False},
        ]
        target = _find_target_feature(features)
        assert target is None


# ---------------------------------------------------------------------------
# _load_report tests
# ---------------------------------------------------------------------------

class TestLoadReport:
    def test_load_existing_report(self, workspace, sample_report):
        path = workspace / "execution-report.md"
        path.write_text(sample_report, encoding="utf-8")
        content = _load_report(path)
        assert "# Execution Report" in content

    def test_load_missing_report(self, workspace):
        content = _load_report(workspace / "nonexistent.md")
        assert content == ""


# ---------------------------------------------------------------------------
# _build_target_section tests
# ---------------------------------------------------------------------------

class TestBuildTargetSection:
    def test_basic_target(self):
        target = {
            "id": "F-002",
            "description": "Feature two",
            "acceptance": ["criterion A", "criterion B"],
        }
        section = _build_target_section(target)
        assert "### Target Task" in section
        assert "**F-002**" in section
        assert "Feature two" in section
        assert "criterion A" in section
        assert "criterion B" in section

    def test_rework_target(self):
        target = {
            "id": "F-002",
            "description": "Feature two",
            "acceptance": ["criterion A"],
            "rework": True,
            "rework_count": 2,
            "rework_note": "Tests failed",
        }
        section = _build_target_section(target)
        assert "REWORK" in section
        assert "attempt #2" in section
        assert "Tests failed" in section

    def test_no_acceptance(self):
        target = {"id": "F-001", "description": "Simple"}
        section = _build_target_section(target)
        assert "### Target Task" in section
        assert "Acceptance Criteria" not in section

    def test_no_description(self):
        target = {"id": "F-001"}
        section = _build_target_section(target)
        assert "No description" in section


# ---------------------------------------------------------------------------
# _extract_attempt_history tests
# ---------------------------------------------------------------------------

class TestExtractAttemptHistory:
    def test_extract_timeline_entries(self, sample_report):
        history = _extract_attempt_history("F-002", sample_report)
        assert "### Previous Attempts" in history
        assert "Session 2" in history
        assert "Session 3" in history
        assert "failed" in history

    def test_no_entries_for_feature(self, sample_report):
        history = _extract_attempt_history("F-999", sample_report)
        assert history == ""

    def test_empty_report(self):
        history = _extract_attempt_history("F-001", "")
        assert history == ""

    def test_single_entry(self, sample_report):
        history = _extract_attempt_history("F-001", sample_report)
        assert "### Previous Attempts" in history
        assert "Session 1" in history
        assert "completed" in history


# ---------------------------------------------------------------------------
# _extract_last_error tests
# ---------------------------------------------------------------------------

class TestExtractLastError:
    def test_error_from_rework_note(self):
        target = {
            "id": "F-002",
            "rework_note": "Test failed: assertion error",
        }
        error = _extract_last_error(target, "")
        assert "### Last Error" in error
        assert "Test failed: assertion error" in error

    def test_error_from_report(self, sample_report):
        target = {"id": "F-002"}
        error = _extract_last_error(target, sample_report)
        assert "### Last Error" in error
        assert "Verification failed" in error

    def test_no_error(self):
        target = {"id": "F-001"}
        error = _extract_last_error(target, "")
        assert error == ""

    def test_rework_note_takes_priority(self, sample_report):
        """rework_note should be used even when report has error info."""
        target = {
            "id": "F-002",
            "rework_note": "Custom rework note",
        }
        error = _extract_last_error(target, sample_report)
        assert "Custom rework note" in error
        # Should NOT contain report error since rework_note takes priority
        assert "Verification failed" not in error


# ---------------------------------------------------------------------------
# _get_changed_files tests
# ---------------------------------------------------------------------------

class TestGetChangedFiles:
    def test_git_log_returns_files(self, workspace):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="nezha/foo.py\nnezha/bar.py\nnezha/foo.py\n",
        )
        with patch("nezha.dag.handoff.subprocess.run", return_value=mock_result):
            result = _get_changed_files(workspace, "F-001")
        assert "### Changed Files" in result
        assert "`nezha/bar.py`" in result
        assert "`nezha/foo.py`" in result

    def test_git_log_no_results(self, workspace):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="",
        )
        with patch("nezha.dag.handoff.subprocess.run", return_value=mock_result):
            result = _get_changed_files(workspace, "F-001")
        assert result == ""

    def test_git_log_failure(self, workspace):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="",
        )
        with patch("nezha.dag.handoff.subprocess.run", return_value=mock_result):
            result = _get_changed_files(workspace, "F-001")
        assert result == ""

    def test_git_log_timeout(self, workspace):
        with patch(
            "nezha.dag.handoff.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=10),
        ):
            result = _get_changed_files(workspace, "F-001")
        assert result == ""

    def test_deduplicates_files(self, workspace):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="a.py\nb.py\na.py\nb.py\na.py\n",
        )
        with patch("nezha.dag.handoff.subprocess.run", return_value=mock_result):
            result = _get_changed_files(workspace, "F-001")
        assert result.count("`a.py`") == 1
        assert result.count("`b.py`") == 1


# ---------------------------------------------------------------------------
# _get_blocked_downstream tests
# ---------------------------------------------------------------------------

class TestGetBlockedDownstream:
    def test_has_dependents(self, simple_features):
        result = _get_blocked_downstream("F-001", simple_features)
        assert "### Blocked Downstream Tasks" in result
        assert "F-003" in result

    def test_no_dependents(self, simple_features):
        result = _get_blocked_downstream("F-003", simple_features)
        assert result == ""

    def test_multiple_dependents(self):
        features = [
            {"id": "F-001", "description": "Root", "depends_on": [], "passes": False},
            {"id": "F-002", "description": "Child A", "depends_on": ["F-001"], "passes": False},
            {"id": "F-003", "description": "Child B", "depends_on": ["F-001"], "passes": False},
        ]
        result = _get_blocked_downstream("F-001", features)
        assert "F-002" in result
        assert "F-003" in result


# ---------------------------------------------------------------------------
# generate_handoff_context integration tests
# ---------------------------------------------------------------------------

class TestGenerateHandoffContext:
    def test_generates_full_context(self, workspace, simple_features, sample_report):
        # Write feature list and report
        fl_path = workspace / "feature_list.json"
        fl_path.write_text(json.dumps(simple_features), encoding="utf-8")
        report_path = workspace / "execution-report.md"
        report_path.write_text(sample_report, encoding="utf-8")

        with patch("nezha.dag.handoff.subprocess.run") as mock_git:
            mock_git.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="src/main.py\n",
            )
            context = generate_handoff_context(workspace)

        assert "## HANDOFF CONTEXT" in context
        assert "### Target Task" in context
        assert "F-002" in context  # target feature

    def test_no_feature_list(self, workspace):
        """Returns empty string when no feature_list.json."""
        context = generate_handoff_context(workspace)
        assert context == ""

    def test_all_features_completed(self, workspace):
        """Returns empty string when all features pass."""
        features = [
            {"id": "F-001", "description": "A", "depends_on": [], "passes": True},
        ]
        fl_path = workspace / "feature_list.json"
        fl_path.write_text(json.dumps(features), encoding="utf-8")
        context = generate_handoff_context(workspace)
        assert context == ""

    def test_rework_feature_as_target(self, workspace, rework_features):
        fl_path = workspace / "feature_list.json"
        fl_path.write_text(json.dumps(rework_features), encoding="utf-8")

        with patch("nezha.dag.handoff.subprocess.run") as mock_git:
            mock_git.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="",
            )
            context = generate_handoff_context(workspace)

        assert "## HANDOFF CONTEXT" in context
        assert "F-002" in context
        assert "REWORK" in context
        assert "Test failed: assertion error in test_foo" in context

    def test_with_no_report(self, workspace, simple_features):
        """Context still generated when execution-report.md doesn't exist."""
        fl_path = workspace / "feature_list.json"
        fl_path.write_text(json.dumps(simple_features), encoding="utf-8")

        with patch("nezha.dag.handoff.subprocess.run") as mock_git:
            mock_git.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="",
            )
            context = generate_handoff_context(workspace)

        assert "## HANDOFF CONTEXT" in context
        assert "### Target Task" in context
        assert "F-002" in context

    def test_custom_paths(self, workspace, simple_features):
        """Custom paths for feature_list and report."""
        custom_fl = workspace / "custom" / "features.json"
        custom_fl.parent.mkdir(parents=True)
        custom_fl.write_text(json.dumps(simple_features), encoding="utf-8")

        custom_report = workspace / "custom" / "report.md"
        custom_report.write_text("# Empty report", encoding="utf-8")

        with patch("nezha.dag.handoff.subprocess.run") as mock_git:
            mock_git.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="",
            )
            context = generate_handoff_context(
                workspace,
                task_list_path=custom_fl,
                execution_report_path=custom_report,
            )

        assert "## HANDOFF CONTEXT" in context

    def test_context_includes_acceptance_criteria(self, workspace, simple_features):
        fl_path = workspace / "feature_list.json"
        fl_path.write_text(json.dumps(simple_features), encoding="utf-8")

        with patch("nezha.dag.handoff.subprocess.run") as mock_git:
            mock_git.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="",
            )
            context = generate_handoff_context(workspace)

        assert "Acceptance Criteria" in context
        assert "criterion A" in context

    def test_context_includes_blocked_downstream(self, workspace):
        """Downstream features are listed when target has dependents."""
        features = [
            {"id": "F-001", "description": "Root feature", "depends_on": [],
             "passes": False, "acceptance": ["do something"]},
            {"id": "F-002", "description": "Depends on root", "depends_on": ["F-001"],
             "passes": False},
        ]
        fl_path = workspace / "feature_list.json"
        fl_path.write_text(json.dumps(features), encoding="utf-8")

        with patch("nezha.dag.handoff.subprocess.run") as mock_git:
            mock_git.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="",
            )
            context = generate_handoff_context(workspace)

        assert "### Blocked Downstream Tasks" in context
        assert "F-002" in context

    def test_context_includes_changed_files(self, workspace, simple_features):
        fl_path = workspace / "feature_list.json"
        fl_path.write_text(json.dumps(simple_features), encoding="utf-8")

        with patch("nezha.dag.handoff.subprocess.run") as mock_git:
            mock_git.return_value = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout="nezha/engine.py\ntests/test_engine.py\n",
            )
            context = generate_handoff_context(workspace)

        assert "### Changed Files" in context
        assert "`nezha/engine.py`" in context
        assert "`tests/test_engine.py`" in context

    def test_handoff_context_injected_into_variable(self, workspace, simple_features):
        """The context is a string suitable for {{handoff_context}} injection."""
        fl_path = workspace / "feature_list.json"
        fl_path.write_text(json.dumps(simple_features), encoding="utf-8")

        with patch("nezha.dag.handoff.subprocess.run") as mock_git:
            mock_git.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="",
            )
            context = generate_handoff_context(workspace)

        # Should be a non-empty string
        assert isinstance(context, str)
        assert len(context) > 0
        # Should start with the header
        assert context.startswith("## HANDOFF CONTEXT")

        # Simulate template rendering
        template = "Before\n\n{{handoff_context}}\n\nAfter"
        rendered = template.replace("{{handoff_context}}", context)
        assert "## HANDOFF CONTEXT" in rendered
        assert "Before" in rendered
        assert "After" in rendered


# ---------------------------------------------------------------------------
# Vibe prompt template integration
# ---------------------------------------------------------------------------

class TestVibePromptIntegration:
    def test_vibe_prompt_has_handoff_placeholder(self):
        """Verify vibe.md contains the {{handoff_context}} placeholder."""
        vibe_path = Path(__file__).parent.parent / "src" / "nezha" / "templates" / "prompts" / "coding" / "vibe.md"
        if vibe_path.exists():
            content = vibe_path.read_text(encoding="utf-8")
            assert "{{handoff_context}}" in content

    def test_vibe_subprocess_runner_imports_handoff(self):
        """Verify the vibe subprocess runner imports handoff module."""
        from nezha.pipeline.session import _VIBE_SUBPROCESS_RUNNER
        assert "generate_handoff_context" in _VIBE_SUBPROCESS_RUNNER
        assert "handoff_context" in _VIBE_SUBPROCESS_RUNNER
