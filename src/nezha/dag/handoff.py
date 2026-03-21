"""VibeCoding handoff context generator.

When entering vibe mode, automatically reads execution-report.md and
task_list.json to produce a handoff context string that is injected
into the vibe prompt via the {{handoff_context}} variable.

The context includes:
- Target task goal and acceptance criteria
- Previous attempt history (from execution-report.md)
- Last failure error message
- Files changed by the agent (from git log)
- Downstream tasks blocked by the current task
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


def generate_handoff_context(
    workspace: Path,
    task_list_path: Path | None = None,
    execution_report_path: Path | None = None,
) -> str:
    """Generate a handoff context string for the vibe prompt.

    Reads the current DAG state and execution report to build context
    about the target task for the vibe coding agent.

    Args:
        workspace: Workspace directory
        task_list_path: Path to task_list.json (default: workspace/task_list.json)
        execution_report_path: Path to execution-report.md (default: workspace/execution-report.md)

    Returns:
        Handoff context string (Markdown). Empty string if no relevant data.
    """
    if task_list_path is None:
        task_list_path = workspace / "task_list.json"
        # Legacy fallback
        if not task_list_path.exists():
            legacy = workspace / "feature_list.json"
            if legacy.exists():
                task_list_path = legacy
    if execution_report_path is None:
        execution_report_path = workspace / "execution-report.md"

    # Load task list
    tasks = _load_tasks(task_list_path)
    if not tasks:
        return ""

    # Find the target task: first rework, then first non-passing
    target = _find_target_task(tasks)
    if target is None:
        return ""

    # Load execution report
    report_content = _load_report(execution_report_path)

    # Build context sections
    sections: list[str] = []
    sections.append("## HANDOFF CONTEXT")
    sections.append("")

    # 1. Target task goal and acceptance criteria
    sections.append(_build_target_section(target))

    # 2. Previous attempt history from execution report
    history = _extract_attempt_history(target["id"], report_content)
    if history:
        sections.append(history)

    # 3. Last failure error
    error = _extract_last_error(target, report_content)
    if error:
        sections.append(error)

    # 4. Files changed by agent (from git log)
    changed_files = _get_changed_files(workspace, target["id"])
    if changed_files:
        sections.append(changed_files)

    # 5. Blocked downstream tasks
    blocked = _get_blocked_downstream(target["id"], tasks)
    if blocked:
        sections.append(blocked)

    return "\n".join(sections)


def _load_tasks(task_list_path: Path) -> list[dict]:
    """Load and return the task list from JSON."""
    if not task_list_path.exists():
        return []
    try:
        with open(task_list_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _find_target_task(tasks: list[dict]) -> dict | None:
    """Find the target task: first rework task, then first non-passing.

    Priority:
    1. Tasks marked with rework=true (sorted by id)
    2. Tasks with passes=false whose dependencies are all met (sorted by id)
    """
    # First: rework tasks
    rework = [f for f in tasks if f.get("rework")]
    if rework:
        rework.sort(key=lambda f: f["id"])
        return rework[0]

    # Build set of completed task IDs
    completed_ids = {f["id"] for f in tasks if f.get("passes") and not f.get("rework")}

    # Second: non-passing tasks whose deps are all met
    ready = []
    for f in tasks:
        if f.get("passes"):
            continue
        deps = f.get("depends_on", [])
        if all(dep in completed_ids for dep in deps):
            ready.append(f)

    if ready:
        ready.sort(key=lambda f: f["id"])
        return ready[0]

    return None


def _load_report(execution_report_path: Path) -> str:
    """Load execution report content, or empty string if not found."""
    if not execution_report_path.exists():
        return ""
    try:
        return execution_report_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _build_target_section(target: dict) -> str:
    """Build the target task section with description and acceptance criteria."""
    lines = ["### Target Task", ""]
    lines.append(f"**{target['id']}**: {target.get('description', 'No description')}")
    lines.append("")

    acceptance = target.get("acceptance", [])
    if acceptance:
        lines.append("**Acceptance Criteria:**")
        for criterion in acceptance:
            lines.append(f"- {criterion}")
        lines.append("")

    if target.get("rework"):
        lines.append(f"**Status**: REWORK (attempt #{target.get('rework_count', 0)})")
        if target.get("rework_note"):
            lines.append(f"**Rework reason**: {target['rework_note']}")
        lines.append("")

    return "\n".join(lines)


def _extract_attempt_history(task_id: str, report_content: str) -> str:
    """Extract previous attempt history for a task from execution report."""
    if not report_content:
        return ""

    # Look for the task in the Session Timeline table
    # Format: | # | Feature | Type | Duration | Cost | Result |
    timeline_entries = []
    in_timeline = False
    for line in report_content.split("\n"):
        if "## Session Timeline" in line:
            in_timeline = True
            continue
        if in_timeline and line.startswith("## "):
            break
        if in_timeline and task_id in line and line.strip().startswith("|"):
            # Parse table row
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 6:
                timeline_entries.append({
                    "session": parts[0],
                    "feature": parts[1],
                    "type": parts[2],
                    "duration": parts[3],
                    "cost": parts[4],
                    "result": parts[5],
                })

    if not timeline_entries:
        return ""

    lines = ["### Previous Attempts", ""]
    for entry in timeline_entries:
        lines.append(
            f"- Session {entry['session']}: {entry['type']} — "
            f"{entry['result']} ({entry['duration']}, {entry['cost']})"
        )
    lines.append("")
    return "\n".join(lines)


def _extract_last_error(target: dict, report_content: str) -> str:
    """Extract the last error message for the target task."""
    # First check task's own rework_note
    if target.get("rework_note"):
        lines = ["### Last Error", ""]
        lines.append(f"```")
        lines.append(target["rework_note"])
        lines.append(f"```")
        lines.append("")
        return "\n".join(lines)

    # Check the Failure Records section from the report
    if not report_content:
        return ""

    task_id = target["id"]
    # Look for ### <task_id> in Failure Records section
    in_failures = False
    in_task = False
    last_error = ""
    for line in report_content.split("\n"):
        if "## Failure Records" in line:
            in_failures = True
            continue
        if in_failures and line.startswith("## "):
            break
        if in_failures and line.strip() == f"### {task_id}":
            in_task = True
            continue
        if in_task and line.startswith("### "):
            break
        if in_task and "**Last error**:" in line:
            # Extract error text after the label
            match = re.search(r"\*\*Last error\*\*:\s*(.*)", line)
            if match:
                last_error = match.group(1).strip()

    if last_error:
        lines = ["### Last Error", ""]
        lines.append(f"```")
        lines.append(last_error)
        lines.append(f"```")
        lines.append("")
        return "\n".join(lines)

    return ""


def _get_changed_files(workspace: Path, task_id: str) -> str:
    """Get files changed by the agent for this task from git log."""
    try:
        result = subprocess.run(
            ["git", "log", "--all", "--oneline", f"--grep={task_id}",
             "--name-only", "--pretty=format:"],
            capture_output=True,
            text=True,
            cwd=workspace,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ""

        # Collect unique file paths (non-empty lines)
        files = sorted(set(
            line.strip() for line in result.stdout.split("\n")
            if line.strip()
        ))

        if not files:
            return ""

        lines = ["### Changed Files", ""]
        lines.append(f"Files modified in previous attempts for {task_id}:")
        lines.append("")
        for f in files:
            lines.append(f"- `{f}`")
        lines.append("")
        return "\n".join(lines)

    except (subprocess.TimeoutExpired, OSError):
        return ""


def _get_blocked_downstream(task_id: str, tasks: list[dict]) -> str:
    """Get list of downstream tasks blocked by the current task."""
    # Find tasks that depend on the target (directly or transitively)
    dependents = []
    for f in tasks:
        if task_id in f.get("depends_on", []):
            dependents.append(f)

    if not dependents:
        return ""

    lines = ["### Blocked Downstream Tasks", ""]
    lines.append(f"The following tasks are blocked until {task_id} is completed:")
    lines.append("")
    for dep in dependents:
        lines.append(f"- **{dep['id']}**: {dep.get('description', '')[:80]}")
    lines.append("")
    return "\n".join(lines)


def generate_all_context(workspace: Path, category: str = "coding") -> str:
    """Generate complete context for --context all mode.

    For coding agents: complete task list status summary + standard handoff context.
    For other categories: standard handoff context only (task_list may not exist).

    Args:
        workspace: Agent workspace directory
        category: Agent category from AgentConfig ("coding", "management", etc.)

    Returns:
        Full context string (Markdown). Empty string if no data available.
    """
    parts: list[str] = []

    if category == "coding":
        task_list_path = workspace / "task_list.json"
        # Legacy fallback
        if not task_list_path.exists():
            legacy = workspace / "feature_list.json"
            if legacy.exists():
                task_list_path = legacy
        tasks = _load_tasks(task_list_path)
        if tasks:
            total = len(tasks)
            completed = sum(1 for f in tasks if f.get("passes") and not f.get("rework"))
            rework_count = sum(1 for f in tasks if f.get("rework"))
            pending = total - completed - rework_count

            lines = [
                "## ALL TASKS STATUS",
                "",
                f"Progress: **{completed}/{total}** completed"
                + (f", {rework_count} in rework" if rework_count else "")
                + (f", {pending} pending" if pending else ""),
                "",
            ]
            for f in tasks:
                if f.get("passes") and not f.get("rework"):
                    icon = "✓"
                elif f.get("rework"):
                    icon = "!"
                else:
                    icon = "○"
                desc = f.get("description", "")[:80]
                lines.append(f"{icon} **{f['id']}**: {desc}")
                if f.get("rework_note"):
                    note = f["rework_note"][:100]
                    lines.append(f"   → rework: {note}")
            lines.append("")
            parts.append("\n".join(lines))

    # Always include the standard handoff context (target task + previous attempts)
    handoff = generate_handoff_context(workspace)
    if handoff:
        parts.append(handoff)

    return "\n\n".join(parts)
