"""File logger event handler: writes events to session log files."""

import time
from datetime import datetime
from pathlib import Path

from nezha.events.bus import EventHandler
from nezha.events.types import Event, EventType


class FileLoggerHandler(EventHandler):
    """Write events to a log file in state/logs/.

    Creates one log file per session: ``{agent}_{timestamp}.log``
    When ``feature_id`` is provided (parallel execution), the filename
    becomes ``{agent}_{feature_id}_{timestamp}.log`` for clear separation.
    """

    def __init__(self, logs_dir: str | Path, feature_id: str = ""):
        self._logs_dir = Path(logs_dir)
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        self._feature_id = feature_id
        self._current_file = None
        self._file_handle = None

    async def handle(self, event: Event) -> None:
        # Open a new file on session start
        if event.event_type == EventType.SESSION_STARTED:
            self._open_new_file(event)

        # Write event line
        if self._file_handle is None:
            # No session started yet, create a general log
            self._open_new_file(event)

        ts = datetime.fromtimestamp(event.timestamp).strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {event.event_type.value}"

        if event.payload:
            # Compact payload for readability
            details = _format_payload(event)
            if details:
                line += f" | {details}"

        self._file_handle.write(line + "\n")
        self._file_handle.flush()

    def _open_new_file(self, event: Event):
        """Open a new log file."""
        if self._file_handle:
            self._file_handle.close()

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        agent = event.agent_name or "unknown"
        if self._feature_id:
            filename = f"{agent}_{self._feature_id}_{ts}.log"
        else:
            filename = f"{agent}_{ts}.log"
        filepath = self._logs_dir / filename

        self._file_handle = open(filepath, "a", encoding="utf-8")
        self._current_file = filepath

    async def close(self) -> None:
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None


def _format_payload(event: Event) -> str:
    """Format event payload for log readability."""
    p = event.payload
    et = event.event_type

    if et == EventType.AGENT_THINKING:
        text = p.get("text", "")
        return text[:120] + "..." if len(text) > 120 else text
    elif et == EventType.AGENT_TOOL_CALL:
        return f"tool={p.get('tool', '?')}"
    elif et == EventType.AGENT_TOOL_RESULT:
        status = "OK" if p.get("success") else "ERROR"
        return f"[{status}]"
    elif et == EventType.SESSION_COMPLETED:
        return (
            f"status={p.get('status')} turns={p.get('num_turns')} "
            f"cost=${p.get('cost_usd', 0):.4f} time={p.get('duration_ms')}ms"
        )
    elif et == EventType.GUARD_BLOCKED:
        return p.get("reason", "")
    elif et == EventType.FEATURE_REWORK_TRIGGERED:
        return (
            f"{p.get('feature_id', '?')} | "
            f"reason: {p.get('reason', '?')} | "
            f"source: {p.get('source', '?')}"
        )
    elif et == EventType.FEATURE_REWORK_COMPLETED:
        return (
            f"{p.get('feature_id', '?')} | "
            f"rework_count: {p.get('rework_count', 0)}"
        )
    elif et == EventType.FEATURE_REWORK_FAILED:
        return (
            f"{p.get('feature_id', '?')} | "
            f"rework_count: {p.get('rework_count', 0)} | "
            f"note: {p.get('rework_note', '?')}"
        )
    elif et == EventType.VIBE_SESSION_STARTED:
        return f"instruction: {p.get('instruction', '?')[:100]}"
    elif et == EventType.VIBE_SESSION_ENDED:
        return f"status={p.get('status', '?')} cost=${p.get('cost_usd', 0):.4f}"
    elif et == EventType.DAG_LOADED:
        summary = p.get("summary", {})
        counts = summary.get("counts", {})
        return (
            f"total={summary.get('total', '?')} "
            f"completed={counts.get('completed', 0)} "
            f"ready={counts.get('ready', 0)} "
            f"blocked={counts.get('blocked', 0)}"
        )
    elif et == EventType.DAG_FEATURE_STARTED:
        rework = " (rework)" if p.get("is_rework") else ""
        return f"{p.get('feature_id', '?')}{rework}"
    elif et == EventType.DAG_FEATURE_COMPLETED:
        rework = " (was rework)" if p.get("was_rework") else ""
        return f"{p.get('feature_id', '?')}{rework}"
    elif et == EventType.DAG_FEATURE_BLOCKED:
        downstream = p.get("blocked_downstream", [])
        return (
            f"{p.get('feature_id', '?')} | "
            f"blocking {len(downstream)} downstream"
        )
    elif et == EventType.DAG_DEADLOCKED:
        return f"blocked={p.get('blocked', 0)}"
    elif et == EventType.DAG_ALL_COMPLETED:
        summary = p.get("summary", {})
        return f"total={summary.get('total', '?')}"
    else:
        # Generic: just show keys
        return ", ".join(f"{k}={v}" for k, v in p.items()) if p else ""
