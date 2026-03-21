"""Tests for target_scope — monorepo subdirectory constraint."""

from pathlib import Path

import pytest
import yaml

from nezha.config import AgentConfig, load_agent_config
from nezha.executor import _resolve_target, _resolve_target_scope


# ---------------------------------------------------------------------------
# _resolve_target_scope
# ---------------------------------------------------------------------------


class TestResolveTargetScope:
    def test_returns_none_when_no_target(self):
        assert _resolve_target_scope(None, "frontend") is None

    def test_returns_none_when_no_scope(self):
        assert _resolve_target_scope(Path("/repo"), None) is None

    def test_returns_none_when_both_none(self):
        assert _resolve_target_scope(None, None) is None

    def test_returns_scoped_path(self, tmp_path):
        (tmp_path / "frontend").mkdir()
        result = _resolve_target_scope(tmp_path, "frontend")
        assert result == tmp_path / "frontend"

    def test_returns_none_when_scope_dir_missing(self, tmp_path):
        result = _resolve_target_scope(tmp_path, "nonexistent")
        assert result is None

    def test_nested_scope(self, tmp_path):
        (tmp_path / "packages" / "web").mkdir(parents=True)
        result = _resolve_target_scope(tmp_path, "packages/web")
        assert result == tmp_path / "packages" / "web"


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


class TestTargetScopeConfig:
    def test_agent_config_default_none(self):
        config = AgentConfig()
        assert config.target_scope is None

    def test_load_agent_config_with_scope(self, tmp_path):
        yaml_content = {
            "agent": {"name": "test-agent", "category": "coding"},
            "engine": {"model": "claude-sonnet-4-6"},
            "session": {"mode": "multi_round"},
            "target": "/path/to/repo",
            "target_scope": "backend",
        }
        config_path = tmp_path / "test-agent.yaml"
        config_path.write_text(yaml.dump(yaml_content))

        config = load_agent_config(config_path)
        assert config.target == "/path/to/repo"
        assert config.target_scope == "backend"

    def test_load_agent_config_without_scope(self, tmp_path):
        yaml_content = {
            "agent": {"name": "test-agent", "category": "coding"},
            "engine": {"model": "claude-sonnet-4-6"},
            "session": {"mode": "multi_round"},
            "target": "/path/to/repo",
        }
        config_path = tmp_path / "test-agent.yaml"
        config_path.write_text(yaml.dump(yaml_content))

        config = load_agent_config(config_path)
        assert config.target == "/path/to/repo"
        assert config.target_scope is None


# ---------------------------------------------------------------------------
# Integration: resolve target + scope together
# ---------------------------------------------------------------------------


class TestTargetAndScopeIntegration:
    def test_scope_relative_to_target(self, tmp_path):
        """target_scope resolves relative to the resolved target path."""
        repo = tmp_path / "my-repo"
        repo.mkdir()
        (repo / "frontend").mkdir()
        (repo / "backend").mkdir()

        # Simulate: target resolves to repo, scope = "frontend"
        scope_path = _resolve_target_scope(repo, "frontend")
        assert scope_path == repo / "frontend"
        assert scope_path.is_dir()

    def test_git_root_vs_scope_separation(self, tmp_path):
        """Git operations should use target (root), sessions use scope."""
        repo = tmp_path / "monorepo"
        repo.mkdir()
        (repo / "backend").mkdir()

        target = repo  # git ops here
        scope = _resolve_target_scope(target, "backend")  # session cwd here

        # Different paths
        assert target != scope
        # Both exist
        assert target.is_dir()
        assert scope.is_dir()
        # Scope is under target
        assert str(scope).startswith(str(target))
