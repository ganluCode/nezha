"""Tests for the Codex CLI runtime adapter."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from nezha.config import AgentConfig, EngineConfig
from nezha.runtime.codex_cli import (
    CodexCliRuntime,
    _event_from_codex_json,
    build_codex_command,
    run_session,
)
from nezha.runtime.types import RuntimeContext, SessionEvent, SessionResult


def test_build_codex_command_uses_unattended_stdin_mode(tmp_path):
    agent_config = AgentConfig(
        engine=EngineConfig(
            runtime="codex_cli",
            model="gpt-5.3-codex",
            security={"sandbox": "read-only"},
        )
    )
    output = tmp_path / ".codex-last-message.txt"

    command = build_codex_command(agent_config, tmp_path, output)

    assert command == [
        "codex",
        "--ask-for-approval",
        "never",
        "exec",
        "--cd",
        str(tmp_path),
        "--skip-git-repo-check",
        "--model",
        "gpt-5.3-codex",
        "--sandbox",
        "read-only",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--json",
        "--output-last-message",
        str(output),
        "-",
    ]


def test_build_codex_command_defaults_to_workspace_write(tmp_path):
    command = build_codex_command(
        AgentConfig(engine=EngineConfig(runtime="codex_cli", model="gpt-5.3-codex")),
        tmp_path,
        tmp_path / ".codex-last-message.txt",
    )
    assert command[command.index("--sandbox") + 1] == "workspace-write"


def test_build_codex_command_adds_workspace_when_cwd_is_target(tmp_path):
    target = tmp_path / "target"
    workspace = tmp_path / "workspace" / "features" / "f01"
    target.mkdir()
    workspace.mkdir(parents=True)

    command = build_codex_command(
        AgentConfig(engine=EngineConfig(runtime="codex_cli", model="gpt-5.4-mini")),
        target,
        workspace / ".codex-last-message.txt",
        workspace=workspace,
    )

    assert command[command.index("--cd") + 1] == str(target)
    assert command[command.index("--add-dir") + 1] == str(workspace.resolve())


def test_build_codex_command_adds_git_metadata_dirs_for_worktree(tmp_path):
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    workspace = tmp_path / "workspace"
    repo.mkdir()
    workspace.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    (repo / "README.md").write_text("demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "-m", "init",
        ],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "worktree", "add", str(worktree), "-b", "feature/test"],
        cwd=repo,
        check=True,
    )

    command = build_codex_command(
        AgentConfig(engine=EngineConfig(runtime="codex_cli", model="gpt-5.4-mini")),
        worktree,
        workspace / ".codex-last-message.txt",
        workspace=workspace,
    )

    add_dirs = [
        Path(command[i + 1])
        for i, arg in enumerate(command)
        if arg == "--add-dir"
    ]
    assert workspace.resolve() in add_dirs
    assert (repo / ".git" / "worktrees" / "worktree").resolve() in add_dirs
    assert (repo / ".git").resolve() in add_dirs


def test_build_codex_command_infers_provider_from_openai_env(tmp_path):
    command = build_codex_command(
        AgentConfig(engine=EngineConfig(runtime="codex_cli", model="kimi-k2-coder")),
        tmp_path,
        tmp_path / ".codex-last-message.txt",
        env={
            "OPENAI_BASE_URL": "https://api.example.com/v1",
            "OPENAI_API_KEY": "sk-example",
        },
    )

    assert "-c" in command
    assert 'model_provider="nezha_dynamic"' in command
    assert 'model_providers.nezha_dynamic.base_url="https://api.example.com/v1"' in command
    assert 'model_providers.nezha_dynamic.env_key="OPENAI_API_KEY"' in command
    assert 'model_providers.nezha_dynamic.wire_api="responses"' in command


def test_build_codex_command_keeps_default_provider_without_base_url(tmp_path):
    command = build_codex_command(
        AgentConfig(engine=EngineConfig(runtime="codex_cli", model="gpt-5.3-codex")),
        tmp_path,
        tmp_path / ".codex-last-message.txt",
        env={"OPENAI_API_KEY": "sk-example"},
    )

    assert not any(arg.startswith("model_provider=") for arg in command)


def test_build_codex_command_supports_codex_specific_env_aliases(tmp_path):
    command = build_codex_command(
        AgentConfig(engine=EngineConfig(runtime="codex_cli", model="custom-model")),
        tmp_path,
        tmp_path / ".codex-last-message.txt",
        env={
            "CODEX_PROVIDER": "openrouter",
            "CODEX_BASE_URL": "https://openrouter.ai/api/v1",
            "CODEX_API_KEY": "sk-or",
            "CODEX_WIRE_API": "chat",
        },
    )

    assert 'model_provider="openrouter"' in command
    assert 'model_providers.openrouter.base_url="https://openrouter.ai/api/v1"' in command
    assert 'model_providers.openrouter.env_key="CODEX_API_KEY"' in command
    assert 'model_providers.openrouter.wire_api="chat"' in command


def test_codex_json_maps_command_start_to_tool_call():
    event = _event_from_codex_json({
        "type": "item.started",
        "item": {
            "id": "item_1",
            "type": "command_execution",
            "command": "rg --files",
        },
    })
    assert event == SessionEvent(
        event_type="tool_call",
        data={
            "tool": "Bash",
            "tool_use_id": "item_1",
            "input": {"command": "rg --files"},
        },
    )


def test_codex_json_maps_command_completion_to_tool_result():
    event = _event_from_codex_json({
        "type": "item.completed",
        "item": {
            "id": "item_1",
            "type": "command_execution",
            "command": "rg --files",
            "aggregated_output": "a.py\n",
            "exit_code": 0,
        },
    })
    assert event is not None
    assert event.event_type == "tool_result"
    assert event.data["success"] is True
    assert event.data["output"] == "a.py\n"


def test_codex_json_maps_agent_message_to_thinking():
    event = _event_from_codex_json({
        "type": "item.completed",
        "item": {
            "id": "item_0",
            "type": "agent_message",
            "text": "I will inspect the repo.",
        },
    })
    assert event == SessionEvent(
        event_type="thinking",
        data={"text": "I will inspect the repo."},
    )


class _FakeStdin:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data):
        self.data += data

    async def drain(self):
        return None

    def close(self):
        self.closed = True


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""


class _HangingStdout:
    async def readline(self):
        await asyncio.sleep(60)
        return b""


class _FakeStderr:
    def __init__(self, text=""):
        self.text = text

    async def read(self):
        return self.text.encode("utf-8")


class _FakeProc:
    def __init__(self, lines, returncode=0, stderr=""):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(lines)
        self.stderr = _FakeStderr(stderr)
        self.returncode = returncode
        self.pid = 999999

    async def wait(self):
        return self.returncode


class _HangingProc(_FakeProc):
    def __init__(self):
        super().__init__([], returncode=None)
        self.stdout = _HangingStdout()

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(0.01)
        return self.returncode


async def _collect(async_iterable):
    return [item async for item in async_iterable]


@pytest.mark.asyncio
async def test_run_session_maps_usage_and_last_message(monkeypatch, tmp_path):
    last_message = tmp_path / ".codex-last-message.txt"
    proc = _FakeProc([
        b'{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"pwd","status":"in_progress"}}\n',
        b'{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"pwd","aggregated_output":"/tmp","exit_code":0,"status":"completed"}}\n',
        b'{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":3,"cached_input_tokens":5}}\n',
    ])

    async def fake_exec(*args, **kwargs):
        last_message.write_text("done", encoding="utf-8")
        assert kwargs["env"]["OPENAI_API_KEY"] == "sk-example"
        assert 'model_providers.nezha_dynamic.env_key="OPENAI_API_KEY"' in args
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    events = await _collect(run_session(
        prompt="hi",
        agent_config=AgentConfig(engine=EngineConfig(runtime="codex_cli", model="gpt-5.3-codex")),
        workspace=tmp_path,
        cwd=tmp_path,
        env={
            "OPENAI_BASE_URL": "https://api.example.com/v1",
            "OPENAI_API_KEY": "sk-example",
        },
        timeout=1,
    ))

    assert [type(e) for e in events] == [SessionEvent, SessionEvent, SessionResult]
    result = events[-1]
    assert result.status == "completed"
    assert result.input_tokens == 10
    assert result.output_tokens == 3
    assert result.cost_usd is None
    assert result.result_text == "done"
    assert proc.stdin.data == b"hi"
    assert proc.stdin.closed is True


@pytest.mark.asyncio
async def test_run_session_missing_codex_binary_returns_error(monkeypatch, tmp_path):
    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    events = await _collect(run_session(
        prompt="hi",
        agent_config=AgentConfig(engine=EngineConfig(runtime="codex_cli", model="gpt-5.3-codex")),
        workspace=tmp_path,
        cwd=tmp_path,
    ))

    assert events == [SessionResult(status="error", error="codex CLI not found in PATH")]


@pytest.mark.asyncio
async def test_run_session_nonzero_returncode_returns_error(monkeypatch, tmp_path):
    proc = _FakeProc([], returncode=2, stderr="bad flag")

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    events = await _collect(run_session(
        prompt="hi",
        agent_config=AgentConfig(engine=EngineConfig(runtime="codex_cli", model="gpt-5.3-codex")),
        workspace=tmp_path,
        cwd=tmp_path,
    ))

    assert len(events) == 1
    assert events[0].status == "error"
    assert "code 2" in events[0].error
    assert "bad flag" in events[0].error


@pytest.mark.asyncio
async def test_run_session_timeout_invokes_process_cleanup(monkeypatch, tmp_path):
    proc = _HangingProc()
    cleanup_called = False

    async def fake_exec(*args, **kwargs):
        return proc

    async def fake_cleanup(target_proc):
        nonlocal cleanup_called
        cleanup_called = True
        assert target_proc is proc
        proc.returncode = -15

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("nezha.runtime.codex_cli._terminate_process_group", fake_cleanup)

    events = await _collect(run_session(
        prompt="hi",
        agent_config=AgentConfig(engine=EngineConfig(runtime="codex_cli", model="gpt-5.3-codex")),
        workspace=tmp_path,
        cwd=tmp_path,
        timeout=0.01,
    ))

    assert cleanup_called is True
    assert len(events) == 1
    assert events[0].status == "error"
    assert "timed out" in events[0].error


@pytest.mark.asyncio
async def test_runtime_requires_agent_config(tmp_path):
    runtime = CodexCliRuntime()
    context = RuntimeContext(workspace=tmp_path)

    with pytest.raises(ValueError, match="requires context.agent_config"):
        await _collect(runtime.run_session("hi", context))
