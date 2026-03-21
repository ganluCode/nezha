"""State writer event handler: maintains executor_status.json and history."""

import json
import time
from datetime import datetime
from pathlib import Path

from nezha.events.bus import EventHandler
from nezha.events.types import Event, EventType


class StateWriterHandler(EventHandler):
    """Maintain executor_status.json with real-time state.

    Also writes session history to state/history/.

    When ``feature_id`` is provided (parallel execution), the status file is
    written as ``executor_status_{feature_id}.json`` to avoid concurrent writes
    to the same file from multiple parallel features.
    """

    def __init__(self, status_path: str | Path, state_dir: str | Path,
                 feature_id: str = ""):
        self._status_path = Path(status_path)
        if feature_id:
            stem = self._status_path.stem
            suffix = self._status_path.suffix
            self._status_path = self._status_path.with_name(
                f"{stem}_{feature_id}{suffix}"
            )
        self._feature_id = feature_id
        self._state_dir = Path(state_dir)
        self._history_dir = self._state_dir / "history"
        self._status_path.parent.mkdir(parents=True, exist_ok=True)
        self._history_dir.mkdir(parents=True, exist_ok=True)

        self._state = {
            "status": "idle",
            "current_agent": None,
            "session_id": 0,
            "started_at": None,
            "last_updated": None,
            "progress": {},
            "guards": {},
            "rework_stats": {
                "total_reworks": 0,
                "active_reworks": 0,
                "features_in_rework": [],
            },
        }

    async def handle(self, event: Event) -> None:
        et = event.event_type

        if et == EventType.EXECUTOR_STARTED:
            self._state["status"] = "running"
            self._state["started_at"] = datetime.fromtimestamp(event.timestamp).isoformat()

        elif et == EventType.EXECUTOR_STOPPED:
            self._state["status"] = "idle"
            self._state["current_agent"] = None

        elif et == EventType.SESSION_STARTED:
            self._state["status"] = "executing"
            self._state["current_agent"] = event.agent_name
            self._state["session_id"] = event.session_id

        elif et == EventType.SESSION_COMPLETED:
            self._state["status"] = "running"
            self._state["progress"] = event.payload
            # Write to history
            self._write_history(event)

        elif et == EventType.SESSION_ERROR:
            self._state["status"] = "error"
            self._state["progress"] = event.payload
            self._write_history(event)

        elif et == EventType.GUARD_BLOCKED:
            self._state["status"] = "blocked"
            self._state["guards"]["last_block"] = event.payload.get("reason", "")

        elif et == EventType.GUARD_PASSED:
            self._state["guards"]["last_block"] = None

        elif et == EventType.FEATURE_REWORK_TRIGGERED:
            stats = self._state["rework_stats"]
            stats["total_reworks"] += 1
            fid = event.payload.get("feature_id", "")
            if fid and fid not in stats["features_in_rework"]:
                stats["features_in_rework"].append(fid)
            stats["active_reworks"] = len(stats["features_in_rework"])
            self._write_history(event)

        elif et == EventType.FEATURE_REWORK_COMPLETED:
            stats = self._state["rework_stats"]
            fid = event.payload.get("feature_id", "")
            if fid in stats["features_in_rework"]:
                stats["features_in_rework"].remove(fid)
            stats["active_reworks"] = len(stats["features_in_rework"])
            self._write_history(event)

        elif et == EventType.FEATURE_REWORK_FAILED:
            self._write_history(event)

        elif et in (EventType.VIBE_SESSION_STARTED, EventType.VIBE_SESSION_ENDED):
            if et == EventType.VIBE_SESSION_STARTED:
                self._state["status"] = "vibe"
            else:
                self._state["status"] = "running"
            self._write_history(event)

        elif et == EventType.DAG_LOADED:
            summary = event.payload.get("summary", {})
            self._state["dag"] = {
                "total": summary.get("total", 0),
                "counts": summary.get("counts", {}),
            }

        elif et == EventType.DAG_FEATURE_STARTED:
            self._state["dag_current_feature"] = event.payload.get("feature_id")
            self._write_history(event)

        elif et == EventType.DAG_FEATURE_COMPLETED:
            self._state["dag_current_feature"] = None
            self._write_history(event)

        elif et == EventType.DAG_FEATURE_BLOCKED:
            self._write_history(event)

        elif et == EventType.DAG_DEADLOCKED:
            self._state["status"] = "deadlocked"
            self._write_history(event)

        elif et == EventType.DAG_ALL_COMPLETED:
            self._state["status"] = "all_completed"
            self._write_history(event)

        self._state["last_updated"] = datetime.fromtimestamp(event.timestamp).isoformat()
        self._flush()

    def _flush(self):
        """Write current state to disk."""
        with open(self._status_path, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2, ensure_ascii=False)

    def _write_history(self, event: Event):
        """Append a session record to history."""
        ts = datetime.fromtimestamp(event.timestamp).strftime("%Y%m%d_%H%M%S")
        filename = f"{event.agent_name}_{ts}.json"
        filepath = self._history_dir / filename

        record = {
            "agent": event.agent_name,
            "session_id": event.session_id,
            "timestamp": datetime.fromtimestamp(event.timestamp).isoformat(),
            "event_type": event.event_type.value,
            **event.payload,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)

    async def close(self) -> None:
        self._state["status"] = "idle"
        self._state["last_updated"] = datetime.now().isoformat()
        self._flush()
