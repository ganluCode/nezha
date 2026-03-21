"""Tests for git strategy configuration and safety checks."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nezha.config import GitConfig, load_agent_config
from nezha.executor import _check_coding_safety, _git_commit, _git_push, _resolve_target


# ---------------------------------------------------------------------------
# GitConfig dataclass
# ---------------------------------------------------------------------------

class TestGitConfig:
    def test_defaults(self):
        cfg = GitConfig()
        assert cfg.auto_commit is False
        assert cfg.auto_push is False
        assert cfg.branch_per_task is False
        assert cfg.branch_prefix == "feat/"
        assert cfg.base_branch == "main"

    def test_custom_values(self):
        cfg = GitConfig(
            auto_commit=True,
            auto_push=True,
            branch_per_task=True,
            branch_prefix="task/",
            base_branch="develop",
        )
        assert cfg.auto_commit is True
        assert cfg.auto_push is True
        assert cfg.branch_per_task is True
        assert cfg.branch_prefix == "task/"
        assert cfg.base_branch == "develop"


# ---------------------------------------------------------------------------
# _check_coding_safety()
# ---------------------------------------------------------------------------

class TestCheckCodingSafety:
    def test_clean_tree_passes(self, tmp_path):
        """Passes when git reports no changes (empty stdout)."""
        with patch("nezha.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            # Should not raise
            _check_coding_safety(tmp_path)

    def test_dirty_tree_raises(self, tmp_path):
        """Raises RuntimeError when there are uncommitted changes."""
        dirty_output = " M src/main.py\n?? new_file.txt\n"
        with patch("nezha.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=dirty_output, returncode=0)
            with pytest.raises(RuntimeError, match="uncommitted changes"):
                _check_coding_safety(tmp_path)

    def test_runs_git_status_porcelain(self, tmp_path):
        """Uses 'git status --porcelain' command."""
        with patch("nezha.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            _check_coding_safety(tmp_path)
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "git" in cmd
            assert "status" in cmd
            assert "--porcelain" in cmd

    def test_uses_target_as_cwd(self, tmp_path):
        """Runs git in the target directory."""
        with patch("nezha.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            _check_coding_safety(tmp_path)
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs.get("cwd") == tmp_path

    def test_error_message_includes_target(self, tmp_path):
        """RuntimeError message includes the target path."""
        with patch("nezha.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=" M file.py", returncode=0)
            with pytest.raises(RuntimeError) as exc_info:
                _check_coding_safety(tmp_path)
            assert str(tmp_path) in str(exc_info.value)


# ---------------------------------------------------------------------------
# _git_commit()
# ---------------------------------------------------------------------------

class TestGitCommit:
    def test_stages_and_commits(self, tmp_path):
        """Runs git add -A then git commit."""
        with patch("nezha.executor.subprocess.run") as mock_run:
            # First call: git add -A (returns 0)
            # Second call: git diff --cached --quiet (returncode=1 means changes exist)
            # Third call: git commit
            mock_run.side_effect = [
                MagicMock(returncode=0),   # git add -A
                MagicMock(returncode=1),   # git diff --cached --quiet (has changes)
                MagicMock(returncode=0),   # git commit
            ]
            _git_commit(tmp_path, "2026-02-19-11-18-53")
            assert mock_run.call_count == 3

    def test_commit_message_includes_task_id(self, tmp_path):
        """Commit message includes the task ID."""
        with patch("nezha.executor.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),   # git add
                MagicMock(returncode=1),   # diff has changes
                MagicMock(returncode=0),   # git commit
            ]
            task_id = "2026-02-19-11-18-53"
            _git_commit(tmp_path, task_id)
            commit_call = mock_run.call_args_list[2]
            cmd = commit_call[0][0]
            assert task_id in " ".join(cmd)

    def test_skips_commit_when_no_changes(self, tmp_path, capsys):
        """Skips git commit when there are no staged changes."""
        with patch("nezha.executor.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),   # git add
                MagicMock(returncode=0),   # git diff --cached --quiet: no changes
            ]
            _git_commit(tmp_path, "task-id")
            assert mock_run.call_count == 2  # no commit call
            out = capsys.readouterr().out
            assert "No changes" in out


# ---------------------------------------------------------------------------
# _git_push()
# ---------------------------------------------------------------------------

class TestGitPush:
    def test_push_with_branch(self, tmp_path):
        """Calls git push origin <branch>."""
        with patch("nezha.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _git_push(tmp_path, "feat/my-feature")
            cmd = mock_run.call_args[0][0]
            assert "git" in cmd
            assert "push" in cmd
            assert "origin" in cmd
            assert "feat/my-feature" in cmd

    def test_push_without_branch(self, tmp_path):
        """Calls git push origin (no branch specified)."""
        with patch("nezha.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _git_push(tmp_path, None)
            cmd = mock_run.call_args[0][0]
            assert "git" in cmd
            assert "push" in cmd
            assert "origin" in cmd


# ---------------------------------------------------------------------------
# _resolve_target()
# ---------------------------------------------------------------------------

class TestResolveTarget:
    def test_none_when_no_target_configured(self, tmp_path):
        """Returns None when neither agent nor executor has target."""
        from nezha.config import AgentConfig, ExecutorConfig
        config = AgentConfig()
        result = _resolve_target(config, ExecutorConfig(), tmp_path)
        assert result is None

    def test_absolute_target_returned_as_is(self, tmp_path):
        """Absolute target path is returned unchanged."""
        from nezha.config import AgentConfig, ExecutorConfig
        config = AgentConfig()
        config.target = str(tmp_path)
        result = _resolve_target(config, ExecutorConfig(), Path("/some/base"))
        assert result == tmp_path

    def test_relative_target_resolved_from_base(self, tmp_path):
        """Relative target path is resolved relative to base_dir."""
        from nezha.config import AgentConfig, ExecutorConfig
        config = AgentConfig()
        config.target = "my-project"
        result = _resolve_target(config, ExecutorConfig(), tmp_path)
        assert result == tmp_path / "my-project"

    def test_dot_slash_target(self, tmp_path):
        """'./' target resolves to base_dir itself (evolve-agent pattern)."""
        from nezha.config import AgentConfig, ExecutorConfig
        config = AgentConfig()
        config.target = "./"
        result = _resolve_target(config, ExecutorConfig(), tmp_path)
        assert result == tmp_path

    def test_executor_target_fallback(self, tmp_path):
        """Falls back to executor_config.target when coding agent has no target."""
        from nezha.config import AgentConfig, AgentMeta, ExecutorConfig
        agent_cfg = AgentConfig(agent=AgentMeta(category="coding"))
        exec_cfg = ExecutorConfig(target=str(tmp_path / "repo"))
        result = _resolve_target(agent_cfg, exec_cfg, tmp_path)
        assert result == tmp_path / "repo"

    def test_agent_target_overrides_executor(self, tmp_path):
        """Agent-level target takes priority over executor-level."""
        from nezha.config import AgentConfig, ExecutorConfig
        agent_cfg = AgentConfig()
        agent_cfg.target = str(tmp_path / "agent-repo")
        exec_cfg = ExecutorConfig(target=str(tmp_path / "exec-repo"))
        result = _resolve_target(agent_cfg, exec_cfg, tmp_path)
        assert result == tmp_path / "agent-repo"

    def test_executor_relative_target_resolved(self, tmp_path):
        """Relative executor target resolves from base_dir."""
        from nezha.config import AgentConfig, AgentMeta, ExecutorConfig
        agent_cfg = AgentConfig(agent=AgentMeta(category="coding"))
        exec_cfg = ExecutorConfig(target="my-project")
        result = _resolve_target(agent_cfg, exec_cfg, tmp_path)
        assert result == tmp_path / "my-project"

    def test_planning_agent_ignores_executor_target(self, tmp_path):
        """Planning agents do NOT fall back to executor target."""
        from nezha.config import AgentConfig, AgentMeta, ExecutorConfig
        agent_cfg = AgentConfig(agent=AgentMeta(category="planning"))
        exec_cfg = ExecutorConfig(target=str(tmp_path / "repo"))
        result = _resolve_target(agent_cfg, exec_cfg, tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# GitConfig loading from YAML (via config.py)
# ---------------------------------------------------------------------------

class TestGitConfigFromYaml:
    def test_git_config_defaults_when_not_in_yaml(self, tmp_path):
        """GitConfig has defaults when 'git' key is absent from YAML."""
        yaml_content = """
agent:
  name: test-agent
engine:
  model: claude-sonnet-4-5-20250929
"""
        cfg_path = tmp_path / "test-agent.yaml"
        cfg_path.write_text(yaml_content)
        config = load_agent_config(cfg_path)
        assert config.git.auto_commit is False
        assert config.git.auto_push is False
        assert config.git.branch_per_task is False

    def test_git_config_parsed_from_yaml(self, tmp_path):
        """GitConfig fields are parsed from YAML."""
        yaml_content = """
agent:
  name: test-agent
git:
  auto_commit: true
  auto_push: false
  branch_per_task: true
  branch_prefix: "task/"
  base_branch: develop
"""
        cfg_path = tmp_path / "test-agent.yaml"
        cfg_path.write_text(yaml_content)
        config = load_agent_config(cfg_path)
        assert config.git.auto_commit is True
        assert config.git.auto_push is False
        assert config.git.branch_per_task is True
        assert config.git.branch_prefix == "task/"
        assert config.git.base_branch == "develop"

    def test_target_parsed_from_yaml(self, tmp_path):
        """AgentConfig.target is parsed from YAML."""
        yaml_content = """
agent:
  name: frontend-agent
target: "/path/to/my-project"
"""
        cfg_path = tmp_path / "frontend-agent.yaml"
        cfg_path.write_text(yaml_content)
        config = load_agent_config(cfg_path)
        assert config.target == "/path/to/my-project"

    def test_target_none_when_absent(self, tmp_path):
        """AgentConfig.target is None when not in YAML."""
        yaml_content = """
agent:
  name: planner-agent
"""
        cfg_path = tmp_path / "planner-agent.yaml"
        cfg_path.write_text(yaml_content)
        config = load_agent_config(cfg_path)
        assert config.target is None


# ---------------------------------------------------------------------------
# session.py structural checks: target parameter
# ---------------------------------------------------------------------------

class TestSessionTargetSupport:
    def test_run_single_round_accepts_target(self):
        """run_single_round signature includes target parameter."""
        import inspect
        from nezha.pipeline.session import run_single_round
        sig = inspect.signature(run_single_round)
        assert "target" in sig.parameters

    def test_run_multi_round_accepts_target(self):
        """run_multi_round signature includes target parameter."""
        import inspect
        from nezha.pipeline.session import run_multi_round
        sig = inspect.signature(run_multi_round)
        assert "target" in sig.parameters

    def test_run_vibe_session_accepts_target(self):
        """run_vibe_session signature includes target parameter."""
        import inspect
        from nezha.pipeline.session import run_vibe_session
        sig = inspect.signature(run_vibe_session)
        assert "target" in sig.parameters

    def test_subprocess_runner_has_cwd_variable(self):
        """_SUBPROCESS_RUNNER template uses cwd variable for build_options."""
        from nezha.pipeline.session import _SUBPROCESS_RUNNER
        assert "cwd" in _SUBPROCESS_RUNNER
        assert "build_options(" in _SUBPROCESS_RUNNER
        assert "agent_config" in _SUBPROCESS_RUNNER

    def test_vibe_subprocess_runner_has_cwd_variable(self):
        """_VIBE_SUBPROCESS_RUNNER template uses cwd variable for build_options."""
        from nezha.pipeline.session import _VIBE_SUBPROCESS_RUNNER
        assert "cwd" in _VIBE_SUBPROCESS_RUNNER
        assert "build_options(" in _VIBE_SUBPROCESS_RUNNER
        assert "agent_config" in _VIBE_SUBPROCESS_RUNNER
