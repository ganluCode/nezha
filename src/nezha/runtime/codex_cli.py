"""Codex CLI runtime adapter."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import AsyncGenerator

from nezha.config import AgentConfig
from nezha.runtime.types import (
    RuntimeCapabilities,
    RuntimeContext,
    SessionEvent,
    SessionResult,
)


CODEX_CLI_CAPABILITIES = RuntimeCapabilities(
    tool_events=True,
    token_tracking=True,
    cost_tracking=False,
    pre_tool_hook=False,
    sandbox=True,
    mcp=False,
    output_schema=True,
    resume=False,
)


def _sandbox_from_config(agent_config: AgentConfig) -> str:
    sandbox = agent_config.engine.security.get("sandbox", "workspace-write")
    return str(sandbox or "workspace-write")


def _env_value(env: dict[str, str], *keys: str) -> tuple[str, str]:
    """Return the first configured env key/value pair from a list of aliases."""
    for key in keys:
        value = env.get(key, "")
        if value and value != f"${{{key}}}":
            return key, value
    return "", ""


def _codex_provider_config_args(env: dict[str, str] | None) -> list[str]:
    """Build Codex `-c` provider overrides inferred from environment config.

    If no base URL is configured, Codex keeps its default provider/auth. This
    mirrors Claude runtime behavior: model_map env overrides are optional.
    """
    env = env or {}
    _, base_url = _env_value(
        env,
        "CODEX_BASE_URL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    )
    if not base_url:
        return []

    api_key_env, _ = _env_value(
        env,
        "CODEX_API_KEY",
        "OPENAI_API_KEY",
    )
    _, provider = _env_value(env, "CODEX_PROVIDER", "OPENAI_PROVIDER")
    _, wire_api = _env_value(env, "CODEX_WIRE_API")

    provider = provider or "nezha_dynamic"
    wire_api = wire_api or "responses"

    args = [
        "-c", f'model_provider="{provider}"',
        "-c", f'model_providers.{provider}.name="{provider}"',
        "-c", f'model_providers.{provider}.base_url="{base_url}"',
        "-c", f'model_providers.{provider}.wire_api="{wire_api}"',
    ]
    if api_key_env:
        args.extend(["-c", f'model_providers.{provider}.env_key="{api_key_env}"'])
    return args


def build_codex_command(
    agent_config: AgentConfig,
    cwd: Path,
    output_last_message: Path,
    env: dict[str, str] | None = None,
    workspace: Path | None = None,
    extra_writable_dirs: list[Path] | None = None,
) -> list[str]:
    """Build the unattended `codex exec` command."""
    command = [
        "codex",
        "--ask-for-approval",
        "never",
        "exec",
        *_codex_provider_config_args(env),
        "--cd",
        str(cwd),
        "--skip-git-repo-check",
    ]

    add_dirs = _collect_add_dirs(cwd, workspace, extra_writable_dirs)
    for path in add_dirs:
        command.extend(["--add-dir", str(path)])

    command.extend([
        "--model",
        agent_config.engine.model,
        "--sandbox",
        _sandbox_from_config(agent_config),
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--json",
        "--output-last-message",
        str(output_last_message),
        "-",
    ])
    return command


def _collect_add_dirs(
    cwd: Path,
    workspace: Path | None,
    extra_writable_dirs: list[Path] | None = None,
) -> list[Path]:
    """Collect additional writable dirs for Codex sandbox execution."""
    cwd_resolved = cwd.resolve()
    dirs: list[Path] = []

    def _add(path: Path | None) -> None:
        if path is None:
            return
        resolved = path.resolve()
        if resolved == cwd_resolved:
            return
        if resolved not in dirs:
            dirs.append(resolved)

    _add(workspace)
    for path in extra_writable_dirs or []:
        _add(path)
    for path in _git_metadata_dirs(cwd):
        _add(path)
    return dirs


def _git_metadata_dirs(cwd: Path) -> list[Path]:
    """Return git metadata directories needed for worktree commits."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-dir", "--git-common-dir"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []

    dirs: list[Path] = []
    for raw in proc.stdout.splitlines():
        if not raw.strip():
            continue
        path = Path(raw.strip())
        if not path.is_absolute():
            path = cwd / path
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.is_dir() and resolved not in dirs:
            dirs.append(resolved)
    return dirs


def _event_from_codex_json(data: dict) -> SessionEvent | None:
    """Map one Codex JSONL object to a runtime event, if it is event-like."""
    if data.get("type") not in {"item.started", "item.completed"}:
        return None

    item = data.get("item")
    if not isinstance(item, dict):
        return None

    item_type = item.get("type")
    if item_type == "agent_message" and data.get("type") == "item.completed":
        return SessionEvent(
            event_type="thinking",
            data={"text": item.get("text", "")},
        )

    if item_type == "command_execution":
        command = item.get("command", "")
        if data.get("type") == "item.started":
            return SessionEvent(
                event_type="tool_call",
                data={
                    "tool": "Bash",
                    "tool_use_id": item.get("id", ""),
                    "input": {"command": command},
                },
            )

        if data.get("type") == "item.completed":
            exit_code = item.get("exit_code")
            output = item.get("aggregated_output", "")
            return SessionEvent(
                event_type="tool_result",
                data={
                    "tool_use_id": item.get("id", ""),
                    "success": exit_code == 0,
                    "content": str(output)[:2000] if output else "",
                    "output": output,
                    "exit_code": exit_code,
                    "is_error": exit_code not in (0, None),
                },
            )

    return None


def _usage_from_codex_json(data: dict) -> dict:
    if data.get("type") != "turn.completed":
        return {}
    usage = data.get("usage")
    return usage if isinstance(usage, dict) else {}


async def _terminate_process_group(proc: asyncio.subprocess.Process) -> None:
    """Terminate the process group for a Codex CLI subprocess."""
    if proc.returncode is not None:
        return

    try:
        pgid = os.getpgid(proc.pid)
        if pgid != os.getpgid(0):
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        return
    except PermissionError:
        proc.terminate()

    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
        return
    except asyncio.TimeoutError:
        pass

    try:
        pgid = os.getpgid(proc.pid)
        if pgid != os.getpgid(0):
            os.killpg(pgid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        return
    except PermissionError:
        proc.kill()
    await proc.wait()


async def run_session(
    prompt: str,
    agent_config: AgentConfig,
    workspace: Path,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    extra_writable_dirs: list[Path] | None = None,
) -> AsyncGenerator[SessionEvent | SessionResult, None]:
    """Run one unattended Codex CLI session."""
    started = time.monotonic()
    workspace.mkdir(parents=True, exist_ok=True)
    output_last_message = workspace / ".codex-last-message.txt"
    command = build_codex_command(
        agent_config,
        cwd,
        output_last_message,
        env=env,
        workspace=workspace,
        extra_writable_dirs=extra_writable_dirs,
    )
    extra_dirs = []
    for i, arg in enumerate(command):
        if arg == "--add-dir" and i + 1 < len(command):
            extra_dirs.append(command[i + 1])
    if extra_dirs:
        print(f"[codex_cli] writable extra dirs: {', '.join(extra_dirs)}")
    process_env = {**os.environ, **(env or {})}

    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=process_env,
            start_new_session=True,
        )
    except FileNotFoundError:
        yield SessionResult(status="error", error="codex CLI not found in PATH")
        return
    except Exception as exc:
        yield SessionResult(status="error", error=f"Failed to start codex CLI: {exc}")
        return

    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None

    proc.stdin.write(prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()

    usage: dict = {}
    parse_errors: list[str] = []
    deadline = (time.monotonic() + timeout) if timeout else None

    async def _read_stderr() -> str:
        stderr_bytes = await proc.stderr.read()
        return stderr_bytes.decode("utf-8", errors="replace").strip()

    stderr_task = asyncio.create_task(_read_stderr())

    try:
        while True:
            line_task = asyncio.create_task(proc.stdout.readline())
            remaining = None
            if deadline is not None:
                remaining = max(0.0, deadline - time.monotonic())
            done, _pending = await asyncio.wait(
                {line_task},
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                line_task.cancel()
                await _terminate_process_group(proc)
                stderr_text = await stderr_task
                error = f"Codex session timed out ({timeout}s)"
                if stderr_text:
                    error += f"\n{stderr_text[-500:]}"
                yield SessionResult(status="error", error=error)
                return

            line = line_task.result()
            if not line:
                break

            try:
                data = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError as exc:
                parse_errors.append(str(exc))
                continue

            event = _event_from_codex_json(data)
            if event:
                yield event
            usage_update = _usage_from_codex_json(data)
            if usage_update:
                usage = usage_update
    finally:
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                await _terminate_process_group(proc)

    stderr_text = await stderr_task
    duration_ms = int((time.monotonic() - started) * 1000)

    result_text = ""
    if output_last_message.exists():
        try:
            result_text = output_last_message.read_text(encoding="utf-8")
        except Exception:
            result_text = ""

    if proc.returncode != 0:
        error = f"codex CLI exited with code {proc.returncode}"
        if stderr_text:
            error += f"\n{stderr_text[-500:]}"
        yield SessionResult(
            status="error",
            duration_ms=duration_ms,
            cost_usd=None,
            input_tokens=usage.get("input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
            result_text=result_text,
            error=error,
        )
        return

    if parse_errors:
        yield SessionResult(
            status="error",
            duration_ms=duration_ms,
            cost_usd=None,
            input_tokens=usage.get("input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
            result_text=result_text,
            error=f"Failed to parse codex JSONL: {parse_errors[0]}",
        )
        return

    yield SessionResult(
        status="completed",
        duration_ms=duration_ms,
        num_turns=1 if usage else 0,
        cost_usd=None,
        input_tokens=usage.get("input_tokens", 0) or 0,
        output_tokens=usage.get("output_tokens", 0) or 0,
        result_text=result_text,
        error=stderr_text,
    )


class CodexCliRuntime:
    """AgentRuntime implementation backed by `codex exec`."""

    name = "codex_cli"
    capabilities = CODEX_CLI_CAPABILITIES

    async def run_session(
        self,
        prompt: str,
        context: RuntimeContext,
    ) -> AsyncGenerator[SessionEvent | SessionResult, None]:
        if context.agent_config is None:
            raise ValueError("CodexCliRuntime requires context.agent_config")
        cwd = context.cwd or context.workspace
        async for event in run_session(
            prompt=prompt,
            agent_config=context.agent_config,
            workspace=context.workspace,
            cwd=cwd,
            env=context.env,
            timeout=context.timeout,
            extra_writable_dirs=[
                Path(p) for p in context.metadata.get("extra_writable_dirs", [])
            ],
        ):
            yield event
