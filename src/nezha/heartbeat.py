"""Heartbeat: periodically ping configured models to keep them warm."""

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

from nezha.config import HeartbeatConfig, HeartbeatModelEntry

_HEARTBEAT_PROMPT = "hi"
_PID_FILE = ".heartbeat.pid"


def _pid_file_path(state_dir: Path) -> Path:
    return state_dir / _PID_FILE


def _is_claude_model(model: str) -> bool:
    return model.startswith("claude-")


async def _ping_model(entry: HeartbeatModelEntry) -> str:
    """Send a single 'hi' to a model. Returns 'ok' or error message."""
    model = entry.model
    env = entry.env or {}

    try:
        if _is_claude_model(model):
            return await _ping_sdk(model, env)
        elif env.get("OPENAI_BASE_URL") or env.get("OPENAI_API_KEY"):
            return await _ping_openai(model, env)
        else:
            return await _ping_sdk(model, env)
    except Exception as e:
        return f"error: {e}"


async def _ping_sdk(model: str, env: dict) -> str:
    """Ping via claude-code-sdk (reuses Claude Code auth)."""
    import nezha.engine  # noqa: F401 — triggers monkey-patch for unknown message types
    from claude_code_sdk import ClaudeCodeOptions, query as sdk_query

    options = ClaudeCodeOptions(
        model=model,
        max_turns=1,
        permission_mode="bypassPermissions",
        env=env or {},
    )
    async for msg in sdk_query(prompt=_HEARTBEAT_PROMPT, options=options):
        if msg is None:
            continue
        if type(msg).__name__ == "ResultMessage":
            return "ok"
    return "ok"


async def _ping_openai(model: str, env: dict) -> str:
    """Ping via OpenAI-compatible SDK."""
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise ImportError("openai package required. Install: pip install openai")

    client = AsyncOpenAI(
        api_key=env.get("OPENAI_API_KEY", "sk-placeholder"),
        **({"base_url": env["OPENAI_BASE_URL"]} if env.get("OPENAI_BASE_URL") else {}),
    )
    await client.chat.completions.create(
        model=model,
        max_tokens=5,
        messages=[{"role": "user", "content": _HEARTBEAT_PROMPT}],
    )
    return "ok"


async def run_once(config: HeartbeatConfig) -> None:
    """Ping all configured models once."""
    if not config.models:
        print("[heartbeat] No models configured")
        return

    for entry in config.models:
        print(f"[heartbeat] Pinging {entry.model} ... ", end="", flush=True)
        result = await _ping_model(entry)
        print(result)


async def run_loop(config: HeartbeatConfig) -> None:
    """Run heartbeat loop indefinitely."""
    print(f"[heartbeat] Started — interval={config.interval}s, "
          f"models={[m.model for m in config.models]}")

    def _handle_term(signum, frame):
        print("\n[heartbeat] Received SIGTERM — stopping")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_term)

    while True:
        await run_once(config)
        print(f"[heartbeat] Next ping in {config.interval}s")
        await asyncio.sleep(config.interval)


def start(config: HeartbeatConfig, state_dir: Path) -> None:
    """Start heartbeat as a background process (idempotent)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    pid_file = _pid_file_path(state_dir)

    # Idempotency: check if already running
    if pid_file.exists():
        try:
            data = json.loads(pid_file.read_text())
            pid = data.get("pid")
            if pid:
                os.kill(pid, 0)  # signal 0 = check existence
                print(f"[heartbeat] Already running (pid={pid})")
                print(f"  Use 'nezha heartbeat stop' to stop it first")
                return
        except (ProcessLookupError, OSError):
            pid_file.unlink(missing_ok=True)  # stale PID file
        except Exception:
            pid_file.unlink(missing_ok=True)

    if not config.models:
        print("[heartbeat] No models configured in executor.yaml heartbeat.models")
        return

    log_path = state_dir / "heartbeat.log"

    # Launch background subprocess
    cmd = [
        sys.executable, "-c",
        (
            "import asyncio, sys, json;"
            "from pathlib import Path;"
            "from nezha.config import load_executor_config;"
            f"cfg = load_executor_config({str(Path(state_dir.parent / 'executor.yaml'))!r});"
            "asyncio.run(__import__('nezha.heartbeat', fromlist=['run_loop']).run_loop(cfg.heartbeat))"
        ),
    ]

    import subprocess
    with open(log_path, "w") as log_f:
        proc = subprocess.Popen(
            cmd,
            stdout=log_f,
            stderr=log_f,
            start_new_session=True,
        )

    pid_file.write_text(json.dumps({"pid": proc.pid, "log": str(log_path)}))
    print(f"[heartbeat] Started (pid={proc.pid})")
    print(f"  Models: {[m.model for m in config.models]}")
    print(f"  Interval: {config.interval}s")
    print(f"  Log: {log_path}")


def stop(state_dir: Path) -> None:
    """Stop the background heartbeat process."""
    pid_file = _pid_file_path(state_dir)

    if not pid_file.exists():
        print("[heartbeat] Not running (no PID file)")
        return

    try:
        data = json.loads(pid_file.read_text())
        pid = data.get("pid")
    except Exception:
        pid_file.unlink(missing_ok=True)
        print("[heartbeat] Invalid PID file — cleaned up")
        return

    if not pid:
        pid_file.unlink(missing_ok=True)
        return

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        print(f"[heartbeat] Process {pid} is no longer running")
        pid_file.unlink(missing_ok=True)
        return

    try:
        os.kill(pid, signal.SIGTERM)
        pid_file.unlink(missing_ok=True)
        print(f"[heartbeat] Stopped (pid={pid})")
    except OSError as e:
        print(f"[heartbeat] Failed to stop process {pid}: {e}")
