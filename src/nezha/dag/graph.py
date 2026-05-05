"""Task dependency DAG: load task_list.json, compute statuses, visualize tree."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Task status constants
# ---------------------------------------------------------------------------

STATUS_COMPLETED = "completed"
STATUS_REWORK = "rework"
STATUS_READY = "ready"
STATUS_BLOCKED = "blocked"
STATUS_SKIPPED = "skipped"

# Status display symbols
_SYMBOLS = {
    STATUS_COMPLETED: "\u2713",  # ✓
    STATUS_REWORK: "!",
    STATUS_READY: "\u2192",      # →
    STATUS_BLOCKED: "\u00b7",    # ·
    STATUS_SKIPPED: "\u2717",    # ✗
}

REWORK_MAX_COUNT = 3


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Task:
    """A single task parsed from task_list.json."""
    id: str
    description: str = ""
    category: str = ""
    acceptance: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    passes: bool = False
    rework: bool = False
    rework_note: str | dict = ""
    rework_count: int = 0
    complexity: str = ""  # "low" | "medium" | "high"; used for model_map resolution
    model: str = ""  # Optional explicit model override; empty = resolve via model_map or agent default
    # Raw dict for passing to .dag_context.json
    _raw: dict = field(default_factory=dict, repr=False)


@dataclass
class BlockedInfo:
    """Why a task is blocked."""
    task_id: str
    blocked_by: list[str]  # IDs of unmet dependencies


# ---------------------------------------------------------------------------
# TaskDAG
# ---------------------------------------------------------------------------

class TaskDAG:
    """Dependency graph built from task_list.json.

    All statuses are computed dynamically — nothing is written back to JSON.
    """

    def __init__(self, tasks: list[Task]):
        self._tasks: dict[str, Task] = {f.id: f for f in tasks}
        # Build adjacency: parent → children (downstream)
        self._children: dict[str, list[str]] = {fid: [] for fid in self._tasks}
        for f in tasks:
            for dep_id in f.depends_on:
                if dep_id in self._children:
                    self._children[dep_id].append(f.id)

    # -- Factory ----------------------------------------------------------

    @classmethod
    def load(cls, task_list_path: Path) -> TaskDAG:
        """Load from a task_list.json file."""
        with open(task_list_path, encoding="utf-8") as f:
            raw_text = f.read()
        try:
            raw_list = json.loads(raw_text)
        except json.JSONDecodeError:
            # Attempt repair via _try_fix_json
            from nezha.pipeline.direct_api import _try_fix_json
            fixed = _try_fix_json(raw_text)
            raw_list = json.loads(fixed)  # let it raise if still broken
            # Rewrite the repaired file
            task_list_path.write_text(
                json.dumps(raw_list, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"[DAG] Repaired invalid JSON in {task_list_path.name}")

        tasks = []
        for item in raw_list:
            tasks.append(Task(
                id=item["id"],
                description=item.get("description", ""),
                category=item.get("category", ""),
                acceptance=item.get("acceptance", []),
                depends_on=item.get("depends_on", []),
                passes=item.get("passes", False),
                rework=item.get("rework", False),
                rework_note=item.get("rework_note", ""),
                rework_count=item.get("rework_count", 0),
                complexity=item.get("complexity", ""),
                model=item.get("model", ""),
                _raw=item,
            ))
        return cls(tasks)

    # -- Status computation -----------------------------------------------

    def get_status(self, task_id: str) -> str:
        """Compute the current status of a task."""
        f = self._tasks.get(task_id)
        if f is None:
            return STATUS_BLOCKED

        # Completed (passes takes priority over rework)
        if f.passes:
            return STATUS_COMPLETED

        # Skipped (rework exhausted)
        if f.rework_count >= REWORK_MAX_COUNT:
            return STATUS_SKIPPED

        # Rework needed
        if f.rework:
            return STATUS_REWORK

        # Check dependencies
        unmet = self._get_unmet_deps(task_id)
        if unmet:
            return STATUS_BLOCKED

        return STATUS_READY

    def _get_unmet_deps(self, task_id: str) -> list[str]:
        """Return list of dependency IDs that are not completed."""
        f = self._tasks[task_id]
        unmet = []
        for dep_id in f.depends_on:
            if self.get_status(dep_id) != STATUS_COMPLETED:
                unmet.append(dep_id)
        return unmet

    # -- Queries ----------------------------------------------------------

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> list[Task]:
        return list(self._tasks.values())

    def get_rework_tasks(self) -> list[Task]:
        """Tasks needing rework (highest priority)."""
        return [
            f for f in self._tasks.values()
            if self.get_status(f.id) == STATUS_REWORK
        ]

    def get_ready_tasks(self) -> list[Task]:
        """Tasks ready to execute (all deps met)."""
        return [
            f for f in self._tasks.values()
            if self.get_status(f.id) == STATUS_READY
        ]

    def get_blocked_tasks(self) -> list[BlockedInfo]:
        """Tasks blocked by unmet dependencies."""
        result = []
        for f in self._tasks.values():
            if self.get_status(f.id) == STATUS_BLOCKED:
                unmet = self._get_unmet_deps(f.id)
                result.append(BlockedInfo(task_id=f.id, blocked_by=unmet))
        return result

    def get_downstream(self, task_id: str) -> list[str]:
        """Get all transitive downstream task IDs."""
        visited = set()
        stack = [task_id]
        while stack:
            current = stack.pop()
            for child in self._children.get(current, []):
                if child not in visited:
                    visited.add(child)
                    stack.append(child)
        return sorted(visited)

    def is_all_done(self) -> bool:
        """True if every task is completed or skipped."""
        for f in self._tasks.values():
            status = self.get_status(f.id)
            if status not in (STATUS_COMPLETED, STATUS_SKIPPED):
                return False
        return True

    def is_deadlocked(self) -> bool:
        """True if no tasks are executable but not all are done."""
        if self.is_all_done():
            return False
        return (
            len(self.get_rework_tasks()) == 0
            and len(self.get_ready_tasks()) == 0
        )

    # -- Summary ----------------------------------------------------------

    def summary(self) -> dict:
        """Return a status summary dict."""
        counts = {
            STATUS_COMPLETED: 0,
            STATUS_REWORK: 0,
            STATUS_READY: 0,
            STATUS_BLOCKED: 0,
            STATUS_SKIPPED: 0,
        }
        by_status: dict[str, list[str]] = {k: [] for k in counts}

        for f in self._tasks.values():
            s = self.get_status(f.id)
            counts[s] = counts.get(s, 0) + 1
            by_status[s].append(f.id)

        return {
            "total": len(self._tasks),
            "counts": counts,
            "by_status": by_status,
        }

    # -- DAG context for worker prompt ------------------------------------

    def build_dag_context(self, target_task: Task) -> dict:
        """Build the .dag_context.json content for a session."""
        s = self.summary()

        # Build blocked map: task_id -> reason
        blocked_map = {}
        for bi in self.get_blocked_tasks():
            blocked_map[bi.task_id] = f"blocked by {', '.join(bi.blocked_by)}"

        target_data = {
            "id": target_task.id,
            "description": target_task.description,
            "category": target_task.category,
            "acceptance": target_task.acceptance,
            "depends_on": target_task.depends_on,
            "is_rework": target_task.rework,
            "rework_note": target_task.rework_note or None,
            "rework_count": target_task.rework_count,
        }

        return {
            "target_task": target_data,
            "dag_status": {
                "completed": s["by_status"][STATUS_COMPLETED],
                "ready": s["by_status"][STATUS_READY],
                "blocked": blocked_map,
                "rework": s["by_status"][STATUS_REWORK],
                "skipped": s["by_status"][STATUS_SKIPPED],
            },
        }

    # -- Tree visualization -----------------------------------------------

    def format_tree(self) -> str:
        """Render the DAG as an ASCII tree string."""
        lines = []

        # Find root nodes (no dependencies)
        roots = [
            f for f in self._tasks.values()
            if not f.depends_on
        ]
        # Sort roots by ID for stable output
        roots.sort(key=lambda f: f.id)

        # Track which tasks have been rendered (for multi-parent nodes)
        rendered = set()

        for i, root in enumerate(roots):
            self._render_node(root.id, "", i == len(roots) - 1, lines, rendered)

        # Render orphaned nodes (deps not in the list — shouldn't happen but be safe)
        for f in self._tasks.values():
            if f.id not in rendered:
                status = self.get_status(f.id)
                sym = _SYMBOLS.get(status, "?")
                desc = f.description[:50]
                lines.append(f"  {f.id} [{sym}] {desc}")
                rendered.add(f.id)

        return "\n".join(lines)

    def _render_node(
        self,
        task_id: str,
        prefix: str,
        is_last: bool,
        lines: list[str],
        rendered: set[str],
    ):
        """Recursively render a node and its children."""
        if task_id in rendered:
            return
        rendered.add(task_id)

        f = self._tasks.get(task_id)
        if f is None:
            return

        status = self.get_status(task_id)
        sym = _SYMBOLS.get(status, "?")
        desc = f.description[:50]

        # Extra info for blocked/rework
        extra = ""
        if status == STATUS_BLOCKED:
            unmet = self._get_unmet_deps(task_id)
            if unmet and f.depends_on != unmet:
                # Only show if partially blocked (some deps met)
                extra = f" (blocked by: {', '.join(unmet)})"
        elif status == STATUS_REWORK:
            extra = f" (rework #{f.rework_count})"

        # Connector
        if prefix == "":
            # Root node
            connector = "  "
        else:
            connector = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "

        lines.append(f"{prefix}{connector}{task_id} [{sym}] {desc}{extra}")

        # Children
        children = sorted(self._children.get(task_id, []))
        if not children:
            return

        # Child prefix
        if prefix == "":
            child_prefix = "  "
        else:
            child_prefix = prefix + ("    " if is_last else "\u2502   ")

        for j, child_id in enumerate(children):
            self._render_node(
                child_id,
                child_prefix,
                j == len(children) - 1,
                lines,
                rendered,
            )
