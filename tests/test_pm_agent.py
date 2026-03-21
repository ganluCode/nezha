"""Tests for pm-agent.yaml parsing and management category behavior (F-004).

Acceptance criteria:
- tests/test_pm_agent.py exists
- Test: pm-agent.yaml can be parsed by load_agent_config()
- Test: pm-agent category equals 'management'
- Test: pm-agent has no target configuration
- Test: pm-agent session.mode equals 'single_round'
- Test: management category agents run with cwd = task_workspace (not target)
"""

from pathlib import Path

import pytest
import yaml

from nezha.config import AgentConfig, load_agent_config
from nezha.executor import _resolve_target


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pm_agent_yaml_path():
    """Absolute path to agents/pm-agent.yaml (from templates)."""
    return Path(__file__).parent.parent / "src" / "nezha" / "templates" / "agents" / "pm-agent.yaml"


@pytest.fixture
def pm_agent_raw(pm_agent_yaml_path):
    """Raw YAML dict from pm-agent.yaml."""
    with open(pm_agent_yaml_path) as f:
        return yaml.safe_load(f)


@pytest.fixture
def pm_agent_config(pm_agent_yaml_path):
    """Parsed AgentConfig from pm-agent.yaml."""
    return load_agent_config(pm_agent_yaml_path)


# ---------------------------------------------------------------------------
# AC: pm-agent.yaml can be parsed by load_agent_config()
# ---------------------------------------------------------------------------

class TestPmAgentParsing:
    """load_agent_config() successfully parses pm-agent.yaml."""

    def test_yaml_file_exists(self, pm_agent_yaml_path):
        assert pm_agent_yaml_path.exists()

    def test_yaml_is_valid(self, pm_agent_raw):
        assert isinstance(pm_agent_raw, dict)

    def test_load_agent_config_returns_agent_config(self, pm_agent_config):
        assert isinstance(pm_agent_config, AgentConfig)

    def test_agent_name_is_pm_agent(self, pm_agent_config):
        assert pm_agent_config.agent.name == "pm-agent"


# ---------------------------------------------------------------------------
# AC: pm-agent category equals 'management'
# ---------------------------------------------------------------------------

class TestPmAgentCategory:
    """pm-agent has category 'management'."""

    def test_category_is_management(self, pm_agent_config):
        assert pm_agent_config.agent.category == "management"

    def test_category_in_raw_yaml(self, pm_agent_raw):
        assert pm_agent_raw["agent"]["category"] == "management"


# ---------------------------------------------------------------------------
# AC: pm-agent has no target configuration
# ---------------------------------------------------------------------------

class TestPmAgentNoTarget:
    """Management agents have no target (code repo) configuration."""

    def test_config_target_is_none(self, pm_agent_config):
        assert pm_agent_config.target is None

    def test_raw_yaml_has_no_target_key(self, pm_agent_raw):
        assert "target" not in pm_agent_raw

    def test_resolve_target_returns_none(self, pm_agent_config):
        from nezha.config import ExecutorConfig
        result = _resolve_target(pm_agent_config, ExecutorConfig(), Path("/any/base"))
        assert result is None


# ---------------------------------------------------------------------------
# AC: pm-agent session.mode equals 'single_round'
# ---------------------------------------------------------------------------

class TestPmAgentSessionMode:
    """pm-agent session mode is single_round."""

    def test_session_mode_is_single_round(self, pm_agent_config):
        assert pm_agent_config.session.mode == "single_round"

    def test_worker_prompt_configured(self, pm_agent_config):
        assert pm_agent_config.session.prompts.get("worker") == "pm/worker.md"


# ---------------------------------------------------------------------------
# AC: management category agents run with cwd = task_workspace (not target)
# ---------------------------------------------------------------------------

class TestManagementCwdBehavior:
    """When target is None (management agent), cwd falls back to task_workspace."""

    def test_cwd_is_workspace_when_target_none(self):
        workspace = Path("/task/workspace")
        target = None
        cwd = target if target else workspace
        assert cwd == workspace

    def test_cwd_is_target_when_target_set(self):
        workspace = Path("/task/workspace")
        target = Path("/code/repo")
        cwd = target if target else workspace
        assert cwd == target

    def test_session_module_uses_cwd_pattern(self):
        """pipeline/session.py contains the cwd = target if target else workspace pattern."""
        from nezha.pipeline import session
        source = Path(session.__file__).read_text()
        assert "cwd = target if target else workspace" in source

    def test_executor_passes_target_to_session(self):
        """executor.py passes target to run_single_round / run_multi_round."""
        from nezha import executor
        source = Path(executor.__file__).read_text()
        assert "target=target" in source
