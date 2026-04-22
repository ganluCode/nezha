"""Feature queue: Port/Adapter pattern for feature lifecycle management.

Defines:
- FeatureStatus enum
- Feature dataclass
- FeatureQueue Protocol
- FileFeatureQueue implementation
"""

import datetime
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

import yaml


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------

class FeatureStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    PARTIAL = "partial"   # Some tasks skipped/blocked; DAG deadlocked
    FAILED = "failed"


STEP_PENDING = "pending"
STEP_READY = "ready"          # computed: all deps completed, not started
STEP_RUNNING = "running"
STEP_COMPLETED = "completed"
STEP_NEEDS_REVIEW = "needs_review"  # review_gate=True → waiting for human
STEP_SKIPPED = "skipped"


@dataclass
class FeatureStep:
    """One stage in a multi-stage feature execution pipeline."""
    id: str
    agent: str                    # agent name to execute (e.g. "evolve-agent")
    depends_on: list[str] = field(default_factory=list)  # step IDs
    status: str = STEP_PENDING    # stored status (pending | running | completed | needs_review | skipped)
    review_gate: bool = False     # pause for human review after completion
    note: str = ""                # human note (reject reason, etc.)


@dataclass
class Feature:
    id: str
    title: str          # slug, e.g. "user-auth"
    status: FeatureStatus
    created_at: str
    priority: int = 50  # 0–100; higher = runs first (default 50)
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)
    steps: list[FeatureStep] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class FeatureQueue(Protocol):
    def create(self, title: str = "", priority: int = 50, branch: str = "", base_branch: str = "") -> Feature: ...
    def get_next(self, agent_name: str | None = None) -> Feature | None: ...
    def get(self, feature_id: str) -> Feature | None: ...
    def update_status(
        self, feature_id: str, status: FeatureStatus, error: str | None = None
    ) -> None: ...
    def update_metadata(self, feature_id: str, metadata: dict) -> None: ...
    def list_features(
        self, agent_name: str | None = None, status: FeatureStatus | None = None
    ) -> list[Feature]: ...
    def feature_workspace(self, feature_id: str) -> Path: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Convert text to an ASCII-safe slug suitable for git branch names.

    E.g. "User Auth Feature" → "user-auth-feature"
         "P1F1: 项目骨架 + 基础设施" → "p1f1"
    """
    text = text.lower().strip()
    # Keep only ASCII letters, digits, underscores, whitespace, and hyphens
    text = re.sub(r"[^a-z0-9_\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def split_task_list(feature_workspace: Path) -> dict[str, Path]:
    """Split master task_list.json into per-agent files.

    Reads task_list.json from feature_workspace, groups tasks by the
    ``assigned_to`` field, and writes ``task_list.<agent>.json`` for
    each agent.  Tasks without ``assigned_to`` are skipped.

    Args:
        feature_workspace: Feature directory containing task_list.json

    Returns:
        Dict mapping agent_name → path of the per-agent file written.
        Empty dict if task_list.json has no ``assigned_to`` fields.
    """
    master_path = feature_workspace / "task_list.json"
    if not master_path.exists():
        # Legacy fallback: check for feature_list.json
        legacy_path = feature_workspace / "feature_list.json"
        if legacy_path.exists():
            master_path = legacy_path
        else:
            return {}

    with open(master_path, encoding="utf-8") as f:
        tasks = json.load(f)

    # Group by assigned_to
    by_agent: dict[str, list] = {}
    for task in tasks:
        agent = task.get("assigned_to")
        if agent:
            by_agent.setdefault(agent, []).append(task)

    written: dict[str, Path] = {}
    for agent_name, agent_tasks in by_agent.items():
        out_path = feature_workspace / f"task_list.{agent_name}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(agent_tasks, f, indent=2, ensure_ascii=False)
            f.write("\n")
        written[agent_name] = out_path

    return written


# ---------------------------------------------------------------------------
# FileFeatureQueue implementation
# ---------------------------------------------------------------------------

class FileFeatureQueue:
    """File-system backed feature queue.

    Features are user stories that may span multiple agents.

    Directory layout:
        <workspace_base>/
          features/
            2026-02-23-10-30-00_user-auth/
              feature.yaml
              input/
              task_list.json            ← planner output (master)
              task_list.backend-agent.json   ← auto-split per agent
              task_list.frontend-agent.json
            2026-02-23-10-45-00_payment-flow/
              feature.yaml
              input/
    """

    def __init__(self, base_dir: Path):
        """Initialize the queue.

        Args:
            base_dir: Workspace root (e.g. workspace/).  Features are stored
                      under <base_dir>/features/.
        """
        features_dir = base_dir / "features"
        legacy_dir = base_dir / "tasks"
        # Prefer features/ if it has content; fall back to tasks/ if it has content
        features_has_content = features_dir.exists() and any(features_dir.iterdir())
        legacy_has_content = legacy_dir.exists() and any(legacy_dir.iterdir())
        if features_has_content or not legacy_has_content:
            self._features_dir = features_dir
            self._features_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._features_dir = legacy_dir

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _feature_dir(self, feature_id: str) -> Path:
        return self._features_dir / feature_id

    def _feature_yaml_path(self, feature_id: str) -> Path:
        p = self._feature_dir(feature_id) / "feature.yaml"
        if not p.exists():
            legacy = self._feature_dir(feature_id) / "task.yaml"
            if legacy.exists():
                return legacy
        return p

    def _read_feature(self, feature_id: str) -> Feature | None:
        yaml_path = self._feature_yaml_path(feature_id)
        if not yaml_path.exists():
            return None
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        steps_raw = data.get("steps") or []
        steps = [
            FeatureStep(
                id=s["id"],
                agent=s.get("agent", ""),
                depends_on=s.get("depends_on", []),
                status=s.get("status", STEP_PENDING),
                review_gate=s.get("review_gate", False),
                note=s.get("note", ""),
            )
            for s in steps_raw
        ]
        return Feature(
            id=data["id"],
            title=data.get("title", ""),
            status=FeatureStatus(data["status"]),
            created_at=data["created_at"],
            priority=int(data.get("priority", 50)),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            error=data.get("error"),
            metadata=data.get("metadata") or {},
            steps=steps,
        )

    def _write_feature(self, feature: Feature) -> None:
        yaml_path = self._feature_dir(feature.id) / "feature.yaml"
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {
            "id": feature.id,
            "title": feature.title,
            "status": feature.status.value,
            "priority": feature.priority,
            "created_at": feature.created_at,
            "started_at": feature.started_at,
            "completed_at": feature.completed_at,
            "error": feature.error,
            "metadata": feature.metadata,
        }
        if feature.steps:
            data["steps"] = [
                {
                    "id": s.id,
                    "agent": s.agent,
                    "depends_on": s.depends_on,
                    "status": s.status,
                    "review_gate": s.review_gate,
                    "note": s.note,
                }
                for s in feature.steps
            ]
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    def _has_agent_work(self, feature_id: str, agent_name: str) -> bool:
        """Return True if this feature has a task_list file for the given agent."""
        feature_dir = self._feature_dir(feature_id)
        # Check agent-specific name first
        if (feature_dir / f"task_list.{agent_name}.json").exists():
            return True
        # Legacy agent-specific fallback
        if (feature_dir / f"feature_list.{agent_name}.json").exists():
            return True
        # Generic task_list.json (planner generates this by default)
        return (feature_dir / "task_list.json").exists()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create(self, title: str = "", priority: int = 50, branch: str = "", base_branch: str = "") -> Feature:
        """Create a new pending feature with a timestamp-based ID.

        Args:
            title: Human-readable title (e.g. "User Auth").
                   Slugified and appended to the timestamp ID.
            priority: Scheduling priority 0–100 (default 50, higher runs first).
            branch: Git branch name to bind to this feature. Defaults to
                    "feat/<feature-id>" if not provided.
            base_branch: Git base branch to create from (overrides agent config).
                         Useful for chaining: api02 builds on top of api01's branch.
        """
        now = datetime.datetime.now().astimezone()
        timestamp = now.strftime("%Y-%m-%d-%H-%M-%S")

        slug = _slugify(title) if title else ""
        feature_id = f"{timestamp}_{slug}" if slug else timestamp

        # Ensure uniqueness if multiple features created within the same second
        if self._feature_dir(feature_id).exists():
            ms = now.microsecond // 1000
            feature_id = f"{timestamp}-{ms:03d}_{slug}" if slug else f"{timestamp}-{ms:03d}"

        resolved_branch = branch if branch else f"feat/{feature_id}"

        metadata: dict = {"branch": resolved_branch}
        if base_branch:
            metadata["base_branch"] = base_branch

        feature = Feature(
            id=feature_id,
            title=title or slug,
            status=FeatureStatus.PENDING,
            created_at=now.isoformat(),
            priority=max(0, min(100, priority)),
            metadata=metadata,
        )
        feature_dir = self._feature_dir(feature_id)
        feature_dir.mkdir(parents=True, exist_ok=True)
        (feature_dir / "input").mkdir(exist_ok=True)
        self._write_feature(feature)
        return feature

    def get_next(self, agent_name: str | None = None) -> Feature | None:
        """Return the earliest pending feature whose dependencies are met.

        Args:
            agent_name: If given, only return features that have a
                        ``task_list.<agent_name>.json`` in the feature dir.
                        If None, return any pending feature.
        """
        pending = self.list_features(agent_name=agent_name, status=FeatureStatus.PENDING)
        if not pending:
            return None
        # Higher priority first; break ties by creation time (earliest first)
        sorted_pending = sorted(pending, key=lambda t: (-t.priority, t.created_at))
        for candidate in sorted_pending:
            if not self._phase_deps_met(candidate):
                continue
            return candidate
        return None

    def _phase_deps_met(self, feature: Feature) -> bool:
        """Check if all phase-level dependencies are completed.

        Returns True if:
        - Feature has no phase_id (not part of a phase — backward compat)
        - Feature has no phase_depends_on (root in phase)
        - All depended-on features are COMPLETED
        """
        phase_id = feature.metadata.get("phase_id")
        if not phase_id:
            return True

        phase_deps = feature.metadata.get("phase_depends_on", [])
        if not phase_deps:
            return True

        # Load phase manifest to resolve step_id → feature_id
        phases_dir = self._features_dir.parent / "phases"
        phase_yaml = phases_dir / phase_id / "phase.yaml"
        if not phase_yaml.exists():
            return True  # manifest missing — don't block

        try:
            import yaml as _yaml
            with open(phase_yaml, encoding="utf-8") as f:
                phase_data = _yaml.safe_load(f) or {}
        except Exception:
            return True  # parse error — don't block

        step_to_fid = {
            feat["step_id"]: feat["feature_id"]
            for feat in phase_data.get("features", [])
        }

        for dep_step_id in phase_deps:
            dep_fid = step_to_fid.get(dep_step_id)
            if not dep_fid:
                continue  # unknown dep — don't block
            dep_feature = self._read_feature(dep_fid)
            if dep_feature is None or dep_feature.status != FeatureStatus.COMPLETED:
                return False

        return True

    def get(self, feature_id: str) -> Feature | None:
        """Return a specific feature by ID, or None if not found."""
        return self._read_feature(feature_id)

    def update_status(
        self, feature_id: str, status: FeatureStatus, error: str | None = None
    ) -> None:
        """Update feature status and set timestamps accordingly."""
        feature = self._read_feature(feature_id)
        if feature is None:
            raise ValueError(f"Feature not found: {feature_id}")

        feature.status = status
        now = datetime.datetime.now().astimezone().isoformat()

        if status == FeatureStatus.RUNNING:
            feature.started_at = now
        elif status in (FeatureStatus.COMPLETED, FeatureStatus.PARTIAL, FeatureStatus.FAILED):
            feature.completed_at = now

        if error is not None:
            feature.error = error

        self._write_feature(feature)

    def update_metadata(self, feature_id: str, metadata: dict) -> None:
        """Merge metadata into the feature's metadata dict."""
        feature = self._read_feature(feature_id)
        if feature is None:
            raise ValueError(f"Feature not found: {feature_id}")
        feature.metadata.update(metadata)
        self._write_feature(feature)

    def list_features(
        self,
        agent_name: str | None = None,
        status: FeatureStatus | None = None,
    ) -> list[Feature]:
        """List features, optionally filtered by agent and/or status.

        Args:
            agent_name: If given, only include features that have a
                        ``task_list.<agent_name>.json`` file.
            status: If given, only include features with this status.
        """
        features = []
        if not self._features_dir.exists():
            return features
        for feature_dir in sorted(self._features_dir.iterdir()):
            if not feature_dir.is_dir():
                continue
            feature = self._read_feature(feature_dir.name)
            if feature is None:
                continue
            if agent_name and not self._has_agent_work(feature.id, agent_name):
                continue
            if status and feature.status != status:
                continue
            features.append(feature)
        return features

    def feature_workspace(self, feature_id: str) -> Path:
        """Return the feature's directory."""
        return self._feature_dir(feature_id)

    # ------------------------------------------------------------------
    # Step management
    # ------------------------------------------------------------------

    def _get_step_status(self, feature: Feature, step_id: str) -> str:
        """Compute effective status: 'ready' if stored as pending and all deps completed."""
        step = next((s for s in feature.steps if s.id == step_id), None)
        if step is None:
            return STEP_PENDING
        # Non-pending states are stored as-is
        if step.status != STEP_PENDING:
            return step.status
        # Check deps
        for dep_id in step.depends_on:
            dep = next((s for s in feature.steps if s.id == dep_id), None)
            if dep is None or dep.status != STEP_COMPLETED:
                return STEP_PENDING  # blocked
        return STEP_READY

    def get_next_ready_step(self, feature_id: str) -> FeatureStep | None:
        """Return the first step whose deps are all completed and status is pending."""
        feature = self._read_feature(feature_id)
        if feature is None or not feature.steps:
            return None
        for step in feature.steps:
            if self._get_step_status(feature, step.id) == STEP_READY:
                return step
        return None

    def needs_review(self, feature_id: str) -> list[FeatureStep]:
        """Return steps waiting for human review."""
        feature = self._read_feature(feature_id)
        if feature is None:
            return []
        return [s for s in feature.steps if s.status == STEP_NEEDS_REVIEW]

    def all_steps_done(self, feature_id: str) -> bool:
        """True if all steps are completed or skipped."""
        feature = self._read_feature(feature_id)
        if feature is None or not feature.steps:
            return True
        return all(
            s.status in (STEP_COMPLETED, STEP_SKIPPED)
            for s in feature.steps
        )

    def update_step_status(
        self, feature_id: str, step_id: str, status: str, note: str = "",
    ) -> None:
        """Update a step's status and optional note."""
        feature = self._read_feature(feature_id)
        if feature is None:
            raise ValueError(f"Feature not found: {feature_id}")
        step = next((s for s in feature.steps if s.id == step_id), None)
        if step is None:
            raise ValueError(f"Step not found: {step_id} in feature {feature_id}")
        step.status = status
        if note:
            step.note = note
        self._write_feature(feature)
