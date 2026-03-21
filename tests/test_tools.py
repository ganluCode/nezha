"""Tests for Tool system: BaseTool Protocol, GitTool, TestTool, config parsing."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nezha.config import PostToolConfig, PipelineConfig, load_agent_config
from nezha.tools import GitTool, TestTool, create_tool
from nezha.tools.base import BaseTool, ToolResult


# ---------------------------------------------------------------------------
# Protocol / registry
# ---------------------------------------------------------------------------

class TestBaseToolProtocol:
    def test_git_tool_satisfies_protocol(self):
        assert isinstance(GitTool(), BaseTool)

    def test_test_tool_satisfies_protocol(self):
        assert isinstance(TestTool(), BaseTool)

    def test_create_tool_known(self):
        tool = create_tool("git-tool")
        assert isinstance(tool, GitTool)

        tool = create_tool("test-tool")
        assert isinstance(tool, TestTool)

    def test_create_tool_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown tool"):
            create_tool("unknown-tool")

    def test_tool_result_defaults(self):
        r = ToolResult(success=True)
        assert r.output == ""
        assert r.error == ""


# ---------------------------------------------------------------------------
# GitTool
# ---------------------------------------------------------------------------

class TestGitTool:
    def test_unknown_action(self, tmp_path):
        tool = GitTool()
        result = tool.run("nonexistent", tmp_path, {})
        assert not result.success
        assert "Unknown git-tool action" in result.error

    @patch("nezha.tools.git_tool.subprocess.run")
    def test_commit_nothing_to_commit(self, mock_run, tmp_path):
        # git add -A succeeds, status returns empty (nothing to commit)
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),   # git add -A
            MagicMock(returncode=0, stdout="", stderr=""),   # git status --porcelain
        ]
        tool = GitTool()
        result = tool.run("commit", tmp_path, {})
        assert result.success
        assert "Nothing to commit" in result.output

    @patch("nezha.tools.git_tool.subprocess.run")
    def test_commit_success(self, mock_run, tmp_path):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),          # git add -A
            MagicMock(returncode=0, stdout="M file.py", stderr=""), # git status --porcelain
            MagicMock(returncode=0, stdout="[main abc1234] chore: auto-commit", stderr=""),
        ]
        tool = GitTool()
        result = tool.run("commit", tmp_path, {"message": "chore: auto-commit"})
        assert result.success
        assert "abc1234" in result.output

    @patch("nezha.tools.git_tool.subprocess.run")
    def test_commit_add_fails(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not a git repo")
        tool = GitTool()
        result = tool.run("commit", tmp_path, {})
        assert not result.success
        assert "not a git repo" in result.error

    @patch("nezha.tools.git_tool.subprocess.run")
    def test_push_uses_current_branch(self, mock_run, tmp_path):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="feat/my-feature\n", stderr=""),  # rev-parse
            MagicMock(returncode=0, stdout="", stderr="Everything up-to-date"),
        ]
        tool = GitTool()
        result = tool.run("push", tmp_path, {})
        assert result.success

    @patch("nezha.tools.git_tool.subprocess.run")
    def test_push_explicit_branch(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        tool = GitTool()
        result = tool.run("push", tmp_path, {"branch": "feat/my-feature", "remote": "origin"})
        assert result.success
        # Should NOT call rev-parse when branch is given
        call_args = mock_run.call_args_list[0][0][0]
        assert "push" in call_args

    @patch("nezha.tools.git_tool.subprocess.run")
    def test_push_failure(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="rejected: non-fast-forward"
        )
        tool = GitTool()
        result = tool.run("push", tmp_path, {"branch": "main"})
        assert not result.success
        assert "rejected" in result.error

    @patch("nezha.tools.git_tool.subprocess.run")
    def test_create_pr_success(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/org/repo/pull/42", stderr=""
        )
        tool = GitTool()
        result = tool.run("create-pr", tmp_path, {"title": "My PR", "base": "main"})
        assert result.success
        assert "pull/42" in result.output

    @patch("nezha.tools.git_tool.subprocess.run")
    def test_create_pr_failure(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="gh: not authenticated")
        tool = GitTool()
        result = tool.run("create-pr", tmp_path, {"title": "T", "base": "main"})
        assert not result.success


# ---------------------------------------------------------------------------
# TestTool
# ---------------------------------------------------------------------------

class TestTestTool:
    def test_unknown_action(self, tmp_path):
        tool = TestTool()
        result = tool.run("build", tmp_path, {})
        assert not result.success
        assert "Unknown test-tool action" in result.error

    @patch("nezha.tools.test_tool.subprocess.run")
    def test_run_success(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="5 passed in 0.12s",
            stderr="",
        )
        tool = TestTool()
        result = tool.run("run", tmp_path, {"command": "pytest tests/"})
        assert result.success
        assert "passed" in result.output

    @patch("nezha.tools.test_tool.subprocess.run")
    def test_run_failure(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="FAILED test_foo.py::test_bar",
            stderr="",
        )
        tool = TestTool()
        result = tool.run("run", tmp_path, {"command": "pytest"})
        assert not result.success
        assert "exit 1" in result.error

    @patch("nezha.tools.test_tool.subprocess.run")
    def test_run_timeout(self, mock_run, tmp_path):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="pytest", timeout=5)
        tool = TestTool()
        result = tool.run("run", tmp_path, {"command": "pytest", "timeout": "5"})
        assert not result.success
        assert "timed out" in result.error

    @patch("nezha.tools.test_tool.subprocess.run")
    def test_run_default_command(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        tool = TestTool()
        tool.run("run", tmp_path, {})
        cmd = mock_run.call_args[1]["args"] if "args" in mock_run.call_args[1] else mock_run.call_args[0][0]
        assert "pytest" in cmd


# ---------------------------------------------------------------------------
# Config: PostToolConfig + PipelineConfig parsing
# ---------------------------------------------------------------------------

class TestPostToolConfig:
    def test_default_values(self):
        pt = PostToolConfig()
        assert pt.name == ""
        assert pt.action == ""
        assert pt.params == {}

    def test_with_values(self):
        pt = PostToolConfig(name="git-tool", action="commit", params={"message": "hi"})
        assert pt.name == "git-tool"
        assert pt.params["message"] == "hi"

    def test_pipeline_config_has_post_tools(self):
        pc = PipelineConfig()
        assert pc.post_tools == []

    def test_pipeline_config_with_post_tools(self):
        pc = PipelineConfig(post_tools=[
            PostToolConfig(name="test-tool", action="run"),
            PostToolConfig(name="git-tool", action="commit"),
        ])
        assert len(pc.post_tools) == 2
        assert pc.post_tools[0].name == "test-tool"
        assert pc.post_tools[1].action == "commit"


class TestAgentConfigPostToolsParsing:
    def test_load_agent_without_post_tools(self, tmp_path):
        """Agent YAML without post_tools still loads correctly."""
        yaml_content = """
agent:
  name: "test-agent"
  category: "coding"
pipeline:
  pre_agents: []
"""
        config_file = tmp_path / "test-agent.yaml"
        config_file.write_text(yaml_content)
        config = load_agent_config(config_file)
        assert config.pipeline.post_tools == []

    def test_load_agent_with_post_tools(self, tmp_path):
        """Agent YAML with post_tools is parsed correctly."""
        yaml_content = """
agent:
  name: "test-agent"
  category: "coding"
pipeline:
  pre_agents: []
  post_tools:
    - name: "test-tool"
      action: "run"
      command: "python -m pytest"
    - name: "git-tool"
      action: "commit"
      message: "auto commit"
"""
        config_file = tmp_path / "test-agent.yaml"
        config_file.write_text(yaml_content)
        config = load_agent_config(config_file)

        assert len(config.pipeline.post_tools) == 2

        tt = config.pipeline.post_tools[0]
        assert tt.name == "test-tool"
        assert tt.action == "run"
        assert tt.params["command"] == "python -m pytest"

        gt = config.pipeline.post_tools[1]
        assert gt.name == "git-tool"
        assert gt.action == "commit"
        assert gt.params["message"] == "auto commit"

    def test_post_tools_extra_params_captured(self, tmp_path):
        """Extra keys in post_tools entry are captured in params."""
        yaml_content = """
agent:
  name: "test-agent"
pipeline:
  post_tools:
    - name: "git-tool"
      action: "push"
      remote: "upstream"
      branch: "feat/x"
"""
        config_file = tmp_path / "test-agent.yaml"
        config_file.write_text(yaml_content)
        config = load_agent_config(config_file)
        pt = config.pipeline.post_tools[0]
        assert pt.params["remote"] == "upstream"
        assert pt.params["branch"] == "feat/x"
