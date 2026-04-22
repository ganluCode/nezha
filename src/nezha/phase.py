"""Phase: batch feature orchestration with inter-feature dependency DAG.

A Phase groups multiple features into a dependency graph (outer DAG).
Each feature within a phase may contain its own task_list.json (inner DAG).

Usage:
    nezha phase plan phase.yaml       # batch create features + run planner
    nezha phase show <phase-id>       # show outer DAG status
    nezha phase list                  # list all phases
"""

from __future__ import annotations

import datetime
import re
import shutil
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class PhaseStatus(str, Enum):
    PLANNED = "planned"        # all features created (planner may have run)
    RUNNING = "running"        # at least one feature executing
    COMPLETED = "completed"    # all features completed
    PARTIAL = "partial"        # some features failed/partial
    FAILED = "failed"          # phase-level failure


@dataclass
class PhaseFeatureRef:
    """Reference to a feature within a phase."""

    step_id: str           # phase-internal short ID (e.g. "db-schema")
    feature_id: str        # generated feature ID (e.g. "2026-04-17-10-30-00_db-schema")
    title: str
    depends_on: list[str] = field(default_factory=list)  # step IDs
    priority: int = 50


@dataclass
class Phase:
    """A group of related features with inter-feature dependencies."""

    id: str
    title: str
    status: PhaseStatus
    created_at: str
    base_branch: str = "main"
    agent: str = ""           # default agent hint (for run command suggestion)
    features: list[PhaseFeatureRef] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Input parsing & validation
# ---------------------------------------------------------------------------


def load_phase_input(path: Path) -> dict:
    """Parse a user-authored phase.yaml input file.

    Returns the raw dict. Raises ValueError on missing required fields.
    """
    import yaml

    if not path.exists():
        raise FileNotFoundError(f"Phase file not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not data.get("title"):
        raise ValueError("Phase YAML must have a 'title' field")

    features = data.get("features")
    if not features or not isinstance(features, list):
        raise ValueError("Phase YAML must have a non-empty 'features' list")

    for i, feat in enumerate(features):
        if not feat.get("id"):
            raise ValueError(f"Feature at index {i} must have an 'id' field")
        if not feat.get("title"):
            raise ValueError(f"Feature '{feat.get('id')}' must have a 'title' field")

    return data


def validate_phase_dag(features: list[dict]) -> None:
    """Validate the feature dependency graph.

    Checks:
    - No duplicate step IDs
    - All depends_on references exist
    - No cycles (Kahn's algorithm)

    Raises ValueError on any violation.
    """
    ids = [f["id"] for f in features]

    # Duplicate check
    seen = set()
    for fid in ids:
        if fid in seen:
            raise ValueError(f"Duplicate feature ID: '{fid}'")
        seen.add(fid)

    id_set = set(ids)

    # Missing dependency check
    for feat in features:
        for dep in feat.get("depends_on", []):
            if dep not in id_set:
                raise ValueError(
                    f"Feature '{feat['id']}' depends on '{dep}' which does not exist"
                )
            if dep == feat["id"]:
                raise ValueError(f"Feature '{feat['id']}' depends on itself")

    # Cycle detection (Kahn's algorithm)
    in_degree = {fid: 0 for fid in ids}
    children: dict[str, list[str]] = {fid: [] for fid in ids}
    for feat in features:
        for dep in feat.get("depends_on", []):
            in_degree[feat["id"]] += 1
            children[dep].append(feat["id"])

    queue = deque(fid for fid, deg in in_degree.items() if deg == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for child in children[node]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    if visited != len(ids):
        # Find cycle members for error message
        cycle_members = [fid for fid, deg in in_degree.items() if deg > 0]
        raise ValueError(f"Dependency cycle detected among: {cycle_members}")


def topo_sort(features: list[dict]) -> list[dict]:
    """Topologically sort features by depends_on.

    Returns features in execution order (roots first).
    Assumes validate_phase_dag() has already been called.
    """
    id_to_feat = {f["id"]: f for f in features}
    in_degree = {f["id"]: 0 for f in features}
    children: dict[str, list[str]] = {f["id"]: [] for f in features}

    for feat in features:
        for dep in feat.get("depends_on", []):
            in_degree[feat["id"]] += 1
            children[dep].append(feat["id"])

    # Use a priority queue to maintain stable ordering within same level
    queue = sorted(
        [fid for fid, deg in in_degree.items() if deg == 0],
        key=lambda fid: id_to_feat[fid].get("priority", 50),
        reverse=True,
    )
    result = []
    while queue:
        node = queue.pop(0)
        result.append(id_to_feat[node])
        ready = []
        for child in children[node]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                ready.append(child)
        # Insert ready nodes in priority order
        ready.sort(key=lambda fid: id_to_feat[fid].get("priority", 50), reverse=True)
        queue = sorted(queue + ready, key=lambda fid: id_to_feat[fid].get("priority", 50), reverse=True)

    return result


def compute_branch_chain(
    features: list[dict],
    feature_ids: dict[str, str],
    base_branch: str = "main",
) -> dict[str, str]:
    """Compute base_branch for each feature using linear chain order.

    Branch chaining is ALWAYS LINEAR regardless of DAG structure:
    each feature bases on the PREVIOUS feature in topological order.
    This ensures every feature has ALL previous features' code.

    DAG controls execution order (parallelism), but branches are sequential.

    Example:
        DAG:  A → B, A → C, B+C → D
        Topo: A, B, C, D
        Chain: A(base:main) → B(base:A) → C(base:B) → D(base:C)

    Args:
        features: topologically sorted feature dicts
        feature_ids: mapping step_id -> generated feature_id
        base_branch: base branch for the first feature

    Returns:
        dict mapping step_id -> base_branch name
    """
    result: dict[str, str] = {}
    prev_feature_id: str | None = None

    for feat in features:
        if prev_feature_id is None:
            result[feat["id"]] = base_branch
        else:
            result[feat["id"]] = f"feat/{prev_feature_id}"
        prev_feature_id = feature_ids[feat["id"]]

    return result


# ---------------------------------------------------------------------------
# Phase store (file-based persistence)
# ---------------------------------------------------------------------------


class FilePhaseStore:
    """CRUD operations for Phase manifests stored on disk."""

    def __init__(self, workspace_base: Path):
        self._phases_dir = workspace_base / "phases"

    def save(self, phase: Phase) -> Path:
        """Save phase manifest to disk. Returns the phase directory path."""
        import yaml

        phase_dir = self._phases_dir / phase.id
        phase_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "id": phase.id,
            "title": phase.title,
            "status": phase.status.value,
            "created_at": phase.created_at,
            "base_branch": phase.base_branch,
            "agent": phase.agent,
            "features": [
                {
                    "step_id": ref.step_id,
                    "feature_id": ref.feature_id,
                    "title": ref.title,
                    "depends_on": ref.depends_on,
                    "priority": ref.priority,
                }
                for ref in phase.features
            ],
        }

        phase_path = phase_dir / "phase.yaml"
        phase_path.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        return phase_dir

    def get(self, phase_id: str) -> Phase | None:
        """Load a phase by ID. Returns None if not found."""
        import yaml

        phase_path = self._phases_dir / phase_id / "phase.yaml"
        if not phase_path.exists():
            return None

        with open(phase_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return Phase(
            id=data["id"],
            title=data.get("title", ""),
            status=PhaseStatus(data.get("status", "planned")),
            created_at=data.get("created_at", ""),
            base_branch=data.get("base_branch", "main"),
            agent=data.get("agent", ""),
            features=[
                PhaseFeatureRef(
                    step_id=f["step_id"],
                    feature_id=f["feature_id"],
                    title=f.get("title", ""),
                    depends_on=f.get("depends_on", []),
                    priority=f.get("priority", 50),
                )
                for f in data.get("features", [])
            ],
        )

    def list_phases(self) -> list[Phase]:
        """List all phases, sorted by created_at descending."""
        if not self._phases_dir.exists():
            return []
        phases = []
        for phase_dir in sorted(self._phases_dir.iterdir()):
            if phase_dir.is_dir():
                phase = self.get(phase_dir.name)
                if phase:
                    phases.append(phase)
        return phases

    def update_status(self, phase_id: str, status: PhaseStatus) -> None:
        """Update phase status."""
        phase = self.get(phase_id)
        if phase is None:
            raise ValueError(f"Phase not found: {phase_id}")
        phase.status = status
        self.save(phase)


# ---------------------------------------------------------------------------
# Phase planning (batch feature creation)
# ---------------------------------------------------------------------------


def _generate_phase_id(title: str) -> str:
    """Generate a phase ID from timestamp + slugified title."""
    now = datetime.datetime.now().astimezone()
    timestamp = now.strftime("%Y-%m-%d-%H-%M-%S")
    slug = re.sub(r"[^a-z0-9_\s-]", "", title.lower().strip())
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    if slug:
        return f"{timestamp}_{slug}"
    return timestamp


async def plan_phase(
    phase_input_path: Path,
    config_path: str = "executor.yaml",
    base_branch: str = "main",
    skip_planner: bool = False,
) -> Phase:
    """Create features from a phase YAML file.

    This is the main orchestration function:
    1. Load + validate phase input
    2. Topologically sort features
    3. Batch create features with auto-chained branches
    4. Optionally run planner for each
    5. Save phase manifest

    Args:
        phase_input_path: Path to user-authored phase.yaml
        config_path: Path to executor.yaml
        base_branch: Git base branch for root features
        skip_planner: If True, skip running planner-agent

    Returns:
        Phase object with all feature references
    """
    from nezha.config import load_executor_config, load_agent_config
    from nezha.executor import _find_callable_planner, _run_planner_for_task_list
    from nezha.feature_queue import FeatureStatus, FileFeatureQueue

    # --- Load & validate ---
    raw = load_phase_input(phase_input_path)
    phase_base_branch = raw.get("base_branch", base_branch)
    agent_hint = raw.get("agent", "")
    features_raw = raw["features"]

    validate_phase_dag(features_raw)
    sorted_features = topo_sort(features_raw)

    # --- Resolve paths ---
    base_dir = Path(config_path).parent.resolve()
    executor_config = load_executor_config(config_path)
    ws_base_raw = Path(executor_config.workspace.base)
    workspace_base = (
        ws_base_raw if ws_base_raw.is_absolute() else base_dir / ws_base_raw
    ).resolve()

    queue = FileFeatureQueue(workspace_base)
    store = FilePhaseStore(workspace_base)

    # --- Check for existing phase (idempotent re-run) ---
    existing_phase: Phase | None = None
    existing_step_map: dict[str, PhaseFeatureRef] = {}

    # Scan existing phases for one with matching title
    for p in store.list_phases():
        if p.title == raw["title"]:
            existing_phase = p
            existing_step_map = {ref.step_id: ref for ref in p.features}
            print(f"[phase] Found existing phase: {p.id} — checking feature status...")
            break

    phase_id = existing_phase.id if existing_phase else _generate_phase_id(raw["title"])

    # --- Find planner (if needed) ---
    planner_config = None
    if not skip_planner:
        planner_config = _find_callable_planner(base_dir, executor_config)
        if planner_config:
            print(f"[phase] Found planner: {planner_config.agent.name}")
        else:
            print("[phase] No callable planner found, skipping auto-plan")

    # --- Batch create features ---
    feature_ids: dict[str, str] = {}  # step_id -> feature_id
    phase_refs: list[PhaseFeatureRef] = []
    input_base_dir = phase_input_path.parent

    import time

    total = len(sorted_features)
    planner_failures: list[str] = []
    skipped = 0
    created = 0
    prev_feature_id: str | None = None  # for linear branch chaining

    for i, feat in enumerate(sorted_features):
        step_id = feat["id"]
        title = feat["title"]
        priority = feat.get("priority", 50)
        deps = feat.get("depends_on", [])

        # --- Idempotent check: does this feature already exist and is OK? ---
        existing_ref = existing_step_map.get(step_id)
        if existing_ref:
            existing_feature = queue.get(existing_ref.feature_id)
            if existing_feature:
                feature_workspace = queue.feature_workspace(existing_ref.feature_id)
                task_list = feature_workspace / "task_list.json"
                has_task_list = task_list.exists()

                # Feature is OK: exists + has task_list (or planner skipped)
                if existing_feature.status in (
                    FeatureStatus.PENDING, FeatureStatus.COMPLETED,
                    FeatureStatus.RUNNING,
                ) and (has_task_list or skip_planner):
                    feature_ids[step_id] = existing_ref.feature_id
                    phase_refs.append(existing_ref)
                    task_count = ""
                    if has_task_list:
                        try:
                            import json
                            tasks = json.loads(task_list.read_text(encoding="utf-8"))
                            task_count = f" ({len(tasks)} tasks)"
                        except Exception:
                            pass
                    print(f"[phase] Feature {i+1}/{total}: {title} — SKIP (exists{task_count})")
                    skipped += 1
                    prev_feature_id = existing_ref.feature_id
                    continue

                # Feature exists but broken (failed/partial, or missing task_list)
                # Re-use feature ID, just re-run planner
                feature_ids[step_id] = existing_ref.feature_id
                print(f"[phase] Feature {i+1}/{total}: {title} — RETRY (status={existing_feature.status.value}, task_list={'yes' if has_task_list else 'no'})")

                # Reset to pending if failed
                if existing_feature.status in (FeatureStatus.FAILED, FeatureStatus.PARTIAL):
                    queue.update_status(existing_ref.feature_id, FeatureStatus.PENDING)

                # Run planner for this feature
                if planner_config and not skip_planner and not has_task_list:
                    print(f"[phase] Running planner for: {title}...")
                    merged_env = {**(executor_config.env or {})}
                    project_dir = (workspace_base / "project").resolve()
                    if not project_dir.exists():
                        project_dir = None
                    planner_result = await _run_planner_for_task_list(
                        planner_config=planner_config,
                        executor_config=executor_config,
                        feature_workspace=feature_workspace,
                        base_dir=base_dir,
                        merged_env=merged_env,
                        project_dir=project_dir,
                    )
                    if planner_result == "success":
                        if task_list.exists():
                            tasks = json.loads(task_list.read_text(encoding="utf-8"))
                            print(f"[phase] Planner completed: {len(tasks)} tasks")
                    else:
                        print(f"[phase] Planner FAILED for: {title}")
                        planner_failures.append(step_id)

                phase_refs.append(existing_ref)
                prev_feature_id = existing_ref.feature_id
                continue

        # --- New feature: linear branch chain (each bases on previous) ---
        if prev_feature_id is None:
            feat_base_branch = phase_base_branch
        else:
            feat_base_branch = f"feat/{prev_feature_id}"

        # Create feature
        feature = queue.create(
            title=title,
            priority=priority,
            base_branch=feat_base_branch,
        )
        feature_ids[step_id] = feature.id
        prev_feature_id = feature.id
        created += 1

        # Set phase metadata
        queue.update_metadata(feature.id, {
            "phase_id": phase_id,
            "step_id": step_id,
            "phase_depends_on": deps,
        })

        # Copy input files
        input_files = feat.get("input", [])
        if input_files:
            feature_input_dir = queue.feature_workspace(feature.id) / "input"
            feature_input_dir.mkdir(parents=True, exist_ok=True)
            for input_path in input_files:
                src = input_base_dir / input_path
                if src.exists():
                    dst = feature_input_dir / src.name
                    shutil.copy2(src, dst)
                else:
                    print(f"[phase] Warning: input file not found: {src}")

        branch = feature.metadata.get("branch", f"feat/{feature.id}")
        print(f"[phase] Feature {i+1}/{total}: {title}")
        print(f"  ID: {feature.id}")
        print(f"  Branch: {branch} (base: {feat_base_branch})")

        # Run planner
        if planner_config and not skip_planner:
            print(f"[phase] Running planner for: {title}...")
            feature_workspace = queue.feature_workspace(feature.id)
            merged_env = {**(executor_config.env or {})}

            project_dir = (workspace_base / "project").resolve()
            if not project_dir.exists():
                project_dir = None

            planner_result = await _run_planner_for_task_list(
                planner_config=planner_config,
                executor_config=executor_config,
                feature_workspace=feature_workspace,
                base_dir=base_dir,
                merged_env=merged_env,
                project_dir=project_dir,
            )
            if planner_result == "success":
                task_list = feature_workspace / "task_list.json"
                if task_list.exists():
                    import json
                    tasks = json.loads(task_list.read_text(encoding="utf-8"))
                    print(f"[phase] Planner completed: {len(tasks)} tasks")
                else:
                    print(f"[phase] Planner completed (no task count available)")
            else:
                print(f"[phase] Planner FAILED for: {title}")
                planner_failures.append(step_id)

        phase_refs.append(PhaseFeatureRef(
            step_id=step_id,
            feature_id=feature.id,
            title=title,
            depends_on=deps,
            priority=priority,
        ))

        # Small delay to avoid timestamp collision
        if i < total - 1:
            time.sleep(0.05)

    # --- Save phase manifest ---
    now = datetime.datetime.now().astimezone()
    status = PhaseStatus.PLANNED if not planner_failures else PhaseStatus.PARTIAL
    phase = Phase(
        id=phase_id,
        title=raw["title"],
        status=status,
        created_at=existing_phase.created_at if existing_phase else now.isoformat(),
        base_branch=phase_base_branch,
        agent=agent_hint,
        features=phase_refs,
    )
    store.save(phase)

    print(f"\n[phase] Phase: {phase_id}")
    print(f"  Features: {total} (created: {created}, skipped: {skipped}, retried: {total - created - skipped})")
    if planner_failures:
        print(f"  Planner failures: {planner_failures}")
    print(f"  Manifest: {workspace_base}/phases/{phase_id}/phase.yaml")

    return phase
