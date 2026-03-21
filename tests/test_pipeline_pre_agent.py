"""Tests for pipeline pre-agent auto-invocation logic."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from nezha.config import (
    AgentConfig,
    AgentMeta,
    ArtifactConfig,
    EngineConfig,
    ExecutorConfig,
    IOConfig,
    PipelineConfig,
    PreAgentConfig,
    SessionConfig,
    VerificationConfig,
    load_agent_config,
    _parse_pipeline_config,
)
from nezha.engine import SessionResult
from nezha.pipeline.session import _run_pre_agents


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path):
    return tmp_path


@pytest.fixture
def project_root(tmp_path):
    """Create a mock project root with agents/ directory."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "agents").mkdir()
    (root / "prompts" / "planner").mkdir(parents=True)
    (root / "executor.yaml").write_text("executor:\n  name: test\n")
    return root


def _write_agent_yaml(agents_dir: Path, name: str, callable: bool = True, category: str = "planning"):
    """Helper to write a minimal agent YAML file."""
    config = {
        "agent": {
            "name": name,
            "category": category,
            "callable": callable,
            "description": f"Test {name}",
        },
        "engine": {
            "model": "claude-sonnet-4-5-20250929",
            "max_turns": 50,
        },
        "session": {
            "mode": "single_round",
            "prompts": {"worker": "planner/worker.md"},
        },
    }
    path = agents_dir / f"{name}.yaml"
    path.write_text(yaml.dump(config))
    return path


# ---------------------------------------------------------------------------
# Config parsing tests
# ---------------------------------------------------------------------------

class TestConfigParsing:
    """Test config.py correctly parses category, callable, and pipeline fields."""

    def test_agent_meta_defaults(self):
        """AgentMeta defaults: category='', callable=False."""
        meta = AgentMeta()
        assert meta.category == ""
        assert meta.callable is False

    def test_agent_meta_with_values(self):
        """AgentMeta accepts category and callable."""
        meta = AgentMeta(name="test", category="planning", callable=True)
        assert meta.category == "planning"
        assert meta.callable is True

    def test_pre_agent_config(self):
        """PreAgentConfig has name and artifact fields."""
        pa = PreAgentConfig(name="planner-agent", artifact="task_list.json")
        assert pa.name == "planner-agent"
        assert pa.artifact == "task_list.json"

    def test_pipeline_config_default_empty(self):
        """PipelineConfig defaults to empty pre_agents list."""
        pc = PipelineConfig()
        assert pc.pre_agents == []

    def test_pipeline_config_with_pre_agents(self):
        """PipelineConfig holds list of PreAgentConfig."""
        pc = PipelineConfig(pre_agents=[
            PreAgentConfig(name="planner-agent", artifact="task_list.json"),
        ])
        assert len(pc.pre_agents) == 1
        assert pc.pre_agents[0].name == "planner-agent"

    def test_agent_config_has_pipeline(self):
        """AgentConfig includes pipeline field."""
        config = AgentConfig()
        assert isinstance(config.pipeline, PipelineConfig)
        assert config.pipeline.pre_agents == []

    def test_parse_pipeline_config_none(self):
        """_parse_pipeline_config returns empty PipelineConfig for None."""
        result = _parse_pipeline_config(None)
        assert result.pre_agents == []

    def test_parse_pipeline_config_empty(self):
        """_parse_pipeline_config returns empty PipelineConfig for empty dict."""
        result = _parse_pipeline_config({})
        assert result.pre_agents == []

    def test_parse_pipeline_config_with_pre_agents(self):
        """_parse_pipeline_config correctly parses pre_agents list."""
        data = {
            "pre_agents": [
                {"name": "planner-agent", "artifact": "task_list.json"},
                {"name": "validator-agent", "artifact": "validation_report.md"},
            ]
        }
        result = _parse_pipeline_config(data)
        assert len(result.pre_agents) == 2
        assert result.pre_agents[0].name == "planner-agent"
        assert result.pre_agents[0].artifact == "task_list.json"
        assert result.pre_agents[1].name == "validator-agent"

    def test_load_agent_config_with_pipeline(self, tmp_path):
        """load_agent_config parses pipeline section from YAML."""
        config_data = {
            "agent": {"name": "coding-agent", "category": "coding"},
            "session": {
                "prompts": {"worker": "coding/worker.md"},
            },
            "pipeline": {
                "pre_agents": [
                    {"name": "planner-agent", "artifact": "task_list.json"},
                ]
            },
        }
        config_path = tmp_path / "coding-agent.yaml"
        config_path.write_text(yaml.dump(config_data))

        config = load_agent_config(config_path)
        assert config.agent.category == "coding"
        assert len(config.pipeline.pre_agents) == 1
        assert config.pipeline.pre_agents[0].name == "planner-agent"

    def test_load_agent_config_callable_true(self, tmp_path):
        """load_agent_config correctly parses callable: true."""
        config_data = {
            "agent": {"name": "planner", "callable": True, "category": "planning"},
            "session": {"prompts": {"worker": "planner/worker.md"}},
        }
        config_path = tmp_path / "planner.yaml"
        config_path.write_text(yaml.dump(config_data))

        config = load_agent_config(config_path)
        assert config.agent.callable is True
        assert config.agent.category == "planning"

    def test_load_agent_config_no_pipeline(self, tmp_path):
        """load_agent_config works without pipeline section (backward compat)."""
        config_data = {
            "agent": {"name": "legacy-agent"},
            "session": {"prompts": {"worker": "legacy/worker.md"}},
        }
        config_path = tmp_path / "legacy.yaml"
        config_path.write_text(yaml.dump(config_data))

        config = load_agent_config(config_path)
        assert config.pipeline.pre_agents == []
        assert config.agent.callable is False
        assert config.agent.category == ""


# ---------------------------------------------------------------------------
# _run_pre_agents tests
# ---------------------------------------------------------------------------

class TestRunPreAgents:
    """Test _run_pre_agents function logic."""

    def test_no_pre_agents_returns_empty(self, workspace, project_root):
        """Returns empty list when no pre_agents configured."""
        agent_config = AgentConfig(
            pipeline=PipelineConfig(pre_agents=[]),
        )
        results = _run_pre_agents(
            agent_config=agent_config,
            workspace=workspace,
            project_root=project_root,
            executor_config_path=project_root / "executor.yaml",
            prompts_dir=project_root / "prompts",
        )
        assert results == []

    def test_artifact_already_exists_skips(self, workspace, project_root):
        """Skips pre-agent when artifact already exists."""
        (workspace / "task_list.json").write_text("[]")

        agent_config = AgentConfig(
            pipeline=PipelineConfig(pre_agents=[
                PreAgentConfig(name="planner-agent", artifact="task_list.json"),
            ]),
        )
        results = _run_pre_agents(
            agent_config=agent_config,
            workspace=workspace,
            project_root=project_root,
            executor_config_path=project_root / "executor.yaml",
            prompts_dir=project_root / "prompts",
        )
        assert results == []

    def test_pre_agent_config_not_found(self, workspace, project_root):
        """Returns error when pre-agent YAML doesn't exist."""
        agent_config = AgentConfig(
            pipeline=PipelineConfig(pre_agents=[
                PreAgentConfig(name="nonexistent-agent", artifact="output.json"),
            ]),
        )
        results = _run_pre_agents(
            agent_config=agent_config,
            workspace=workspace,
            project_root=project_root,
            executor_config_path=project_root / "executor.yaml",
            prompts_dir=project_root / "prompts",
        )
        assert len(results) == 1
        assert results[0].status == "error"
        assert "not found" in results[0].error

    def test_non_callable_agent_rejected(self, workspace, project_root):
        """Returns error when pre-agent has callable=false."""
        _write_agent_yaml(project_root / "agents", "private-agent", callable=False)

        agent_config = AgentConfig(
            pipeline=PipelineConfig(pre_agents=[
                PreAgentConfig(name="private-agent", artifact="output.json"),
            ]),
        )
        results = _run_pre_agents(
            agent_config=agent_config,
            workspace=workspace,
            project_root=project_root,
            executor_config_path=project_root / "executor.yaml",
            prompts_dir=project_root / "prompts",
        )
        assert len(results) == 1
        assert results[0].status == "error"
        assert "not callable" in results[0].error

    def test_pre_agent_no_worker_prompt(self, workspace, project_root):
        """Returns error when pre-agent has no worker prompt."""
        # Write agent YAML with empty prompts
        config = {
            "agent": {"name": "bad-agent", "callable": True},
            "session": {"prompts": {}},
        }
        (project_root / "agents" / "bad-agent.yaml").write_text(yaml.dump(config))

        agent_config = AgentConfig(
            pipeline=PipelineConfig(pre_agents=[
                PreAgentConfig(name="bad-agent", artifact="output.json"),
            ]),
        )
        results = _run_pre_agents(
            agent_config=agent_config,
            workspace=workspace,
            project_root=project_root,
            executor_config_path=project_root / "executor.yaml",
            prompts_dir=project_root / "prompts",
        )
        assert len(results) == 1
        assert results[0].status == "error"
        assert "no worker prompt" in results[0].error

    @patch("nezha.pipeline.session._run_isolated_session")
    def test_callable_agent_invoked(self, mock_run, workspace, project_root):
        """Callable pre-agent is invoked via _run_isolated_session."""
        _write_agent_yaml(project_root / "agents", "planner-agent", callable=True)

        mock_run.return_value = SessionResult(
            status="completed", num_turns=10, cost_usd=0.5
        )

        agent_config = AgentConfig(
            pipeline=PipelineConfig(pre_agents=[
                PreAgentConfig(name="planner-agent", artifact="task_list.json"),
            ]),
        )
        results = _run_pre_agents(
            agent_config=agent_config,
            workspace=workspace,
            project_root=project_root,
            executor_config_path=project_root / "executor.yaml",
            prompts_dir=project_root / "prompts",
        )
        assert len(results) == 1
        assert results[0].status == "completed"
        mock_run.assert_called_once()

        # Verify correct args passed
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["workspace"] == workspace
        assert "planner-agent.yaml" in str(call_kwargs["agent_config_path"])
        assert call_kwargs["prompt_path"] == "planner/worker.md"

    @patch("nezha.pipeline.session._run_isolated_session")
    def test_multiple_pre_agents(self, mock_run, workspace, project_root):
        """Multiple pre-agents are processed in order."""
        _write_agent_yaml(project_root / "agents", "agent-a", callable=True)
        _write_agent_yaml(project_root / "agents", "agent-b", callable=True)

        mock_run.return_value = SessionResult(status="completed")

        agent_config = AgentConfig(
            pipeline=PipelineConfig(pre_agents=[
                PreAgentConfig(name="agent-a", artifact="output-a.json"),
                PreAgentConfig(name="agent-b", artifact="output-b.json"),
            ]),
        )
        results = _run_pre_agents(
            agent_config=agent_config,
            workspace=workspace,
            project_root=project_root,
            executor_config_path=project_root / "executor.yaml",
            prompts_dir=project_root / "prompts",
        )
        assert len(results) == 2
        assert mock_run.call_count == 2

    @patch("nezha.pipeline.session._run_isolated_session")
    def test_skip_existing_invoke_missing(self, mock_run, workspace, project_root):
        """Skips pre-agents whose artifacts exist, invokes those with missing artifacts."""
        _write_agent_yaml(project_root / "agents", "agent-a", callable=True)
        _write_agent_yaml(project_root / "agents", "agent-b", callable=True)

        # agent-a's artifact already exists
        (workspace / "output-a.json").write_text("{}")

        mock_run.return_value = SessionResult(status="completed")

        agent_config = AgentConfig(
            pipeline=PipelineConfig(pre_agents=[
                PreAgentConfig(name="agent-a", artifact="output-a.json"),
                PreAgentConfig(name="agent-b", artifact="output-b.json"),
            ]),
        )
        results = _run_pre_agents(
            agent_config=agent_config,
            workspace=workspace,
            project_root=project_root,
            executor_config_path=project_root / "executor.yaml",
            prompts_dir=project_root / "prompts",
        )
        # Only agent-b was invoked
        assert len(results) == 1
        assert mock_run.call_count == 1
        call_kwargs = mock_run.call_args[1]
        assert "agent-b.yaml" in str(call_kwargs["agent_config_path"])


# ---------------------------------------------------------------------------
# Planner agent YAML loading tests
# ---------------------------------------------------------------------------

class TestPlannerAgentYaml:
    """Test that planner-agent.yaml loads correctly."""

    def test_load_planner_agent_config(self):
        """planner-agent.yaml loads with correct category and callable."""
        config_path = Path(__file__).parent.parent / "src" / "nezha" / "templates" / "agents" / "planner-agent.yaml"
        if not config_path.exists():
            pytest.skip("planner-agent.yaml not found")

        config = load_agent_config(config_path)
        assert config.agent.name == "planner-agent"
        assert config.agent.category == "planning"
        assert config.agent.callable is True
        assert config.session.mode in ("single_round", "direct")
        assert "worker" in config.session.prompts

    def test_load_evolve_agent_config_with_pipeline(self):
        """evolve-agent.yaml loads with pipeline.pre_agents."""
        config_path = Path(__file__).parent.parent / "src" / "nezha" / "templates" / "agents" / "evolve-agent.yaml"
        if not config_path.exists():
            pytest.skip("evolve-agent.yaml not found")

        config = load_agent_config(config_path)
        assert config.agent.category == "coding"
        assert len(config.pipeline.pre_agents) >= 1
        assert config.pipeline.pre_agents[0].name == "planner-agent"
        assert config.pipeline.pre_agents[0].artifact == "task_list.json"
