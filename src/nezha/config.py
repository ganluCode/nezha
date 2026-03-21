"""Configuration loading: YAML → Python dataclass objects."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ComposeConfig:
    """Configuration for composing a prompt from modules."""
    base: str = ""                    # e.g. "coding/base.md"
    sections: list[str] = field(default_factory=list)  # e.g. ["phases/context-acquisition", "stacks/java-spring"]


# ---------------------------------------------------------------------------
# Executor Config (executor.yaml)
# ---------------------------------------------------------------------------

@dataclass
class ExecutorMeta:
    name: str = "agent-executor"
    description: str = ""


@dataclass
class WorkspaceConfig:
    base: str = "./workspace"
    strategy: str = "per_agent"  # per_agent | shared | custom


@dataclass
class SchedulerConfig:
    mode: str = "manual"  # manual | continuous | cron
    interval: int = 3
    cron: str = ""
    timezone: str = "Asia/Shanghai"
    max_backoff: int = 3600       # max wait between rounds (seconds); 0 = no cap
    backoff_on_no_task: bool = True  # also back off when queue is empty
    concurrency: int = 1  # max parallel feature executions; 1 = sequential
    failure_strategy: str = "ai_judge"  # "stop" | "continue" | "ai_judge"
    stop_on_empty: bool = True   # stop scheduler when no pending features remain
    judge_model: str = "claude-haiku-4-5-20251001"  # model for ai_judge evaluation
    judge_api_type: str = "anthropic"  # "anthropic" | "openai" — same as engine.api_type
    judge_env: dict[str, str] = field(default_factory=dict)  # env overrides for judge (API keys, base URL)


@dataclass
class GuardConfig:
    type: str = ""
    enabled: bool = False
    # All extra fields stored here
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class EventHandlerConfig:
    type: str = ""
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)


_DEFAULT_TASK_FACTORS: dict[str, float] = {"low": 1.2, "medium": 1.0, "high": 0.8}


@dataclass
class ModelMapEntry:
    """Maps a complexity level to a specific model and optional env overrides."""
    model: str = ""
    env: dict[str, str] = field(default_factory=dict)
    task_factor: float = 0.0  # 0 = use level-specific default from _DEFAULT_TASK_FACTORS

    def effective_task_factor(self, level: str = "medium") -> float:
        """Return task_factor, falling back to level-specific default if not set."""
        if self.task_factor > 0:
            return self.task_factor
        return _DEFAULT_TASK_FACTORS.get(level, 1.0)


@dataclass
class ExecutorConfig:
    executor: ExecutorMeta = field(default_factory=ExecutorMeta)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    guards: list[GuardConfig] = field(default_factory=list)
    event_handlers: list[EventHandlerConfig] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    mcp_servers: dict[str, Any] = field(default_factory=dict)  # global MCP servers shared by all agents
    agents_dir: str = "./agents"
    prompts_dir: str = "./prompts"
    state_dir: str = "./state"
    locale: str = "en"   # "en" | "zh_CN" — overridden by AGENT_EXEC_LANG env var
    model_map: dict[str, ModelMapEntry] = field(default_factory=dict)  # project-level model_map (from global config)
    target: str | None = None  # project-level target (code repo path); agent YAML can override


# ---------------------------------------------------------------------------
# Agent Config (agents/*.yaml)
# ---------------------------------------------------------------------------

@dataclass
class AgentMeta:
    name: str = ""
    description: str = ""
    category: str = ""          # "coding" | "planning" | "testing" | "design" | "review" | ...
    callable: bool = False      # True = can be auto-invoked by other agents


def build_model_map_info(model_map: dict[str, "ModelMapEntry"]) -> str:
    """Format model_map as a human-readable string for prompt injection."""
    if not model_map:
        return "Not configured (all tasks use default model, task_factor=1.0)"
    lines = []
    for level in ("low", "medium", "high"):
        entry = model_map.get(level)
        if entry:
            factor = entry.effective_task_factor(level)
            lines.append(f"- {level}: model={entry.model}, task_factor={factor}")
    return "\n".join(lines) if lines else "Not configured"


@dataclass
class EngineConfig:
    model: str = "claude-sonnet-4-5-20250929"
    max_turns: int = 1000
    tools: list[str] = field(default_factory=lambda: [
        "Read", "Write", "Edit", "Bash", "Glob", "Grep",
    ])
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    security: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    api_type: str = "anthropic"  # "anthropic" | "openai" — used in direct mode
    model_map: dict[str, ModelMapEntry] = field(default_factory=dict)
    session_timeout: int = 3600  # subprocess session timeout in seconds (default 1 hour)


@dataclass
class SessionConfig:
    mode: str = "single_round"  # single_round | multi_round
    max_iterations: int | None = None
    auto_continue_delay: int = 3
    prompts: dict[str, str] = field(default_factory=dict)
    max_cost_usd: float | None = None  # Total cost limit (USD); None = no limit
    max_sessions: int | None = None    # Total session count limit; None = no limit
    compose: dict[str, "ComposeConfig"] = field(default_factory=dict)  # keyed by prompt_key


@dataclass
class AgentWorkspaceConfig:
    path: str | None = None  # None = use executor global strategy


@dataclass
class IOConfig:
    type: str = "file"
    path: str = ""
    files: list[str] = field(default_factory=list)
    watch: bool = False
    git_commit: bool = False


@dataclass
class ArtifactConfig:
    name: str = ""
    path: str = ""


@dataclass
class VerificationConfig:
    command: str | None = None  # e.g. "python -m pytest" or "npm run build"


@dataclass
class GitConfig:
    auto_commit: bool = False       # commit after every session/task
    auto_push: bool = False         # push to remote (default off for safety)
    branch_per_task: bool = False   # create a new branch for each task
    branch_prefix: str = "feat/"   # prefix for auto-created branch names
    base_branch: str = "main"       # branch to base new branches on
    use_worktree: bool = False      # use git worktree for task isolation (requires branch_per_task)


@dataclass
class PreAgentConfig:
    name: str = ""          # agent name, e.g. "planner-agent"
    artifact: str = ""      # file it produces, e.g. "feature_list.json"


@dataclass
class PostToolConfig:
    name: str = ""          # tool name, e.g. "git-tool", "test-tool"
    action: str = ""        # action to perform, e.g. "commit", "run"
    params: dict[str, Any] = field(default_factory=dict)  # extra params passed to tool.run()


@dataclass
class PostTaskTestConfig:
    """Post-task integration test cycle: run after DAG completes, auto-fix on failure."""
    enabled: bool = False
    command: str = ""           # e.g. "./mvnw verify -pl integration-tests"
    max_cycles: int = 3         # max test→fix cycles before marking FAILED
    timeout: int = 600          # test command timeout in seconds


@dataclass
class PipelineConfig:
    pre_agents: list[PreAgentConfig] = field(default_factory=list)
    post_tools: list[PostToolConfig] = field(default_factory=list)
    post_task_test: PostTaskTestConfig = field(default_factory=PostTaskTestConfig)


@dataclass
class AgentConfig:
    agent: AgentMeta = field(default_factory=AgentMeta)
    engine: EngineConfig = field(default_factory=EngineConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    workspace: AgentWorkspaceConfig = field(default_factory=AgentWorkspaceConfig)
    input: IOConfig = field(default_factory=IOConfig)
    output: IOConfig = field(default_factory=IOConfig)
    artifacts: list[ArtifactConfig] = field(default_factory=list)
    verification: VerificationConfig = field(default_factory=VerificationConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    target: str | None = None           # coding agent: path to the code repo (cwd for LLM)
    target_scope: str | None = None     # monorepo: subdirectory within target (e.g. "frontend")
    git: GitConfig = field(default_factory=GitConfig)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _make_dataclass(cls, data: dict | None):
    """Create a dataclass instance from a dict, ignoring unknown fields."""
    if data is None:
        return cls()
    known_fields = {f.name for f in cls.__dataclass_fields__.values()}
    filtered = {}
    extra = {}
    for k, v in data.items():
        if k in known_fields:
            filtered[k] = v
        else:
            extra[k] = v
    obj = cls(**filtered)
    # Store extra fields in 'params' if the dataclass has it
    if hasattr(obj, "params") and extra:
        obj.params = extra
    return obj


def _normalize_scheduler(data: dict | None) -> dict | None:
    """Accept 'type' as alias for 'mode' in scheduler config."""
    if data and "type" in data and "mode" not in data:
        data = dict(data)
        data["mode"] = data.pop("type")
    return data


def _load_dotenv(directory: Path) -> dict[str, str]:
    """Load .env file from directory if it exists.

    Uses python-dotenv to parse .env; returns dict without modifying os.environ.
    """
    env_file = directory / ".env"
    if not env_file.exists():
        return {}
    from dotenv import dotenv_values
    values = dotenv_values(env_file)
    return {k: v for k, v in values.items() if v is not None}


_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


def resolve_env_refs(env: dict[str, str], base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Resolve ${VAR} references in env values.

    Lookup order: base_env (e.g. dotenv values) → os.environ → keep literal.
    """
    lookup = {**os.environ, **(base_env or {})}
    resolved: dict[str, str] = {}
    for key, value in env.items():
        def _replace(m: re.Match) -> str:
            return lookup.get(m.group(1), m.group(0))
        resolved[key] = _ENV_VAR_RE.sub(_replace, value)
    return resolved


def load_executor_config(config_path: str | Path) -> ExecutorConfig:
    """Load executor.yaml into an ExecutorConfig object.

    Also loads .env from the same directory (if present).
    Priority: .env < executor.yaml env (YAML overrides .env).
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Executor config not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    # Load .env as base, then overlay YAML env on top, then resolve ${VAR} refs
    dotenv = _load_dotenv(path.parent.resolve())
    yaml_env = {str(k): str(v) for k, v in (raw.get("env") or {}).items()}
    merged_env = resolve_env_refs({**dotenv, **yaml_env}, dotenv)

    config = ExecutorConfig(
        executor=_make_dataclass(ExecutorMeta, raw.get("executor")),
        workspace=_make_dataclass(WorkspaceConfig, raw.get("workspace")),
        scheduler=_make_dataclass(SchedulerConfig, _normalize_scheduler(raw.get("scheduler"))),
        guards=[
            _make_dataclass(GuardConfig, g)
            for g in (raw.get("guards") or [])
        ],
        event_handlers=[
            _make_dataclass(EventHandlerConfig, h)
            for h in (raw.get("event_handlers") or [])
        ],
        env=merged_env,
        mcp_servers=raw.get("mcp_servers") or {},
        agents_dir=raw.get("agents_dir", "./agents"),
        prompts_dir=raw.get("prompts_dir", "./prompts"),
        state_dir=raw.get("state_dir", "./state"),
        locale=raw.get("locale", "en"),
        target=raw.get("target"),
    )

    # Parse model_map (same logic as agent config)
    model_map_raw = raw.get("model_map") or {}
    parsed_model_map: dict[str, ModelMapEntry] = {}
    for level, entry in model_map_raw.items():
        if isinstance(entry, dict):
            parsed_model_map[level] = ModelMapEntry(
                model=entry.get("model", ""),
                env={str(k): str(v) for k, v in entry.get("env", {}).items()},
                task_factor=float(entry.get("task_factor", 0)),
            )
        elif isinstance(entry, str):
            parsed_model_map[level] = ModelMapEntry(model=entry)
    config.model_map = parsed_model_map

    return config


def load_agent_config(config_path: str | Path) -> AgentConfig:
    """Load an agent YAML file into an AgentConfig object."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Agent config not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    config = AgentConfig(
        agent=_make_dataclass(AgentMeta, raw.get("agent")),
        engine=_make_dataclass(EngineConfig, raw.get("engine")),
        session=_make_dataclass(SessionConfig, raw.get("session")),
        workspace=_make_dataclass(AgentWorkspaceConfig, raw.get("workspace")),
        input=_make_dataclass(IOConfig, raw.get("input")),
        output=_make_dataclass(IOConfig, raw.get("output")),
        artifacts=[
            _make_dataclass(ArtifactConfig, a)
            for a in (raw.get("artifacts") or [])
        ],
        verification=_make_dataclass(VerificationConfig, raw.get("verification")),
        pipeline=_parse_pipeline_config(raw.get("pipeline")),
        target=raw.get("target"),
        target_scope=raw.get("target_scope"),
        git=_make_dataclass(GitConfig, raw.get("git")),
    )

    # Parse compose config (nested dataclass not handled by _make_dataclass)
    session_raw = raw.get("session") or {}
    compose_raw = session_raw.get("compose") or {}
    parsed_compose: dict[str, ComposeConfig] = {}
    for key, val in compose_raw.items():
        if isinstance(val, dict):
            parsed_compose[key] = ComposeConfig(
                base=val.get("base", ""),
                sections=val.get("sections", []),
            )
    config.session.compose = parsed_compose

    # Parse model_map (nested dataclass not handled by _make_dataclass)
    engine_raw = raw.get("engine") or {}
    model_map_raw = engine_raw.get("model_map") or {}
    parsed_model_map: dict[str, ModelMapEntry] = {}
    for level, entry in model_map_raw.items():
        if isinstance(entry, dict):
            parsed_model_map[level] = ModelMapEntry(
                model=entry.get("model", ""),
                env={str(k): str(v) for k, v in entry.get("env", {}).items()},
                task_factor=float(entry.get("task_factor", 0)),
            )
        elif isinstance(entry, str):
            # Shorthand: model_map: { low: "claude-haiku-4-5-20251001" }
            parsed_model_map[level] = ModelMapEntry(model=entry)
    config.engine.model_map = parsed_model_map

    return config


def _parse_pipeline_config(data: dict | None) -> PipelineConfig:
    """Parse pipeline config section from raw YAML data."""
    if not data:
        return PipelineConfig()
    pre_agents = [
        _make_dataclass(PreAgentConfig, pa)
        for pa in (data.get("pre_agents") or [])
    ]
    post_tools = []
    for pt in (data.get("post_tools") or []):
        name = pt.get("name", "")
        action = pt.get("action", "")
        params = {k: v for k, v in pt.items() if k not in ("name", "action")}
        post_tools.append(PostToolConfig(name=name, action=action, params=params))
    post_task_test = _make_dataclass(PostTaskTestConfig, data.get("post_task_test"))
    return PipelineConfig(pre_agents=pre_agents, post_tools=post_tools, post_task_test=post_task_test)


def resolve_workspace(
    executor_config: ExecutorConfig,
    agent_config: AgentConfig,
    cli_workspace: str | None = None,
    base_dir: Path | None = None,
) -> Path:
    """Resolve the workspace path. Priority: CLI arg > Agent config > Executor strategy.

    Args:
        executor_config: The executor configuration
        agent_config: The agent configuration
        cli_workspace: Workspace path from CLI --workspace argument
        base_dir: Base directory for resolving relative paths (default: CWD)
    """
    base = base_dir or Path.cwd()

    # Priority 1: CLI argument
    if cli_workspace:
        p = Path(cli_workspace)
        return p if p.is_absolute() else base / p

    # Priority 2: Agent-level workspace config
    if agent_config.workspace.path:
        p = Path(agent_config.workspace.path)
        return p if p.is_absolute() else base / p

    # Priority 3: Executor global strategy
    ws_base = Path(executor_config.workspace.base)
    if not ws_base.is_absolute():
        ws_base = base / ws_base

    strategy = executor_config.workspace.strategy
    if strategy == "per_agent":
        return ws_base / agent_config.agent.name
    elif strategy == "shared":
        return ws_base
    else:  # custom — fallback to per_agent
        return ws_base / agent_config.agent.name
