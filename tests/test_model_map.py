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
    RuntimeStrategy,
    build_model_map_info,
    load_agent_config,
    load_executor_config,
)
from nezha.dag.graph import Task, TaskDAG


# ---------------------------------------------------------------------------
# ModelMapEntry & EngineConfig
# ---------------------------------------------------------------------------

class TestModelMapEntry:
    def test_default_values(self):
        entry = ModelMapEntry()
        assert entry.runtime == ""
        assert entry.model == ""
        assert entry.env == {}

    def test_with_runtime_model_and_env(self):
        entry = ModelMapEntry(
            runtime="codex_cli",
            model="gpt-5.4-codex",
            env={"OPENAI_API_KEY": "sk-xxx"},
        )
        assert entry.runtime == "codex_cli"
        assert entry.model == "gpt-5.4-codex"
        assert entry.env == {"OPENAI_API_KEY": "sk-xxx"}

    def test_build_model_map_info_includes_runtime(self):
        info = build_model_map_info({
            "low": ModelMapEntry(runtime="codex_cli", model="gpt-5.3-codex"),
        })
        assert "runtime=codex_cli" in info
        assert "model=gpt-5.3-codex" in info


class TestRuntimeStrategy:
    def test_executor_default_strategy_loads_from_yaml(self, tmp_path):
        executor_yaml = tmp_path / "executor.yaml"
        executor_yaml.write_text(yaml.dump({
            "default_strategy": {
                "runtime": "codex_cli",
                "model": "gpt-5.4",
                "env": {"OPENAI_API_KEY": "${OPENAI_API_KEY}"},
            },
            "env": {"OPENAI_API_KEY": "sk-default"},
        }))

        config = load_executor_config(executor_yaml)

        assert config.default_strategy.runtime == "codex_cli"
        assert config.default_strategy.model == "gpt-5.4"
        assert config.default_strategy.env == {"OPENAI_API_KEY": "sk-default"}

    def test_scheduler_judge_strategy_loads_from_yaml(self, tmp_path):
        executor_yaml = tmp_path / "executor.yaml"
        executor_yaml.write_text(yaml.dump({
            "scheduler": {
                "failure_strategy": "ai_judge",
                "judge_strategy": {
                    "runtime": "codex_cli",
                    "model": "gpt-5.4-mini",
                    "env": {"OPENAI_API_KEY": "sk-judge"},
                },
            },
        }))

        config = load_executor_config(executor_yaml)

        assert config.scheduler.judge_strategy.runtime == "codex_cli"
        assert config.scheduler.judge_strategy.model == "gpt-5.4-mini"
        assert config.scheduler.judge_strategy.env == {"OPENAI_API_KEY": "sk-judge"}

    def test_executor_agent_strategies_load_from_yaml(self, tmp_path):
        executor_yaml = tmp_path / "executor.yaml"
        executor_yaml.write_text(yaml.dump({
            "agent_strategies": {
                "planner-agent": {
                    "runtime": "codex_cli",
                    "model": "gpt-5.4",
                },
                "product-agent": {
                    "runtime": "claude_code",
                    "model": "claude-sonnet-4-6",
                },
            },
        }))

        config = load_executor_config(executor_yaml)

        assert config.agent_strategies["planner-agent"].runtime == "codex_cli"
        assert config.agent_strategies["planner-agent"].model == "gpt-5.4"
        assert config.agent_strategies["product-agent"].runtime == "claude_code"


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
                        "runtime": "codex_cli",
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
        assert config.engine.model_map["low"].runtime == "codex_cli"
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
        assert config.engine.model_map["low"].runtime == ""
        assert config.engine.model_map["low"].env == {}
        assert config.engine.model_map["medium"].model == "claude-sonnet-4-6"

    def test_executor_model_map_dict_format_with_runtime(self, tmp_path):
        executor_yaml = tmp_path / "executor.yaml"
        executor_yaml.write_text(yaml.dump({
            "model_map": {
                "low": {
                    "runtime": "codex_cli",
                    "model": "gpt-5.3-codex",
                    "env": {"OPENAI_API_KEY": "sk-low"},
                    "task_factor": 1.3,
                },
                "high": {
                    "runtime": "claude_code",
                    "model": "claude-opus-4-7",
                },
            },
        }))
        config = load_executor_config(executor_yaml)
        assert config.model_map["low"].runtime == "codex_cli"
        assert config.model_map["low"].model == "gpt-5.3-codex"
        assert config.model_map["low"].env == {"OPENAI_API_KEY": "sk-low"}
        assert config.model_map["low"].task_factor == 1.3
        assert config.model_map["high"].runtime == "claude_code"

    def test_executor_model_map_env_refs_resolve_from_dotenv(self, tmp_path):
        (tmp_path / ".env").write_text(
            "OPENAI_BASE_URL=https://api.example.com/v1\n"
            "OPENAI_API_KEY=sk-dotenv\n"
        )
        executor_yaml = tmp_path / "executor.yaml"
        executor_yaml.write_text(yaml.dump({
            "model_map": {
                "low": {
                    "runtime": "codex_cli",
                    "model": "kimi-k2-coder",
                    "env": {
                        "OPENAI_BASE_URL": "${OPENAI_BASE_URL}",
                        "OPENAI_API_KEY": "${OPENAI_API_KEY}",
                    },
                },
            },
        }))

        config = load_executor_config(executor_yaml)

        assert config.model_map["low"].env == {
            "OPENAI_BASE_URL": "https://api.example.com/v1",
            "OPENAI_API_KEY": "sk-dotenv",
        }

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
    def _make_engine(self, model_map=None, default_strategy=None, agent_strategy=None):
        from nezha.dag.engine import DAGEngine
        engine = DAGEngine(
            task_list_path=Path("/fake"),
            workspace=Path("/fake"),
            run_session_fn=lambda *a: None,
            model_map=model_map,
            default_strategy=default_strategy,
            agent_strategy=agent_strategy,
        )
        return engine

    def test_explicit_task_model_is_ignored(self, capsys):
        """task.model is deprecated and ignored; model_map still routes."""
        engine = self._make_engine(model_map={
            "low": ModelMapEntry(runtime="codex_cli", model="haiku"),
        })
        task = Task(id="F-001", complexity="low", model="explicit-model")
        runtime, model, env = engine._resolve_runtime_model(task)
        assert runtime == "codex_cli"
        assert model == "haiku"
        assert env == {}
        assert "task.model is deprecated and ignored" in capsys.readouterr().out

    def test_model_map_resolution(self):
        """complexity → model_map lookup when task.model is empty."""
        engine = self._make_engine(model_map={
            "low": ModelMapEntry(runtime="codex_cli", model="haiku", env={"KEY": "val"}),
            "medium": ModelMapEntry(model="sonnet"),
        })
        task_low = Task(id="F-001", complexity="low")
        runtime, model, env = engine._resolve_runtime_model(task_low)
        assert runtime == "codex_cli"
        assert model == "haiku"
        assert env == {"KEY": "val"}

        task_med = Task(id="F-002", complexity="medium")
        runtime, model, env = engine._resolve_runtime_model(task_med)
        assert runtime == ""
        assert model == "sonnet"
        assert env == {}

    def test_fallback_to_default_strategy(self):
        """No matching complexity → executor default_strategy."""
        engine = self._make_engine(model_map={
            "low": ModelMapEntry(model="haiku"),
        }, default_strategy=RuntimeStrategy(runtime="codex_cli", model="gpt-5.4"))
        task = Task(id="F-001", complexity="high")  # "high" not in map
        runtime, model, env = engine._resolve_runtime_model(task)
        assert runtime == "codex_cli"
        assert model == "gpt-5.4"
        assert env == {}

    def test_fallback_to_agent_strategy_before_default(self):
        """No matching complexity → agent strategy before executor default."""
        engine = self._make_engine(
            model_map={"low": ModelMapEntry(model="haiku")},
            agent_strategy=RuntimeStrategy(runtime="claude_code", model="claude-opus-4-6"),
            default_strategy=RuntimeStrategy(runtime="codex_cli", model="gpt-5.4"),
        )
        task = Task(id="F-001", complexity="high")
        runtime, model, env = engine._resolve_runtime_model(task)
        assert runtime == "claude_code"
        assert model == "claude-opus-4-6"
        assert env == {}

    def test_no_complexity_no_model(self):
        """No complexity → executor default_strategy."""
        engine = self._make_engine(model_map={
            "low": ModelMapEntry(model="haiku"),
        }, default_strategy=RuntimeStrategy(runtime="codex_cli", model="gpt-5.4"))
        task = Task(id="F-001")
        runtime, model, env = engine._resolve_runtime_model(task)
        assert runtime == "codex_cli"
        assert model == "gpt-5.4"
        assert env == {}

    def test_empty_model_map(self):
        """Empty model_map → executor default_strategy."""
        engine = self._make_engine(
            model_map={},
            default_strategy=RuntimeStrategy(runtime="codex_cli", model="gpt-5.4"),
        )
        task = Task(id="F-001", complexity="low")
        runtime, model, env = engine._resolve_runtime_model(task)
        assert runtime == "codex_cli"
        assert model == "gpt-5.4"
        assert env == {}

    def test_none_model_map(self):
        """None model_map → executor default_strategy."""
        engine = self._make_engine(
            model_map=None,
            default_strategy=RuntimeStrategy(runtime="codex_cli", model="gpt-5.4"),
        )
        task = Task(id="F-001", complexity="medium")
        runtime, model, env = engine._resolve_runtime_model(task)
        assert runtime == "codex_cli"
        assert model == "gpt-5.4"
        assert env == {}

    def test_run_session_with_strategy_passes_runtime_to_four_arg_callback(self):
        """Four-arg session callbacks receive runtime/model/env separately."""
        captured = []

        def run_session(prompt_path, runtime_override="", model_override="", env_override=None):
            captured.append((prompt_path, runtime_override, model_override, env_override or {}))

        engine = self._make_engine()
        engine._run_session_fn = run_session

        engine._run_session_with_strategy(
            "worker.md",
            runtime_override="codex_cli",
            model_override="gpt-5.4",
            env_override={"OPENAI_API_KEY": "sk-test"},
        )

        assert captured == [
            ("worker.md", "codex_cli", "gpt-5.4", {"OPENAI_API_KEY": "sk-test"})
        ]

    def test_run_session_with_strategy_supports_legacy_three_arg_callback(self):
        """Legacy callbacks still receive model/env without runtime."""
        captured = []

        def run_session(prompt_path, model_override="", env_override=None):
            captured.append((prompt_path, model_override, env_override or {}))

        engine = self._make_engine()
        engine._run_session_fn = run_session

        engine._run_session_with_strategy(
            "worker.md",
            runtime_override="codex_cli",
            model_override="gpt-5.4",
            env_override={"OPENAI_API_KEY": "sk-test"},
        )

        assert captured == [
            ("worker.md", "gpt-5.4", {"OPENAI_API_KEY": "sk-test"})
        ]


# ---------------------------------------------------------------------------
# Priority chain: model_map[complexity] > engine defaults; task.model is ignored
# ---------------------------------------------------------------------------

class TestModelResolutionPriority:
    """Integration-level tests for the 3-layer priority."""

    def test_priority_task_model_ignored(self, capsys):
        """Explicit task.model no longer wins over model_map."""
        from nezha.dag.engine import DAGEngine
        mm = {"low": ModelMapEntry(runtime="codex_cli", model="haiku")}
        engine = DAGEngine(
            task_list_path=Path("/fake"),
            workspace=Path("/fake"),
            run_session_fn=lambda *a: None,
            model_map=mm,
        )
        task = Task(id="F-001", complexity="low", model="override-model")
        runtime, model, env = engine._resolve_runtime_model(task)
        assert runtime == "codex_cli"
        assert model == "haiku"
        assert env == {}
        assert "deprecated" in capsys.readouterr().out

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
        runtime, model, env = engine._resolve_runtime_model(task)
        assert runtime == ""
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
        runtime, model, env = engine._resolve_runtime_model(task)
        assert runtime == ""
        assert model == ""  # will use agent_config.engine.model at runtime


class TestExecutorAgentStrategyResolution:
    def test_agent_strategy_overrides_agent_engine_for_single_round(self):
        from nezha.config import AgentConfig, AgentMeta, ExecutorConfig
        from nezha.executor import _resolve_default_session_strategy

        executor_config = ExecutorConfig()
        executor_config.agent_strategies = {
            "planner-agent": RuntimeStrategy(runtime="codex_cli", model="gpt-5.4")
        }
        agent_config = AgentConfig(
            agent=AgentMeta(name="planner-agent"),
            engine=EngineConfig(runtime="claude_code", model="claude-sonnet-4-6"),
        )

        strategy = _resolve_default_session_strategy(executor_config, agent_config)

        assert strategy.runtime == "codex_cli"
        assert strategy.model == "gpt-5.4"
