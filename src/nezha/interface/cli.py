"""CLI commands: status, history, logs, rework, plan, feature — read/write state/ directory."""

import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from nezha.i18n import t


# ---------------------------------------------------------------------------
# Global user config: ~/.nezha/config.yaml
# ---------------------------------------------------------------------------

GLOBAL_CONFIG_DIR = Path.home() / ".nezha"
GLOBAL_CONFIG_PATH = GLOBAL_CONFIG_DIR / "config.yaml"

# Keys that are merged from global config into project executor.yaml
_GLOBAL_MERGE_KEYS = ("locale", "timezone", "env", "model_map")


def load_global_config() -> dict:
    """Load user-level global config from ~/.nezha/config.yaml.

    Returns an empty dict if the file does not exist or is invalid.
    """
    if not GLOBAL_CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        with open(GLOBAL_CONFIG_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _apply_global_config(executor_yaml_path: Path) -> list[str]:
    """Merge global config values into a newly created executor.yaml.

    Only merges keys listed in _GLOBAL_MERGE_KEYS.
    For dict-type keys (env, model_map), merges entries.
    For scalar keys (locale, timezone), overwrites.

    Returns list of applied key names for display.
    """
    global_cfg = load_global_config()
    if not global_cfg:
        return []

    import yaml

    with open(executor_yaml_path, encoding="utf-8") as f:
        project_cfg = yaml.safe_load(f) or {}

    applied = []
    for key in _GLOBAL_MERGE_KEYS:
        if key not in global_cfg:
            continue
        val = global_cfg[key]
        if isinstance(val, dict) and isinstance(project_cfg.get(key), dict):
            # Merge dicts
            project_cfg[key].update(val)
        else:
            # Overwrite scalar or replace
            project_cfg[key] = val
        applied.append(key)

    if applied:
        with open(executor_yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(project_cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    return applied


def _resolve_state_dir(config_path: str = "executor.yaml") -> Path:
    """Get state directory from executor config, with fallback."""
    base = Path(config_path).parent.resolve()
    config = base / config_path if not Path(config_path).is_absolute() else Path(config_path)

    if config.exists():
        import yaml
        with open(config) as f:
            raw = yaml.safe_load(f) or {}
        state_dir = raw.get("state_dir", "./state")
    else:
        state_dir = "./state"

    path = Path(state_dir)
    return path if path.is_absolute() else base / path


def cmd_status(config_path: str = "executor.yaml"):
    """Show executor current status from executor_status.json."""
    state_dir = _resolve_state_dir(config_path)

    # Show background process info if running
    pid_file = state_dir / "run.pid"
    if pid_file.exists():
        try:
            with open(pid_file) as f:
                pid_data = json.load(f)
            pid = pid_data.get("pid")
            import os
            try:
                os.kill(pid, 0)
                is_running = True
            except (ProcessLookupError, TypeError):
                is_running = False
            except PermissionError:
                is_running = True

            if is_running:
                print("[background] Agent running in background:")
                print(f"  PID:     {pid}")
                print(f"  Agent:   {pid_data.get('agent', '-')}")
                print(f"  Started: {pid_data.get('started_at', '-')}")
                print(f"  Log:     {pid_data.get('log', '-')}")
                print()
            else:
                print("[background] Previous background process (PID %d) is no longer running" % pid)
                pid_file.unlink(missing_ok=True)
                print()
        except (json.JSONDecodeError, KeyError):
            pass

    status_file = state_dir / "executor_status.json"

    if not status_file.exists():
        print(t('cli.status.no_file'))
        print(t('cli.status.expected', path=status_file))
        return

    with open(status_file) as f:
        data = json.load(f)

    print("=" * 50)
    print(t('cli.status.title'))
    print("=" * 50)
    print(t('cli.status.status', value=data.get('status', 'unknown')))
    print(t('cli.status.current_agent', agent=data.get('current_agent', '-')))
    print(t('cli.status.session_id', id=data.get('session_id', 0)))
    print(t('cli.status.started', time=data.get('started_at', '-')))
    print(t('cli.status.updated', time=data.get('last_updated', '-')))

    progress = data.get("progress", {})
    if progress:
        print(t('cli.status.last_session'))
        print(t('cli.status.session_status', status=progress.get('status', '-')))
        print(t('cli.status.turns', turns=progress.get('num_turns', '-')))
        cost_str = f"${progress.get('cost_usd', 0):.4f}"
        print(t('cli.status.cost', cost=cost_str))
        print(t('cli.status.duration', ms=progress.get('duration_ms', 0)))

    guards = data.get("guards", {})
    if guards.get("last_block"):
        print(t('cli.status.guard_block', block=guards['last_block']))

    rework = data.get("rework_stats", {})
    if rework.get("active_reworks"):
        print(t('cli.status.rework'))
        print(t('cli.status.rework_active', count=rework['active_reworks']))
        print(t('cli.status.rework_total', count=rework['total_reworks']))
        features_str = ', '.join(rework.get('features_in_rework', []))
        print(t('cli.status.rework_features', features=features_str))
    print("=" * 50)


def cmd_history(config_path: str = "executor.yaml"):
    """Show execution history from state/history/."""
    state_dir = _resolve_state_dir(config_path)
    history_dir = state_dir / "history"

    if not history_dir.exists():
        print(t('cli.history.no_history'))
        return

    files = sorted(history_dir.glob("*.json"))
    if not files:
        print(t('cli.history.no_records'))
        return

    print("=" * 60)
    print(t('cli.history.title'))
    print("=" * 60)
    for f in files:
        with open(f) as fh:
            data = json.load(fh)
        agent = data.get("agent", "?")
        ts = data.get("timestamp", "?")
        event = data.get("event_type", "?")
        status = data.get("status", "-")
        turns = data.get("num_turns", "-")
        cost = data.get("cost_usd", 0)

        line = f"  {ts} | {agent} | {event}"
        if status != "-":
            line += f" | status={status}"
        if turns != "-":
            line += f" | turns={turns}"
        if cost:
            line += f" | ${cost:.4f}"
        print(line)
    print("=" * 60)
    print(t('cli.history.total', count=len(files)))


def cmd_logs(config_path: str = "executor.yaml", follow: bool = False):
    """Show or follow execution logs from state/logs/."""
    state_dir = _resolve_state_dir(config_path)
    logs_dir = state_dir / "logs"

    if not logs_dir.exists():
        print(t('cli.logs.no_logs'))
        return

    files = sorted(logs_dir.glob("*.log"))
    if not files:
        print(t('cli.logs.no_files'))
        return

    if follow:
        # Follow the latest log file (like tail -f)
        latest = files[-1]
        print(t('cli.logs.following', name=latest.name))
        print("-" * 50)
        try:
            with open(latest) as f:
                # Print existing content
                content = f.read()
                if content:
                    print(content, end="")
                # Follow new lines
                while True:
                    line = f.readline()
                    if line:
                        print(line, end="")
                    else:
                        time.sleep(0.5)
        except KeyboardInterrupt:
            print(t('cli.logs.stopped'))
    else:
        # Show the latest log file
        latest = files[-1]
        print(t('cli.logs.latest', name=latest.name))
        print("-" * 50)
        with open(latest) as f:
            print(f.read())


def cmd_rework(
    agent_name: str,
    feature_ids: str,
    note: str,
    config_path: str = "executor.yaml",
):
    """Mark tasks for rework in the agent's task_list.json.

    Args:
        agent_name: Agent name (e.g. coding-agent)
        feature_ids: Comma-separated task IDs (e.g. "F-003" or "F-003,F-005")
        note: Rework reason/note
        config_path: Path to executor config
    """
    from nezha.config import load_agent_config, load_executor_config, resolve_workspace

    base_dir = Path(config_path).parent.resolve()
    executor_config = load_executor_config(config_path)

    agent_config_path = Path(executor_config.agents_dir) / f"{agent_name}.yaml"
    if not agent_config_path.is_absolute():
        agent_config_path = base_dir / agent_config_path
    if not agent_config_path.exists():
        print(t('cli.rework.agent_not_found', path=agent_config_path))
        return

    agent_config = load_agent_config(agent_config_path)
    workspace = resolve_workspace(executor_config, agent_config, base_dir=base_dir)

    # Support both new name and legacy name
    task_list_path = workspace / "task_list.json"
    if not task_list_path.exists():
        task_list_path = workspace / "feature_list.json"
    if not task_list_path.exists():
        print(t('cli.rework.no_list', path=workspace / "task_list.json"))
        return

    with open(task_list_path) as f:
        features = json.load(f)

    ids = [fid.strip() for fid in feature_ids.split(",")]
    updated = []

    for feature in features:
        if feature.get("id") in ids:
            feature["passes"] = False
            feature["rework"] = True
            feature["rework_note"] = note
            feature["rework_count"] = feature.get("rework_count", 0) + 1
            updated.append(feature["id"])

    if not updated:
        print(t('cli.rework.no_features', ids=ids))
        available = [f.get("id") for f in features]
        print(t('cli.rework.available', ids=available))
        return

    with open(task_list_path, "w", encoding="utf-8") as f:
        json.dump(features, f, indent=2, ensure_ascii=False)

    # Write rework event to history
    state_dir = _resolve_state_dir(config_path)
    history_dir = state_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    for fid in updated:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        record = {
            "type": "rework",
            "feature_id": fid,
            "source": "manual",
            "reason": note,
            "timestamp": datetime.now().isoformat(),
            "agent": agent_name,
            "resolved": False,
        }
        filepath = history_dir / f"rework_{fid}_{ts}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)

    print(t('cli.rework.marked', count=len(updated)))
    for fid in updated:
        print(t('cli.rework.feature', id=fid, note=note))
    print(t('cli.rework.run_hint', agent=agent_name))


def cmd_plan(agent_name: str, config_path: str = "executor.yaml"):
    """Show the task dependency DAG for an agent.

    Args:
        agent_name: Agent name
        config_path: Path to executor config
    """
    from nezha.config import load_agent_config, load_executor_config, resolve_workspace
    from nezha.dag.graph import (
        TaskDAG,
        STATUS_COMPLETED,
        STATUS_REWORK,
        STATUS_READY,
        STATUS_BLOCKED,
        STATUS_SKIPPED,
    )

    base_dir = Path(config_path).parent.resolve()
    executor_config = load_executor_config(config_path)

    agent_config_path = Path(executor_config.agents_dir) / f"{agent_name}.yaml"
    if not agent_config_path.is_absolute():
        agent_config_path = base_dir / agent_config_path
    if not agent_config_path.exists():
        print(t('cli.plan.agent_not_found', path=agent_config_path))
        return

    agent_config = load_agent_config(agent_config_path)
    workspace = resolve_workspace(executor_config, agent_config, base_dir=base_dir)

    # Look for task_list.json (or legacy feature_list.json)
    task_list_path = workspace / "task_list.json"
    if not task_list_path.exists():
        # Legacy fallback
        legacy_path = workspace / "feature_list.json"
        if legacy_path.exists():
            task_list_path = legacy_path
        else:
            # Try input directory
            input_path = workspace / "input" / "task_list.json"
            input_legacy = workspace / "input" / "feature_list.json"
            if input_path.exists():
                task_list_path = input_path
            elif input_legacy.exists():
                task_list_path = input_legacy
            else:
                print(t('cli.plan.no_feature_list', path=workspace))
                return

    dag = TaskDAG.load(task_list_path)
    s = dag.summary()

    print(t('cli.plan.title', agent=agent_name))
    print("=" * 60)
    print(dag.format_tree())
    print("=" * 60)
    print(t('cli.plan.legend'))
    print()
    print(t('cli.plan.total', count=s['total']))
    print(t('cli.plan.completed', done=s['counts'][STATUS_COMPLETED], total=s['total']))

    ready_ids = s["by_status"][STATUS_READY]
    if ready_ids:
        print(t('cli.plan.ready', count=len(ready_ids), ids=', '.join(ready_ids)))

    rework_ids = s["by_status"][STATUS_REWORK]
    if rework_ids:
        print(t('cli.plan.rework', count=len(rework_ids), ids=', '.join(rework_ids)))

    blocked_ids = s["by_status"][STATUS_BLOCKED]
    if blocked_ids:
        print(t('cli.plan.blocked', count=len(blocked_ids)))

    skipped_ids = s["by_status"][STATUS_SKIPPED]
    if skipped_ids:
        print(t('cli.plan.skipped', count=len(skipped_ids), ids=', '.join(skipped_ids)))

    # Show blocked details
    blocked_tasks = dag.get_blocked_tasks()
    if blocked_tasks:
        print(t('cli.plan.blocked_details'))
        for bi in blocked_tasks:
            print(t('cli.plan.blocked_item', id=bi.task_id, deps=', '.join(bi.blocked_by)))

    if dag.is_all_done():
        print(t('cli.plan.all_done'))
    elif dag.is_deadlocked():
        print(t('cli.plan.deadlocked'))
        print(t('cli.plan.deadlock_hint', agent=agent_name))


# ---------------------------------------------------------------------------
# Feature queue commands
# ---------------------------------------------------------------------------

def _resolve_agent_workspace(agent_name: str, config_path: str) -> Path:
    """Resolve the per-agent workspace directory from config."""
    from nezha.config import load_agent_config, load_executor_config, resolve_workspace

    base_dir = Path(config_path).parent.resolve()
    executor_config = load_executor_config(config_path)

    agent_config_path = Path(executor_config.agents_dir) / f"{agent_name}.yaml"
    if not agent_config_path.is_absolute():
        agent_config_path = base_dir / agent_config_path
    if not agent_config_path.exists():
        print(t('cli.feature.agent_not_found', path=agent_config_path))
        sys.exit(1)

    agent_config = load_agent_config(agent_config_path)
    return resolve_workspace(executor_config, agent_config, base_dir=base_dir)


def _resolve_workspace_base(config_path: str) -> Path:
    """Resolve the workspace base (project root for shared features/ directory)."""
    from nezha.config import load_executor_config

    base_dir = Path(config_path).parent.resolve()
    executor_config = load_executor_config(config_path)
    ws_base_raw = Path(executor_config.workspace.base)
    return (ws_base_raw if ws_base_raw.is_absolute() else base_dir / ws_base_raw).resolve()


def _resolve_features_dir(workspace_base: Path) -> Path | None:
    """Find the features directory, preferring one with content.

    Checks global features/ and tasks/ first, then per-agent subdirectories.
    Returns None if no features directory with content is found.
    """
    def _pick(base: Path) -> Path | None:
        features = base / "features"
        tasks = base / "tasks"
        f_content = features.exists() and any(features.iterdir())
        t_content = tasks.exists() and any(tasks.iterdir())
        if f_content:
            return features
        if t_content:
            return tasks
        return None

    # 1) Global level
    result = _pick(workspace_base)
    if result:
        return result

    # 2) Per-agent subdirectories
    if workspace_base.exists():
        for sub in sorted(workspace_base.iterdir()):
            if sub.is_dir():
                result = _pick(sub)
                if result:
                    return result

    return None


def cmd_feature_create(
    title: str = "",
    input_files: list[str] | None = None,
    priority: int = 50,
    branch: str = "",
    base_branch: str = "",
    config_path: str = "executor.yaml",
) -> str:
    """Create a new pending feature.

    Args:
        title: Human-readable feature title (e.g. "User Auth")
        input_files: Optional list of file paths to copy into feature's input/
        priority: Scheduling priority 0–100 (default 50, higher runs first)
        branch: Git branch to bind (defaults to feat/<feature-id>)
        base_branch: Git base branch to create from (overrides agent config base_branch)
        config_path: Path to executor config

    Returns:
        Feature ID of the created feature
    """
    from nezha.feature_queue import FileFeatureQueue

    workspace_base = _resolve_workspace_base(config_path)
    queue = FileFeatureQueue(workspace_base)
    task = queue.create(title, priority=priority, branch=branch, base_branch=base_branch)
    task_ws = queue.feature_workspace(task.id)

    print(t('cli.feature.created', id=task.id))
    print(t('cli.feature.task_status', status=task.status.value))
    print(f"  Priority:    {task.priority}")
    print(f"  Branch:      {task.metadata.get('branch', '')}")
    if task.metadata.get('base_branch'):
        print(f"  Base branch: {task.metadata['base_branch']}")
    print(t('cli.feature.task_path', path=task_ws))

    if input_files:
        input_dir = task_ws / "input"
        for src_path in input_files:
            src = Path(src_path)
            if not src.exists():
                print(t('cli.feature.input_warning', path=src))
                continue
            dst = input_dir / src.name
            shutil.copy2(src, dst)
            print(t('cli.feature.input_copied', name=src.name, dst=dst))

    print(t('cli.feature.run_hint', agent="<agent>", id=task.id))

    return task.id


# Backward compatibility alias
cmd_task_create = cmd_feature_create


def cmd_feature_create_and_return(
    title: str = "",
    input_files: list[str] | None = None,
    priority: int = 50,
    branch: str = "",
    base_branch: str = "",
    config_path: str = "executor.yaml",
) -> str:
    """Create a new pending feature and return its ID (for inline run).

    Same as cmd_feature_create but returns the feature ID instead of printing
    a run hint — used by ``nezha run --title``.
    """
    from nezha.feature_queue import FileFeatureQueue

    workspace_base = _resolve_workspace_base(config_path)
    queue = FileFeatureQueue(workspace_base)
    task = queue.create(title, priority=priority, branch=branch, base_branch=base_branch)
    task_ws = queue.feature_workspace(task.id)

    print(t('cli.feature.created', id=task.id))
    print(t('cli.feature.task_path', path=task_ws))

    if input_files:
        input_dir = task_ws / "input"
        for src_path in input_files:
            src = Path(src_path)
            if not src.exists():
                print(t('cli.feature.input_warning', path=src))
                continue
            dst = input_dir / src.name
            shutil.copy2(src, dst)
            print(t('cli.feature.input_copied', name=src.name, dst=dst))

    return task.id


# Backward compatibility alias
cmd_task_create_and_return = cmd_feature_create_and_return


def cmd_feature_list(
    agent_name: str | None = None,
    status: str | None = None,
    config_path: str = "executor.yaml",
):
    """List features, optionally filtered by agent and/or status.

    Args:
        agent_name: Optional agent filter (features with task_list.<agent>.json)
        status: Optional status filter
        config_path: Path to executor config
    """
    from nezha.feature_queue import FileFeatureQueue, FeatureStatus

    workspace_base = _resolve_workspace_base(config_path)
    filter_status = FeatureStatus(status) if status else None

    # Collect features from all possible locations
    tasks: list = []

    # 1) Global features/ or tasks/ directory
    global_features = workspace_base / "features"
    global_tasks = workspace_base / "tasks"
    if global_features.exists() or global_tasks.exists():
        queue = FileFeatureQueue(workspace_base)
        tasks.extend(queue.list_features(agent_name=agent_name, status=filter_status))

    # 2) Per-agent subdirectories (legacy: workspace/<agent>/tasks/ or features/)
    if not tasks:
        for sub in sorted(workspace_base.iterdir()):
            if not sub.is_dir():
                continue
            if (sub / "features").exists() or (sub / "tasks").exists():
                queue = FileFeatureQueue(sub)
                found = queue.list_features(agent_name=agent_name, status=filter_status)
                tasks.extend(found)

    if not tasks:
        filter_msg = t('cli.feature.no_tasks_filter', status=status) if status else ""
        print(t('cli.feature.no_tasks', agent=agent_name or "any", filter=filter_msg))
        return

    if not tasks:
        filter_msg = t('cli.feature.no_tasks_filter', status=status) if status else ""
        print(t('cli.feature.no_tasks', agent=agent_name or "any", filter=filter_msg))
        return

    status_icons = {
        "pending": "○",
        "running": "►",
        "paused": "‖",
        "completed": "✓",
        "partial": "△",
        "failed": "✗",
    }

    # Pre-compute tokens for each feature
    task_tokens: list[str] = []
    for task in tasks:
        # Try to find execution-report in the feature workspace
        tokens_str = "-"
        for base in [workspace_base, *(
            sub for sub in workspace_base.iterdir()
            if sub.is_dir() and (sub / "features").exists() or (sub / "tasks").exists()
        )]:
            q = FileFeatureQueue(base)
            ws = q.feature_workspace(task.id)
            if ws.exists():
                tokens_str = _get_feature_tokens(ws)
                break
        task_tokens.append(tokens_str)

    header_label = agent_name or "all agents"
    print(t('cli.feature.title', agent=header_label))
    print("=" * 100)
    print(f"  {'#':<4}  {'ID':<35}  {'STATUS':<10}  {'TOKENS':<12}  {'CREATED':<20}")
    print("-" * 100)
    for i, task in enumerate(tasks, 1):
        icon = status_icons.get(task.status.value, "?")
        created = task.created_at[:19] if task.created_at else "-"
        tokens = task_tokens[i - 1]
        print(f"  [{i:<2}] {icon} {task.id:<31}  {task.status.value:<10}  {tokens:<12}  {created}")
    print("=" * 100)
    print(f"  Total: {len(tasks)}")
    print("\n提示: 使用序号 [#] 快速引用 feature")
    print("例如: nezha integrate 1 2 --branch temp/review")


# Backward compatibility alias
cmd_task_list = cmd_feature_list


# ---------------------------------------------------------------------------
# Report parsing helpers (shared by feature show, feature list, dashboard)
# ---------------------------------------------------------------------------

def _parse_report_summary(report_path: Path) -> dict | None:
    """Parse execution-report.md and extract summary data.

    Returns dict with keys: exit_reason, completed, total, sessions, cost, time_ms,
                            tokens, tokens_in, tokens_out,
                            timeline (list of dicts per session).
    Returns None if file doesn't exist.
    """
    if not report_path.exists():
        return None

    text = report_path.read_text(encoding="utf-8")
    result: dict = {}

    # Exit reason
    m = re.search(r"Exit reason:\s*(.+)", text)
    if m:
        result["exit_reason"] = m.group(1).strip()

    # Completed count  e.g. "| Completed | 6/6 |"
    m = re.search(r"\|\s*Completed\s*\|\s*(\d+)/(\d+)\s*\|", text)
    if m:
        result["completed"] = int(m.group(1))
        result["total"] = int(m.group(2))

    # Total sessions
    m = re.search(r"Total sessions:\s*(\d+)", text)
    if m:
        result["sessions"] = int(m.group(1))

    # Total tokens  e.g. "Total tokens: 1.2M (in: 900.0K, out: 300.0K)"
    m = re.search(r"Total tokens:\s*(\S+)\s*\(in:\s*(\S+),\s*out:\s*(\S+)\)", text)
    if m:
        result["tokens"] = m.group(1)
        result["tokens_in"] = m.group(2)
        result["tokens_out"] = m.group(3)

    # Total cost
    m = re.search(r"Total cost:\s*\$([0-9.]+)", text)
    if m:
        result["cost"] = float(m.group(1))

    # Total time
    m = re.search(r"Total time:\s*(\d+)ms", text)
    if m:
        result["time_ms"] = int(m.group(1))

    # Session Timeline table rows
    # Format: | # | Feature | Type | Duration | Tokens | Cost | Result |
    timeline = []
    for row in re.finditer(
        r"\|\s*(\d+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\d+)ms\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|",
        text,
    ):
        timeline.append({
            "num": int(row.group(1)),
            "feature": row.group(2),
            "type": row.group(3),
            "duration_ms": int(row.group(4)),
            "tokens": row.group(5),
            "cost": row.group(6),
            "result": row.group(7),
        })
    # Fallback: old 6-column format (no tokens column)
    if not timeline:
        for row in re.finditer(
            r"\|\s*(\d+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\d+)ms\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|",
            text,
        ):
            timeline.append({
                "num": int(row.group(1)),
                "feature": row.group(2),
                "type": row.group(3),
                "duration_ms": int(row.group(4)),
                "tokens": "-",
                "cost": row.group(5),
                "result": row.group(6),
            })
    if timeline:
        result["timeline"] = timeline

    return result if result else None


def _get_feature_tokens(feature_workspace: Path) -> str:
    """Extract total tokens string from a feature's execution-report.md.

    Returns formatted tokens string (e.g. "1.2M") or "-".
    """
    summary = _parse_report_summary(feature_workspace / "execution-report.md")
    if summary and "tokens" in summary:
        return summary["tokens"]
    return "-"


def _get_feature_cost(feature_workspace: Path) -> str:
    """Extract total cost string from a feature's execution-report.md.

    Returns formatted cost string (e.g. "$1.2345") or "-".
    """
    summary = _parse_report_summary(feature_workspace / "execution-report.md")
    if summary and "cost" in summary:
        return f"${summary['cost']:.4f}"
    return "-"


def cmd_feature_show(
    feature_id: str,
    config_path: str = "executor.yaml",
):
    """Show details for a specific feature.

    Args:
        feature_id: Feature ID
        config_path: Path to executor config
    """
    from nezha.feature_queue import FileFeatureQueue

    workspace_base = _resolve_workspace_base(config_path)
    queue = FileFeatureQueue(workspace_base)
    task = queue.get(feature_id)

    if task is None:
        print(t('cli.feature.not_found', id=feature_id))
        return

    task_ws = queue.feature_workspace(feature_id)

    print(t('cli.feature.show_title', id=task.id))
    print("=" * 50)
    if task.title:
        print(f"  Title:     {task.title}")
    print(t('cli.feature.show_status', status=task.status.value))
    print(t('cli.feature.show_created', time=task.created_at))
    print(t('cli.feature.show_started', time=task.started_at or '-'))
    print(t('cli.feature.show_completed', time=task.completed_at or '-'))
    if task.error:
        print(t('cli.feature.show_error', error=task.error))
    branch = task.metadata.get("branch")
    base_branch = task.metadata.get("base_branch")
    if branch:
        print(t('cli.feature.show_branch', branch=branch))
        print(t('cli.feature.show_base', base=base_branch or '-'))
    print(t('cli.feature.show_path', path=task_ws))

    # List input files
    input_dir = task_ws / "input"
    if input_dir.exists():
        files = list(input_dir.iterdir())
        if files:
            print(t('cli.feature.input_files'))
            for f in sorted(files):
                print(t('cli.feature.input_file', name=f.name))

    if task.metadata:
        print(t('cli.feature.metadata', data=task.metadata))

    # Display steps if present
    if task.steps:
        from nezha.feature_queue import (
            STEP_COMPLETED, STEP_NEEDS_REVIEW, STEP_RUNNING, STEP_SKIPPED,
        )
        step_icons = {
            "pending": "○",
            "ready": "→",
            "running": "◉",
            STEP_COMPLETED: "✓",
            STEP_NEEDS_REVIEW: "⏸",
            STEP_SKIPPED: "✗",
        }
        print(f"\n  Steps:")
        for s in task.steps:
            # Compute effective status (ready if pending + deps met)
            effective = queue._get_step_status(task, s.id)
            icon = step_icons.get(effective, "?")
            deps = f" (depends: {', '.join(s.depends_on)})" if s.depends_on else ""
            gate = " [review]" if s.review_gate else ""
            note = f" — {s.note}" if s.note else ""
            print(f"    {icon} {s.id} → {s.agent}{deps}{gate}{note}")

    # Display execution report summary
    report_path = task_ws / "execution-report.md"
    summary = _parse_report_summary(report_path)
    if summary:
        print(f"\n  Execution Report:")
        if "exit_reason" in summary:
            print(f"    Exit reason: {summary['exit_reason']}")
        if "completed" in summary and "total" in summary:
            print(f"    Completed:   {summary['completed']}/{summary['total']}")
        if "sessions" in summary:
            print(f"    Sessions:    {summary['sessions']}")
        if "tokens" in summary:
            print(f"    Tokens:      {summary['tokens']} (in: {summary.get('tokens_in', '-')}, out: {summary.get('tokens_out', '-')})")
        if "time_ms" in summary:
            minutes = summary["time_ms"] / 60_000
            print(f"    Total time:  {minutes:.1f}min ({summary['time_ms']}ms)")
        if "timeline" in summary:
            print(f"\n    Session Timeline:")
            print(f"    {'#':<4} {'Task':<10} {'Type':<8} {'Duration':<12} {'Tokens':<10} {'Result'}")
            print(f"    {'-'*60}")
            for s in summary["timeline"]:
                dur = f"{s['duration_ms']}ms"
                print(f"    {s['num']:<4} {s['feature']:<10} {s['type']:<8} {dur:<12} {s.get('tokens', '-'):<10} {s['result']}")


# Backward compatibility alias
cmd_task_show = cmd_feature_show


def cmd_feature_approve(
    feature_id: str,
    step_id: str,
    config_path: str = "executor.yaml",
):
    """Approve a step that is waiting for review.

    Args:
        feature_id: Feature ID
        step_id: Step ID to approve
        config_path: Path to executor config
    """
    from nezha.feature_queue import FileFeatureQueue, STEP_COMPLETED, STEP_NEEDS_REVIEW

    workspace_base = _resolve_workspace_base(config_path)
    queue = FileFeatureQueue(workspace_base)
    feature = queue.get(feature_id)

    if feature is None:
        print(t('cli.feature.not_found', id=feature_id))
        return

    step = next((s for s in feature.steps if s.id == step_id), None)
    if step is None:
        print(f"[feature] Step '{step_id}' not found in feature {feature_id}")
        return

    if step.status != STEP_NEEDS_REVIEW:
        print(f"[feature] Step '{step_id}' is not waiting for review (status: {step.status})")
        return

    queue.update_step_status(feature_id, step_id, STEP_COMPLETED)
    print(f"[feature] Step '{step_id}' approved ✓")

    # Check if all steps are now done
    if queue.all_steps_done(feature_id):
        from nezha.feature_queue import FeatureStatus
        queue.update_status(feature_id, FeatureStatus.COMPLETED)
        print(f"[feature] Feature {feature_id}: all steps completed")
    else:
        # Show next ready step
        next_step = queue.get_next_ready_step(feature_id)
        if next_step:
            print(f"[feature] Next ready step: {next_step.id} → {next_step.agent}")


def cmd_feature_reject(
    feature_id: str,
    step_id: str,
    note: str = "",
    config_path: str = "executor.yaml",
):
    """Reject a step and reset to pending for re-execution.

    Args:
        feature_id: Feature ID
        step_id: Step ID to reject
        note: Rejection reason / feedback
        config_path: Path to executor config
    """
    from nezha.feature_queue import FileFeatureQueue, STEP_PENDING, STEP_NEEDS_REVIEW

    workspace_base = _resolve_workspace_base(config_path)
    queue = FileFeatureQueue(workspace_base)
    feature = queue.get(feature_id)

    if feature is None:
        print(t('cli.feature.not_found', id=feature_id))
        return

    step = next((s for s in feature.steps if s.id == step_id), None)
    if step is None:
        print(f"[feature] Step '{step_id}' not found in feature {feature_id}")
        return

    if step.status != STEP_NEEDS_REVIEW:
        print(f"[feature] Step '{step_id}' is not waiting for review (status: {step.status})")
        return

    queue.update_step_status(feature_id, step_id, STEP_PENDING, note=note or "Rejected")
    print(f"[feature] Step '{step_id}' rejected → pending")
    if note:
        print(f"[feature] Note: {note}")
    print(f"[feature] Re-run: nezha run <agent> --feature-id {feature_id}")


def cmd_feature_push(
    agent_name: str,
    feature_id: str,
    config_path: str = "executor.yaml",
):
    """Push the feature's git branch to remote origin.

    Args:
        agent_name: Agent name
        feature_id: Feature ID
        config_path: Path to executor config
    """
    import subprocess

    import os

    from nezha.config import load_agent_config, load_executor_config
    from nezha.executor import _resolve_target
    from nezha.feature_queue import FileFeatureQueue

    base_dir = Path(config_path).parent.resolve()
    executor_config = load_executor_config(config_path)

    agent_config_path = Path(executor_config.agents_dir) / f"{agent_name}.yaml"
    if not agent_config_path.is_absolute():
        agent_config_path = base_dir / agent_config_path
    if not agent_config_path.exists():
        print(t('cli.feature.agent_not_found', path=agent_config_path))
        return

    agent_config = load_agent_config(agent_config_path)
    target = _resolve_target(agent_config, executor_config, base_dir)

    if target is None:
        print(t('cli.feature.no_target', agent=agent_name))
        return

    # Merge env for git operations (GH_TOKEN, etc.)
    from nezha.config import resolve_env_refs
    agent_env = resolve_env_refs(agent_config.engine.env, executor_config.env)
    merged_env = {**executor_config.env, **agent_env}
    git_env = {**os.environ, **merged_env} if merged_env else None

    workspace_base = _resolve_workspace_base(config_path)
    queue = FileFeatureQueue(workspace_base)
    task = queue.get(feature_id)

    if task is None:
        print(t('cli.feature.not_found', id=feature_id))
        return

    branch = task.metadata.get("branch")
    if not branch:
        print(t('cli.feature.no_branch', id=feature_id))
        return

    print(t('cli.feature.pushing', branch=branch))
    result = subprocess.run(
        ["git", "push", "origin", branch],
        cwd=target,
        capture_output=True,
        text=True,
        env=git_env,
    )
    if result.returncode == 0:
        print(t('cli.feature.pushed', branch=branch))
        if result.stdout:
            print(result.stdout)
    else:
        print(t('cli.feature.push_failed'))
        print(result.stderr or result.stdout)


# Backward compatibility alias
cmd_task_push = cmd_feature_push


# ---------------------------------------------------------------------------
# Init command — scaffold a new agent-executor project
# ---------------------------------------------------------------------------


def cmd_init(project_dir: str):
    """Scaffold a new agent-executor project directory.

    Creates:
      <project_dir>/
        executor.yaml        — default executor configuration
        agents/
          coding-agent.yaml  — starter coding agent
        prompts/             — project-level prompts (empty; falls back to package templates)
        workspace/           — agent workspace root
        input/               — task input files
        .gitignore

    Args:
        project_dir: Path to the new project directory to create
    """
    from nezha.templates import AGENTS_DIR, TEMPLATES_DIR

    target = Path(project_dir).resolve()

    if target.exists():
        # If directory already has executor.yaml, just regenerate Claude Code config
        if (target / "executor.yaml").exists():
            claude_files = generate_claude_code_config(target)
            if claude_files:
                print(f"  Regenerated Claude Code config in {target}:")
                for cf in claude_files:
                    print(f"    {cf}")
                print()
                print("  You can now run `claude` in this directory.")
                print("  Type `/` to see available skills.")
            return
        # Allow init into an empty directory
        existing = list(target.iterdir())
        if existing:
            print(t('cli.init.exists_not_empty', path=target))
            print(t('cli.init.aborting'))
            sys.exit(1)
    else:
        target.mkdir(parents=True)

    (target / "agents").mkdir(exist_ok=True)
    (target / "prompts").mkdir(exist_ok=True)
    (target / "workspace").mkdir(exist_ok=True)
    (target / "input").mkdir(exist_ok=True)

    # Copy starter executor config and apply global user preferences
    shutil.copy2(TEMPLATES_DIR / "executor.yaml", target / "executor.yaml")
    applied_keys = _apply_global_config(target / "executor.yaml")

    # Copy starter agent YAML templates
    for agent_yaml in AGENTS_DIR.glob("*.yaml"):
        shutil.copy2(agent_yaml, target / "agents" / agent_yaml.name)

    # Minimal .gitignore
    (target / ".gitignore").write_text(
        "workspace/\nstate/\n__pycache__/\n*.pyc\n.env\n.claude/settings.json\n",
        encoding="utf-8",
    )

    # Generate .env.example
    (target / ".env.example").write_text(_ENV_EXAMPLE_TEMPLATE, encoding="utf-8")

    # Generate Claude Code integration (CLAUDE.md + .claude/skills/)
    claude_files = generate_claude_code_config(target)

    name = Path(project_dir).name
    print(t('cli.init.initialized', path=target))
    print(t('cli.init.created'))
    print("    executor.yaml")
    print("    agents/coding-agent.yaml")
    print("    agents/frontend-agent.yaml")
    print("    agents/planner-agent.yaml")
    print("    agents/product-agent.yaml")
    print("    agents/pm-agent.yaml")
    print("    agents/helper-agent.yaml")
    print("    prompts/  (empty — add custom prompts here, or rely on built-in fallbacks)")
    print("    workspace/")
    print("    input/")
    print("    .env.example")
    print("    .gitignore")
    for cf in claude_files:
        print(f"    {cf}")

    if applied_keys:
        print()
        print(f"  Applied global config (~/.nezha/config.yaml):")
        for key in applied_keys:
            print(f"    {key}")
    print()

    # --- Environment checks ---
    import os
    warnings = []

    # Check nezha in PATH
    if not shutil.which("nezha"):
        # Try to find where pip installed it
        pip_bin_dirs = ["/opt/homebrew/bin", str(Path.home() / ".local" / "bin")]
        found = next((d for d in pip_bin_dirs if (Path(d) / "nezha").exists()), None)
        if found:
            warnings.append(t('cli.init.warn_agent_exec', dir=found))
        else:
            warnings.append(t('cli.init.warn_agent_exec_notfound'))

    # Check claude CLI for `nezha code`
    claude_locations = ["/usr/local/bin/claude", str(Path.home() / ".claude" / "local" / "claude")]
    claude_ok = shutil.which("claude") or any(Path(p).exists() for p in claude_locations)
    if not claude_ok:
        warnings.append(t('cli.init.warn_claude'))

    if warnings:
        print(t('cli.init.env_warnings'))
        for w in warnings:
            print(w)
        print()

    print(t('cli.init.next_steps'))
    print(t('cli.init.next_cd', name=name))
    print(t('cli.init.next_run'))
    print()
    print("  Claude Code integration:")
    print(f"    cd {name} && claude     # Launch Claude Code with skills")
    print("    Type / to see available skills (/status, /prd, /review, ...)")
    print()
    print("  To regenerate Claude Code config later:")
    print(f"    nezha init {name}  # Re-run on existing project")


# ---------------------------------------------------------------------------
# Agent-context commands
# ---------------------------------------------------------------------------

_AGENT_CONTEXT_TEMPLATE = """\
# Agent Memory

<!-- This file is the agent's cross-task persistent memory.
     The agent updates it automatically after completing tasks.
     Keep it concise — it is injected into every session prompt. -->
"""


def cmd_agent_context_init(agent_name: str, config_path: str = "executor.yaml"):
    """Create an empty agent-context.md in the agent's workspace.

    Args:
        agent_name: Agent name
        config_path: Path to executor config
    """
    workspace = _resolve_agent_workspace(agent_name, config_path)
    filepath = workspace / "agent-context.md"

    if filepath.exists():
        print(t('cli.agent_context.exists', path=filepath))
        print(t('cli.agent_context.view_hint', agent=agent_name))
        return

    workspace.mkdir(parents=True, exist_ok=True)
    filepath.write_text(_AGENT_CONTEXT_TEMPLATE, encoding="utf-8")
    print(t('cli.agent_context.created', path=filepath))
    print(t('cli.agent_context.update_hint'))


def cmd_agent_context_show(agent_name: str, config_path: str = "executor.yaml"):
    """Show agent-context.md content for an agent.

    Args:
        agent_name: Agent name
        config_path: Path to executor config
    """
    workspace = _resolve_agent_workspace(agent_name, config_path)
    filepath = workspace / "agent-context.md"

    if not filepath.exists():
        print(t('cli.agent_context.not_found', agent=agent_name))
        print(t('cli.agent_context.expected', path=filepath))
        print(t('cli.agent_context.create_hint', agent=agent_name))
        return

    print(f"[agent-context] {filepath}")
    print("=" * 60)
    print(filepath.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Project commands
# ---------------------------------------------------------------------------

_PROJECT_YAML_TEMPLATE = """\
# Project configuration
name: ""
description: ""
repo: ""
"""

_TECH_STACK_YAML_TEMPLATE = """\
# Tech stack configuration
# Fill in the technologies used in this project
# Example:
#   language: python
#   framework: fastapi
#   database: postgresql
"""

_CLAUDE_MD_TEMPLATE = """\
# Project Knowledge

<!-- Add project-specific knowledge, rules, and conventions here -->
"""

_ROADMAP_MD_TEMPLATE = """\
# Roadmap

## Current


## Backlog

"""

_QUALITY_MD_TEMPLATE = """\
# 代码质量评分

<!-- 由 evolve-agent 在每次执行后自动更新。人工也可直接编辑。-->

Last updated: (not yet scored)

## 各模块评分（1-10 分）

| 模块 | 评分 | 说明 |
|------|------|------|
| (模块名) | - | 待评估 |

## 技术债记录

<!-- 格式: - [ ] <描述> [high/medium/low] -->

"""

_PRD_TEMPLATE = """\
# PRD Template — Requirements Document Specification

This template defines the structure for requirements documents (spec.md) submitted to the Planner Agent.

## How to Use

1. Use this template as a structural reference when generating `input/spec.md` for each Feature
2. Fill in every section; mark inapplicable sections as "N/A"
3. **Functional requirements are the core** — the more specific, the better the decomposition

## Template Structure

### 1. Overview
One sentence: what to build, for whom, what problem it solves.

### 2. Technical Context
- **Tech Stack**: Language, framework, database, dependencies
- **Current State**: Greenfield / extending / refactoring; existing modules
- **Directory Structure** (if applicable)

### 3. Functional Requirements
List by module/interface. Each feature should include:
- **User scenario** or **API definition** (method, path, parameters)
- **Input/Output** (data format, field descriptions)
- **Business rules** (logic, state transitions, access control)
- **Edge cases** (null values, limits, concurrency, error paths)

### 4. Non-Functional Requirements
- Performance, Security, Compatibility, Observability (mark N/A if not applicable)

### 5. Constraints
- Out of scope features
- Technical limitations (must-use / must-not-use)
- Required standards (coding style, commit conventions)
- Dependency constraints (external API limits)

### 6. References (optional)
- API docs, design mockups, related Issues/PRs

## Key Principles
- PRD describes **what** to build, not **how** to decompose — task decomposition is the Planner's job
- Functional requirements must be **verifiable** (e.g., "POST /users returns 201")
- Technical context helps the Planner assess task complexity levels
"""

_PRD_TEMPLATE_ZH = """\
# PRD 模板 — 需求文档规范

本模板定义了提交给 Planner Agent 的需求文档（spec.md）应包含的结构和内容。

## 使用方式

1. 将此模板作为结构参考，为每个 Feature 生成 `input/spec.md`
2. 每个章节都应尽量填写，不适用的章节标注"不适用"
3. **功能需求是核心** — 描述越具体，Planner 拆解越准确

## 模板结构

### 1. 概述
一句话说明做什么、为谁做、解决什么问题。

### 2. 技术上下文
- **技术栈**：语言、框架、数据库、其他依赖
- **项目现状**：全新项目 / 扩展 / 重构；已有哪些相关模块
- **目录结构**（如适用）

### 3. 功能需求
按模块/接口逐项列出。每个功能应包含：
- **用户场景** 或 **API 定义**（方法、路径、参数）
- **输入/输出**（数据格式、字段说明）
- **业务规则**（计算逻辑、状态流转、权限控制）
- **边界条件**（空值、超限、并发、异常路径）

### 4. 非功能需求
- 性能、安全、兼容性、可观测性（不适用标注"不适用"）

### 5. 约束
- 不在本次范围内的功能
- 技术限制（必须使用/不能使用的技术）
- 必须遵守的规范（编码风格、commit 规范）
- 依赖约束（外部 API 限制、第三方服务约定）

### 6. 参考资料（可选）
- API 文档链接、设计稿、相关 Issue/PR

## 核心原则
- PRD 描述 **做什么**（What），不要描述 **怎么拆**（How）— 任务拆解是 Planner 的职责
- 功能需求要具体到 **可验证**（如"POST /users 返回 201"）
- 技术上下文帮助 Planner 判断 task 的 complexity 分级
"""


# ---------------------------------------------------------------------------
# .env.example template
# ---------------------------------------------------------------------------

_ENV_EXAMPLE_TEMPLATE = """\
# =============================================================================
# Agent Executor Environment Variables
# Copy this file to .env and fill in actual values.
# .env is in .gitignore and will NOT be committed.
# =============================================================================

# --- Anthropic API (default, used by Claude Code SDK) ---
ANTHROPIC_API_KEY=sk-ant-xxx

# --- Git HTTPS auth (for auto_push / feature push) ---
GH_TOKEN=ghp_xxx

# --- Third-party model APIs (configure as needed) ---
# Agent YAML files reference these via ${VAR} syntax, e.g.:
#   engine:
#     env:
#       ANTHROPIC_BASE_URL: "${MINIMAX_BASE_URL}"
#       ANTHROPIC_API_KEY: "${MINIMAX_API_KEY}"

# MiniMax
# MINIMAX_API_KEY=
# MINIMAX_BASE_URL=https://api.minimaxi.com/anthropic

# GLM (Zhipu)
# GLM_API_KEY=
# GLM_BASE_URL=https://open.bigmodel.cn/api/anthropic

# Kimi (Moonshot)
# KIMI_API_KEY=
# KIMI_BASE_URL=https://api.moonshot.cn/v1
"""

# ---------------------------------------------------------------------------
# Claude Code integration: CLAUDE.md + .claude/skills/
# ---------------------------------------------------------------------------

_CLAUDE_MD_PROJECT_TEMPLATE = """\
# Agent Executor Project

This is an nezha managed AI Agent orchestration project.
You are working in the project configuration directory.

## Project Configuration

@executor.yaml

## Agent Configurations
{agent_imports}

## Project Knowledge

@workspace/project/knowledge/CLAUDE.md

## Directory Structure

```
.
├── executor.yaml          # Global config (workspace, scheduler, guards, events)
├── agents/                # Agent YAML configs (one per agent)
├── prompts/               # Prompt templates (worker.md per agent)
├── workspace/             # Runtime workspace
│   ├── features/          # Feature directories (feature.yaml + task_list.json + reports)
│   └── project/           # Shared project knowledge (standards, PRD templates)
├── input/                 # Input files for features (spec.md etc.)
├── .claude/skills/        # Claude Code skills (type / to see available skills)
└── state/                 # Runtime state (executor status, logs)
```

## Key Concepts

- **Feature**: A deliverable (requirement/user story), stored in `workspace/features/<id>/`
- **Task**: A coding subtask within a feature, defined in `task_list.json`, executed by DAG engine
- **Agent**: An AI agent with specific role (planning, coding, design, management)
- Target code repository path is defined in each agent's YAML config under `target:`

## Workflow

1. Create a feature: `nezha feature create --title "xxx" --input input/spec.md`
2. Plan (generate tasks): `nezha run planner-agent`
3. Execute (auto-code): `nezha run <coding-agent>`
4. Review results: use `/review` skill or `nezha feature show <id>`
5. Rework if needed: use `/rework` skill

## Available Skills

Type `/` to see all available skills. Key skills:

**Core Workflow:**
- `/status` — View project execution status
- `/feature-list` — List all features and their status
- `/feature-show <id>` — Show feature details
- `/create-feature <title>` — Create a new feature

**Documentation:**
- `/prd <title>` — Write a PRD document
- `/architecture <module>` — Write architecture documentation

**Execution & Review:**
- `/review <feature-id>` — Review execution results
- `/rework <feature-id>` — Debug and rework failed tasks
- `/dashboard` — Generate and open visual dashboard

**Analysis & Operations:**
- `/estimate <feature-id>` — Estimate cost/time before running
- `/health` — Comprehensive project health check
- `/compare <id1> <id2>` — Compare two execution reports
- `/optimize` — Suggest configuration optimizations
- `/rollback <feature-id>` — Rollback changes from failed feature
- `/test-report [cmd]` — Run tests and analyze results
- `/batch-features [prds]` — Create chained features from multiple PRDs

## CLI Reference

```bash
nezha feature create --title "xxx"           # Create feature
nezha feature create --title "xxx" --input input/spec.md  # Create with input
nezha feature list                            # List features
nezha feature list --status partial           # Filter by status
nezha feature show <feature-id>               # Show details
nezha feature approve <id> <step-id>          # Approve a step
nezha feature reject <id> <step-id> --note "" # Reject a step
nezha run <agent-name>                        # Run agent (auto mode)
nezha run <agent> --feature-id <id>           # Run on specific feature
nezha status                                  # Execution status
nezha dashboard --open                        # Visual dashboard
nezha logs -f                                 # Follow logs
nezha project init                            # Init project knowledge
```
"""

_SKILL_STATUS = """\
---
name: status
description: View nezha project execution status and feature overview
user-invocable: true
---

## Execution Status

!`nezha status`

## Feature Overview

!`nezha feature list`
"""

_SKILL_FEATURE_LIST = """\
---
name: feature-list
description: List all features with status, priority, and cost
user-invocable: true
---

## All Features

!`nezha feature list`

Analyze the feature list and help the user decide next steps.
"""

_SKILL_FEATURE_SHOW = """\
---
name: feature-show
description: Show detailed information about a specific feature
user-invocable: true
argument-hint: [feature-id]
---

## Feature Details

!`nezha feature show $ARGUMENTS`
"""

_SKILL_CREATE_FEATURE = """\
---
name: create-feature
description: Create a new feature (requirement/deliverable)
user-invocable: true
argument-hint: [title]
---

Create a new feature for the user.

If the user provided a title in `$ARGUMENTS`, run:
```bash
nezha feature create --title "$ARGUMENTS"
```

If no title was provided, ask the user for:
1. Feature title (required)
2. Whether they have an input spec file (optional)
3. Priority (optional, default 5)

For features with input files:
```bash
nezha feature create --title "<title>" --input <path-to-spec>
```

After creation, suggest next steps:
- Write a PRD with `/prd` if requirements need documentation
- Run planner to generate tasks: `nezha run planner-agent`
"""

_SKILL_PRD = """\
---
name: prd
description: Write a PRD (Product Requirements Document) following project template
user-invocable: true
argument-hint: [requirement title or description]
---

## PRD Template

!`cat workspace/project/prd-template.md`

## Existing PRDs

!`ls workspace/project/prds/`

## Project Tech Stack

!`cat workspace/project/tech_stack.yaml`

## Instructions

Write a PRD for: **$ARGUMENTS**

1. Follow the template structure above
2. Output directory: `workspace/project/prds/`
3. Filename format: `<date>-<short-title>.md` (e.g. `2026-03-15-user-auth.md`)
4. Ask the user clarifying questions if the requirement is vague
5. After writing, suggest:
   - Create a feature: `nezha feature create --title "<title>" --input workspace/project/prds/<filename>`
   - Or create an input spec: copy key sections to `input/spec.md`
"""

_SKILL_ARCHITECTURE = """\
---
name: architecture
description: Write or update architecture and design documentation
user-invocable: true
argument-hint: [module or topic]
---

## Existing Architecture Documents

!`ls workspace/project/standards/`

## Project Tech Stack

!`cat workspace/project/tech_stack.yaml`

## Agent Configurations (for understanding project structure)

!`grep -rn -E "name:|category:|target:" agents/`

## Instructions

Write or update architecture documentation for: **$ARGUMENTS**

1. Output directory: `workspace/project/standards/`
2. Include: module responsibilities, core interfaces, data flow, dependencies
3. If updating an existing doc, read it first and preserve existing content
4. These documents are shared with all coding agents as reference
5. If the target code repository exists, explore it first to understand current architecture
"""

_SKILL_REVIEW = """\
---
name: review
description: Review feature execution results, analyze successes and failures
user-invocable: true
argument-hint: [feature-id]
---

## Feature Status

!`nezha feature show $ARGUMENTS`

## Execution Report

!`cat workspace/features/$ARGUMENTS/execution-report.md`

## Task List Progress

!`cat workspace/features/$ARGUMENTS/task_list.json`

## Analysis

Please analyze the execution results:
1. Summary: how many tasks succeeded vs failed
2. For each failed task: root cause analysis
3. Suggested fix approach for failures
4. Whether to rework (use `/rework`) or accept partial results
5. Overall quality assessment of completed work
"""

_SKILL_REWORK = """\
---
name: rework
description: Debug and rework failed tasks in a feature
user-invocable: true
argument-hint: [feature-id]
---

## Feature Status

!`nezha feature show $ARGUMENTS`

## Execution Report

!`cat workspace/features/$ARGUMENTS/execution-report.md`

## Task List

!`cat workspace/features/$ARGUMENTS/task_list.json`

## Instructions

Help the user debug and fix failed tasks:

1. Identify which tasks failed and why (read error details above)
2. Check the target code repository for the relevant code
3. Work with the user to understand and fix the root cause
4. After fixing, the user can re-run: `nezha run <agent> --feature-id $ARGUMENTS`
5. Or mark specific tasks for rework: `nezha rework <agent> --feature-id $ARGUMENTS --task <task-id>`
"""

_SKILL_DASHBOARD = """\
---
name: dashboard
description: Generate and open the visual project dashboard
user-invocable: true
---

Generate the project dashboard:

```bash
nezha dashboard --open
```

If `--open` doesn't work in the current environment, generate without opening:

```bash
nezha dashboard
```

Then tell the user the dashboard file location (usually `state/dashboard.html`).
"""

_SKILL_ESTIMATE = """\
---
name: estimate
description: Estimate cost, time, and session count before running a feature
user-invocable: true
argument-hint: [feature-id]
---

## Feature Info

!`nezha feature show $ARGUMENTS`

## Task List

!`cat workspace/features/$ARGUMENTS/task_list.json`

## Model Map Configuration

!`grep -A 10 'model_map:' executor.yaml`

## Historical Reference (recent completed features)

!`ls workspace/features/`

## Instructions

Estimate the execution cost for this feature:

1. Count tasks by complexity level (low/medium/high)
2. Estimate sessions per task (typically 1-3 for low, 2-5 for medium, 3-8 for high)
3. Estimate cost per session based on model_map (Sonnet ~$0.05-0.15/session, Opus ~$0.30-1.00/session)
4. Calculate total: estimated sessions x cost per session
5. Estimate wall-clock time: sessions x avg session duration (~2-5 min)
6. Compare with historical data from previous features if available
7. Flag any high-risk tasks that might need rework (multiplier: 1.5x-2x)

Present as a summary table.
"""

_SKILL_HEALTH = """\
---
name: health
description: Comprehensive project health check — features, costs, failures, pending work
user-invocable: true
---

## Feature Status Overview

!`nezha feature list`

## Execution Status

!`nezha status`

## Recent Execution History

!`nezha history`

## Cost Summary

Read the execution-report.md files in workspace/features/*/execution-report.md to calculate total cost across all features.

## Failed / Partial Features

!`nezha feature list --status failed`

## Instructions

Provide a project health report:

1. **Status Summary**: Total features, completion rate (completed / total)
2. **Cost Summary**: Total spend, average cost per feature, trend
3. **Failure Analysis**: Any failed/partial features, common failure patterns
4. **Pending Work**: Features still in pending/running state
5. **Recommendations**: Configuration tuning, priority adjustments, risk areas
"""

_SKILL_ROLLBACK = """\
---
name: rollback
description: Rollback changes from a failed feature execution
user-invocable: true
argument-hint: [feature-id]
---

## Feature Details

!`nezha feature show $ARGUMENTS`

## Task List (to identify branches)

!`cat workspace/features/$ARGUMENTS/task_list.json`

## Target Repository Info

!`grep -rn "target:" agents/`

## Instructions

Help the user rollback changes from a failed feature:

1. Identify the target code repository from agent configs above
2. List branches created for this feature: `git branch --list "*$ARGUMENTS*"` in the target repo
3. Show what changes were made: `git log --oneline <branch>` for each branch
4. Offer rollback options:
   - **Soft rollback**: Delete feature branches, keep main untouched
   - **Hard rollback**: `git revert` specific commits if already merged
   - **Selective**: Keep successful task branches, only remove failed ones
5. **Always confirm with the user before executing any destructive git operations**
6. After rollback, update feature status if needed
"""

_SKILL_COMPARE = """\
---
name: compare
description: Compare execution reports between two features or two runs
user-invocable: true
argument-hint: [feature-id-1 feature-id-2]
---

## Report 1

!`cat workspace/features/$0/execution-report.md`

## Report 2

!`cat workspace/features/$1/execution-report.md`

## Instructions

Compare the two execution reports:

1. **Completion**: Tasks completed in each (X/Y vs X/Y)
2. **Cost**: Total cost comparison, cost per task
3. **Duration**: Total time, average session time
4. **Quality**: Which had fewer failures/reworks
5. **Model Usage**: If different models were used, compare effectiveness
6. Present as a side-by-side comparison table
"""

_SKILL_OPTIMIZE = """\
---
name: optimize
description: Analyze execution history and suggest configuration optimizations
user-invocable: true
---

## Current Configuration

!`cat executor.yaml`

## Model Map

!`grep -A 15 'model_map:' executor.yaml`

## All Execution Reports

Read execution-report.md files from workspace/features/ subdirectories to analyze execution history.

## Task Complexity Distribution

Read task_list.json files from workspace/features/ subdirectories to count task complexity distribution.

## Instructions

Analyze execution history and suggest optimizations:

1. **Model Map Tuning**:
   - Are low-complexity tasks over-served by expensive models? Suggest downgrade
   - Are high-complexity tasks failing frequently? Suggest stronger model
   - Compare cost-effectiveness across complexity levels

2. **Scheduler Settings**:
   - Current concurrency: is it optimal based on feature independence?
   - Interval/backoff: too aggressive or too conservative?

3. **Task Factor Tuning**:
   - Are tasks too granular (many tiny tasks) or too coarse (few large tasks)?
   - Suggest task_factor adjustments per complexity level

4. **Guard Settings**:
   - Cost limits: too tight (jobs hitting limits) or too loose?
   - Time window: appropriate for the workload?

5. Present as actionable recommendations with specific YAML changes.
"""

_SKILL_TEST_REPORT = """\
---
name: test-report
description: Run tests in the target repository and analyze results
user-invocable: true
argument-hint: [test command or path]
---

## Target Repositories

!`grep -rn "target:" agents/`

## Instructions

Run tests and analyze results:

1. If the user provided a specific test command in `$ARGUMENTS`, use that
2. Otherwise, detect the test framework from the target repo:
   - Python: `pytest`, `python -m pytest`
   - Java: `mvn test`, `gradle test`
   - Node.js: `npm test`, `jest`
   - Go: `go test ./...`
3. Run the tests and capture output
4. Analyze results:
   - Total tests, passed, failed, skipped
   - For each failure: test name, error message, likely cause
   - Suggest fixes for failing tests
5. If tests are related to a specific feature, cross-reference with the task list
"""

# ---------------------------------------------------------------------------
# Chinese (zh_CN) versions of CLAUDE.md + skills
# ---------------------------------------------------------------------------

_CLAUDE_MD_PROJECT_TEMPLATE_ZH = """\
# Agent Executor 项目

这是一个 nezha 管理的 AI Agent 编排项目。
你正在项目配置目录中工作。

## 项目配置

@executor.yaml

## Agent 配置
{agent_imports}

## 项目知识

@workspace/project/knowledge/CLAUDE.md

## 目录结构

```
.
├── executor.yaml          # 全局配置（workspace、scheduler、guards、events）
├── agents/                # Agent YAML 配置（每个 agent 一个文件）
├── prompts/               # Prompt 模板（每个 agent 的 worker.md）
├── workspace/             # 运行时工作区
│   ├── features/          # Feature 目录（feature.yaml + task_list.json + 报告）
│   └── project/           # 共享项目知识（规范、PRD 模板）
├── input/                 # Feature 输入文件（spec.md 等）
├── .claude/skills/        # Claude Code 技能（输入 / 查看可用技能）
└── state/                 # 运行时状态（执行状态、日志）
```

## 核心概念

- **Feature**：交付物（需求/用户故事），存储在 `workspace/features/<id>/`
- **Task**：Feature 中的编码子任务，定义在 `task_list.json`，由 DAG 引擎调度执行
- **Agent**：具有特定角色的 AI Agent（规划、编码、设计、管理）
- Target 代码仓库路径定义在各 agent YAML 配置的 `target:` 字段中

## 工作流程

1. 创建 feature：`nezha feature create --title "xxx" --input input/spec.md`
2. 规划（生成任务）：`nezha run planner-agent`
3. 执行（自动编码）：`nezha run <coding-agent>`
4. 评审结果：使用 `/review` 技能或 `nezha feature show <id>`
5. 需要返工：使用 `/rework` 技能

## 可用技能

输入 `/` 查看所有可用技能。主要技能：

**核心工作流：**
- `/status` — 查看项目执行状态
- `/feature-list` — 列出所有 feature 及状态
- `/feature-show <id>` — 查看 feature 详情
- `/create-feature <标题>` — 创建新 feature

**文档编写：**
- `/prd <标题>` — 编写 PRD 文档
- `/architecture <模块>` — 编写架构文档

**执行与评审：**
- `/review <feature-id>` — 评审执行结果
- `/rework <feature-id>` — 调试和返工失败任务
- `/dashboard` — 生成并打开可视化面板

**分析与运维：**
- `/estimate <feature-id>` — 执行前预估费用/时间
- `/health` — 项目健康检查
- `/compare <id1> <id2>` — 对比两次执行报告
- `/optimize` — 配置优化建议
- `/rollback <feature-id>` — 回滚失败 feature 的变更
- `/test-report [命令]` — 运行测试并分析结果
- `/batch-features [PRD文件]` — 从多个 PRD 创建链式 feature

## CLI 命令参考

```bash
nezha feature create --title "xxx"           # 创建 feature
nezha feature create --title "xxx" --input input/spec.md  # 带输入文件创建
nezha feature list                            # 列出 features
nezha feature list --status partial           # 按状态筛选
nezha feature show <feature-id>               # 查看详情
nezha feature approve <id> <step-id>          # 审批步骤
nezha feature reject <id> <step-id> --note "" # 驳回步骤
nezha run <agent-name>                        # 运行 agent（自动模式）
nezha run <agent> --feature-id <id>           # 运行指定 feature
nezha status                                  # 执行状态
nezha dashboard --open                        # 可视化面板
nezha logs -f                                 # 跟踪日志
nezha project init                            # 初始化项目知识库
```
"""

_SKILL_STATUS_ZH = """\
---
name: status
description: 查看 nezha 项目执行状态和 feature 概览
user-invocable: true
---

## 执行状态

!`nezha status`

## Feature 概览

!`nezha feature list`
"""

_SKILL_FEATURE_LIST_ZH = """\
---
name: feature-list
description: 列出所有 feature 的状态、优先级和费用
user-invocable: true
---

## 所有 Feature

!`nezha feature list`

分析 feature 列表，帮助用户决定下一步操作。
"""

_SKILL_FEATURE_SHOW_ZH = """\
---
name: feature-show
description: 查看指定 feature 的详细信息
user-invocable: true
argument-hint: [feature-id]
---

## Feature 详情

!`nezha feature show $ARGUMENTS`
"""

_SKILL_CREATE_FEATURE_ZH = """\
---
name: create-feature
description: 创建新的 feature（需求/交付物）
user-invocable: true
argument-hint: [标题]
---

为用户创建新 feature。

如果用户在 `$ARGUMENTS` 中提供了标题，执行：
```bash
nezha feature create --title "$ARGUMENTS"
```

如果没有提供标题，询问用户：
1. Feature 标题（必填）
2. 是否有输入规格文件（可选）
3. 优先级（可选，默认 5）

对于有输入文件的 feature：
```bash
nezha feature create --title "<标题>" --input <规格文件路径>
```

创建后，建议下一步：
- 使用 `/prd` 编写 PRD（如果需求需要文档化）
- 运行 planner 生成任务：`nezha run planner-agent`
"""

_SKILL_PRD_ZH = """\
---
name: prd
description: 按照项目模板编写 PRD（产品需求文档）
user-invocable: true
argument-hint: [需求标题或描述]
---

## PRD 模板

!`cat workspace/project/prd-template.zh.md`

## 已有 PRD

!`ls workspace/project/prds/`

## 项目技术栈

!`cat workspace/project/tech_stack.yaml`

## 指引

为以下需求编写 PRD：**$ARGUMENTS**

1. 遵循上面的模板结构
2. 输出目录：`workspace/project/prds/`
3. 文件名格式：`<日期>-<简短标题>.md`（如 `2026-03-15-用户认证.md`）
4. 如果需求描述模糊，向用户提出澄清问题
5. 写完后建议：
   - 创建 feature：`nezha feature create --title "<标题>" --input workspace/project/prds/<文件名>`
   - 或创建输入规格：将关键部分复制到 `input/spec.md`
"""

_SKILL_ARCHITECTURE_ZH = """\
---
name: architecture
description: 编写或更新架构设计文档
user-invocable: true
argument-hint: [模块或主题]
---

## 现有架构文档

!`ls workspace/project/standards/`

## 项目技术栈

!`cat workspace/project/tech_stack.yaml`

## Agent 配置（了解项目结构）

!`grep -rn -E "name:|category:|target:" agents/`

## 指引

为以下模块编写或更新架构文档：**$ARGUMENTS**

1. 输出目录：`workspace/project/standards/`
2. 包含：模块职责、核心接口、数据流、依赖关系
3. 如果是更新已有文档，先读取现有内容再修改
4. 这些文档会被所有 coding agent 共享作为编码参考
5. 如果 target 代码仓库存在，先探索了解当前架构
"""

_SKILL_REVIEW_ZH = """\
---
name: review
description: 评审 feature 执行结果，分析成功和失败的任务
user-invocable: true
argument-hint: [feature-id]
---

## Feature 状态

!`nezha feature show $ARGUMENTS`

## 执行报告

!`cat workspace/features/$ARGUMENTS/execution-report.md`

## 任务列表进度

!`cat workspace/features/$ARGUMENTS/task_list.json`

## 分析要求

请分析执行结果：
1. 摘要：多少任务成功 vs 失败
2. 每个失败任务的根因分析
3. 建议的修复方案
4. 是否需要返工（使用 `/rework`）还是接受部分结果
5. 已完成工作的整体质量评估
"""

_SKILL_REWORK_ZH = """\
---
name: rework
description: 对失败任务进行调试和返工
user-invocable: true
argument-hint: [feature-id]
---

## Feature 状态

!`nezha feature show $ARGUMENTS`

## 执行报告

!`cat workspace/features/$ARGUMENTS/execution-report.md`

## 任务列表

!`cat workspace/features/$ARGUMENTS/task_list.json`

## 指引

帮助用户调试和修复失败的任务：

1. 识别哪些任务失败及原因（参考上面的错误详情）
2. 检查 target 代码仓库中的相关代码
3. 和用户一起理解和修复根因
4. 修复后，用户可以重新执行：`nezha run <agent> --feature-id $ARGUMENTS`
5. 或标记特定任务返工：`nezha rework <agent> --feature-id $ARGUMENTS --task <task-id>`
"""

_SKILL_DASHBOARD_ZH = """\
---
name: dashboard
description: 生成并打开项目可视化面板
user-invocable: true
---

生成项目 Dashboard：

```bash
nezha dashboard --open
```

如果当前环境无法自动打开浏览器，不加 --open 生成：

```bash
nezha dashboard
```

然后告诉用户 Dashboard 文件位置（通常是 `state/dashboard.html`）。
"""

_SKILL_ESTIMATE_ZH = """\
---
name: estimate
description: 执行前预估 feature 的费用、时间和 session 数
user-invocable: true
argument-hint: [feature-id]
---

## Feature 信息

!`nezha feature show $ARGUMENTS`

## 任务列表

!`cat workspace/features/$ARGUMENTS/task_list.json`

## Model Map 配置

!`grep -A 10 'model_map:' executor.yaml`

## 历史参考（最近完成的 feature）

!`ls workspace/features/`

## 指引

预估此 feature 的执行成本：

1. 按复杂度级别（low/medium/high）统计任务数
2. 估算每个任务的 session 数（low 通常 1-3，medium 2-5，high 3-8）
3. 根据 model_map 估算每个 session 的费用（Sonnet ~$0.05-0.15/session，Opus ~$0.30-1.00/session）
4. 计算总费用：预估 session 数 x 每 session 费用
5. 估算耗时：session 数 x 平均 session 时长（约 2-5 分钟）
6. 如有历史数据，与之前的 feature 对比
7. 标注可能需要返工的高风险任务（乘数：1.5x-2x）

以汇总表格形式呈现。
"""

_SKILL_HEALTH_ZH = """\
---
name: health
description: 项目健康检查 — feature 状态、费用、失败率、待办
user-invocable: true
---

## Feature 状态概览

!`nezha feature list`

## 执行状态

!`nezha status`

## 最近执行历史

!`nezha history`

## 费用汇总

读取 workspace/features/ 下各 feature 的 execution-report.md 文件，汇总计算总费用。

## 失败/部分完成的 Feature

!`nezha feature list --status failed`

## 指引

提供项目健康报告：

1. **状态摘要**：总 feature 数、完成率（已完成 / 总数）
2. **费用摘要**：总支出、平均每 feature 费用、趋势
3. **失败分析**：失败/部分完成的 feature，常见失败模式
4. **待办工作**：仍在 pending/running 状态的 feature
5. **建议**：配置调优、优先级调整、风险区域
"""

_SKILL_ROLLBACK_ZH = """\
---
name: rollback
description: 回滚失败 feature 执行的代码变更
user-invocable: true
argument-hint: [feature-id]
---

## Feature 详情

!`nezha feature show $ARGUMENTS`

## 任务列表（用于识别分支）

!`cat workspace/features/$ARGUMENTS/task_list.json`

## Target 仓库信息

!`grep -rn "target:" agents/`

## 指引

帮助用户回滚失败 feature 的变更：

1. 从上面的 agent 配置中识别 target 代码仓库
2. 列出为此 feature 创建的分支：在 target 仓库中执行 `git branch --list "*$ARGUMENTS*"`
3. 查看做了哪些变更：对每个分支执行 `git log --oneline <branch>`
4. 提供回滚选项：
   - **软回滚**：删除 feature 分支，保持 main 不动
   - **硬回滚**：如果已合并，`git revert` 特定提交
   - **选择性回滚**：保留成功的任务分支，只移除失败的
5. **执行任何破坏性 git 操作前必须和用户确认**
6. 回滚后，必要时更新 feature 状态
"""

_SKILL_COMPARE_ZH = """\
---
name: compare
description: 对比两个 feature 或两次执行的报告
user-invocable: true
argument-hint: [feature-id-1 feature-id-2]
---

## 报告 1

!`cat workspace/features/$0/execution-report.md`

## 报告 2

!`cat workspace/features/$1/execution-report.md`

## 指引

对比两份执行报告：

1. **完成度**：各自完成的任务数（X/Y vs X/Y）
2. **费用**：总费用对比，每任务费用
3. **时长**：总耗时，平均 session 时长
4. **质量**：哪个失败/返工更少
5. **模型使用**：如果使用了不同模型，对比效果

以并排对比表格呈现。
"""

_SKILL_OPTIMIZE_ZH = """\
---
name: optimize
description: 分析执行历史并建议配置优化
user-invocable: true
---

## 当前配置

!`cat executor.yaml`

## Model Map

!`grep -A 15 'model_map:' executor.yaml`

## 所有执行报告

读取 workspace/features/ 下各 feature 的 execution-report.md 文件来分析执行历史。

## 任务复杂度分布

读取 workspace/features/ 下各 feature 的 task_list.json 文件来统计任务复杂度分布。

## 指引

分析执行历史并建议优化：

1. **Model Map 调优**：
   - 低复杂度任务是否用了过贵的模型？建议降级
   - 高复杂度任务是否频繁失败？建议升级模型
   - 对比各复杂度级别的性价比

2. **调度器设置**：
   - 当前并发数：基于 feature 独立性是否最优？
   - 间隔/退避：是否过于激进或保守？

3. **Task Factor 调优**：
   - 任务是否过于细碎（大量小任务）或过于粗糙（少量大任务）？
   - 按复杂度级别建议 task_factor 调整

4. **Guard 设置**：
   - 费用限制：是否太紧（任务触达限制）或太松？
   - 时间窗口：是否适合当前工作量？

5. 以可操作的建议呈现，包含具体的 YAML 配置变更。
"""

_SKILL_TEST_REPORT_ZH = """\
---
name: test-report
description: 在 target 仓库中运行测试并分析结果
user-invocable: true
argument-hint: [测试命令或路径]
---

## Target 仓库

!`grep -rn "target:" agents/`

## 指引

运行测试并分析结果：

1. 如果用户在 `$ARGUMENTS` 中提供了具体的测试命令，使用该命令
2. 否则，从 target 仓库检测测试框架：
   - Python：`pytest`、`python -m pytest`
   - Java：`mvn test`、`gradle test`
   - Node.js：`npm test`、`jest`
   - Go：`go test ./...`
3. 运行测试并捕获输出
4. 分析结果：
   - 总测试数、通过、失败、跳过
   - 每个失败：测试名、错误信息、可能原因
   - 建议修复方案
5. 如果测试与特定 feature 相关，与任务列表交叉对照
"""

_SKILL_BATCH_FEATURES = """\
---
name: batch-features
description: Create chained features from multiple PRD documents, each branching from the previous
user-invocable: true
argument-hint: [prd files or directory]
---

## Available PRD Documents

!`ls workspace/project/prds/`

## Current Features

!`nezha feature list`

## Agent Git Config (branch naming)

!`grep -rn -E "branch_prefix|base_branch" agents/`

## Instructions

Create a chain of features from multiple PRD documents. Each feature's branch is based on the previous feature's branch, forming a linear dependency chain.

### Workflow

1. **Identify PRD files**: From `$ARGUMENTS` or ask the user which PRDs to use and in what order.
   - Confirm the execution order with the user before proceeding

2. **Create features in sequence**:
   - First feature (uses default base branch):
     ```bash
     nezha feature create --title "<title-from-prd-1>" --input <prd-1-path>
     ```
   - Capture the feature ID from output (format: `YYYY-MM-DD-HH-MM-SS-<slug>`)

   - Second feature onward (chain from previous):
     ```bash
     nezha feature create --title "<title-from-prd-2>" --input <prd-2-path> --base-branch feat/<previous-feature-id>
     ```

3. **Summary**: After all features are created, show the chain and suggest next steps:
   - `nezha run planner-agent` to generate task lists
   - `nezha run <coding-agent>` to execute (continuous scheduler handles ordering)

### Key Points
- Branch convention: `feat/<feature-id>` (verify prefix in agent git config above)
- `--base-branch` makes each feature branch from the previous, so changes stack
- Continuous scheduler executes features in priority/creation order automatically
"""

_SKILL_BATCH_FEATURES_ZH = """\
---
name: batch-features
description: 从多个 PRD 文档创建链式 feature，每个分支基于上一个
user-invocable: true
argument-hint: [PRD 文件或目录]
---

## 可用 PRD 文档

!`ls workspace/project/prds/`

## 当前 Feature

!`nezha feature list`

## Agent Git 配置（分支命名）

!`grep -rn -E "branch_prefix|base_branch" agents/`

## 指引

从多个 PRD 文档创建链式 feature。每个 feature 的分支基于上一个 feature 的分支，形成线性依赖链。

### 工作流程

1. **确定 PRD 文件**：从 `$ARGUMENTS` 或询问用户要用哪些 PRD、以什么顺序执行。
   - 开始前和用户确认执行顺序

2. **按顺序创建 feature**：
   - 第一个 feature（使用默认 base branch）：
     ```bash
     nezha feature create --title "<PRD-1-标题>" --input <prd-1-路径>
     ```
   - 从输出中获取 feature ID（格式：`YYYY-MM-DD-HH-MM-SS-<slug>`）

   - 第二个及后续 feature（链式分支）：
     ```bash
     nezha feature create --title "<PRD-2-标题>" --input <prd-2-路径> --base-branch feat/<上一个-feature-id>
     ```

3. **汇总**：所有 feature 创建完成后，展示链路关系并建议下一步：
   - `nezha run planner-agent` 为所有 feature 生成任务列表
   - `nezha run <coding-agent>` 执行（continuous 调度器自动按顺序处理）

### 要点
- 分支约定：`feat/<feature-id>`（检查上面的 agent git 配置确认实际前缀）
- `--base-branch` 使每个 feature 从上一个分支创建，代码变更层层叠加
- Continuous 调度器按优先级/创建顺序自动执行
"""

# All skills: (directory_name, en_content, zh_content)
_SKILLS = [
    ("status", _SKILL_STATUS, _SKILL_STATUS_ZH),
    ("feature-list", _SKILL_FEATURE_LIST, _SKILL_FEATURE_LIST_ZH),
    ("feature-show", _SKILL_FEATURE_SHOW, _SKILL_FEATURE_SHOW_ZH),
    ("create-feature", _SKILL_CREATE_FEATURE, _SKILL_CREATE_FEATURE_ZH),
    ("prd", _SKILL_PRD, _SKILL_PRD_ZH),
    ("architecture", _SKILL_ARCHITECTURE, _SKILL_ARCHITECTURE_ZH),
    ("review", _SKILL_REVIEW, _SKILL_REVIEW_ZH),
    ("rework", _SKILL_REWORK, _SKILL_REWORK_ZH),
    ("dashboard", _SKILL_DASHBOARD, _SKILL_DASHBOARD_ZH),
    ("estimate", _SKILL_ESTIMATE, _SKILL_ESTIMATE_ZH),
    ("health", _SKILL_HEALTH, _SKILL_HEALTH_ZH),
    ("rollback", _SKILL_ROLLBACK, _SKILL_ROLLBACK_ZH),
    ("compare", _SKILL_COMPARE, _SKILL_COMPARE_ZH),
    ("optimize", _SKILL_OPTIMIZE, _SKILL_OPTIMIZE_ZH),
    ("test-report", _SKILL_TEST_REPORT, _SKILL_TEST_REPORT_ZH),
    ("batch-features", _SKILL_BATCH_FEATURES, _SKILL_BATCH_FEATURES_ZH),
]


def _detect_locale(project_dir: Path) -> str:
    """Detect locale from global config or executor.yaml."""
    # 1. Global config
    global_cfg = load_global_config()
    locale = global_cfg.get("locale", "")
    if locale:
        return locale

    # 2. executor.yaml in project dir
    executor_yaml = project_dir / "executor.yaml"
    if executor_yaml.exists():
        try:
            import yaml
            with open(executor_yaml, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            locale = data.get("locale", "")
            if locale:
                return locale
        except Exception:
            pass

    return "en"


def generate_claude_code_config(project_dir: Path) -> list[str]:
    """Generate CLAUDE.md and .claude/skills/ for Claude Code integration.

    Locale-aware: generates Chinese versions when locale is zh_CN/zh.

    Args:
        project_dir: Root of the nezha project (where executor.yaml lives).

    Returns:
        List of generated file paths (relative to project_dir).
    """
    generated = []
    locale = _detect_locale(project_dir)
    use_zh = locale.startswith("zh")

    # Select templates based on locale
    claude_md_template = _CLAUDE_MD_PROJECT_TEMPLATE_ZH if use_zh else _CLAUDE_MD_PROJECT_TEMPLATE
    # Marker text for detecting our template (present in both EN and ZH)
    template_markers = ("Agent Executor Project", "Agent Executor 项目")

    # Discover agent YAML files for @import in CLAUDE.md
    agents_dir = project_dir / "agents"
    agent_imports = ""
    if agents_dir.exists():
        agent_files = sorted(agents_dir.glob("*.yaml"))
        if agent_files:
            lines = [f"@agents/{f.name}" for f in agent_files]
            agent_imports = "\n".join(lines)
    if not agent_imports:
        no_agents_msg = "（暂无 agent 配置 — 请在 agents/ 下添加 YAML 文件）" if use_zh else "(no agent configs found yet — add YAML files to agents/)"
        agent_imports = no_agents_msg

    # Generate CLAUDE.md (skip if user already has one with custom content)
    claude_md_path = project_dir / "CLAUDE.md"
    if claude_md_path.exists():
        existing = claude_md_path.read_text(encoding="utf-8").strip()
        # Only skip if it has substantial custom content (not our template or empty)
        if existing and not any(m in existing for m in template_markers):
            print(f"  Skipping CLAUDE.md (already exists with custom content)")
        else:
            claude_md_path.write_text(
                claude_md_template.format(agent_imports=agent_imports),
                encoding="utf-8",
            )
            generated.append("CLAUDE.md")
    else:
        claude_md_path.write_text(
            claude_md_template.format(agent_imports=agent_imports),
            encoding="utf-8",
        )
        generated.append("CLAUDE.md")

    # Generate .claude/settings.json — pre-authorize commands used in skills
    settings_path = project_dir / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if not settings_path.exists():
        settings = {
            "permissions": {
                "allow": [
                    "Bash(nezha *)",
                    "Bash(cat *)",
                    "Bash(ls *)",
                    "Bash(grep *)",
                ],
            },
        }
        settings_path.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        generated.append(".claude/settings.json")
    else:
        # Merge: ensure nezha is in the allow list
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
            perms = existing.setdefault("permissions", {})
            allow = perms.setdefault("allow", [])
            required = ["Bash(nezha *)"]
            added = False
            for rule in required:
                if rule not in allow:
                    allow.append(rule)
                    added = True
            if added:
                settings_path.write_text(
                    json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                generated.append(".claude/settings.json (updated)")
        except Exception:
            pass  # Don't break on malformed settings

    # Generate .claude/skills/
    skills_dir = project_dir / ".claude" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    for skill_name, skill_en, skill_zh in _SKILLS:
        skill_content = skill_zh if use_zh else skill_en
        skill_path = skills_dir / skill_name / "SKILL.md"
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(skill_content, encoding="utf-8")
        generated.append(f".claude/skills/{skill_name}/SKILL.md")

    return generated


def cmd_project_init(config_path: str = "executor.yaml"):
    """Create the project-level shared knowledge directory structure.

    Creates <workspace.base>/project/ with template files:
      - project.yaml   (name, description, repo)
      - tech_stack.yaml (empty template)
      - standards/.gitkeep
      - knowledge/CLAUDE.md
      - roadmap.md

    Skips (does not overwrite) if the project directory already exists.
    """
    from nezha.config import load_executor_config

    base_dir = Path(config_path).parent.resolve()
    executor_config = load_executor_config(config_path)

    ws_base = Path(executor_config.workspace.base)
    if not ws_base.is_absolute():
        ws_base = base_dir / ws_base

    project_dir = ws_base / "project"

    if project_dir.exists():
        print(t('cli.project.exists', path=project_dir))
        print(t('cli.project.skip'))
        return

    # Create directory structure
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "standards").mkdir(parents=True, exist_ok=True)
    (project_dir / "knowledge").mkdir(parents=True, exist_ok=True)

    # Write template files
    (project_dir / "project.yaml").write_text(_PROJECT_YAML_TEMPLATE, encoding="utf-8")
    (project_dir / "tech_stack.yaml").write_text(_TECH_STACK_YAML_TEMPLATE, encoding="utf-8")
    (project_dir / "standards" / ".gitkeep").write_text("", encoding="utf-8")
    (project_dir / "knowledge" / "CLAUDE.md").write_text(_CLAUDE_MD_TEMPLATE, encoding="utf-8")
    (project_dir / "roadmap.md").write_text(_ROADMAP_MD_TEMPLATE, encoding="utf-8")
    (project_dir / "quality.md").write_text(_QUALITY_MD_TEMPLATE, encoding="utf-8")
    (project_dir / "prd-template.md").write_text(_PRD_TEMPLATE, encoding="utf-8")
    (project_dir / "prd-template.zh.md").write_text(_PRD_TEMPLATE_ZH, encoding="utf-8")

    print(t('cli.project.initialized', path=project_dir))
    print(t('cli.project.created'))
    print("    project.yaml")
    print("    tech_stack.yaml")
    print("    standards/.gitkeep")
    print("    knowledge/CLAUDE.md")
    print("    roadmap.md")
    print("    quality.md")
    print("    prd-template.md")
    print("    prd-template.zh.md")

    # Update .claude/settings.json with target directories from agent configs
    _sync_targets_to_claude_settings(base_dir, executor_config)


def _sync_targets_to_claude_settings(
    base_dir: Path,
    executor_config,
) -> None:
    """Scan agent YAML configs for target paths and write them into
    .claude/settings.json ``additionalDirectories`` so that Claude Code
    can access target repos without repeated permission prompts.
    """
    import yaml as _yaml

    agents_dir_str = getattr(executor_config, "agents_dir", None) or "./agents"
    agents_dir = Path(agents_dir_str)
    if not agents_dir.is_absolute():
        agents_dir = base_dir / agents_dir

    # Collect unique, resolved target paths
    targets: list[str] = []

    # Project-level target from executor.yaml
    project_target = getattr(executor_config, "target", None)
    if project_target:
        p = Path(project_target)
        if not p.is_absolute():
            p = base_dir / p
        resolved = str(p.resolve())
        if resolved not in targets:
            targets.append(resolved)

    # Agent-level target overrides
    for agent_yaml in sorted(agents_dir.glob("*.yaml")):
        try:
            with open(agent_yaml, encoding="utf-8") as f:
                raw = _yaml.safe_load(f) or {}
            target = raw.get("target")
            if not target:
                continue
            p = Path(target)
            if not p.is_absolute():
                p = base_dir / p
            resolved = str(p.resolve())
            if resolved not in targets:
                targets.append(resolved)
        except Exception:
            continue

    if not targets:
        return

    settings_path = base_dir / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing or create new
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    else:
        settings = {}

    existing_dirs = settings.get("additionalDirectories", [])
    added = []
    for t_path in targets:
        if t_path not in existing_dirs:
            existing_dirs.append(t_path)
            added.append(t_path)

    if not added:
        return

    settings["additionalDirectories"] = existing_dirs
    settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\n  Updated .claude/settings.json — additionalDirectories:")
    for d in added:
        print(f"    + {d}")


# ---------------------------------------------------------------------------
# code command — launch Claude Code with agent env + pre-loaded context
# ---------------------------------------------------------------------------


def cmd_code(
    agent_name: str,
    config_path: str = "executor.yaml",
    feature_id: str | None = None,
):
    """Launch Claude Code (claude CLI) pre-configured with agent env and context.

    Sets ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY from agent config, resolves the
    feature workspace, builds a context summary, and exec()s into `claude` so the
    user gets the full Claude Code interactive experience with the right model
    and project context already loaded.

    Args:
        agent_name: Agent name (e.g. frontend-agent)
        config_path: Path to executor.yaml
        feature_id: Optional feature ID — opens that feature's workspace
    """
    import os

    from nezha.config import load_agent_config, load_executor_config
    from nezha.executor import resolve_workspace, _resolve_target, _resolve_target_scope
    from nezha.feature_queue import FileFeatureQueue

    base_dir = Path(config_path).parent.resolve()
    executor_config = load_executor_config(config_path)

    agent_config_path = Path(executor_config.agents_dir) / f"{agent_name}.yaml"
    if not agent_config_path.is_absolute():
        agent_config_path = base_dir / agent_config_path
    if not agent_config_path.exists():
        print(t('cli.code.agent_not_found', path=agent_config_path))
        sys.exit(1)

    agent_config = load_agent_config(agent_config_path)
    workspace = resolve_workspace(executor_config, agent_config, base_dir=base_dir)
    target = _resolve_target(agent_config, executor_config, base_dir)
    target_scope = _resolve_target_scope(target, agent_config.target_scope)

    # Resolve feature workspace
    ws_base_raw = Path(executor_config.workspace.base)
    ws_base = (ws_base_raw if ws_base_raw.is_absolute() else base_dir / ws_base_raw).resolve()
    if feature_id:
        queue = FileFeatureQueue(ws_base)
        task = queue.get(feature_id)
        if task is None:
            print(t('cli.code.task_not_found', id=feature_id))
            print(t('cli.code.list_hint', agent=agent_name))
            sys.exit(1)
        workspace = queue.feature_workspace(feature_id)
        print(t('cli.code.task_workspace', path=workspace))
    else:
        # Use latest feature if any exist
        features_dir = ws_base / "features"
        tasks_dir_legacy = ws_base / "tasks"
        active_dir = features_dir if features_dir.exists() else tasks_dir_legacy
        if active_dir.exists():
            queue = FileFeatureQueue(ws_base)
            latest = sorted(active_dir.iterdir(), reverse=True)
            if latest:
                latest_id = latest[0].name
                workspace = queue.feature_workspace(latest_id)
                print(t('cli.code.latest_task', id=latest_id))

    # Build env: start from user's environment, then inject agent config.
    # Claude Code reads ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL / ANTHROPIC_DEFAULT_*_MODEL
    # from env — so we map the agent's engine config to those vars.
    env = {**os.environ}

    from nezha.config import resolve_env_refs
    resolved_agent_env = resolve_env_refs(agent_config.engine.env, executor_config.env)
    agent_env = {**executor_config.env, **resolved_agent_env}
    model = agent_config.engine.model

    if agent_env.get("ANTHROPIC_BASE_URL"):
        env["ANTHROPIC_BASE_URL"] = agent_env["ANTHROPIC_BASE_URL"]

    # Claude Code uses ANTHROPIC_AUTH_TOKEN (not ANTHROPIC_API_KEY)
    api_key = agent_env.get("ANTHROPIC_AUTH_TOKEN") or agent_env.get("ANTHROPIC_API_KEY")
    if api_key:
        env["ANTHROPIC_AUTH_TOKEN"] = api_key
        env.pop("ANTHROPIC_API_KEY", None)  # avoid confusion

    # Map model to Claude Code's model-tier env vars
    if model:
        env.setdefault("ANTHROPIC_DEFAULT_SONNET_MODEL", model)
        env.setdefault("ANTHROPIC_DEFAULT_OPUS_MODEL", model)
        env.setdefault("ANTHROPIC_DEFAULT_HAIKU_MODEL", model)

    # Helpful defaults for proxy/third-party endpoints
    if agent_env.get("ANTHROPIC_BASE_URL"):
        env.setdefault("API_TIMEOUT_MS", "600000")
        env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")

    # Ensure common tool paths are in PATH (node, pipx, etc.)
    home = str(Path.home())
    extra_paths = ["/opt/homebrew/bin", "/usr/local/bin", f"{home}/.local/bin"]
    existing_path = env.get("PATH", "")
    extra = ":".join(p for p in extra_paths if p not in existing_path)
    if extra:
        env["PATH"] = extra + ":" + existing_path

    # Load agent role prompt (vibe preferred, fallback to worker)
    # This gives Claude Code the agent's identity and rules.
    from nezha.i18n import get_locale
    from nezha.pipeline.prompt_template import load_and_render, resolve_prompt_path

    prompts_dir = Path(executor_config.prompts_dir)
    if not prompts_dir.is_absolute():
        prompts_dir = base_dir / prompts_dir
    locale = get_locale()

    role_prompt = ""
    for prompt_key in ("vibe", "worker"):
        prompt_rel = agent_config.session.prompts.get(prompt_key, "")
        if not prompt_rel:
            continue
        try:
            prompt_path = resolve_prompt_path(prompts_dir, prompt_rel, locale=locale)
            project_name = target.name if target and target.exists() else workspace.name
            role_prompt = load_and_render(prompt_path, {
                "workspace": str(workspace),
                "project_name": project_name,
                "input_files": "",
                "handoff_context": "",
                "user_instruction": "",
            })
            break
        except Exception:
            continue

    # Build context summary from key files in workspace
    context_parts = []
    _task_list_fname = "task_list.json"
    _task_list_path = workspace / _task_list_fname
    if not _task_list_path.exists():
        # Legacy fallback
        _legacy = workspace / "feature_list.json"
        if _legacy.exists():
            _task_list_path = _legacy
            _task_list_fname = "feature_list.json"
    for fname in ("progress.md", _task_list_fname, "agent-context.md"):
        fpath = workspace / fname
        if not fpath.exists():
            continue
        content = fpath.read_text(encoding="utf-8")
        if fname in ("task_list.json", "feature_list.json"):
            # Summarise instead of dumping full JSON
            try:
                features = json.load(open(fpath))
                done = sum(1 for f in features if f.get("passes"))
                total = len(features)
                pending_ids = [f["id"] for f in features if not f.get("passes")][:5]
                summary = (
                    f"task_list.json: {done}/{total} passed. "
                    f"Remaining: {', '.join(pending_ids)}"
                    + (" ..." if len(pending_ids) == 5 else "")
                )
                context_parts.append(summary)
            except Exception:
                context_parts.append(f"--- {fname} ---\n{content[:500]}")
        else:
            context_parts.append(f"--- {fname} ---\n{content[:2000]}")

    initial_prompt = None
    parts = []
    if role_prompt:
        parts.append(role_prompt)
    if target and target.exists() and target.resolve() != workspace.resolve():
        if target_scope:
            context_parts.insert(0,
                f"Target code repository: {target}\n"
                f"Your working scope: {target_scope}\n"
                f"You have read access to the entire repository for reference, "
                f"but you MUST only create/modify files under: {target_scope}/\n"
                f"Run tests scoped to your directory. Use absolute paths to access code files."
            )
        else:
            context_parts.insert(0, f"Target code repository: {target}\nYou have full read/write access to this directory. Use absolute paths to access code files.")
    if context_parts:
        parts.append(
            t("cli.code.info.context_header", agent=agent_name, workspace=str(workspace))
            + "\n\n"
            + "\n\n".join(context_parts)
        )
    if parts:
        initial_prompt = "\n\n---\n\n".join(parts)

    # Find claude binary — check PATH and known native-installer locations
    _candidates = [
        shutil.which("claude"),
        "/usr/local/bin/claude",
        str(Path.home() / ".claude" / "local" / "claude"),
        str(Path.home() / ".local" / "bin" / "claude"),
    ]
    claude_bin = next((c for c in _candidates if c and Path(c).exists()), None)
    if not claude_bin:
        print(t('cli.code.no_claude'))
        print(t('cli.code.claude_install'))
        sys.exit(1)

    # Pass context as the initial user message (positional arg to claude)
    cmd = [claude_bin]
    if initial_prompt:
        cmd.append(initial_prompt)

    # Grant Claude Code access to target directory via --add-dir.
    # cwd will be workspace (metadata), but all agents can read/write target code.
    if target and target.exists() and target.resolve() != workspace.resolve():
        cmd.extend(["--add-dir", str(target)])

    print("=" * 60)
    print(t('cli.code.launching'))
    print(t('cli.code.info.agent', agent=agent_name))
    print(t('cli.code.info.model', model=model or '(claude default)'))
    print(t('cli.code.info.workspace', path=workspace))
    if target:
        print(t('cli.code.info.target', path=target))
    if target_scope:
        print(t('cli.code.info.target_scope', path=target_scope))
    if env.get("ANTHROPIC_BASE_URL"):
        print(t('cli.code.info.base_url', url=env['ANTHROPIC_BASE_URL']))
    print("=" * 60)

    # Write .mcp.json so Claude Code CLI picks up executor/agent MCP servers.
    # Claude Code reads mcpServers from .mcp.json in cwd automatically.
    # Merge priority (lowest → highest): base_dir/.mcp.json < executor.yaml mcp_servers < agent mcp_servers
    merged_mcp: dict = {}
    root_mcp_path = base_dir / ".mcp.json"
    if root_mcp_path.exists():
        try:
            root_mcp = json.loads(root_mcp_path.read_text(encoding="utf-8"))
            merged_mcp.update(root_mcp.get("mcpServers", {}))
        except Exception:
            pass
    merged_mcp.update(executor_config.mcp_servers)
    agent_mcp = agent_config.engine.mcp_servers if hasattr(agent_config.engine, "mcp_servers") else {}
    merged_mcp.update(agent_mcp)  # agent-level overrides global

    # .mcp.json only supports stdio servers (command-based).
    # HTTP/streamable servers must live in ~/.claude.json — skip them here.
    stdio_mcp = {k: v for k, v in merged_mcp.items() if isinstance(v, dict) and "command" in v}

    workspace.mkdir(parents=True, exist_ok=True)
    if stdio_mcp:
        mcp_content = json.dumps({"mcpServers": stdio_mcp}, indent=2, ensure_ascii=False)
        mcp_json_path = workspace / ".mcp.json"
        mcp_json_path.write_text(mcp_content, encoding="utf-8")
        print(t('cli.code.mcp_written', count=len(stdio_mcp), path=mcp_json_path))

    # exec into claude — replaces this process entirely
    # cwd = workspace (metadata home base for all agent types).
    # Target is accessible via --add-dir (added above).
    os.chdir(workspace)
    os.execvpe(claude_bin, cmd, env)


def cmd_integrate(
    tasks: list[str],
    branch: str | None = None,
    base: str | None = None,
    push: bool = False,
    repo: str | None = None,
    config_path: str = "executor.yaml",
):
    """Merge multiple task branches into a local integration branch.

    Args:
        tasks: List of task indices (from 'task list') or branch names
        branch: Integration branch name (default: integration/<timestamp>)
        base: Base branch to create integration from (default: ai-dev or main)
        push: Push integration branch to remote after merge
        repo: Path to git repository (default: auto-detect)
        config_path: Path to executor config
    """
    import subprocess
    from datetime import datetime

    base_dir = _resolve_workspace_base(config_path)
    tasks_dir = _resolve_features_dir(base_dir)

    if tasks_dir is None:
        print(t('cli.feature.no_tasks_dir', agent=""))
        return

    # Find git repository - use --repo, or search upward, or use cwd
    if repo:
        git_repo = Path(repo).resolve()
        if not (git_repo / ".git").exists():
            print(f"[integrate] 错误: {repo} 不是 git 仓库")
            sys.exit(1)
    else:
        git_repo = base_dir
        while git_repo != git_repo.parent:
            if (git_repo / ".git").exists():
                break
            git_repo = git_repo.parent
        else:
            # Try cwd as fallback
            cwd = Path.cwd()
            if (cwd / ".git").exists():
                git_repo = cwd
            else:
                print("[integrate] 错误: 未找到 git 仓库")
                print("请使用 --repo 指定仓库路径，或在仓库目录下运行")
                sys.exit(1)

    print(f"[integrate] Git 仓库: {git_repo}")

    # Resolve branch names from task indices or direct branch names
    branches_to_merge = []
    for task_ref in tasks:
        # Check if it's a numeric index
        if task_ref.isdigit():
            idx = int(task_ref)
            # List all task directories and get by index
            # Sort must match task list order (ascending by name = oldest first)
            # Only include valid task directories (must be a directory)
            all_tasks = sorted(
                [p for p in tasks_dir.iterdir() if p.is_dir()],
                key=lambda p: p.name
            )
            if idx < 1 or idx > len(all_tasks):
                print(f"[integrate] 错误: 任务序号 {idx} 超出范围 (1-{len(all_tasks)})")
                sys.exit(1)
            task_id = all_tasks[idx - 1].name
            # Derive branch name from task_id
            branch_name = f"feat/{task_id}"
            branches_to_merge.append((task_id, branch_name))
        else:
            # Treat as branch name directly
            branch_name = task_ref
            if not branch_name.startswith("feat/"):
                branch_name = f"feat/{branch_name}"
            branches_to_merge.append((task_ref, branch_name))

    if not branches_to_merge:
        print("[integrate] 错误: 没有指定要合并的任务")
        sys.exit(1)

    # Determine base branch
    if not base:
        # Try ai-dev first, then main
        result = subprocess.run(
            ["git", "branch", "--list", "ai-dev"],
            cwd=git_repo, capture_output=True, text=True
        )
        if result.stdout.strip():
            base = "ai-dev"
        else:
            base = "main"

    # Generate integration branch name if not specified
    if not branch:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        branch = f"integration/{timestamp}"

    print("=" * 60)
    print("[integrate] 合并任务到临时分支")
    print("=" * 60)
    print(f"  基础分支: {base}")
    print(f"  目标分支: {branch}")
    print(f"  要合并的任务:")
    for i, (task_id, br) in enumerate(branches_to_merge, 1):
        print(f"    [{i}] {task_id} → {br}")
    print("=" * 60)

    # Check if integration branch already exists
    result = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=git_repo, capture_output=True, text=True
    )
    if result.stdout.strip():
        print(f"[integrate] 分支 {branch} 已存在，删除并重建...")
        subprocess.run(["git", "branch", "-D", branch], cwd=git_repo, check=True)

    # Create integration branch from base
    print(f"\n[integrate] 创建分支 {branch} (基于 {base})...")
    result = subprocess.run(
        ["git", "checkout", "-b", branch, base],
        cwd=git_repo, capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"[integrate] 创建分支失败: {result.stderr}")
        sys.exit(1)

    # Fetch remote branches first
    print("[integrate] 从远程获取最新分支...")
    subprocess.run(["git", "fetch", "--all"], cwd=git_repo, capture_output=True)

    # Merge each task branch
    merged_count = 0
    for task_id, br in branches_to_merge:
        print(f"\n[integrate] 合并: {br}")

        # Try local branch first, then remote
        result = subprocess.run(
            ["git", "branch", "--list", br],
            cwd=git_repo, capture_output=True, text=True
        )
        if result.stdout.strip():
            # Local branch exists
            merge_result = subprocess.run(
                ["git", "merge", br, "-m", f"Merge {br}"],
                cwd=git_repo, capture_output=True, text=True
            )
        else:
            # Try remote branch
            remote_branch = f"origin/{br}"
            merge_result = subprocess.run(
                ["git", "merge", remote_branch, "-m", f"Merge {br}"],
                cwd=git_repo, capture_output=True, text=True
            )

        if merge_result.returncode != 0:
            print(f"[integrate] 警告: 合并 {br} 失败")
            print(merge_result.stderr)
            # Continue with other branches
        else:
            merged_count += 1
            print(f"[integrate] ✓ {br} 合并成功")

    print("\n" + "=" * 60)
    print(f"[integrate] 完成！成功合并 {merged_count}/{len(branches_to_merge)} 个分支")
    print(f"[integrate] 当前分支: {branch}")
    print("=" * 60)
    print("\n后续步骤:")
    print(f"  1. 查看修改: git diff {base}")
    print(f"  2. 审核完成后合并到开发分支:")
    print(f"     git checkout {base} && git merge {branch}")
    print(f"  3. 删除临时分支:")
    print(f"     git branch -d {branch}")

    if push:
        print(f"\n[integrate] 推送到远程...")
        subprocess.run(["git", "push", "-u", "origin", branch], cwd=git_repo)


def cmd_dashboard(
    config_path: str = "executor.yaml",
    output: str = "",
    open_browser: bool = False,
):
    """Generate an HTML dashboard showing feature status and costs.

    Args:
        config_path: Path to executor config file
        output: Output HTML path (default: state_dir/dashboard.html)
        open_browser: Open the generated file in the default web browser
    """
    from nezha.interface.dashboard import write_dashboard

    workspace_base = _resolve_workspace_base(config_path)
    state_dir = _resolve_state_dir(config_path)

    if output:
        output_path = Path(output).resolve()
    else:
        output_path = state_dir / "dashboard.html"

    written = write_dashboard(workspace_base, output_path, state_dir=state_dir)
    print(f"[dashboard] Dashboard written to: {written}")

    if open_browser:
        import webbrowser
        webbrowser.open(str(written))

