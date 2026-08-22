"""Tests for runtime abstraction layer."""

from pathlib import Path

import pytest

from nezha.config import EngineConfig, load_agent_config
from nezha.engine import ClaudeCodeOptions
from nezha.engine import SessionEvent as EngineSessionEvent
from nezha.engine import SessionResult as EngineSessionResult
from nezha.runtime import (
    ClaudeCodeRuntime,
    CodexCliRuntime,
    RuntimeCapabilities,
    RuntimeContext,
    SessionEvent,
    SessionResult,
    get_runtime,
)


def test_engine_config_default_runtime_is_claude_code():
    config = EngineConfig()
    assert config.runtime == "claude_code"


def test_agent_config_loads_runtime_field(tmp_path):
    agent_yaml = tmp_path / "agent.yaml"
    agent_yaml.write_text(
        """
agent:
  name: test-agent
engine:
  runtime: codex_cli
  model: gpt-5.3-codex
"""
    )
    config = load_agent_config(agent_yaml)
    assert config.engine.runtime == "codex_cli"


def test_runtime_factory_returns_claude_code_runtime():
    runtime = get_runtime("claude_code")
    assert isinstance(runtime, ClaudeCodeRuntime)
    assert runtime.name == "claude_code"


def test_runtime_factory_accepts_legacy_claude_alias():
    runtime = get_runtime("claude-code")
    assert isinstance(runtime, ClaudeCodeRuntime)


def test_runtime_factory_returns_codex_cli_runtime():
    runtime = get_runtime("codex_cli")
    assert isinstance(runtime, CodexCliRuntime)
    assert runtime.name == "codex_cli"


def test_runtime_factory_accepts_codex_aliases():
    assert isinstance(get_runtime("codex-cli"), CodexCliRuntime)
    assert isinstance(get_runtime("codex"), CodexCliRuntime)


def test_runtime_factory_rejects_unknown_runtime():
    with pytest.raises(ValueError, match="Unknown runtime: unknown_runtime"):
        get_runtime("unknown_runtime")


def test_engine_module_reexports_session_types():
    assert EngineSessionEvent is SessionEvent
    assert EngineSessionResult is SessionResult
    assert ClaudeCodeOptions is not None


def test_runtime_types_are_importable():
    assert RuntimeCapabilities().tool_events is False
    assert RuntimeContext(workspace=Path(".")).workspace == Path(".")


def test_claude_code_runtime_capabilities():
    caps = get_runtime("claude_code").capabilities
    assert caps.tool_events is True
    assert caps.token_tracking is True
    assert caps.cost_tracking is True
    assert caps.pre_tool_hook is True
    assert caps.sandbox is False
    assert caps.mcp is True
    assert caps.output_schema is False
    assert caps.resume is False


def test_codex_cli_runtime_capabilities():
    caps = get_runtime("codex_cli").capabilities
    assert caps.tool_events is True
    assert caps.token_tracking is True
    assert caps.cost_tracking is False
    assert caps.pre_tool_hook is False
    assert caps.sandbox is True
    assert caps.mcp is False
    assert caps.output_schema is True
    assert caps.resume is False


def test_session_runner_has_codex_claude_md_fallback():
    from nezha.pipeline.session import _SUBPROCESS_RUNNER

    assert 'runtime_name in {{"codex", "codex_cli"}}' in _SUBPROCESS_RUNNER
    assert 'not (cwd / "AGENTS.md").is_file()' in _SUBPROCESS_RUNNER
    assert '(cwd / "CLAUDE.md").is_file()' in _SUBPROCESS_RUNNER
