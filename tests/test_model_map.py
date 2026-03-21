"""Tests for model_map feature: complexity → model + env resolution."""

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from nezha.config import (
    EngineConfig,
    ModelMapEntry,
    load_agent_config,
)
from nezha.dag.graph import Task, TaskDAG


# ---------------------------------------------------------------------------
# ModelMapEntry & EngineConfig
# ---------------------------------------------------------------------------

class TestModelMapEntry:
    def test_default_values(self):
        entry = ModelMapEntry()
        assert entry.model == ""
        assert entry.env == {}

    def test_with_model_and_env(self):
        entry = ModelMapEntry(model="claude-haiku-4-5-20251001", env={"ANTHROPIC_API_KEY": "sk-xxx"})
        assert entry.model == "claude-haiku-4-5-20251001"
        assert entry.env == {"ANTHROPIC_API_KEY": "sk-xxx"}


class TestEngineConfigModelMap:
    def test_default_empty_model_map(self):
        config = EngineConfig()
        assert config.model_map == {}

    def test_model_map_field(self):
        mm = {
            "low": ModelMapEntry(model="claude-haiku-4-5-20251001"),
            "medium": ModelMapEntry(model="claude-sonnet-4-6"),
        }
        config = EngineConfig(model_map=mm)
        assert config.model_map["low"].model == "claude-haiku-4-5-20251001"
        assert config.model_map["medium"].model == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

class TestLoadAgentConfigModelMap:
    def test_model_map_dict_format(self, tmp_path):
        """model_map with full dict entries (model + env)."""
        agent_yaml = tmp_path / "agent.yaml"
        agent_yaml.write_text(yaml.dump({
            "agent": {"name": "test-agent"},
            "engine": {
                "model": "claude-sonnet-4-6",
                "model_map": {
                    "low": {
                        "model": "claude-haiku-4-5-20251001",
                        "env": {"ANTHROPIC_API_KEY": "sk-low"},
                    },
                    "medium": {
                        "model": "claude-sonnet-4-6",
                    },
                    "high": {
                        "model": "claude-sonnet-4-6",
                        "env": {"ANTHROPIC_API_KEY": "sk-high"},
                    },
                },
            },
        }))
        config = load_agent_config(agent_yaml)
        assert len(config.engine.model_map) == 3
        assert config.engine.model_map["low"].model == "claude-haiku-4-5-20251001"
        assert config.engine.model_map["low"].env == {"ANTHROPIC_API_KEY": "sk-low"}
        assert config.engine.model_map["medium"].model == "claude-sonnet-4-6"
        assert config.engine.model_map["medium"].env == {}
        assert config.engine.model_map["high"].env == {"ANTHROPIC_API_KEY": "sk-high"}

    def test_model_map_string_shorthand(self, tmp_path):
        """model_map with string shorthand: { low: "model-id" }."""
        agent_yaml = tmp_path / "agent.yaml"
        agent_yaml.write_text(yaml.dump({
            "agent": {"name": "test-agent"},
            "engine": {
                "model": "claude-sonnet-4-6",
                "model_map": {
                    "low": "claude-haiku-4-5-20251001",
                    "medium": "claude-sonnet-4-6",
                },
            },
        }))
        config = load_agent_config(agent_yaml)
        assert config.engine.model_map["low"].model == "claude-haiku-4-5-20251001"
        assert config.engine.model_map["low"].env == {}
        assert config.engine.model_map["medium"].model == "claude-sonnet-4-6"

    def test_no_model_map(self, tmp_path):
        """No model_map in YAML → empty dict."""
        agent_yaml = tmp_path / "agent.yaml"
        agent_yaml.write_text(yaml.dump({
            "agent": {"name": "test-agent"},
            "engine": {"model": "claude-sonnet-4-6"},
        }))
        config = load_agent_config(agent_yaml)
        assert config.engine.model_map == {}


# ---------------------------------------------------------------------------
# Task complexity field
# ---------------------------------------------------------------------------

class TestTaskComplexity:
    def test_task_has_complexity(self):
        t = Task(id="F-001", complexity="low")
        assert t.complexity == "low"

    def test_task_default_complexity(self):
        t = Task(id="F-001")
        assert t.complexity == ""

    def test_task_load_with_complexity(self, tmp_path):
        """task_list.json with complexity field is loaded correctly."""
        task_list = tmp_path / "task_list.json"
        task_list.write_text(json.dumps([
            {"id": "F-001", "description": "Setup", "complexity": "low", "passes": False},
            {"id": "F-002", "description": "Logic", "complexity": "medium", "depends_on": ["F-001"], "passes": False},
            {"id": "F-003", "description": "Arch", "complexity": "high", "depends_on": ["F-001"], "passes": False},
        ]))
        dag = TaskDAG.load(task_list)
        assert dag._tasks["F-001"].complexity == "low"
        assert dag._tasks["F-002"].complexity == "medium"
        assert dag._tasks["F-003"].complexity == "high"

    def test_task_load_without_complexity(self, tmp_path):
        """task_list.json without complexity field defaults to empty."""
        task_list = tmp_path / "task_list.json"
        task_list.write_text(json.dumps([
            {"id": "F-001", "description": "Setup", "passes": False},
        ]))
        dag = TaskDAG.load(task_list)
        assert dag._tasks["F-001"].complexity == ""

    def test_backward_compat_model_field(self, tmp_path):
        """task_list.json with explicit model field still works (backward compat)."""
        task_list = tmp_path / "task_list.json"
        task_list.write_text(json.dumps([
            {"id": "F-001", "description": "Setup", "complexity": "low",
             "model": "claude-haiku-4-5-20251001", "passes": False},
        ]))
        dag = TaskDAG.load(task_list)
        assert dag._tasks["F-001"].model == "claude-haiku-4-5-20251001"
        assert dag._tasks["F-001"].complexity == "low"


# ---------------------------------------------------------------------------
# DAGEngine model resolution
# ---------------------------------------------------------------------------

class TestDAGEngineModelResolution:
    def _make_engine(self, model_map=None):
        from nezha.dag.engine import DAGEngine
        engine = DAGEngine(
            task_list_path=Path("/fake"),
            workspace=Path("/fake"),
            run_session_fn=lambda *a: None,
            model_map=model_map,
        )
        return engine

    def test_explicit_model_wins(self):
        """task.model takes priority over model_map."""
        engine = self._make_engine(model_map={
            "low": ModelMapEntry(model="haiku"),
        })
        task = Task(id="F-001", complexity="low", model="explicit-model")
        model, env = engine._resolve_model(task)
        assert model == "explicit-model"
        assert env == {}

    def test_model_map_resolution(self):
        """complexity → model_map lookup when task.model is empty."""
        engine = self._make_engine(model_map={
            "low": ModelMapEntry(model="haiku", env={"KEY": "val"}),
            "medium": ModelMapEntry(model="sonnet"),
        })
        task_low = Task(id="F-001", complexity="low")
        model, env = engine._resolve_model(task_low)
        assert model == "haiku"
        assert env == {"KEY": "val"}

        task_med = Task(id="F-002", complexity="medium")
        model, env = engine._resolve_model(task_med)
        assert model == "sonnet"
        assert env == {}

    def test_fallback_to_agent_default(self):
        """No model, no matching complexity → empty (agent default)."""
        engine = self._make_engine(model_map={
            "low": ModelMapEntry(model="haiku"),
        })
        task = Task(id="F-001", complexity="high")  # "high" not in map
        model, env = engine._resolve_model(task)
        assert model == ""
        assert env == {}

    def test_no_complexity_no_model(self):
        """No model, no complexity → empty."""
        engine = self._make_engine(model_map={
            "low": ModelMapEntry(model="haiku"),
        })
        task = Task(id="F-001")
        model, env = engine._resolve_model(task)
        assert model == ""
        assert env == {}

    def test_empty_model_map(self):
        """Empty model_map → always empty."""
        engine = self._make_engine(model_map={})
        task = Task(id="F-001", complexity="low")
        model, env = engine._resolve_model(task)
        assert model == ""
        assert env == {}

    def test_none_model_map(self):
        """None model_map → always empty."""
        engine = self._make_engine(model_map=None)
        task = Task(id="F-001", complexity="medium")
        model, env = engine._resolve_model(task)
        assert model == ""
        assert env == {}


# ---------------------------------------------------------------------------
# Priority chain: task.model > model_map[complexity] > engine.model
# ---------------------------------------------------------------------------

class TestModelResolutionPriority:
    """Integration-level tests for the 3-layer priority."""

    def test_priority_explicit_model(self):
        """Explicit task.model always wins, regardless of model_map."""
        from nezha.dag.engine import DAGEngine
        mm = {"low": ModelMapEntry(model="haiku")}
        engine = DAGEngine(
            task_list_path=Path("/fake"),
            workspace=Path("/fake"),
            run_session_fn=lambda *a: None,
            model_map=mm,
        )
        task = Task(id="F-001", complexity="low", model="override-model")
        model, env = engine._resolve_model(task)
        assert model == "override-model"

    def test_priority_model_map_over_default(self):
        """model_map lookup returns non-empty → used instead of agent default."""
        from nezha.dag.engine import DAGEngine
        mm = {"medium": ModelMapEntry(model="sonnet-special")}
        engine = DAGEngine(
            task_list_path=Path("/fake"),
            workspace=Path("/fake"),
            run_session_fn=lambda *a: None,
            model_map=mm,
        )
        task = Task(id="F-001", complexity="medium")
        model, env = engine._resolve_model(task)
        assert model == "sonnet-special"

    def test_priority_falls_through_to_default(self):
        """No task.model, no matching complexity → empty string (agent default)."""
        from nezha.dag.engine import DAGEngine
        mm = {"low": ModelMapEntry(model="haiku")}
        engine = DAGEngine(
            task_list_path=Path("/fake"),
            workspace=Path("/fake"),
            run_session_fn=lambda *a: None,
            model_map=mm,
        )
        task = Task(id="F-001", complexity="high")
        model, env = engine._resolve_model(task)
        assert model == ""  # will use agent_config.engine.model at runtime
