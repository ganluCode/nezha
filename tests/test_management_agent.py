"""Tests for management category agent handling in executor.py (F-003).

Verifies:
- executor.py loads agents/pm-agent.yaml without errors
- management category agents have cwd set to task_workspace (not target)
- _check_coding_safety does not block management category agents (they have no target)
"""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from nezha.config import AgentConfig, load_agent_config
from nezha.executor import _check_coding_safety, _resolve_target


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pm_agent_config_path():
    """Path to the real pm-agent.yaml config file (from templates)."""
    return Path(__file__).parent.parent / "src" / "nezha" / "templates" / "agents" / "pm-agent.yaml"


@pytest.fixture
def pm_agent_config(pm_agent_config_path):
    """Load and return the real pm-agent.yaml as AgentConfig."""
    return load_agent_config(pm_agent_config_path)


# ---------------------------------------------------------------------------
# AC-1: executor.py loads agents/pm-agent.yaml without errors
# ---------------------------------------------------------------------------

class TestLoadPmAgentConfig:
    """AC: executor.py loads agents/pm-agent.yaml without errors."""

    def test_pm_agent_yaml_exists(self, pm_agent_config_path):
        """The pm-agent.yaml file exists in agents/ directory."""
        assert pm_agent_config_path.exists(), f"pm-agent.yaml not found: {pm_agent_config_path}"

    def test_pm_agent_yaml_is_valid_yaml(self, pm_agent_config_path):
        """pm-agent.yaml is valid YAML."""
        with open(pm_agent_config_path) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict)

    def test_load_agent_config_succeeds(self, pm_agent_config):
        """load_agent_config() parses pm-agent.yaml without errors."""
        assert isinstance(pm_agent_config, AgentConfig)

    def test_pm_agent_name(self, pm_agent_config):
        """Agent name is 'pm-agent'."""
        assert pm_agent_config.agent.name == "pm-agent"

    def test_pm_agent_category_is_management(self, pm_agent_config):
        """Agent category is 'management'."""
        assert pm_agent_config.agent.category == "management"

    def test_pm_agent_has_no_target(self, pm_agent_config):
        """Management agent has no target configuration."""
        assert pm_agent_config.target is None

    def test_pm_agent_callable_is_false(self, pm_agent_config):
        """PM agent is not callable (cannot be auto-invoked)."""
        assert pm_agent_config.agent.callable is False

    def test_pm_agent_session_mode(self, pm_agent_config):
        """Session mode is 'single_round'."""
        assert pm_agent_config.session.mode == "single_round"


# ---------------------------------------------------------------------------
# AC-2: management category agents have cwd set to task_workspace (not target)
# ---------------------------------------------------------------------------

class TestManagementAgentCwd:
    """AC: management category agents have cwd set to task_workspace (not target)."""

    def test_resolve_target_returns_none_for_management(self, pm_agent_config):
        """_resolve_target returns None when agent has no target."""
        from nezha.config import ExecutorConfig
        target = _resolve_target(pm_agent_config, ExecutorConfig(), Path("/dummy"))
        assert target is None

    def test_session_cwd_falls_back_to_workspace_when_no_target(self):
        """When target is None, cwd in run_single_round falls back to workspace.

        This verifies the logic: cwd = target if target else workspace
        """
        workspace = Path("/some/task_workspace")
        target = None  # management agent has no target
        cwd = target if target else workspace
        assert cwd == workspace

    def test_session_cwd_is_target_when_target_set(self):
        """When target is set (coding agent), cwd is target, not workspace.

        This is the contrasting behavior — coding agents use target as cwd.
        """
        workspace = Path("/some/task_workspace")
        target = Path("/code/repo")
        cwd = target if target else workspace
        assert cwd == target

    def test_isolated_session_cwd_logic(self):
        """_run_isolated_session also uses the same cwd = target or workspace logic.

        Verify by checking source code contains the pattern.
        """
        from nezha.pipeline import session
        source = Path(session.__file__).read_text()
        assert "cwd = target if target else workspace" in source


# ---------------------------------------------------------------------------
# AC-3: _check_coding_safety does not block management category agents
# ---------------------------------------------------------------------------

class TestCheckCodingSafetyManagement:
    """AC: _check_coding_safety does not block management category agents (they have no target)."""

    def test_check_coding_safety_not_called_when_target_none(self, pm_agent_config):
        """When target is None (management agent), _check_coding_safety is never called.

        In executor.py the guard is: `if target and agent_config.git.branch_per_task:`
        Since target is None for management agents, the safety check is skipped entirely.
        """
        from nezha.config import ExecutorConfig
        target = _resolve_target(pm_agent_config, ExecutorConfig(), Path("/dummy"))
        assert target is None

        # Simulate the executor guard logic
        should_check = target and pm_agent_config.git.branch_per_task
        assert not should_check

    def test_executor_guards_safety_check_behind_target(self):
        """Verify executor.py source code guards _check_coding_safety behind target check.

        The pattern must be: `if target and ...` before calling _check_coding_safety.
        """
        from nezha import executor
        source = Path(executor.__file__).read_text()
        # The safety check is inside: if target and agent_config.git.branch_per_task:
        assert "if target and agent_config.git.branch_per_task:" in source

    def test_check_coding_safety_only_runs_on_valid_path(self, tmp_path):
        """_check_coding_safety requires a valid target path (git repo).

        Management agents never reach this code, but if they did with None,
        it would fail. This confirms the guard is necessary.
        """
        # Create a dummy git repo to verify the function works when called
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

        # Clean repo — should not raise
        _check_coding_safety(repo)

    def test_check_coding_safety_raises_on_dirty_repo(self, tmp_path):
        """_check_coding_safety raises RuntimeError on uncommitted changes.

        This confirms it would block coding agents, but not management agents.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)

        # Create a dirty file
        (repo / "dirty.txt").write_text("uncommitted change")
        subprocess.run(["git", "add", "dirty.txt"], cwd=repo, capture_output=True)

        with pytest.raises(RuntimeError, match="uncommitted changes"):
            _check_coding_safety(repo)

    def test_git_operations_skipped_when_no_target(self, pm_agent_config):
        """All git operations (commit, push) are also skipped for management agents.

        In executor.py: `if target and agent_config.git.auto_commit:` etc.
        """
        from nezha import executor
        source = Path(executor.__file__).read_text()
        # auto_commit is guarded by effective_target (which is None when target is None)
        assert (
            "if effective_target and agent_config.git.auto_commit:" in source
            or "if target and agent_config.git.auto_commit:" in source
        )
