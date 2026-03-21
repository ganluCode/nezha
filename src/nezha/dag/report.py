"""Execution report generator: produce execution-report.md after DAG run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from nezha.dag.graph import (
    TaskDAG,
    STATUS_COMPLETED,
    STATUS_REWORK,
    STATUS_READY,
    STATUS_BLOCKED,
    STATUS_SKIPPED,
)


def _format_tokens(n: int) -> str:
    """Format token count in human-readable form (e.g. 1.2M, 150K, 800)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


@dataclass
class SessionRecord:
    """Record of a single session execution."""
    session_number: int
    feature_id: str
    is_rework: bool
    duration_ms: int = 0
    cost_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    result: str = ""  # "completed" | "failed" | "error"
    error: str = ""


@dataclass
class ExecutionReportData:
    """Data collected during DAG execution for report generation."""
    sessions: list[SessionRecord] = field(default_factory=list)
    start_time: str = ""
    end_time: str = ""

    def add_session(self, record: SessionRecord):
        self.sessions.append(record)


def generate_report(
    report_data: ExecutionReportData,
    dag: TaskDAG,
    exit_reason: str,
) -> str:
    """Generate the execution report as a Markdown string.

    Args:
        report_data: Collected session records
        dag: Final DAG state
        exit_reason: Why execution stopped

    Returns:
        Markdown report content
    """
    lines: list[str] = []

    lines.append("# Execution Report")
    lines.append("")
    lines.append(f"Generated: {report_data.end_time}")
    lines.append(f"Started: {report_data.start_time}")
    lines.append(f"Exit reason: {exit_reason}")
    lines.append("")

    # --- Overview section ---
    lines.append("## Overview")
    lines.append("")

    summary = dag.summary()
    counts = summary["counts"]
    total = summary["total"]

    lines.append(f"| Status | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Completed | {counts[STATUS_COMPLETED]}/{total} |")
    lines.append(f"| Failed/Rework | {counts[STATUS_REWORK]} |")
    lines.append(f"| Blocked | {counts[STATUS_BLOCKED]} |")
    lines.append(f"| Skipped | {counts[STATUS_SKIPPED]} |")
    lines.append(f"| Ready | {counts[STATUS_READY]} |")
    lines.append("")

    total_cost = sum(s.cost_usd or 0 for s in report_data.sessions)
    total_input = sum(s.input_tokens for s in report_data.sessions)
    total_output = sum(s.output_tokens for s in report_data.sessions)
    total_time_ms = sum(s.duration_ms for s in report_data.sessions)
    lines.append(f"Total sessions: {len(report_data.sessions)}")
    lines.append(f"Total tokens: {_format_tokens(total_input + total_output)} (in: {_format_tokens(total_input)}, out: {_format_tokens(total_output)})")
    lines.append(f"Total cost: ${total_cost:.4f}")
    lines.append(f"Total time: {total_time_ms}ms")
    lines.append("")

    # --- Timeline section ---
    lines.append("## Session Timeline")
    lines.append("")

    if report_data.sessions:
        lines.append("| # | Feature | Type | Duration | Tokens | Cost | Result |")
        lines.append("|---|---------|------|----------|--------|------|--------|")
        for s in report_data.sessions:
            session_type = "rework" if s.is_rework else "new"
            tokens_str = _format_tokens(s.input_tokens + s.output_tokens)
            cost_str = f"${s.cost_usd:.4f}" if s.cost_usd is not None else "-"
            lines.append(
                f"| {s.session_number} | {s.feature_id} | {session_type} "
                f"| {s.duration_ms}ms | {tokens_str} | {cost_str} | {s.result} |"
            )
        lines.append("")
    else:
        lines.append("No sessions were executed.")
        lines.append("")

    # --- Failures section ---
    lines.append("## Failure Records")
    lines.append("")

    # Collect tasks that failed or need rework
    failure_tasks = []
    for f in dag.get_all_tasks():
        status = dag.get_status(f.id)
        if status in (STATUS_REWORK, STATUS_SKIPPED):
            failure_tasks.append(f)

    if failure_tasks:
        for f in failure_tasks:
            status = dag.get_status(f.id)
            lines.append(f"### {f.id}")
            lines.append("")
            lines.append(f"- **Status**: {status}")
            lines.append(f"- **Attempts**: {f.rework_count}")
            if f.rework_note:
                rn = f.rework_note
                if isinstance(rn, dict):
                    if rn.get("block_reason"):
                        lines.append(f"- **Block reason**: {rn['block_reason']}")
                    if rn.get("tried"):
                        lines.append(f"- **Tried**: {rn['tried']}")
                    if rn.get("not_tried"):
                        lines.append(f"- **Not tried**: {rn['not_tried']}")
                    if rn.get("related_files"):
                        lines.append(f"- **Related files**: {', '.join(rn['related_files'])}")
                else:
                    lines.append(f"- **Last error**: {rn}")
            lines.append("")

            # Show session history for this task
            task_sessions = [
                s for s in report_data.sessions if s.feature_id == f.id
            ]
            if task_sessions:
                lines.append("Session history:")
                lines.append("")
                for s in task_sessions:
                    lines.append(
                        f"- Session {s.session_number}: {s.result}"
                        + (f" — {s.error}" if s.error else "")
                    )
                lines.append("")
    else:
        lines.append("No failures recorded.")
        lines.append("")

    # --- Blocked dependencies section ---
    lines.append("## Blocked Dependencies")
    lines.append("")

    blocked = dag.get_blocked_tasks()
    if blocked:
        for bi in blocked:
            lines.append(
                f"- **{bi.task_id}** blocked by: {', '.join(bi.blocked_by)}"
            )
        lines.append("")

        # Render a simple text dependency graph
        lines.append("Dependency graph:")
        lines.append("")
        lines.append("```")
        lines.append(dag.format_tree())
        lines.append("```")
        lines.append("")
    else:
        lines.append("No blocked tasks.")
        lines.append("")

    return "\n".join(lines)


def generate_exec_plan(dag: TaskDAG) -> str:
    """Generate exec-plan.md — a live progress table of all tasks.

    Updated by the DAG engine after each session so humans and agents
    can quickly see the current state.
    """
    _STATUS_ICON = {
        STATUS_COMPLETED: "✓ completed",
        STATUS_REWORK:    "! rework",
        STATUS_READY:     "→ ready",
        STATUS_SKIPPED:   "✗ skipped",
    }

    lines: list[str] = []
    lines.append("# Execution Plan")
    lines.append("")
    lines.append(f"*Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}*")
    lines.append("")

    # Task table
    lines.append("| Task | Description | Status | Rework # |")
    lines.append("|------|-------------|--------|----------|")

    blocked_map = {bi.task_id: bi.blocked_by for bi in dag.get_blocked_tasks()}
    rework_note_tasks = []

    for f in dag.get_all_tasks():
        status = dag.get_status(f.id)
        if status == STATUS_BLOCKED:
            blockers = blocked_map.get(f.id, [])
            status_icon = f"· blocked by {', '.join(blockers)}" if blockers else "· blocked"
        else:
            status_icon = _STATUS_ICON.get(status, status)

        desc = f.description[:60]
        lines.append(f"| {f.id} | {desc} | {status_icon} | {f.rework_count} |")

        if f.rework_note:
            rework_note_tasks.append(f)

    # Rework notes section
    if rework_note_tasks:
        lines.append("")
        lines.append("## Rework Notes")
        lines.append("")
        for f in rework_note_tasks:
            lines.append(f"### {f.id}")
            lines.append("")
            rn = f.rework_note
            if isinstance(rn, dict):
                if rn.get("block_reason"):
                    lines.append(f"- **Block reason**: {rn['block_reason']}")
                if rn.get("tried"):
                    lines.append(f"- **Tried**: {rn['tried']}")
                if rn.get("not_tried"):
                    lines.append(f"- **Not tried**: {rn['not_tried']}")
                if rn.get("related_files"):
                    lines.append(f"- **Related files**: {', '.join(rn['related_files'])}")
                lines.append(f"- **Attempt**: {rn.get('attempt', '?')}")
            else:
                lines.append(f"- {rn}")
            lines.append("")

    return "\n".join(lines)


def write_exec_plan(dag: TaskDAG, workspace: Path) -> Path:
    """Generate and write exec-plan.md to workspace.

    Returns:
        Path to the generated exec-plan.md
    """
    content = generate_exec_plan(dag)
    plan_path = workspace / "exec-plan.md"
    plan_path.write_text(content, encoding="utf-8")
    return plan_path


def write_report(
    report_data: ExecutionReportData,
    dag: TaskDAG,
    exit_reason: str,
    workspace: Path,
) -> Path:
    """Generate and write the execution report to workspace.

    Args:
        report_data: Collected session records
        dag: Final DAG state
        exit_reason: Why execution stopped
        workspace: Workspace directory to write to

    Returns:
        Path to the generated report file
    """
    content = generate_report(report_data, dag, exit_reason)
    report_path = workspace / "execution-report.md"
    report_path.write_text(content, encoding="utf-8")
    return report_path
