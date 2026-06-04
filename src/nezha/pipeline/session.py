"""Session management: single-round and multi-round execution."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from nezha.config import AgentConfig, ExecutorConfig, load_agent_config
from nezha.engine import (
    SessionEvent,
    SessionResult,
    build_options,
    run_session,
)
from nezha.i18n import setup_locale, t
from nezha.pipeline.io import (
    build_input_context,
    ensure_output_dir,
    scan_input_files,
)
from nezha.pipeline.knowledge import load_agent_context, load_knowledge, load_project_context
from nezha.pipeline.prompt_template import load_and_render, resolve_prompt_path
from nezha.pipeline.security import create_security_hook


def _write_session_manifest(
    workspace: Path,
    agent_name: str,
    model: str,
    cwd: Path,
    input_files: list[Path],
    knowledge: str,
    agent_ctx: str,
    project_context: str | None,
    project_dir: Path | None,
    prompt_total_chars: int,
) -> None:
    """Write a session manifest recording what was injected into the prompt.

    This provides an audit trail answering: "what context did the agent see?"
    The manifest is written to workspace/.session_manifest.json.
    """
    from datetime import datetime

    # Scan project_dir for actual files
    project_files: list[str] = []
    if project_dir and project_dir.is_dir():
        project_files = sorted(
            str(p.relative_to(project_dir))
            for p in project_dir.rglob("*")
            if p.is_file() and p.name != ".gitkeep"
        )

    manifest = {
        "timestamp": datetime.now().isoformat(),
        "agent": agent_name,
        "model": model,
        "cwd": str(cwd),
        "prompt_context": {
            "input_files": [
                {"name": f.name, "size": f.stat().st_size}
                for f in input_files if f.exists()
            ],
            "knowledge": {"source": "CLAUDE.md", "chars": len(knowledge)} if knowledge else None,
            "agent_context": {"source": "agent-context.md", "chars": len(agent_ctx)} if agent_ctx else None,
            "project_context": {
                "source": str(project_dir),
                "chars": len(project_context) if project_context else 0,
                "files": project_files,
            } if project_dir else None,
            "prompt_total_chars": prompt_total_chars,
        },
    }

    manifest_file = workspace / ".session_manifest.json"
    try:
        manifest_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    except Exception:
        pass  # Non-critical — don't fail the session


def _kill_process_group(proc: subprocess.Popen) -> None:
    """Kill all processes in the subprocess's process group.

    This ensures child processes spawned by the session (e.g. vitest workers,
    dev servers) are cleaned up when the session ends.
    """
    try:
        pgid = os.getpgid(proc.pid)
        # Only kill if the process group is different from ours
        if pgid != os.getpgid(0):
            os.killpg(pgid, signal.SIGTERM)
            # Give processes a moment to exit gracefully, then force kill
            time.sleep(0.5)
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # Already exited
    except (ProcessLookupError, PermissionError):
        pass  # Process group already gone


async def run_single_round(
    executor_config: ExecutorConfig,
    agent_config: AgentConfig,
    workspace: Path,
    on_event=None,
    env: dict[str, str] | None = None,
    target: Path | None = None,
    project_dir: Path | None = None,
    agent_workspace: Path | None = None,
    mode: str | None = None,
    base_dir: Path | None = None,
) -> SessionResult:
    """Run a single-round agent session in an isolated subprocess.

    Uses subprocess isolation to avoid claude-code-sdk's anyio cancel scope
    pollution when running consecutive sessions in the same event loop.

    Args:
        executor_config: Executor configuration
        agent_config: Agent configuration
        workspace: Metadata workspace path (input files, feature_list, etc.)
        on_event: Optional async callback for session events (not used in subprocess mode)
        env: Environment variables to pass to the LLM engine
        target: Optional code repository path to use as LLM cwd.
                If None, falls back to workspace.
        project_dir: Optional project-level shared knowledge directory.
        agent_workspace: Agent workspace root for agent-context.md (cross-task memory).
                         Defaults to workspace when not in task queue mode.
        mode: Optional execution mode (e.g. "gardening") — selects an alternate
              prompt from agent_config.session.prompts[mode].
        base_dir: Project base directory (where executor.yaml lives).
    """
    workspace.mkdir(parents=True, exist_ok=True)
    ensure_output_dir(agent_config, workspace)

    # Resolve prompt key based on mode
    prompt_key = mode if (mode and agent_config.session.prompts.get(mode)) else "worker"
    worker_prompt_path = agent_config.session.prompts.get(prompt_key, "")
    worker_compose = agent_config.session.compose.get(prompt_key) if agent_config.session.compose else None
    if not worker_prompt_path and not (worker_compose and worker_compose.base):
        if mode:
            raise ValueError(
                f"Agent {agent_config.agent.name}: no prompt configured for mode '{mode}'. "
                f"Add '{mode}: <path>' under session.prompts in agent config."
            )
        raise ValueError(f"Agent {agent_config.agent.name}: no worker prompt configured")

    # Resolve paths for subprocess isolation
    if base_dir:
        project_root = base_dir
    else:
        project_root = Path.cwd()
    executor_config_path = (project_root / "executor.yaml").resolve()
    agent_config_path = (project_root / "agents" / f"{agent_config.agent.name}.yaml").resolve()
    prompts_dir = Path(executor_config.prompts_dir)
    if not prompts_dir.is_absolute():
        prompts_dir = project_root / prompts_dir

    # Merge env: executor global < agent-level
    merged_env = {**executor_config.env, **agent_config.engine.env, **(env or {})}

    print(f"[session] Starting single-round session for {agent_config.agent.name}")
    print(f"[session] Workspace: {workspace}")
    if target:
        print(f"[session] Target (cwd): {target}")

    # Run in isolated subprocess to avoid SDK cancel scope pollution
    result = _run_isolated_session(
        project_root=project_root,
        executor_config_path=executor_config_path,
        agent_config_path=agent_config_path,
        workspace=workspace,
        prompts_dir=prompts_dir,
        prompt_path=worker_prompt_path,
        env=merged_env,
        target=target,
        project_dir=project_dir,
        agent_workspace=agent_workspace,
        prompt_key=prompt_key,
        timeout=agent_config.engine.session_timeout,
    )

    return result


# ---------------------------------------------------------------------------
# Multi-round: subprocess isolation to avoid SDK cancel scope pollution
# ---------------------------------------------------------------------------

_SUBPROCESS_RUNNER = '''
import asyncio, json, sys, os
from pathlib import Path

sys.path.insert(0, "{project_root}")

from nezha.config import build_model_map_info, load_agent_config, load_executor_config
from nezha.engine import SessionEvent, SessionResult, build_options, run_session
from nezha.i18n import setup_locale, t
from nezha.pipeline.io import build_input_context, ensure_output_dir, scan_input_files
from nezha.pipeline.knowledge import load_agent_context, load_knowledge, load_project_context
from nezha.pipeline.prompt_composer import compose_prompt
from nezha.pipeline.prompt_template import load_and_render, resolve_prompt_path
from nezha.pipeline.security import create_security_hook

async def main():
    executor_config = load_executor_config("{executor_config_path}")
    # Setup locale from executor config
    if executor_config.locale:
        setup_locale(executor_config.locale)
    agent_config = load_agent_config("{agent_config_path}")
    # Apply per-task model override (empty string = use agent default)
    _model_override = "{model_override}"
    if _model_override:
        agent_config.engine.model = _model_override
    workspace = Path("{workspace}")
    # cwd: where the LLM operates (code repo for coding agents, else workspace)
    cwd = Path("{cwd}")
    project_dir = Path("{project_dir}") if "{project_dir}" else None
    agent_workspace = Path("{agent_workspace}") if "{agent_workspace}" else workspace
    prompts_dir = Path("{prompts_dir}")

    workspace.mkdir(parents=True, exist_ok=True)
    ensure_output_dir(agent_config, workspace)

    input_files = scan_input_files(agent_config, workspace)
    if input_files:
        print(f"[session] 注入 input 文件 ({{len(input_files)}}):")
        for _f in input_files:
            _sz = _f.stat().st_size if _f.exists() else 0
            print(f"  - {{_f.name}} ({{_sz}} bytes)")
    else:
        print("[session] 警告: 无 input 文件注入!")
    variables = dict(
        workspace=str(workspace),
        project_name=cwd.name,
        input_files=build_input_context(input_files, workspace),
        project_dir=str(project_dir) if project_dir else "",
        model_map_info=build_model_map_info(executor_config.model_map),
    )

    # Build prompt — compose mode or single template
    prompt_key = "{prompt_key}"
    compose_config = agent_config.session.compose.get(prompt_key) if agent_config.session.compose else None
    if compose_config and compose_config.base:
        prompt = compose_prompt(compose_config, prompts_dir, locale=executor_config.locale or "en", variables=variables)
    else:
        template_path = resolve_prompt_path(prompts_dir, "{prompt_path}", locale=executor_config.locale or "en")
        prompt = load_and_render(template_path, variables)

    # Inject project knowledge from cwd (CLAUDE.md lives in the code repo)
    knowledge = load_knowledge(cwd)
    if knowledge:
        prompt = knowledge + "\\n\\n" + prompt
        print(f"[session] 注入 CLAUDE.md ({{len(knowledge)}} chars)")

    # Inject agent cross-task memory (agent-context.md from agent workspace root)
    agent_ctx = load_agent_context(agent_workspace)
    if agent_ctx:
        prompt = agent_ctx + "\\n\\n" + prompt
        print(f"[session] 注入 agent-context.md ({{len(agent_ctx)}} chars)")

    # Inject project-level context (project layer prioritized — placed before workspace knowledge)
    if project_dir:
        project_context = load_project_context(project_dir)
        if project_context:
            prompt = project_context + "\\n\\n" + prompt
            print(f"[session] 注入 project context ({{len(project_context)}} chars) ← {{project_dir}}")
        else:
            print(f"[session] 警告: project 目录为空或不存在: {{project_dir}}")

    # --- Write session manifest (prompt injection audit log) ---
    from datetime import datetime as _dt
    _manifest = {{
        "timestamp": _dt.now().isoformat(),
        "agent": agent_config.agent.name,
        "model": agent_config.engine.model,
        "cwd": str(cwd),
        "prompt_context": {{
            "input_files": [
                {{"name": _f.name, "size": _f.stat().st_size}}
                for _f in input_files if _f.exists()
            ],
            "knowledge": {{"source": "CLAUDE.md", "chars": len(knowledge)}} if knowledge else None,
            "agent_context": {{"source": "agent-context.md", "chars": len(agent_ctx)}} if agent_ctx else None,
            "project_context": {{
                "source": str(project_dir),
                "chars": len(project_context) if project_context else 0,
                "files": sorted(
                    _p.name for _p in project_dir.rglob("*")
                    if _p.is_file() and _p.name != ".gitkeep"
                ) if project_dir and project_dir.is_dir() else [],
            }} if project_dir else None,
            "prompt_total_chars": len(prompt),
        }},
    }}
    _manifest_file = Path("{workspace}") / ".session_manifest.json"
    _manifest_file.write_text(json.dumps(_manifest, indent=2, ensure_ascii=False))

    allowed = agent_config.engine.security.get("allowed_commands")
    security_hook = create_security_hook(set(allowed) if allowed else None)
    # Merge env: executor global < agent-level, passed via subprocess environment
    merged_env = {{**executor_config.env, **agent_config.engine.env}}
    # Merge MCP servers: global (executor) < agent-level (agent wins)
    options = build_options(
        agent_config, cwd, security_hook, env=merged_env,
        extra_mcp_servers=executor_config.mcp_servers or {{}},
    )

    result = None
    async for event in run_session(prompt, options):
        if isinstance(event, SessionResult):
            result = event
        elif isinstance(event, SessionEvent):
            if event.event_type == "thinking":
                print(event.data.get("text", ""), end="", flush=True)
            elif event.event_type == "tool_call":
                tool_name = event.data.get("tool", "unknown")
                tool_input = event.data.get("input", {{}})
                # Show tool name and key parameters
                detail = ""
                if tool_name == "Read":
                    file_path = tool_input.get("file_path", "")
                    if file_path:
                        detail = f" → {{file_path}}"
                elif tool_name == "Edit":
                    file_path = tool_input.get("file_path", "")
                    if file_path:
                        detail = f" → {{file_path}}"
                elif tool_name == "Write":
                    file_path = tool_input.get("file_path", "")
                    if file_path:
                        detail = f" → {{file_path}}"
                elif tool_name == "Bash":
                    cmd = tool_input.get("command", "")
                    if cmd:
                        # Show first 60 chars of command
                        cmd_preview = cmd[:60] + "..." if len(cmd) > 60 else cmd
                        detail = f" → {{cmd_preview}}"
                elif tool_name == "Grep":
                    pattern = tool_input.get("pattern", "")
                    if pattern:
                        detail = f" → {{pattern}}"
                elif tool_name == "Glob":
                    pattern = tool_input.get("pattern", "")
                    if pattern:
                        detail = f" → {{pattern}}"
                print(f"\\n[Tool: {{tool_name}}]{{detail}}", flush=True)
            elif event.event_type == "tool_result":
                success = event.data.get("success", False)
                status = "成功" if success else "失败"
                # Show result summary for some tools
                result_preview = ""
                if success and "output" in event.data:
                    output = event.data["output"]
                    if isinstance(output, str):
                        lines = output.strip().split("\\n")
                        if len(lines) == 1 and len(lines[0]) <= 50:
                            result_preview = f" → {{lines[0]}}"
                        elif len(lines) > 1:
                            result_preview = f" → {{len(lines)}} lines"
                print(f"  [{{status}}]{{result_preview}}", flush=True)
            elif event.event_type == "rate_limited":
                print("\\n[rate_limited] API rate limit detected — signalling graceful stop", flush=True)
                result = SessionResult(status="rate_limited", error="API rate limit")
                break

    # Write result to file for parent to read (always in metadata workspace)
    result_data = dict(
        status=result.status if result else "error",
        duration_ms=result.duration_ms if result else 0,
        num_turns=result.num_turns if result else 0,
        cost_usd=result.cost_usd if result else None,
        input_tokens=result.input_tokens if result else 0,
        output_tokens=result.output_tokens if result else 0,
        result_text=(result.result_text[:500] if result else ""),
        error=result.error if result else "No result",
    )
    result_file = Path("{workspace}") / ".session_result.json"
    result_file.write_text(json.dumps(result_data))

try:
    asyncio.run(main())
except (RuntimeError, SystemExit):
    # Suppress anyio cancel scope cleanup errors from claude-code-sdk.
    # The result is already written to .session_result.json before cleanup.
    pass
'''


def _run_isolated_session(
    project_root: Path,
    executor_config_path: Path,
    agent_config_path: Path,
    workspace: Path,
    prompts_dir: Path,
    prompt_path: str,
    env: dict[str, str] | None = None,
    target: Path | None = None,
    project_dir: Path | None = None,
    agent_workspace: Path | None = None,
    prompt_key: str = "worker",
    model_override: str = "",
    timeout: int = 3600,
) -> SessionResult:
    """Run a session in an isolated subprocess to avoid SDK event loop contamination.

    Args:
        target: Optional code repository path (cwd for LLM). Defaults to workspace.
        project_dir: Optional project-level shared knowledge directory.
        agent_workspace: Agent workspace root for agent-context.md. Defaults to workspace.
        prompt_key: Key to look up compose config in agent_config.session.compose.
        timeout: Maximum time in seconds for the subprocess (default 3600 = 1 hour).
    """
    cwd = target if target else workspace
    script = _SUBPROCESS_RUNNER.format(
        project_root=str(project_root),
        executor_config_path=str(executor_config_path),
        agent_config_path=str(agent_config_path),
        workspace=str(workspace),
        cwd=str(cwd),
        project_dir=str(project_dir) if project_dir else "",
        agent_workspace=str(agent_workspace) if agent_workspace else "",
        prompts_dir=str(prompts_dir),
        prompt_path=prompt_path,
        prompt_key=prompt_key,
        model_override=model_override,
    )

    # Merge env: system env + custom env (custom overrides system)
    process_env = {**os.environ, **(env or {})}

    # Use Popen with new process group so we can clean up all child processes
    # (e.g. vitest workers, dev servers) when the session ends.
    # Capture stderr for diagnostics when subprocess crashes without writing result file.
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        env=process_env,
        start_new_session=True,
        stderr=subprocess.PIPE,
    )
    try:
        _, stderr_bytes = proc.communicate(timeout=timeout)
        stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace").strip()
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        stderr_text = ""
        try:
            _, stderr_bytes = proc.communicate(timeout=5)
            stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace").strip()
        except Exception:
            pass
        # Try to read partial result even on timeout
        result_file = workspace / ".session_result.json"
        if result_file.exists():
            try:
                with open(result_file) as f:
                    data = json.load(f)
                result_file.unlink()
                data["status"] = "error"
                data["error"] = f"Session timed out ({timeout}s) — partial: {data.get('error', '')}"
                return SessionResult(**data)
            except Exception:
                pass
        return SessionResult(status="error", error=f"Session timed out ({timeout}s)")
    finally:
        _kill_process_group(proc)

    # Print stderr (SDK warnings, cancel scope errors) to console for visibility
    if stderr_text:
        # Filter out known SDK noise (cancel scope / anyio cleanup warnings)
        _important_lines = [
            line for line in stderr_text.split("\n")
            if "cancel scope" not in line.lower()
            and "GeneratorExit" not in line
            and "Task exception was never retrieved" not in line
            and "__aexit__" not in line
            and "query.py" not in line
            and "anyio/_backends" not in line
            and "await query.close()" not in line
        ]
        if _important_lines:
            print(f"[session] stderr: {chr(10).join(_important_lines[-10:])}")

    # Read result from file
    result_file = workspace / ".session_result.json"
    if result_file.exists():
        try:
            with open(result_file) as f:
                data = json.load(f)
            result_file.unlink()
            return SessionResult(**data)
        except Exception:
            pass

    if proc.returncode == 0:
        return SessionResult(status="completed")
    # Include last 500 chars of stderr in error message for diagnostics
    err_detail = f"Process exited with code {proc.returncode}"
    if stderr_text:
        err_detail += f"\n{stderr_text[-500:]}"
    return SessionResult(status="error", error=err_detail)


def _run_pre_agents(
    agent_config: AgentConfig,
    workspace: Path,
    project_root: Path,
    executor_config_path: Path,
    prompts_dir: Path,
    env: dict[str, str] | None = None,
    target: Path | None = None,
    project_dir: Path | None = None,
    agent_workspace: Path | None = None,
) -> list[SessionResult]:
    """Run callable pre-agents to produce missing artifacts.

    Iterates over pipeline.pre_agents config. For each entry, if the artifact
    file doesn't exist in the workspace, loads the pre-agent's config, verifies
    it's callable, and runs it in an isolated session.

    Returns list of SessionResults from pre-agent runs.
    """
    results = []
    pre_agents = agent_config.pipeline.pre_agents
    if not pre_agents:
        return results

    agents_dir = project_root / "agents"

    for pa in pre_agents:
        artifact_path = workspace / pa.artifact
        if artifact_path.exists():
            print(f"[pipeline] Artifact '{pa.artifact}' already exists, skipping pre-agent '{pa.name}'")
            continue

        # Load pre-agent config
        pa_config_path = agents_dir / f"{pa.name}.yaml"
        if not pa_config_path.exists():
            print(f"[pipeline] ERROR: Pre-agent config not found: {pa_config_path}")
            results.append(SessionResult(
                status="error",
                error=f"Pre-agent config not found: {pa_config_path}",
            ))
            continue

        pa_config = load_agent_config(pa_config_path)

        # Safety check: only callable agents can be auto-invoked
        if not pa_config.agent.callable:
            print(f"[pipeline] ERROR: Agent '{pa.name}' is not callable (callable=false), cannot auto-invoke")
            results.append(SessionResult(
                status="error",
                error=f"Agent '{pa.name}' is not callable",
            ))
            continue

        # Get pre-agent's worker prompt
        pa_prompt_path = pa_config.session.prompts.get("worker", "")
        if not pa_prompt_path:
            print(f"[pipeline] ERROR: Pre-agent '{pa.name}' has no worker prompt")
            results.append(SessionResult(
                status="error",
                error=f"Pre-agent '{pa.name}' has no worker prompt",
            ))
            continue

        print(f"\n{'=' * 60}")
        print(f"  PRE-AGENT: {pa.name}")
        print(f"  Producing: {pa.artifact}")
        print(f"{'=' * 60}\n")

        result = _run_isolated_session(
            project_root=project_root,
            executor_config_path=executor_config_path,
            agent_config_path=pa_config_path,
            workspace=workspace,
            prompts_dir=prompts_dir,
            prompt_path=pa_prompt_path,
            env=env,
            target=target,
            project_dir=project_dir,
            agent_workspace=agent_workspace,
            timeout=pa_config.engine.session_timeout,
        )
        results.append(result)

        print(f"\n{'-' * 60}")
        print(f"  Pre-agent {pa.name} {result.status} | "
              f"turns={result.num_turns} | "
              f"cost=${result.cost_usd or 0:.4f}")
        print(f"{'-' * 60}")

        # Verify artifact was produced
        if not artifact_path.exists():
            print(f"[pipeline] WARNING: Pre-agent '{pa.name}' finished but artifact '{pa.artifact}' not found")

    return results


async def run_multi_round(
    executor_config: ExecutorConfig,
    agent_config: AgentConfig,
    workspace: Path,
    max_iterations: int | None = None,
    on_event=None,
    env: dict[str, str] | None = None,
    target: Path | None = None,
    project_dir: Path | None = None,
    agent_workspace: Path | None = None,
    base_dir: Path | None = None,
    skip_planner: bool = False,
) -> "tuple[list[SessionResult], DAGExecutionResult | None]":
    """Run multi-round agent sessions using DAG-driven execution.

    The DAG engine reads feature_list.json, builds a dependency graph,
    picks the next feature (rework priority → ready), writes .dag_context.json,
    and runs each session in an isolated subprocess.

    Args:
        target: Optional code repository path (cwd for LLM). Defaults to workspace.
        project_dir: Optional project-level shared knowledge directory.
        agent_workspace: Agent workspace root for agent-context.md. Defaults to workspace.
    """
    from nezha.dag.engine import DAGEngine, DAGExecutionResult

    workspace.mkdir(parents=True, exist_ok=True)
    ensure_output_dir(agent_config, workspace)

    delay = agent_config.session.auto_continue_delay
    max_iter = max_iterations or agent_config.session.max_iterations

    # Resolve paths - use base_dir (project directory) if provided, otherwise fall back
    if base_dir:
        project_root = base_dir
    else:
        project_root = Path.cwd()
    executor_config_path = (project_root / "executor.yaml").resolve()
    agent_config_path = (project_root / "agents" / f"{agent_config.agent.name}.yaml").resolve()
    prompts_dir = Path(executor_config.prompts_dir)
    if not prompts_dir.is_absolute():
        prompts_dir = project_root / prompts_dir

    # Check for initializer vs worker prompts (supports both compose and single-template modes)
    init_prompt_path = agent_config.session.prompts.get("initializer")
    worker_prompt_path = agent_config.session.prompts.get("worker", "")
    worker_compose = agent_config.session.compose.get("worker") if agent_config.session.compose else None
    if not worker_prompt_path and not (worker_compose and worker_compose.base):
        raise ValueError(f"Agent {agent_config.agent.name}: no worker prompt configured")

    # Support both new name and legacy name
    task_list_path = workspace / "task_list.json"
    if not task_list_path.exists():
        legacy = workspace / "feature_list.json"
        if legacy.exists():
            task_list_path = legacy
    feature_list_path = task_list_path  # internal alias
    results = []

    # --- Task generation compatibility ---
    # If task_list.json (or feature_list.json) exists in input/ but not workspace root, copy it
    input_task_list = workspace / "input" / "task_list.json"
    input_feature_list = workspace / "input" / "feature_list.json"
    if not task_list_path.exists() and input_task_list.exists():
        import shutil
        shutil.copy2(input_task_list, task_list_path)
        print(f"[session] Copied task_list.json from input/ to workspace root")
    elif not task_list_path.exists() and input_feature_list.exists():
        import shutil
        shutil.copy2(input_feature_list, task_list_path)
        print(f"[session] Copied feature_list.json from input/ to workspace root (legacy)")

    # --- Pipeline: auto-invoke callable pre-agents for missing artifacts ---
    if not task_list_path.exists() and agent_config.pipeline.pre_agents:
        pre_results = _run_pre_agents(
            agent_config=agent_config,
            workspace=workspace,
            project_root=project_root,
            executor_config_path=executor_config_path,
            prompts_dir=prompts_dir,
            env=env,
            target=target,
            project_dir=project_dir,
            agent_workspace=agent_workspace,
        )
        results.extend(pre_results)

        # If pre-agents produced task_list.json, skip initializer
        if task_list_path.exists():
            print(f"[pipeline] task_list.json generated by pre-agent")

    # --- Self-planning mode: agent generates its own task_list.json ---
    self_plan_prompt = None
    if skip_planner and not task_list_path.exists() and init_prompt_path is None:
        # Look for built-in self-plan prompt
        builtin_self_plan = Path(__file__).parent.parent / "templates" / "prompts" / "coding" / "self-plan.md"
        if builtin_self_plan.exists():
            self_plan_prompt = str(builtin_self_plan)
            print(f"[session] skip_planner mode: using self-plan prompt")

    # If task_list.json doesn't exist in workspace root, run initializer first
    effective_init_prompt = init_prompt_path or self_plan_prompt
    if not task_list_path.exists() and effective_init_prompt is not None:
        print(f"\n{'=' * 60}")
        if self_plan_prompt:
            print(f"  SESSION 1: SELF-PLANNING")
        else:
            print(f"  SESSION 1: INITIALIZER")
        print(f"{'=' * 60}\n")

        init_result = _run_isolated_session(
            project_root=project_root,
            executor_config_path=executor_config_path,
            agent_config_path=agent_config_path,
            workspace=workspace,
            prompts_dir=prompts_dir,
            prompt_path=effective_init_prompt,
            env=env,
            target=target,
            project_dir=project_dir,
            agent_workspace=agent_workspace,
            timeout=agent_config.engine.session_timeout,
        )
        results.append(init_result)

        print(f"\n{'-' * 60}")
        print(f"  Initializer {init_result.status} | "
              f"turns={init_result.num_turns} | "
              f"cost=${init_result.cost_usd or 0:.4f}")
        print(f"{'-' * 60}")

        if on_event:
            await on_event(SessionEvent(
                event_type="result",
                data={
                    "status": init_result.status,
                    "num_turns": init_result.num_turns,
                    "cost_usd": init_result.cost_usd,
                    "duration_ms": init_result.duration_ms,
                },
            ))

        if init_result.status == "error":
            print(f"[session] Initializer failed: {init_result.error}")
            return results, None

        # If max_iterations was 1, that was the initializer
        if max_iter and max_iter <= 1:
            return results, None

        # Adjust remaining iterations (initializer counts as 1)
        if max_iter:
            max_iter -= 1

        time.sleep(delay)

    # --- Verify task_list.json exists ---
    if not task_list_path.exists():
        print(f"[session] task_list.json not found after initialization: {task_list_path}")
        print(f"[session] Place a task_list.json in the workspace or configure an initializer prompt")
        return results, None

    # --- Split task_list.json into per-agent files (if assigned_to fields present) ---
    from nezha.feature_queue import split_task_list as _split_task_list
    split_result = _split_task_list(workspace)
    if split_result:
        print(f"[session] Split task_list.json → {list(split_result.keys())}")

    # --- Resolve per-agent task_list path (per-agent file takes priority) ---
    agent_fl = workspace / f"task_list.{agent_config.agent.name}.json"
    # Legacy fallback
    if not agent_fl.exists():
        agent_fl_legacy = workspace / f"feature_list.{agent_config.agent.name}.json"
        if agent_fl_legacy.exists():
            agent_fl = agent_fl_legacy
    effective_task_list = agent_fl if agent_fl.exists() else task_list_path

    # --- Generate initial exec-plan.md (synced with task_list.json) ---
    from nezha.dag.graph import TaskDAG
    from nezha.dag.report import write_exec_plan
    _initial_dag = TaskDAG.load(effective_task_list)
    write_exec_plan(_initial_dag, workspace)
    print(f"[session] Generated exec-plan.md")

    # --- DAG-driven execution ---
    def _run_one_session(prompt_path: str, model_override: str = "",
                         env_override: dict[str, str] | None = None):
        """Run a single isolated session — called by DAGEngine."""
        # Merge model_map env overrides into session env
        merged_env = {**(env or {}), **(env_override or {})}
        result = _run_isolated_session(
            project_root=project_root,
            executor_config_path=executor_config_path,
            agent_config_path=agent_config_path,
            workspace=workspace,
            prompts_dir=prompts_dir,
            prompt_path=prompt_path,
            env=merged_env,
            target=target,
            project_dir=project_dir,
            agent_workspace=agent_workspace,
            model_override=model_override,
            timeout=agent_config.engine.session_timeout,
        )
        results.append(result)
        return result

    # Event callback for DAG events
    async def _on_dag_event(event_type: str, data: dict):
        if on_event:
            await on_event(SessionEvent(
                event_type=event_type,
                data=data,
            ))

    # Resolve integration prompt path (optional — enables post-DAG integration session)
    integration_prompt_path_raw = agent_config.session.prompts.get("integration")
    integration_prompt_path = None
    if integration_prompt_path_raw:
        integration_prompt_abs = (prompts_dir / integration_prompt_path_raw).resolve()
        if integration_prompt_abs.exists():
            integration_prompt_path = str(integration_prompt_abs)
        else:
            print(f"[session] WARNING: integration prompt not found: {integration_prompt_abs}")

    engine = DAGEngine(
        task_list_path=effective_task_list,
        workspace=workspace,
        run_session_fn=_run_one_session,
        delay=delay,
        on_dag_event=_on_dag_event,
        verification_command=agent_config.verification.command,
        max_cost_usd=agent_config.session.max_cost_usd,
        max_sessions=agent_config.session.max_sessions,
        integration_prompt_path=integration_prompt_path,
        model_map=agent_config.engine.model_map or executor_config.model_map,
    )

    dag_result = await engine.run(
        worker_prompt_path=worker_prompt_path,
        max_iterations=max_iter,
    )

    print(f"\n[session] DAG execution: {dag_result.exit_reason}")
    print(f"  Sessions: {dag_result.sessions_run}")
    print(f"  Completed: {dag_result.completed}/{dag_result.total_tasks}")
    if dag_result.rework_fixed:
        print(f"  Rework fixed: {dag_result.rework_fixed}")
    if dag_result.blocked:
        print(f"  Blocked: {dag_result.blocked}")
    if dag_result.skipped:
        print(f"  Skipped: {dag_result.skipped}")

    return results, dag_result


# ---------------------------------------------------------------------------
# VibeCoding: interactive REPL session
# ---------------------------------------------------------------------------

_VIBE_SUBPROCESS_RUNNER = '''
import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, "{project_root}")

from nezha.config import build_model_map_info, load_agent_config, load_executor_config
from nezha.dag.handoff import generate_all_context, generate_handoff_context
from nezha.engine import SessionEvent, SessionResult, build_options, run_session
from nezha.pipeline.io import build_input_context, ensure_output_dir, scan_input_files
from nezha.pipeline.knowledge import load_agent_context, load_knowledge, load_project_context
from nezha.pipeline.prompt_composer import compose_prompt
from nezha.pipeline.prompt_template import load_and_render, resolve_prompt_path
from nezha.pipeline.security import create_security_hook

async def main():
    executor_config = load_executor_config("{executor_config_path}")
    agent_config = load_agent_config("{agent_config_path}")
    workspace = Path("{workspace}")
    # cwd: where the LLM operates (code repo for coding agents, else workspace)
    cwd = Path("{cwd}")
    project_dir = Path("{project_dir}") if "{project_dir}" else None
    agent_workspace = Path("{agent_workspace}") if "{agent_workspace}" else workspace
    prompts_dir = Path("{prompts_dir}")

    workspace.mkdir(parents=True, exist_ok=True)
    ensure_output_dir(agent_config, workspace)

    # Generate handoff context based on context_mode
    context_mode = "{context_mode}"
    if context_mode == "none":
        handoff_context = ""
    elif context_mode == "all":
        handoff_context = generate_all_context(workspace, agent_config.agent.category)
    else:  # "latest" (default)
        handoff_context = generate_handoff_context(workspace)

    input_files = scan_input_files(agent_config, workspace)
    if input_files:
        print(f"[session] 注入 input 文件 ({{len(input_files)}}):")
        for _f in input_files:
            _sz = _f.stat().st_size if _f.exists() else 0
            print(f"  - {{_f.name}} ({{_sz}} bytes)")
    else:
        print("[session] 警告: 无 input 文件注入!")
    variables = dict(
        workspace=str(workspace),
        project_name=cwd.name,
        input_files=build_input_context(input_files, workspace),
        user_instruction="{user_instruction}",
        handoff_context=handoff_context,
        project_dir=str(project_dir) if project_dir else "",
        model_map_info=build_model_map_info(executor_config.model_map),
    )

    # Build prompt — compose mode (vibe key) or single template
    vibe_compose_config = agent_config.session.compose.get("vibe") if agent_config.session.compose else None
    if vibe_compose_config and vibe_compose_config.base:
        prompt = compose_prompt(vibe_compose_config, prompts_dir, locale=executor_config.locale or "en", variables=variables)
    else:
        template_path = resolve_prompt_path(prompts_dir, "{prompt_path}", locale=executor_config.locale or "en")
        prompt = load_and_render(template_path, variables)

    # Inject project knowledge from cwd (CLAUDE.md lives in the code repo)
    knowledge = load_knowledge(cwd)
    if knowledge:
        prompt = knowledge + "\\n\\n" + prompt
        print(f"[session] 注入 CLAUDE.md ({{len(knowledge)}} chars)")

    # Inject agent cross-task memory (agent-context.md from agent workspace root)
    agent_ctx = load_agent_context(agent_workspace)
    if agent_ctx:
        prompt = agent_ctx + "\\n\\n" + prompt
        print(f"[session] 注入 agent-context.md ({{len(agent_ctx)}} chars)")

    # Inject project-level context (project layer prioritized — placed before workspace knowledge)
    if project_dir:
        project_context = load_project_context(project_dir)
        if project_context:
            prompt = project_context + "\\n\\n" + prompt
            print(f"[session] 注入 project context ({{len(project_context)}} chars) ← {{project_dir}}")
        else:
            print(f"[session] 警告: project 目录为空或不存在: {{project_dir}}")

    allowed = agent_config.engine.security.get("allowed_commands")
    security_hook = create_security_hook(set(allowed) if allowed else None)
    merged_env = {{**executor_config.env, **agent_config.engine.env}}
    # Merge MCP servers: global (executor) < agent-level (agent wins)
    options = build_options(
        agent_config, cwd, security_hook, env=merged_env,
        extra_mcp_servers=executor_config.mcp_servers or {{}},
    )

    result = None
    async for event in run_session(prompt, options):
        if isinstance(event, SessionResult):
            result = event
        elif isinstance(event, SessionEvent):
            if event.event_type == "thinking":
                print(event.data.get("text", ""), end="", flush=True)
            elif event.event_type == "tool_call":
                tool_name = event.data.get("tool", "unknown")
                tool_input = event.data.get("input", {{}})
                # Show tool name and key parameters
                detail = ""
                if tool_name == "Read":
                    file_path = tool_input.get("file_path", "")
                    if file_path:
                        detail = f" → {{file_path}}"
                elif tool_name == "Edit":
                    file_path = tool_input.get("file_path", "")
                    if file_path:
                        detail = f" → {{file_path}}"
                elif tool_name == "Write":
                    file_path = tool_input.get("file_path", "")
                    if file_path:
                        detail = f" → {{file_path}}"
                elif tool_name == "Bash":
                    cmd = tool_input.get("command", "")
                    if cmd:
                        # Show first 60 chars of command
                        cmd_preview = cmd[:60] + "..." if len(cmd) > 60 else cmd
                        detail = f" → {{cmd_preview}}"
                elif tool_name == "Grep":
                    pattern = tool_input.get("pattern", "")
                    if pattern:
                        detail = f" → {{pattern}}"
                elif tool_name == "Glob":
                    pattern = tool_input.get("pattern", "")
                    if pattern:
                        detail = f" → {{pattern}}"
                print(f"\\n[Tool: {{tool_name}}]{{detail}}", flush=True)
            elif event.event_type == "tool_result":
                success = event.data.get("success", False)
                status = "成功" if success else "失败"
                # Show result summary for some tools
                result_preview = ""
                if success and "output" in event.data:
                    output = event.data["output"]
                    if isinstance(output, str):
                        lines = output.strip().split("\\n")
                        if len(lines) == 1 and len(lines[0]) <= 50:
                            result_preview = f" → {{lines[0]}}"
                        elif len(lines) > 1:
                            result_preview = f" → {{len(lines)}} lines"
                print(f"  [{{status}}]{{result_preview}}", flush=True)
            elif event.event_type == "rate_limited":
                print("\\n[rate_limited] API rate limit detected — signalling graceful stop", flush=True)
                result = SessionResult(status="rate_limited", error="API rate limit")
                break

    result_data = dict(
        status=result.status if result else "error",
        duration_ms=result.duration_ms if result else 0,
        num_turns=result.num_turns if result else 0,
        cost_usd=result.cost_usd if result else None,
        input_tokens=result.input_tokens if result else 0,
        output_tokens=result.output_tokens if result else 0,
        result_text=(result.result_text[:500] if result else ""),
        error=result.error if result else "No result",
    )
    result_file = Path("{workspace}") / ".session_result.json"
    result_file.write_text(json.dumps(result_data))

try:
    asyncio.run(main())
except (RuntimeError, SystemExit):
    # Suppress anyio cancel scope cleanup errors from claude-code-sdk.
    # The result is already written to .session_result.json before cleanup.
    pass
'''


def run_vibe_session(
    executor_config: ExecutorConfig,
    agent_config: AgentConfig,
    workspace: Path,
    user_instruction: str,
    env: dict[str, str] | None = None,
    target: Path | None = None,
    project_dir: Path | None = None,
    context_mode: str = "latest",
    agent_workspace: Path | None = None,
    base_dir: Path | None = None,
) -> SessionResult:
    """Run a single VibeCoding session in an isolated subprocess.

    Args:
        executor_config: Executor configuration
        agent_config: Agent configuration
        workspace: Metadata workspace path
        user_instruction: User's instruction for this vibe session
        env: Environment variables
        target: Optional code repository path (cwd for LLM). Defaults to workspace.
        project_dir: Optional project-level shared knowledge directory.
        context_mode: Handoff context loading mode — "all", "latest" (default), or "none".
        agent_workspace: Agent workspace root for agent-context.md. Defaults to workspace.
        base_dir: Project base directory (where executor.yaml is located).
    """
    cwd = target if target else workspace

    # Resolve paths - use base_dir (project directory) if provided
    if base_dir:
        project_root = base_dir
    else:
        project_root = Path.cwd()
    executor_config_path = (project_root / "executor.yaml").resolve()
    agent_config_path = (project_root / "agents" / f"{agent_config.agent.name}.yaml").resolve()
    prompts_dir = Path(executor_config.prompts_dir)
    if not prompts_dir.is_absolute():
        prompts_dir = project_root / prompts_dir

    # Use vibe prompt
    vibe_prompt_path = agent_config.session.prompts.get("vibe", "coding/vibe.md")

    # Escape user instruction for string embedding
    safe_instruction = user_instruction.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    script = _VIBE_SUBPROCESS_RUNNER.format(
        project_root=str(project_root),
        executor_config_path=str(executor_config_path),
        agent_config_path=str(agent_config_path),
        workspace=str(workspace),
        cwd=str(cwd),
        project_dir=str(project_dir) if project_dir else "",
        agent_workspace=str(agent_workspace) if agent_workspace else "",
        prompts_dir=str(prompts_dir),
        prompt_path=vibe_prompt_path,
        user_instruction=safe_instruction,
        context_mode=context_mode,
    )

    process_env = {**os.environ, **(env or {})}

    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        env=process_env,
        start_new_session=True,
    )
    vibe_timeout = agent_config.engine.session_timeout
    try:
        proc.wait(timeout=vibe_timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        return SessionResult(status="error", error=f"Vibe session timed out ({vibe_timeout}s)")
    finally:
        _kill_process_group(proc)

    result_file = workspace / ".session_result.json"
    if result_file.exists():
        try:
            with open(result_file) as f:
                data = json.load(f)
            result_file.unlink()
            return SessionResult(**data)
        except Exception:
            pass

    if proc.returncode == 0:
        return SessionResult(status="completed")
    return SessionResult(status="error", error=f"Process exited with code {proc.returncode}")
