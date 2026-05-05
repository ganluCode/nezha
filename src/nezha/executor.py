"""Main executor: wires config → scheduler → guards → events → engine → session."""

import asyncio
import os
import subprocess
from pathlib import Path

from nezha.config import (
    AgentConfig,
    ExecutorConfig,
    load_agent_config,
    load_executor_config,
    resolve_workspace,
)
from nezha.events.bus import EventBus
from nezha.events.file_logger import FileLoggerHandler
from nezha.events.state_writer import StateWriterHandler
from nezha.events.trace_writer import TraceWriterHandler
from nezha.events.types import Event, EventType
from nezha.guards import GuardFactory  # noqa: triggers self-registration
from nezha.guards.base import GuardChain
from nezha.i18n import t
from nezha.pipeline.session import run_multi_round, run_single_round, run_vibe_session
from nezha.scheduler import SchedulerFactory  # noqa: triggers self-registration
from nezha.feature_queue import FileFeatureQueue, FeatureStatus
from nezha.tools import create_tool


def _build_event_bus(executor_config: ExecutorConfig, base_dir: Path,
                     feature_id: str = "") -> EventBus:
    """Create EventBus and register handlers from executor config.

    When *feature_id* is provided (parallel execution), file-based handlers
    use per-feature paths to avoid concurrent write conflicts.
    """
    bus = EventBus()

    for handler_cfg in executor_config.event_handlers:
        if not handler_cfg.enabled:
            continue

        if handler_cfg.type == "file_logger":
            logs_dir = handler_cfg.params.get("path", "./state/logs/")
            path = Path(logs_dir)
            if not path.is_absolute():
                path = base_dir / path
            bus.register(FileLoggerHandler(path, feature_id=feature_id))

        elif handler_cfg.type == "state_writer":
            status_path = handler_cfg.params.get("path", "./state/executor_status.json")
            p = Path(status_path)
            if not p.is_absolute():
                p = base_dir / p
            state_dir = base_dir / executor_config.state_dir
            bus.register(StateWriterHandler(p, state_dir, feature_id=feature_id))

    # Always register trace writer for execution path tracking
    state_dir = base_dir / executor_config.state_dir
    bus.register(TraceWriterHandler(state_dir))

    return bus


def _resolve_target(
    agent_config: AgentConfig,
    executor_config: "ExecutorConfig",
    base_dir: Path,
) -> Path | None:
    """Resolve the coding agent's target (code repo) path.

    Priority: agent_config.target > executor_config.target > None.
    Returns None if no target is configured (design/planning agents).
    """
    # Only coding agents fall back to executor-level target.
    # Planning/design/management agents should NOT get a target unless
    # explicitly set in their own agent YAML.
    category = getattr(agent_config.agent, "category", "coding")
    if agent_config.target:
        raw_target = agent_config.target
    elif category == "coding":
        raw_target = getattr(executor_config, "target", None)
    else:
        raw_target = None
    if not raw_target:
        return None
    p = Path(raw_target)
    if p.is_absolute():
        return p
    return base_dir / p


def _resolve_target_scope(target: Path | None, scope: str | None) -> Path | None:
    """Resolve scoped working directory within target repo (monorepo support).

    Returns None if no scope is configured or target is missing.
    The scoped path is used as session cwd while git ops stay at target root.
    """
    if not target or not scope:
        return None
    scoped = target / scope
    if not scoped.is_dir():
        return None
    return scoped


def _build_git_env(extra_env: dict[str, str] | None = None) -> dict[str, str] | None:
    """Build environment for git subprocess calls.

    Merges os.environ with executor/agent env (GH_TOKEN, etc.).
    Returns None if no extra env — subprocess inherits parent env by default.
    """
    if not extra_env:
        return None
    env = {**os.environ, **extra_env}
    return env


def _check_coding_safety(target: Path, env: dict[str, str] | None = None) -> None:
    """Ensure target working tree is clean before switching tasks.

    Raises RuntimeError if there are uncommitted changes.
    """
    git_env = _build_git_env(env)
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=target,
        capture_output=True,
        text=True,
        env=git_env,
    )
    if result.stdout.strip():
        raise RuntimeError(
            t('executor.check.uncommitted',
              changes=result.stdout.strip(),
              target=target)
        )


def _git_commit(target: Path, task_id: str, env: dict[str, str] | None = None) -> None:
    """Stage all changes and commit with a task-based message."""
    git_env = _build_git_env(env)
    subprocess.run(["git", "add", "-A"], cwd=target, check=False, env=git_env)
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=target,
        capture_output=True,
        env=git_env,
    )
    if result.returncode == 0:
        print(t('executor.git.no_changes'))
        return
    msg = f"feat: task {task_id} completed by nezha"
    subprocess.run(["git", "commit", "-m", msg], cwd=target, check=False, env=git_env)
    print(t('executor.git.commit', msg=msg))


def _git_push(target: Path, branch: str | None, env: dict[str, str] | None = None) -> None:
    """Push current branch (or specified branch) to remote origin."""
    git_env = _build_git_env(env)
    cmd = ["git", "push", "origin"]
    if branch:
        cmd.append(branch)
    subprocess.run(cmd, cwd=target, check=False, env=git_env)
    branch_name = branch if branch else t('executor.git.current_branch')
    print(t('executor.git.push', branch=branch_name))


def _git_worktree_add(
    target: Path, worktree_path: Path, branch: str, base_branch: str,
    env: dict[str, str] | None = None,
) -> bool:
    """Create a git worktree with a new branch for task isolation.

    Returns True on success, False on failure.
    If the worktree and branch already exist (e.g. crashed task resumed),
    reuse them instead of failing.
    """
    git_env = _build_git_env(env)

    # If worktree already exists on disk, reuse it
    if worktree_path.exists():
        print(f"[executor] Reusing existing worktree: {worktree_path} (branch: {branch})")
        return True

    result = subprocess.run(
        ["git", "worktree", "add", str(worktree_path), "-b", branch, base_branch],
        cwd=target,
        capture_output=True,
        text=True,
        env=git_env,
    )
    if result.returncode != 0:
        # Branch exists but worktree was removed — re-attach using existing branch
        if "already exists" in result.stderr:
            result2 = subprocess.run(
                ["git", "worktree", "add", str(worktree_path), branch],
                cwd=target,
                capture_output=True,
                text=True,
                env=git_env,
            )
            if result2.returncode == 0:
                print(f"[executor] Re-attached worktree to existing branch: {worktree_path} ({branch})")
                return True
            print(f"[executor] git worktree re-attach failed: {result2.stderr.strip()}")
            return False
        print(f"[executor] git worktree add failed: {result.stderr.strip()}")
        return False
    print(f"[executor] Created worktree: {worktree_path} (branch: {branch} from {base_branch})")
    return True


def _git_worktree_remove(
    target: Path, worktree_path: Path,
    env: dict[str, str] | None = None,
) -> None:
    """Remove a git worktree after task completion.

    Ensures the branch ref is preserved in the main repo before removal.
    Downstream features may depend on this branch as their base_branch.
    """
    git_env = _build_git_env(env)

    # Before removing worktree, get the branch name and ensure it's preserved.
    # "git worktree remove" detaches HEAD but keeps the branch ref if there
    # are commits on it. However, if the branch has NO new commits (same as
    # base), git may prune it. We ensure the branch exists by checking it.
    branch_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        env=git_env,
    )
    branch_name = branch_result.stdout.strip() if branch_result.returncode == 0 else ""

    # Remove the worktree
    subprocess.run(
        ["git", "worktree", "remove", str(worktree_path), "--force"],
        cwd=target,
        capture_output=True,
        check=False,
        env=git_env,
    )

    # Ensure the branch ref still exists in main repo (even if no new commits).
    # If worktree removal pruned the branch, re-create it at the same commit.
    if branch_name and branch_name != "HEAD":
        check = subprocess.run(
            ["git", "rev-parse", "--verify", branch_name],
            cwd=target,
            capture_output=True,
            text=True,
            env=git_env,
        )
        if check.returncode != 0:
            # Branch was pruned — re-create from the worktree's last commit
            # (which is still in reflog or we can use the base branch)
            # Try to find the commit from worktree's HEAD reflog
            reflog = subprocess.run(
                ["git", "reflog", "show", branch_name, "-1", "--format=%H"],
                cwd=target,
                capture_output=True,
                text=True,
                env=git_env,
            )
            commit = reflog.stdout.strip() if reflog.returncode == 0 else ""
            if not commit:
                # Fallback: use the base branch (branch was created but had no commits)
                # This still preserves the branch name for downstream features
                commit = "HEAD"
            subprocess.run(
                ["git", "branch", branch_name, commit],
                cwd=target,
                capture_output=True,
                check=False,
                env=git_env,
            )
            print(f"[executor] Preserved branch ref: {branch_name}")

    print(f"[executor] Removed worktree: {worktree_path}")


def _find_callable_planner(base_dir: Path, executor_config) -> "AgentConfig | None":
    """Find a callable planner-agent to generate task_list.json.

    Looks for agents with:
      - category == "planning"
      - callable == True
    """
    from nezha.config import load_agent_config, AgentConfig

    agents_dir = base_dir / executor_config.agents_dir
    if not agents_dir.exists():
        return None

    for agent_file in agents_dir.glob("*.yaml"):
        try:
            cfg = load_agent_config(agent_file)
            if cfg.agent.category == "planning" and cfg.agent.callable:
                return cfg
        except Exception:
            continue

    return None


async def _run_planner_for_task_list(
    planner_config: "AgentConfig",
    executor_config,
    feature_workspace: Path,
    base_dir: Path,
    merged_env: dict[str, str] | None,
    project_dir: Path,
) -> str:
    """Run planner-agent to generate task_list.json for a coding agent.

    The planner reads the feature's input/ files and generates task_list.json
    in the feature workspace. Supports both direct API and single_round (Claude
    Code SDK) modes based on the planner's session.mode config.
    """
    print(f"[executor] Running planner: {planner_config.agent.name}")

    session_mode = planner_config.session.mode

    if session_mode == "direct":
        from nezha.pipeline.direct_api import run_direct_api
        result = await run_direct_api(
            executor_config=executor_config,
            agent_config=planner_config,
            workspace=feature_workspace,
            env=merged_env,
            target=None,  # planners don't need a code target
            project_dir=project_dir,
            agent_workspace=feature_workspace,
            mode=None,
        )
    else:
        # single_round: use Claude Code SDK subprocess
        from nezha.pipeline.session import run_single_round
        result = await run_single_round(
            executor_config=executor_config,
            agent_config=planner_config,
            workspace=feature_workspace,
            env=merged_env,
            target=None,
            project_dir=project_dir,
            agent_workspace=feature_workspace,
            base_dir=base_dir,
        )

    if result.status == "error":
        print(f"[executor] Planner failed: {result.error}")
        return "failure"

    # Verify task_list.json was created (or legacy feature_list.json)
    task_list_path = feature_workspace / "task_list.json"
    if not task_list_path.exists():
        legacy_path = feature_workspace / "feature_list.json"
        if legacy_path.exists():
            task_list_path = legacy_path
        else:
            print(f"[executor] Planner completed but no task_list.json generated")
            return "failure"

    # Validate & repair JSON
    if not _validate_and_repair_task_list(task_list_path):
        return "failure"
    return "success"


def _validate_and_repair_task_list(task_list_path: Path) -> bool:
    """Validate task_list.json is valid JSON; attempt repair if not.

    Returns True if the file is valid (possibly after repair), False otherwise.
    """
    import json
    from nezha.pipeline.direct_api import _try_fix_json

    raw = task_list_path.read_text(encoding="utf-8")

    # Try parsing as-is
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and len(parsed) > 0:
            return True
        print(f"[executor] task_list.json is valid JSON but not a non-empty array")
        return False
    except json.JSONDecodeError as e:
        print(f"[executor] task_list.json has invalid JSON: {e}")

    # Attempt repair
    try:
        fixed = _try_fix_json(raw)
        parsed = json.loads(fixed)
        if isinstance(parsed, list) and len(parsed) > 0:
            # Rewrite with properly formatted JSON
            task_list_path.write_text(
                json.dumps(parsed, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"[executor] task_list.json repaired successfully ({len(parsed)} tasks)")
            return True
        print(f"[executor] Repaired JSON is not a valid task list")
        return False
    except json.JSONDecodeError as e2:
        print(f"[executor] JSON repair failed: {e2}")
        return False


async def _ai_judge_continue(
    failed_feature_id: str,
    failed_error: str,
    next_feature_title: str,
    report_path: Path | None,
    env: dict[str, str],
    model: str = "claude-haiku-4-5-20251001",
    api_type: str = "anthropic",
) -> bool:
    """Use LLM to judge whether to continue executing the next feature.

    Called when failure_strategy is 'ai_judge' and a feature fails/partial.
    Supports both Anthropic and OpenAI-compatible APIs (GLM, Kimi, MiniMax, etc.).
    Returns True if the next feature can proceed independently.
    """
    # Build context about the failed feature
    report_summary = ""
    if report_path and report_path.exists():
        try:
            content = report_path.read_text(encoding="utf-8")
            report_summary = content[:800]
        except Exception:
            pass

    prompt = (
        f"You are a CI/CD arbiter. A feature just failed. Decide if the next feature "
        f"can be executed independently.\n\n"
        f"## Failed Feature\n"
        f"- ID: {failed_feature_id}\n"
        f"- Error: {failed_error}\n"
    )
    if report_summary:
        prompt += f"- Report:\n```\n{report_summary}\n```\n"
    prompt += (
        f"\n## Next Pending Feature\n"
        f"- Title: {next_feature_title}\n\n"
        f"## Question\n"
        f"Can the next feature be executed independently, without depending on "
        f"the failed parts of the previous feature?\n\n"
        f"Answer with EXACTLY one word: CONTINUE or STOP."
    )

    print(f"[ai_judge] Evaluating: '{failed_feature_id}' failed → "
          f"can '{next_feature_title}' proceed?")
    print(f"[ai_judge] Using model={model}, api_type={api_type}")

    try:
        if api_type == "openai":
            answer = await _judge_call_openai(prompt, model, env)
        else:
            answer = await _judge_call_anthropic(prompt, model, env)
        answer = answer.strip().upper()
        print(f"[ai_judge] LLM response: {answer}")
        return "CONTINUE" in answer
    except Exception as e:
        print(f"[ai_judge] LLM call failed: {e}, defaulting to STOP")
        return False


async def _judge_call_anthropic(prompt: str, model: str, env: dict) -> str:
    """Judge call via Anthropic SDK."""
    import asyncio
    from anthropic import Anthropic

    api_key = env.get("ANTHROPIC_API_KEY") or None
    base_url = env.get("ANTHROPIC_BASE_URL") or None
    client = Anthropic(
        api_key=api_key,
        **({"base_url": base_url} if base_url else {}),
    )

    def _sync_call():
        resp = client.messages.create(
            model=model,
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        return next(
            block.text for block in resp.content if hasattr(block, "text")
        )

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_call)


async def _judge_call_openai(prompt: str, model: str, env: dict) -> str:
    """Judge call via OpenAI-compatible SDK (GLM, Kimi, MiniMax, etc.)."""
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise ImportError(
            "openai package is required for judge_api_type='openai'. "
            "Install it with: pip install openai"
        )

    api_key = env.get("OPENAI_API_KEY") or "sk-placeholder"
    base_url = env.get("OPENAI_BASE_URL") or None

    client = AsyncOpenAI(
        api_key=api_key,
        **({"base_url": base_url} if base_url else {}),
    )

    resp = await client.chat.completions.create(
        model=model,
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""


async def execute_agent(
    agent_name: str,
    config_path: str = "executor.yaml",
    cli_workspace: str | None = None,
    max_iterations: int | None = None,
    feature_id: str | None = None,
    mode: str | None = None,
    skip_planner: bool = False,
):
    """Execute a named agent with scheduler, guard chain, and event bus.

    Args:
        agent_name: Name of the agent (matches agents/<name>.yaml)
        config_path: Path to executor.yaml
        cli_workspace: Optional workspace override from CLI
        max_iterations: Optional max iterations override
        feature_id: Optional specific feature ID to run (feature queue mode only)
        mode: Optional execution mode (e.g. "gardening") — selects an
              alternate prompt from agent_config.session.prompts
        skip_planner: If True, skip auto-planner even when task_list.json
                      is missing. Let the coding agent figure out the plan.
    """
    # Load configs
    base_dir = Path(config_path).parent.resolve()
    executor_config = load_executor_config(config_path)

    agent_config_path = Path(executor_config.agents_dir) / f"{agent_name}.yaml"
    if not agent_config_path.is_absolute():
        agent_config_path = base_dir / agent_config_path

    if not agent_config_path.exists():
        available = list((base_dir / executor_config.agents_dir).glob("*.yaml"))
        names = [f.stem for f in available]
        raise FileNotFoundError(
            f"Agent config not found: {agent_config_path}\n"
            f"Available agents: {names}"
        )

    agent_config = load_agent_config(agent_config_path)

    # Resolve workspace (per-agent metadata directory)
    workspace = resolve_workspace(
        executor_config, agent_config,
        cli_workspace=cli_workspace,
        base_dir=base_dir,
    )

    # Resolve workspace_base (project root for shared tasks/ directory)
    ws_base_raw = Path(executor_config.workspace.base)
    workspace_base = (ws_base_raw if ws_base_raw.is_absolute() else base_dir / ws_base_raw).resolve()

    # Resolve target (code repo, coding agents only)
    target = _resolve_target(agent_config, executor_config, base_dir)
    # Resolve target_scope (monorepo: subdirectory within target)
    target_scope = _resolve_target_scope(target, agent_config.target_scope)

    # Resolve project_dir (shared project-level knowledge directory)
    project_dir = (workspace_base / "project").resolve()

    # Merge env: executor global < agent-level (agent overrides executor)
    # Resolve ${VAR} refs in agent env using executor env as base
    from nezha.config import resolve_env_refs
    agent_env = resolve_env_refs(agent_config.engine.env, executor_config.env)
    merged_env = {**executor_config.env, **agent_env}

    print(t('executor.info.agent', name=agent_config.agent.name))
    print(t('executor.info.model', model=agent_config.engine.model))
    print(t('executor.info.session_mode', mode=agent_config.session.mode))
    print(t('executor.info.scheduler', mode=executor_config.scheduler.mode))
    print(t('executor.info.workspace', path=workspace))
    if target:
        print(t('executor.info.target', path=target))
    if target_scope:
        print(t('executor.info.target_scope', path=target_scope))
    if merged_env:
        safe_keys = list(merged_env.keys())
        print(t('executor.info.env_vars', keys=', '.join(safe_keys)))

    # Build guard chain
    guard_chain = GuardFactory.create_chain(executor_config.guards)
    enabled_guards = [g.guard_type for g in guard_chain.guards if g.enabled]
    if enabled_guards:
        print(t('executor.info.guards', guards=', '.join(enabled_guards)))

    # Build event bus
    event_bus = _build_event_bus(executor_config, base_dir,
                                 feature_id=feature_id or "")
    print(t('executor.info.event_handlers', count=len(event_bus._handlers)))
    print()

    # Determine whether to use feature queue mode
    # Feature Queue mode: features/ directory exists under workspace_base
    # Also check legacy tasks/ directory for backward compatibility
    features_dir = workspace_base / "features"
    tasks_dir_legacy = workspace_base / "tasks"
    use_task_queue = features_dir.exists() or tasks_dir_legacy.exists()

    # Emit executor started
    executor_id = executor_config.executor.name
    await event_bus.emit(Event.create(
        EventType.EXECUTOR_STARTED,
        agent_name=agent_name,
        executor_id=executor_id,
    ))

    # Event callback for session events
    session_counter = [0]

    async def _on_session_event(session_event):
        """Forward session events to the event bus."""
        from nezha.engine import SessionEvent
        if isinstance(session_event, SessionEvent):
            et_map = {
                "thinking": EventType.AGENT_THINKING,
                "tool_call": EventType.AGENT_TOOL_CALL,
                "tool_result": EventType.AGENT_TOOL_RESULT,
            }
            event_type = et_map.get(session_event.event_type)
            if event_type:
                await event_bus.emit(Event.create(
                    event_type,
                    agent_name=agent_name,
                    session_id=session_counter[0],
                    executor_id=executor_id,
                    **session_event.data,
                ))

    # Define the execution function (one cycle of agent work)
    # Returns: "success" | "failure" | "no_task"
    async def _execute_once() -> str:
        # ----------------------------------------------------------------
        # Feature Queue mode: pick a feature, track lifecycle, git operations
        # ----------------------------------------------------------------
        task = None
        feature_workspace = workspace  # default (backward compat)
        effective_target = target   # may be replaced by worktree path
        worktree_path: Path | None = None
        # session_target: used as cwd for session/test (scoped for monorepo)
        session_target = target_scope or target

        if use_task_queue:
            queue = FileFeatureQueue(workspace_base)

            if feature_id:
                task = queue.get(feature_id)
                if task is None:
                    print(t('executor.feature.not_found', id=feature_id))
                    return "no_task"
                if task.status not in (FeatureStatus.PENDING, FeatureStatus.PAUSED):
                    print(t('executor.feature.not_runnable', id=feature_id, status=task.status.value))
                    return "no_task"
            else:
                # Planning/design/management agents work on ANY pending feature
                # (they don't require task_list.<agent>.json to exist).
                # Coding agents only pick features with their task_list file.
                filter_agent = agent_name if agent_config.agent.category == "coding" else None
                if agent_config.agent.category == "planning":
                    # Planning agents skip features that already have task_list.json
                    # (those have already been planned and are waiting for coding agent)
                    pending = queue.list_features(status=FeatureStatus.PENDING)
                    pending = sorted(pending, key=lambda f: (-f.priority, f.created_at))
                    task = None
                    for candidate in pending:
                        ws = queue.feature_workspace(candidate.id)
                        if not (ws / "task_list.json").exists():
                            task = candidate
                            break
                else:
                    task = queue.get_next(filter_agent)
                    # Fallback: coding agent with auto-planner can pick
                    # pending features without task_list (planner runs later).
                    # Still respects priority + creation time ordering.
                    if (
                        task is None
                        and filter_agent
                        and agent_config.session.mode == "multi_round"
                    ):
                        task = queue.get_next(None)
                if task is None:
                    print(t('executor.feature.no_pending', agent=agent_name))
                    print(t('executor.feature.create_hint', agent=agent_name))
                    return "no_task"

            feature_workspace = queue.feature_workspace(task.id)
            print(t('executor.feature.running', id=task.id, status=task.status.value))
            print(t('executor.feature.workspace', path=feature_workspace))

            # Coding agent safety check + branch creation
            if target and agent_config.git.branch_per_task:
                # Prefer branch bound at feature-create time; fall back to agent config prefix
                branch = task.metadata.get("branch") or f"{agent_config.git.branch_prefix}{task.id}"

                # Use feature-level base_branch if set (chain branches); fall back to agent config
                base_branch = task.metadata.get("base_branch") or agent_config.git.base_branch
                if agent_config.git.use_worktree:
                    # Worktree mode: isolated checkout, no safety check needed
                    worktree_path = target.parent / f"{target.name}-{task.id}"
                    if not _git_worktree_add(target, worktree_path, branch, base_branch, env=merged_env):
                        return "failure"
                    effective_target = worktree_path
                    # Re-resolve scope relative to worktree
                    session_target = _resolve_target_scope(
                        worktree_path, agent_config.target_scope,
                    ) or worktree_path
                else:
                    # Classic mode: safety check + branch checkout
                    try:
                        _check_coding_safety(target, env=merged_env)
                    except RuntimeError as e:
                        print(t('executor.feature.safety_failed', error=e))
                        return "failure"

                    result = subprocess.run(
                        ["git", "checkout", "-b", branch, base_branch],
                        cwd=target,
                        capture_output=True,
                        text=True,
                        env=_build_git_env(merged_env),
                    )
                    if result.returncode != 0:
                        print(t('executor.feature.branch_failed',
                                branch=branch, error=result.stderr.strip()))
                        return "failure"
                    print(t('executor.feature.branch_created', branch=branch))

                metadata: dict = {
                    "branch": branch,
                    "base_branch": base_branch,
                }
                if worktree_path:
                    metadata["worktree_path"] = str(worktree_path)
                queue.update_metadata(task.id, metadata)

            queue.update_status(task.id, FeatureStatus.RUNNING)

        # Pre-check guards
        guard_result = await guard_chain.check_all()
        if not guard_result.passed:
            print(t('executor.guard.blocked', reason=guard_result.reason))
            await event_bus.emit(Event.create(
                EventType.GUARD_BLOCKED,
                agent_name=agent_name,
                executor_id=executor_id,
                reason=guard_result.reason,
            ))
            await guard_chain.notify_failure(guard_result.reason)
            if task and use_task_queue:
                queue = FileFeatureQueue(workspace)
                queue.update_status(task.id, FeatureStatus.FAILED, error=guard_result.reason)
            return "failure"

        await event_bus.emit(Event.create(
            EventType.GUARD_PASSED,
            agent_name=agent_name,
            executor_id=executor_id,
        ))

        # ----------------------------------------------------------------
        # Step-based execution: feature with steps → execute one step per cycle
        # ----------------------------------------------------------------
        if task and task.steps:
            from nezha.feature_queue import (
                STEP_RUNNING, STEP_COMPLETED, STEP_NEEDS_REVIEW, STEP_PENDING,
            )

            current_step = queue.get_next_ready_step(task.id)

            if current_step is None:
                reviews = queue.needs_review(task.id)
                if reviews:
                    step_ids = [s.id for s in reviews]
                    print(f"[steps] Feature {task.id}: waiting for review — {step_ids}")
                    print(f"[steps] Approve: nezha feature approve {task.id} <step-id>")
                    return "success"
                if queue.all_steps_done(task.id):
                    queue.update_status(task.id, FeatureStatus.COMPLETED)
                    print(f"[steps] Feature {task.id}: all steps completed")
                    return "success"
                print(f"[steps] Feature {task.id}: no ready steps (blocked by dependencies)")
                return "no_task"

            print(f"\n[steps] Step: {current_step.id} → agent: {current_step.agent}")
            queue.update_step_status(task.id, current_step.id, STEP_RUNNING)

            # Resolve step's agent config
            step_agent_config_path = Path(executor_config.agents_dir) / f"{current_step.agent}.yaml"
            if not step_agent_config_path.is_absolute():
                step_agent_config_path = base_dir / step_agent_config_path

            if not step_agent_config_path.exists():
                print(f"[steps] ERROR: Agent config not found: {step_agent_config_path}")
                queue.update_step_status(task.id, current_step.id, STEP_PENDING)
                return "failure"

            step_config = load_agent_config(step_agent_config_path)

            # Resolve prompts dir
            prompts_dir = Path(executor_config.prompts_dir)
            if not prompts_dir.is_absolute():
                prompts_dir = base_dir / prompts_dir

            step_success = False
            step_error = ""

            try:
                if step_config.session.mode == "multi_round":
                    step_results, _step_dag = await run_multi_round(
                        executor_config, step_config, feature_workspace,
                        max_iterations=max_iterations,
                        on_event=_on_session_event,
                        env=merged_env,
                        target=session_target,
                        project_dir=project_dir,
                        agent_workspace=workspace,
                        base_dir=base_dir,
                    )
                    if step_results and step_results[-1].status != "error":
                        step_success = True
                    if step_results and step_results[-1].status == "error":
                        step_error = step_results[-1].error or ""
                else:
                    step_result = await run_single_round(
                        executor_config, step_config, feature_workspace,
                        on_event=_on_session_event,
                        env=merged_env,
                        target=session_target,
                        project_dir=project_dir,
                        agent_workspace=workspace,
                        base_dir=base_dir,
                    )
                    step_success = step_result.status != "error"
                    if not step_success:
                        step_error = step_result.error or ""
            except Exception as e:
                step_error = str(e)
                print(f"[steps] Step {current_step.id} error: {e}")

            # Update step status based on result
            if step_success:
                if current_step.review_gate:
                    queue.update_step_status(task.id, current_step.id, STEP_NEEDS_REVIEW)
                    print(f"[steps] Step {current_step.id}: completed → waiting for review")
                    print(f"[steps] Approve: nezha feature approve {task.id} {current_step.id}")
                else:
                    queue.update_step_status(task.id, current_step.id, STEP_COMPLETED)
                    print(f"[steps] Step {current_step.id}: completed")

                # Check if all steps are now done
                if queue.all_steps_done(task.id):
                    queue.update_status(task.id, FeatureStatus.COMPLETED)
                    print(f"[steps] Feature {task.id}: all steps completed")
            else:
                queue.update_step_status(task.id, current_step.id, STEP_PENDING, note=step_error)
                queue.update_status(task.id, FeatureStatus.FAILED, error=f"Step {current_step.id}: {step_error}")
                print(f"[steps] Step {current_step.id} failed: {step_error}")

            return "success" if step_success else "failure"

        # ----------------------------------------------------------------
        # Auto-planner: if coding agent needs task_list but it's missing,
        # automatically invoke a callable planner-agent to generate it.
        # ----------------------------------------------------------------
        task_list_path = feature_workspace / "task_list.json"
        task_list_legacy = feature_workspace / "feature_list.json"
        needs_task_list = (
            agent_config.agent.category == "coding"
            and agent_config.session.mode == "multi_round"
            and not task_list_path.exists()
            and not task_list_legacy.exists()
            and not mode  # not in special mode like gardening
            and not skip_planner  # user explicitly wants to skip planner
        )

        if needs_task_list:
            print(t('executor.auto_planner.looking'))
            planner_config = _find_callable_planner(base_dir, executor_config)
            if planner_config:
                print(t('executor.auto_planner.found', name=planner_config.agent.name))
                planner_result = await _run_planner_for_task_list(
                    planner_config=planner_config,
                    executor_config=executor_config,
                    feature_workspace=feature_workspace,
                    base_dir=base_dir,
                    merged_env=merged_env,
                    project_dir=project_dir,
                )
                if planner_result == "success":
                    print(t('executor.auto_planner.success'))
                else:
                    print(t('executor.auto_planner.failed'))
            else:
                print(t('executor.auto_planner.not_found'))

        session_success = False
        last_error = ""
        dag_result = None

        try:
            # Determine session mode:
            #   "direct"       → run_direct_api() (no Claude Code SDK subprocess)
            #   "multi_round"  → run_multi_round() via DAG (default for coding agents)
            #   "single_round" → run_single_round() via subprocess
            # Non-default --mode flag (e.g. gardening) always forces single_round.
            if agent_config.session.mode == "direct":
                session_mode = "direct"
            elif agent_config.session.mode == "multi_round" and not mode:
                session_mode = "multi_round"
            else:
                session_mode = "single_round"

            if session_mode == "direct":
                from nezha.pipeline.direct_api import run_direct_api
                session_counter[0] += 1
                await event_bus.emit(Event.create(
                    EventType.SESSION_STARTED,
                    agent_name=agent_name,
                    session_id=session_counter[0],
                    executor_id=executor_id,
                    mode="direct",
                ))

                result = await run_direct_api(
                    executor_config, agent_config, feature_workspace,
                    env=merged_env,
                    target=session_target,
                    project_dir=project_dir,
                    agent_workspace=workspace,
                    mode=mode,
                )
                print(t('executor.session.result', status=result.status))
                if result.duration_ms:
                    print(f"  Duration: {result.duration_ms}ms")

                if result.status == "error":
                    last_error = result.error
                    await event_bus.emit(Event.create(
                        EventType.SESSION_ERROR,
                        agent_name=agent_name,
                        session_id=session_counter[0],
                        executor_id=executor_id,
                        error=result.error,
                    ))
                    await guard_chain.notify_failure(result.error)
                else:
                    session_success = True
                    await event_bus.emit(Event.create(
                        EventType.SESSION_COMPLETED,
                        agent_name=agent_name,
                        session_id=session_counter[0],
                        executor_id=executor_id,
                        status=result.status,
                        num_turns=result.num_turns,
                        cost_usd=result.cost_usd,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        duration_ms=result.duration_ms,
                    ))
                    await guard_chain.notify_success(cost_usd=result.cost_usd)

            elif session_mode == "multi_round":
                session_counter[0] += 1
                await event_bus.emit(Event.create(
                    EventType.SESSION_STARTED,
                    agent_name=agent_name,
                    session_id=session_counter[0],
                    executor_id=executor_id,
                    mode="multi_round",
                ))

                results, dag_result = await run_multi_round(
                    executor_config, agent_config, feature_workspace,
                    max_iterations=max_iterations,
                    on_event=_on_session_event,
                    env=merged_env,
                    target=session_target,
                    project_dir=project_dir,
                    agent_workspace=workspace,
                    base_dir=base_dir,
                    skip_planner=skip_planner,
                )
                print(t('executor.session.multi_done', count=len(results)))
                for i, r in enumerate(results, 1):
                    cost_str = f"${r.cost_usd or 0:.4f}"
                    print(t('executor.session.session_detail',
                            n=i, status=r.status, turns=r.num_turns, cost=cost_str))

                last = results[-1] if results else None
                if last and last.status == "error":
                    last_error = last.error
                    await event_bus.emit(Event.create(
                        EventType.SESSION_ERROR,
                        agent_name=agent_name,
                        session_id=session_counter[0],
                        executor_id=executor_id,
                        error=last.error,
                    ))
                    await guard_chain.notify_failure(last.error)
                else:
                    session_success = True
                    await event_bus.emit(Event.create(
                        EventType.SESSION_COMPLETED,
                        agent_name=agent_name,
                        session_id=session_counter[0],
                        executor_id=executor_id,
                        status=last.status if last else "unknown",
                        num_turns=last.num_turns if last else 0,
                        cost_usd=last.cost_usd if last else 0,
                        input_tokens=last.input_tokens if last else 0,
                        output_tokens=last.output_tokens if last else 0,
                        duration_ms=last.duration_ms if last else 0,
                    ))
                    await guard_chain.notify_success(
                        cost_usd=last.cost_usd if last else 0,
                    )

            # ----------------------------------------------------------------
            # Post-task integration test cycle (after DAG completes all features)
            # ----------------------------------------------------------------
            ptt = agent_config.pipeline.post_task_test
            if session_success and session_mode == "multi_round" and ptt.enabled and ptt.command:
                from nezha.dag.graph import TaskDAG
                from nezha.pipeline.session import _run_isolated_session
                from nezha.testing.integration import (
                    run_test_command, write_test_report, CycleResult,
                )

                _tl = feature_workspace / "task_list.json"
                if not _tl.exists():
                    _tl = feature_workspace / "feature_list.json"
                dag = TaskDAG.load(_tl)

                if dag.is_all_done():
                    test_cwd = session_target if session_target else feature_workspace

                    # Resolve paths for fix session
                    prompts_dir = Path(executor_config.prompts_dir)
                    if not prompts_dir.is_absolute():
                        prompts_dir = base_dir / prompts_dir

                    previous_fixes: list[dict] = []
                    cycle_result = CycleResult()

                    for cycle in range(ptt.max_cycles):
                        cycle_result.cycles_run = cycle + 1

                        print(f"\n{'=' * 60}")
                        print(t('executor.integration_test.cycle_start',
                                cycle=cycle + 1, max_cycles=ptt.max_cycles))
                        print(t('executor.integration_test.command', command=ptt.command))
                        print(f"{'=' * 60}")

                        await event_bus.emit(Event.create(
                            EventType.INTEGRATION_TEST_STARTED,
                            agent_name=agent_name,
                            executor_id=executor_id,
                            cycle=cycle + 1,
                            max_cycles=ptt.max_cycles,
                        ))

                        test_result = run_test_command(ptt.command, test_cwd, ptt.timeout)

                        if test_result.passed:
                            cycle_result.passed = True
                            cycle_result.exit_reason = "tests_passed"
                            print(t('executor.integration_test.passed', cycle=cycle + 1))
                            await event_bus.emit(Event.create(
                                EventType.INTEGRATION_TEST_PASSED,
                                agent_name=agent_name,
                                executor_id=executor_id,
                                cycle=cycle + 1,
                            ))
                            break

                        # Test failed
                        print(t('executor.integration_test.failed', cycle=cycle + 1))
                        await event_bus.emit(Event.create(
                            EventType.INTEGRATION_TEST_FAILED,
                            agent_name=agent_name,
                            executor_id=executor_id,
                            cycle=cycle + 1,
                            exit_code=test_result.exit_code,
                        ))

                        write_test_report(
                            feature_workspace, cycle + 1, ptt.max_cycles,
                            ptt.command, test_result, previous_fixes,
                        )

                        # Last cycle — no fix session
                        if cycle + 1 >= ptt.max_cycles:
                            cycle_result.exit_reason = "max_cycles"
                            print(t('executor.integration_test.max_cycles', max=ptt.max_cycles))
                            break

                        # Run fix session
                        print(t('executor.integration_test.fix_start'))
                        fix_result = _run_isolated_session(
                            project_root=base_dir,
                            executor_config_path=Path(config_path).resolve(),
                            agent_config_path=agent_config_path,
                            workspace=feature_workspace,
                            prompts_dir=prompts_dir,
                            prompt_path="coding/fix.md",
                            env=merged_env,
                            target=session_target,
                            project_dir=project_dir,
                            agent_workspace=workspace,
                        )
                        cycle_result.total_cost_usd += fix_result.cost_usd or 0

                        previous_fixes.append({
                            "cycle": cycle + 1,
                            "error_summary": test_result.output[:200],
                            "fix_applied": f"session {fix_result.status}, cost=${fix_result.cost_usd or 0:.4f}",
                        })
                        print(t('executor.integration_test.fix_done',
                                status=fix_result.status, cost=f"${fix_result.cost_usd or 0:.4f}"))

                        if fix_result.status == "error":
                            cycle_result.exit_reason = "fix_error"
                            break

                        # Auto-commit fix
                        if effective_target and agent_config.git.auto_commit:
                            try:
                                _git_commit(effective_target, f"{task.id if task else 'fix'}-integration-fix-{cycle + 1}", env=merged_env)
                            except Exception:
                                pass

                    # Update session_success
                    if not cycle_result.passed:
                        session_success = False
                        last_error = (
                            f"Integration tests failed after {cycle_result.cycles_run} "
                            f"cycle(s) ({cycle_result.exit_reason})"
                        )
                        print(t('executor.integration_test.final_fail',
                                cycles=cycle_result.cycles_run, reason=cycle_result.exit_reason))
                    else:
                        print(t('executor.integration_test.final_pass',
                                cycles=cycle_result.cycles_run))

            else:
                session_counter[0] += 1
                await event_bus.emit(Event.create(
                    EventType.SESSION_STARTED,
                    agent_name=agent_name,
                    session_id=session_counter[0],
                    executor_id=executor_id,
                    mode=mode or "single_round",
                ))

                result = await run_single_round(
                    executor_config, agent_config, feature_workspace,
                    on_event=_on_session_event,
                    env=merged_env,
                    target=session_target,
                    project_dir=project_dir,
                    agent_workspace=workspace,
                    mode=mode,
                    base_dir=base_dir,
                )
                print(t('executor.session.result', status=result.status))
                if result.cost_usd:
                    print(t('executor.session.cost', cost=f"${result.cost_usd:.4f}"))

                if result.status == "error":
                    last_error = result.error
                    await event_bus.emit(Event.create(
                        EventType.SESSION_ERROR,
                        agent_name=agent_name,
                        session_id=session_counter[0],
                        executor_id=executor_id,
                        error=result.error,
                    ))
                    await guard_chain.notify_failure(result.error)
                else:
                    session_success = True
                    await event_bus.emit(Event.create(
                        EventType.SESSION_COMPLETED,
                        agent_name=agent_name,
                        session_id=session_counter[0],
                        executor_id=executor_id,
                        status=result.status,
                        num_turns=result.num_turns,
                        cost_usd=result.cost_usd,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        duration_ms=result.duration_ms,
                    ))
                    await guard_chain.notify_success(cost_usd=result.cost_usd)

        except Exception as e:
            last_error = str(e)
            print(t('executor.session.error', error=e))
            await event_bus.emit(Event.create(
                EventType.SESSION_ERROR,
                agent_name=agent_name,
                executor_id=executor_id,
                error=str(e),
            ))
            await guard_chain.notify_failure(str(e))

        # ----------------------------------------------------------------
        # Post-session: post_tools → git operations → task status update
        # ----------------------------------------------------------------

        # Run post_tools (always, regardless of feature queue mode)
        if session_success and agent_config.pipeline.post_tools:
            tool_cwd = session_target if session_target else feature_workspace
            for pt_cfg in agent_config.pipeline.post_tools:
                print(t('executor.post_tool.running', name=pt_cfg.name, action=pt_cfg.action))
                try:
                    tool = create_tool(pt_cfg.name)
                    result = tool.run(pt_cfg.action, tool_cwd, pt_cfg.params)
                    if result.success:
                        if result.output:
                            print(t('executor.post_tool.output',
                                    name=pt_cfg.name, output=result.output[:200]))
                    else:
                        print(t('executor.post_tool.failed',
                                name=pt_cfg.name, error=result.error))
                except Exception as e:
                    print(t('executor.post_tool.error', name=pt_cfg.name, error=e))

        if task and use_task_queue:
            queue = FileFeatureQueue(workspace_base)

            if session_success:
                # Auto-commit if configured
                if effective_target and agent_config.git.auto_commit:
                    try:
                        _git_commit(effective_target, task.id, env=merged_env)
                    except Exception as e:
                        print(t('executor.git.commit_failed', error=e))

                # Auto-push if configured
                if agent_config.git.auto_push:
                    try:
                        branch = task.metadata.get("branch")
                        # Push from main target repo (worktree branch lives in main repo)
                        push_target = target if target else effective_target
                        _git_push(push_target, branch, env=merged_env)
                    except Exception as e:
                        print(t('executor.git.push_failed', error=e))

                # Remove worktree if it was used
                if worktree_path and target:
                    try:
                        _git_worktree_remove(target, worktree_path, env=merged_env)
                    except Exception as e:
                        print(f"[executor] worktree cleanup failed: {e}")

                # Planner agents prepare task_list.json but don't complete the feature
                # Reset to pending so coding agent can run afterwards
                if agent_config.agent.category == "planning":
                    queue.update_status(task.id, FeatureStatus.PENDING)
                    print(t('executor.feature.planner_done', id=task.id))
                elif dag_result and dag_result.exit_reason not in ("all_done", ""):
                    # DAG did not finish all tasks (deadlocked / stuck / limit hit)
                    detail = (
                        f"{dag_result.exit_reason}: "
                        f"{dag_result.completed}/{dag_result.total_tasks} tasks done"
                    )
                    if dag_result.skipped:
                        detail += f", {dag_result.skipped} skipped"
                    if dag_result.blocked:
                        detail += f", {dag_result.blocked} blocked"
                    queue.update_status(task.id, FeatureStatus.PARTIAL, error=detail)
                    print(t('executor.feature.partial', id=task.id, detail=detail))
                else:
                    queue.update_status(task.id, FeatureStatus.COMPLETED)
                    print(t('executor.feature.completed', id=task.id))
            else:
                # Remove worktree even on failure to avoid stale worktrees
                if worktree_path and target:
                    try:
                        _git_worktree_remove(target, worktree_path, env=merged_env)
                    except Exception as e:
                        print(f"[executor] worktree cleanup failed: {e}")

                queue.update_status(task.id, FeatureStatus.FAILED, error=last_error)
                print(t('executor.feature.failed', id=task.id, error=last_error or "unknown"))

        # Determine outcome: partial (DAG deadlocked/stuck) counts as failure
        if not session_success:
            outcome = "failure"
        elif dag_result and dag_result.exit_reason not in ("all_done", ""):
            outcome = "failure"
        else:
            outcome = "success"

        # Track last failure info for AI judge callback
        if outcome == "failure" and task:
            _last_failure["feature_id"] = task.id
            _last_failure["error"] = last_error or (
                task.error if hasattr(task, "error") else "unknown"
            )
            _last_failure["report_path"] = feature_workspace / "execution-report.md"

        print(f"[executor] _execute_once → {outcome}")
        return outcome

    # State for AI judge callback (mutable dict captured by closure)
    _last_failure: dict = {}

    async def _on_failure_judge() -> bool:
        """AI judge callback: decide whether to continue after a failure."""
        queue = FileFeatureQueue(workspace_base)
        filter_agent = agent_name if agent_config.agent.category == "coding" else None
        if agent_config.agent.category == "planning":
            pending = queue.list_features(status=FeatureStatus.PENDING)
            pending = sorted(pending, key=lambda f: (-f.priority, f.created_at))
            next_feature = None
            for candidate in pending:
                ws = queue.feature_workspace(candidate.id)
                if not (ws / "task_list.json").exists():
                    next_feature = candidate
                    break
        else:
            next_feature = queue.get_next(filter_agent)

        if not next_feature:
            return False  # No next feature, stop anyway

        # Merge judge-specific env over merged_env
        judge_env = {**merged_env, **executor_config.scheduler.judge_env}
        return await _ai_judge_continue(
            failed_feature_id=_last_failure.get("feature_id", "unknown"),
            failed_error=_last_failure.get("error", "unknown"),
            next_feature_title=next_feature.title or next_feature.id,
            report_path=_last_failure.get("report_path"),
            env=judge_env,
            model=executor_config.scheduler.judge_model,
            api_type=executor_config.scheduler.judge_api_type,
        )

    # Create scheduler and run
    concurrency = executor_config.scheduler.concurrency
    state_dir = base_dir / executor_config.state_dir
    scheduler = SchedulerFactory.create(executor_config.scheduler, state_dir=state_dir)

    if concurrency > 1 and not feature_id:
        # Parallel mode: run up to N features concurrently
        _sem = asyncio.Semaphore(concurrency)

        async def _execute_parallel() -> str:
            """Run up to `concurrency` pending features in parallel."""
            queue = FileFeatureQueue(workspace_base)
            _filter = agent_name if agent_config.agent.category == "coding" else None
            pending = queue.list_features(agent_name=_filter, status=FeatureStatus.PENDING)
            if not pending:
                return await _execute_once()  # falls through to "no_task"

            # Take up to `concurrency` features
            batch = sorted(pending, key=lambda f: (-f.priority, f.created_at))[:concurrency]
            if len(batch) == 1:
                return await _execute_once()

            print(f"[parallel] Running {len(batch)} features concurrently (max={concurrency})")

            async def _run_one(fid: str) -> str:
                async with _sem:
                    # Re-use the existing execute_agent for each feature
                    try:
                        await execute_agent(
                            agent_name, config_path, cli_workspace,
                            max_iterations, feature_id=fid,
                            mode=mode, skip_planner=skip_planner,
                        )
                        return "success"
                    except Exception as e:
                        print(f"[parallel] Feature {fid} error: {e}")
                        return "failure"

            results = await asyncio.gather(
                *[_run_one(f.id) for f in batch],
                return_exceptions=True,
            )

            # Return worst outcome
            str_results = [r if isinstance(r, str) else "failure" for r in results]
            if "failure" in str_results:
                return "failure"
            return "success"

        try:
            await scheduler.start(_execute_parallel, on_failure_judge=_on_failure_judge)
        finally:
            await event_bus.emit(Event.create(
                EventType.EXECUTOR_STOPPED,
                agent_name=agent_name,
                executor_id=executor_id,
            ))
            await event_bus.close()
    else:
        try:
            await scheduler.start(_execute_once, on_failure_judge=_on_failure_judge)
        finally:
            await event_bus.emit(Event.create(
                EventType.EXECUTOR_STOPPED,
                agent_name=agent_name,
                executor_id=executor_id,
            ))
            await event_bus.close()


def run(
    agent_name: str,
    config_path: str = "executor.yaml",
    cli_workspace: str | None = None,
    max_iterations: int | None = None,
    feature_id: str | None = None,
    mode: str | None = None,
    skip_planner: bool = False,
):
    """Synchronous entry point for running an agent."""
    try:
        asyncio.run(execute_agent(
            agent_name, config_path, cli_workspace, max_iterations, feature_id, mode, skip_planner,
        ))
    except KeyboardInterrupt:
        print(t('executor.error.interrupted'))
        print(t('executor.error.resume_hint'))
    except FileNotFoundError as e:
        print(t('executor.error.not_found', error=e))
    except BaseException as e:
        print(f"[executor] FATAL ({type(e).__name__}): {e}")
        import traceback
        traceback.print_exc()
        if not isinstance(e, Exception):
            raise


def vibe(
    agent_name: str,
    config_path: str = "executor.yaml",
    cli_workspace: str | None = None,
    feature_id: str | None = None,
    context_mode: str = "latest",
):
    """Interactive VibeCoding REPL — user guides the agent conversationally.

    Args:
        agent_name: Agent name to run in vibe mode
        config_path: Path to executor.yaml
        cli_workspace: Optional workspace override from CLI
        feature_id: Optional feature ID to work on a specific feature's workspace
        context_mode: Context loading mode — "all", "latest" (default), or "none"
    """
    base_dir = Path(config_path).parent.resolve()
    executor_config = load_executor_config(config_path)

    agent_config_path = Path(executor_config.agents_dir) / f"{agent_name}.yaml"
    if not agent_config_path.is_absolute():
        agent_config_path = base_dir / agent_config_path
    if not agent_config_path.exists():
        print(t('executor.vibe.config_not_found', path=agent_config_path))
        return

    agent_config = load_agent_config(agent_config_path)
    workspace = resolve_workspace(
        executor_config, agent_config,
        cli_workspace=cli_workspace, base_dir=base_dir,
    )
    target = _resolve_target(agent_config, executor_config, base_dir)
    ws_base_raw = Path(executor_config.workspace.base)
    workspace_base = (ws_base_raw if ws_base_raw.is_absolute() else base_dir / ws_base_raw).resolve()
    project_dir = (workspace_base / "project").resolve()
    agent_env = resolve_env_refs(agent_config.engine.env, executor_config.env)
    merged_env = {**executor_config.env, **agent_env}
    agent_workspace = workspace  # preserve root before feature_id override

    # --feature-id: switch to the specific feature's workspace
    if feature_id:
        features_dir = workspace_base / "features"
        tasks_dir_legacy = workspace_base / "tasks"
        if not features_dir.exists() and not tasks_dir_legacy.exists():
            print(t('executor.vibe.no_tasks_dir', agent=agent_name))
            return
        queue = FileFeatureQueue(workspace_base)
        task = queue.get(feature_id)
        if task is None:
            print(t('executor.vibe.task_not_found', id=feature_id))
            print(t('executor.vibe.list_hint', agent=agent_name))
            return
        workspace = queue.feature_workspace(feature_id)
        print(t('executor.vibe.task_workspace', path=workspace, id=feature_id))

    # Build event bus for observability
    event_bus = _build_event_bus(executor_config, base_dir)
    executor_id = executor_config.executor.name

    context_label = {
        "all": t('executor.vibe.context.all'),
        "latest": t('executor.vibe.context.latest'),
        "none": t('executor.vibe.context.none'),
    }
    print("=" * 60)
    print(t('executor.vibe.title'))
    print(t('executor.vibe.info.agent', name=agent_config.agent.name))
    print(t('executor.vibe.info.model', model=agent_config.engine.model))
    print(t('executor.vibe.info.workspace', path=workspace))
    if target:
        print(t('executor.vibe.info.target', path=target))
    if feature_id:
        print(t('executor.vibe.info.task_id', id=feature_id))
    print(t('executor.vibe.info.context',
            mode=context_mode, label=context_label.get(context_mode, context_mode)))
    print("=" * 60)
    print(t('executor.vibe.help.instruction'))
    print(t('executor.vibe.help.isolation'))

    async def _run():
        await event_bus.emit(Event.create(
            EventType.VIBE_SESSION_STARTED,
            agent_name=agent_name,
            executor_id=executor_id,
        ))

        session_count = 0
        total_cost = 0.0

        try:
            while True:
                try:
                    instruction = input("\n> ").strip()
                except EOFError:
                    break

                if not instruction:
                    continue
                if instruction.lower() in ("quit", "exit", "q"):
                    break

                session_count += 1
                print(t('executor.vibe.session',
                         n=session_count, instruction=instruction[:60]))
                print("-" * 50)

                result = run_vibe_session(
                    executor_config, agent_config, workspace,
                    user_instruction=instruction,
                    env=merged_env,
                    target=target,
                    project_dir=project_dir,
                    context_mode=context_mode,
                    agent_workspace=agent_workspace,
                    base_dir=base_dir,
                )

                cost = result.cost_usd or 0
                total_cost += cost
                cost_str = f"${cost:.4f}"
                print(t('executor.vibe.session_result',
                         status=result.status, turns=result.num_turns,
                         cost=cost_str, time=result.duration_ms))

                if result.status == "error":
                    print(t('executor.vibe.session_error', error=result.error))

        except KeyboardInterrupt:
            print(t('executor.vibe.interrupted'))

        await event_bus.emit(Event.create(
            EventType.VIBE_SESSION_ENDED,
            agent_name=agent_name,
            executor_id=executor_id,
            status="completed",
            sessions=session_count,
            cost_usd=total_cost,
        ))
        await event_bus.close()

        total_cost_str = f"${total_cost:.4f}"
        print(t('executor.vibe.done', sessions=session_count, cost=total_cost_str))

    asyncio.run(_run())
