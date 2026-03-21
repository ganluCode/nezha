"""Tests for global user config: ~/.nezha/config.yaml."""

import yaml
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# load_global_config
# ---------------------------------------------------------------------------

class TestLoadGlobalConfig:
    def test_returns_empty_when_no_file(self, tmp_path):
        from nezha.interface.cli import load_global_config, GLOBAL_CONFIG_PATH
        with patch("nezha.interface.cli.GLOBAL_CONFIG_PATH", tmp_path / "nonexistent.yaml"):
            result = load_global_config()
        assert result == {}

    def test_loads_valid_config(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({
            "locale": "zh_CN",
            "timezone": "Asia/Shanghai",
            "env": {"GH_TOKEN": "ghp_test123"},
        }))
        from nezha.interface.cli import load_global_config
        with patch("nezha.interface.cli.GLOBAL_CONFIG_PATH", config_file):
            result = load_global_config()
        assert result["locale"] == "zh_CN"
        assert result["timezone"] == "Asia/Shanghai"
        assert result["env"]["GH_TOKEN"] == "ghp_test123"

    def test_returns_empty_on_invalid_yaml(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(": : invalid yaml [[[")
        from nezha.interface.cli import load_global_config
        with patch("nezha.interface.cli.GLOBAL_CONFIG_PATH", config_file):
            result = load_global_config()
        assert result == {}

    def test_returns_empty_on_non_dict(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("just a string")
        from nezha.interface.cli import load_global_config
        with patch("nezha.interface.cli.GLOBAL_CONFIG_PATH", config_file):
            result = load_global_config()
        assert result == {}

    def test_returns_empty_on_null_content(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")
        from nezha.interface.cli import load_global_config
        with patch("nezha.interface.cli.GLOBAL_CONFIG_PATH", config_file):
            result = load_global_config()
        assert result == {}


# ---------------------------------------------------------------------------
# _apply_global_config
# ---------------------------------------------------------------------------

class TestApplyGlobalConfig:
    def _make_executor_yaml(self, tmp_path, content=None):
        """Create a minimal executor.yaml and return its path."""
        if content is None:
            content = {
                "executor": {"name": "test"},
                "workspace": {"base": "./workspace"},
                "env": {},
            }
        path = tmp_path / "executor.yaml"
        path.write_text(yaml.dump(content, allow_unicode=True), encoding="utf-8")
        return path

    def test_applies_locale(self, tmp_path):
        executor_yaml = self._make_executor_yaml(tmp_path)
        global_cfg = tmp_path / "global.yaml"
        global_cfg.write_text(yaml.dump({"locale": "zh_CN"}))

        from nezha.interface.cli import _apply_global_config
        with patch("nezha.interface.cli.GLOBAL_CONFIG_PATH", global_cfg):
            applied = _apply_global_config(executor_yaml)

        assert "locale" in applied
        result = yaml.safe_load(executor_yaml.read_text())
        assert result["locale"] == "zh_CN"

    def test_applies_timezone(self, tmp_path):
        executor_yaml = self._make_executor_yaml(tmp_path)
        global_cfg = tmp_path / "global.yaml"
        global_cfg.write_text(yaml.dump({"timezone": "Asia/Shanghai"}))

        from nezha.interface.cli import _apply_global_config
        with patch("nezha.interface.cli.GLOBAL_CONFIG_PATH", global_cfg):
            applied = _apply_global_config(executor_yaml)

        assert "timezone" in applied
        result = yaml.safe_load(executor_yaml.read_text())
        assert result["timezone"] == "Asia/Shanghai"

    def test_merges_env_dict(self, tmp_path):
        executor_yaml = self._make_executor_yaml(tmp_path, {
            "executor": {"name": "test"},
            "env": {"EXISTING": "value"},
        })
        global_cfg = tmp_path / "global.yaml"
        global_cfg.write_text(yaml.dump({"env": {"GH_TOKEN": "ghp_xxx"}}))

        from nezha.interface.cli import _apply_global_config
        with patch("nezha.interface.cli.GLOBAL_CONFIG_PATH", global_cfg):
            applied = _apply_global_config(executor_yaml)

        assert "env" in applied
        result = yaml.safe_load(executor_yaml.read_text())
        assert result["env"]["GH_TOKEN"] == "ghp_xxx"
        assert result["env"]["EXISTING"] == "value"

    def test_applies_model_map(self, tmp_path):
        executor_yaml = self._make_executor_yaml(tmp_path)
        global_cfg = tmp_path / "global.yaml"
        global_cfg.write_text(yaml.dump({
            "model_map": {
                "low": "claude-sonnet-4-6",
                "high": "claude-opus-4-6",
            }
        }))

        from nezha.interface.cli import _apply_global_config
        with patch("nezha.interface.cli.GLOBAL_CONFIG_PATH", global_cfg):
            applied = _apply_global_config(executor_yaml)

        assert "model_map" in applied
        result = yaml.safe_load(executor_yaml.read_text())
        assert result["model_map"]["low"] == "claude-sonnet-4-6"
        assert result["model_map"]["high"] == "claude-opus-4-6"

    def test_ignores_non_merge_keys(self, tmp_path):
        executor_yaml = self._make_executor_yaml(tmp_path)
        global_cfg = tmp_path / "global.yaml"
        global_cfg.write_text(yaml.dump({
            "locale": "zh_CN",
            "scheduler": {"mode": "continuous"},  # not a merge key
            "random_key": "should_be_ignored",
        }))

        from nezha.interface.cli import _apply_global_config
        with patch("nezha.interface.cli.GLOBAL_CONFIG_PATH", global_cfg):
            applied = _apply_global_config(executor_yaml)

        assert applied == ["locale"]
        result = yaml.safe_load(executor_yaml.read_text())
        assert "scheduler" not in result
        assert "random_key" not in result

    def test_returns_empty_when_no_global_config(self, tmp_path):
        executor_yaml = self._make_executor_yaml(tmp_path)

        from nezha.interface.cli import _apply_global_config
        with patch("nezha.interface.cli.GLOBAL_CONFIG_PATH", tmp_path / "nonexistent.yaml"):
            applied = _apply_global_config(executor_yaml)

        assert applied == []

    def test_multiple_keys_applied(self, tmp_path):
        executor_yaml = self._make_executor_yaml(tmp_path)
        global_cfg = tmp_path / "global.yaml"
        global_cfg.write_text(yaml.dump({
            "locale": "zh_CN",
            "timezone": "Asia/Shanghai",
            "env": {"GH_TOKEN": "ghp_xxx", "ANTHROPIC_API_KEY": "sk-ant-xxx"},
        }))

        from nezha.interface.cli import _apply_global_config
        with patch("nezha.interface.cli.GLOBAL_CONFIG_PATH", global_cfg):
            applied = _apply_global_config(executor_yaml)

        assert set(applied) == {"locale", "timezone", "env"}
        result = yaml.safe_load(executor_yaml.read_text())
        assert result["locale"] == "zh_CN"
        assert result["timezone"] == "Asia/Shanghai"
        assert result["env"]["GH_TOKEN"] == "ghp_xxx"
        assert result["env"]["ANTHROPIC_API_KEY"] == "sk-ant-xxx"


# ---------------------------------------------------------------------------
# cmd_init integration with global config
# ---------------------------------------------------------------------------

class TestInitWithGlobalConfig:
    def test_init_applies_global_locale(self, tmp_path):
        global_cfg = tmp_path / "global.yaml"
        global_cfg.write_text(yaml.dump({"locale": "zh_CN"}))

        project_path = tmp_path / "my-project"
        from nezha.interface.cli import cmd_init
        with patch("nezha.interface.cli.GLOBAL_CONFIG_PATH", global_cfg):
            cmd_init(str(project_path))

        executor_yaml = project_path / "executor.yaml"
        result = yaml.safe_load(executor_yaml.read_text())
        assert result["locale"] == "zh_CN"

    def test_init_prints_applied_keys(self, tmp_path, capsys):
        global_cfg = tmp_path / "global.yaml"
        global_cfg.write_text(yaml.dump({
            "locale": "zh_CN",
            "env": {"GH_TOKEN": "ghp_test"},
        }))

        project_path = tmp_path / "my-project"
        from nezha.interface.cli import cmd_init
        with patch("nezha.interface.cli.GLOBAL_CONFIG_PATH", global_cfg):
            cmd_init(str(project_path))

        output = capsys.readouterr().out
        assert "global config" in output.lower()
        assert "locale" in output
        assert "env" in output

    def test_init_works_without_global_config(self, tmp_path, capsys):
        project_path = tmp_path / "my-project"
        from nezha.interface.cli import cmd_init
        with patch("nezha.interface.cli.GLOBAL_CONFIG_PATH", tmp_path / "nonexistent.yaml"):
            cmd_init(str(project_path))

        output = capsys.readouterr().out
        assert "global config" not in output.lower()
        # executor.yaml should still be created
        assert (project_path / "executor.yaml").exists()
