"""Backward-compatibility shim for task_queue module.

The canonical implementation has moved to feature_queue.py.
This module re-exports the old names so that existing code and tests
continue to work without modification.

Mapping:
    TaskStatus      → FeatureStatus
    Task            → Feature
    TaskQueue       → FeatureQueue
    FileTaskQueue   → FileFeatureQueue (wrapping features/ dir layout)
    split_feature_list → split_task_list (with legacy feature_list.json support)
"""

import warnings

from nezha.feature_queue import (
    FeatureStatus as TaskStatus,
    Feature as Task,
    FeatureQueue as TaskQueue,
    FileFeatureQueue as _FileFeatureQueue,
    _slugify,
    split_task_list as split_feature_list,
)
from pathlib import Path


__all__ = [
    "TaskStatus",
    "Task",
    "TaskQueue",
    "FileTaskQueue",
    "split_feature_list",
    "_slugify",
]


class FileTaskQueue(_FileFeatureQueue):
    """Backward-compatible wrapper that stores data in tasks/ (not features/).

    This keeps legacy workspaces working without migration.  New code should
    use FileFeatureQueue directly (which uses features/).
    """

    def __init__(self, base_dir: Path):
        # Bypass FileFeatureQueue.__init__ so we can set _features_dir to tasks/
        from nezha.feature_queue import FeatureStatus  # noqa: F401
        self._features_dir = base_dir / "tasks"
        self._features_dir.mkdir(parents=True, exist_ok=True)

    # Override so legacy yaml name (task.yaml) is used for new writes too.
    def _write_feature(self, feature) -> None:
        yaml_path = self._feature_dir(feature.id) / "task.yaml"
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        import yaml as _yaml
        data = {
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
        with open(yaml_path, "w", encoding="utf-8") as f:
            _yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    # Override agent work check to use feature_list.* (legacy naming).
    def _has_agent_work(self, feature_id: str, agent_name: str) -> bool:
        feature_dir = self._feature_dir(feature_id)
        # Legacy: feature_list.<agent>.json
        if (feature_dir / f"feature_list.{agent_name}.json").exists():
            return True
        # New name fallback
        return (feature_dir / f"task_list.{agent_name}.json").exists()

    # Expose backward-compat method aliases
    def list_tasks(self, agent_name=None, status=None):
        return self.list_features(agent_name=agent_name, status=status)

    def task_workspace(self, task_id: str) -> Path:
        return self.feature_workspace(task_id)
