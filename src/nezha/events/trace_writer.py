"""Execution path trace writer: records tool call sequences per session."""

import json
import time
from datetime import datetime
from pathlib import Path

from nezha.events.bus import EventHandler
from nezha.events.types import Event, EventType


class TraceWriterHandler(EventHandler):
    """Collect tool_call/tool_result events during a session and write a trace file.

    Trace files are written to state/traces/ when a session completes.
    Each session gets its own trace file (no overwriting).
    """

    def __init__(self, state_dir: str | Path):
        self._traces_dir = Path(state_dir) / "traces"
        self._traces_dir.mkdir(parents=True, exist_ok=True)

        # Per-session accumulator
        self._current_agent = ""
        self._session_id = 0
        self._session_start = 0.0
        self._steps: list[dict] = []
        self._step_counter = 0
        self._files_modified: set[str] = set()

    async def handle(self, event: Event) -> None:
        et = event.event_type

        if et == EventType.SESSION_STARTED:
            # Write previous session if it has data (in case SESSION_COMPLETED was missed)
            if self._steps:
                self._write_trace_internal(
                    status="interrupted",
                    cost_usd=None,
                    duration_ms=int((time.time() - self._session_start) * 1000),
                )

            # Start new session accumulator
            self._current_agent = event.agent_name
            self._session_id = event.session_id
            self._session_start = event.timestamp
            self._steps = []
            self._step_counter = 0
            self._files_modified = set()

        elif et == EventType.AGENT_TOOL_CALL:
            self._step_counter += 1
            tool = event.payload.get("tool", "")
            tool_input = event.payload.get("input", {})

            step = {
                "step": self._step_counter,
                "type": "tool_call",
                "tool": tool,
                "timestamp": datetime.fromtimestamp(event.timestamp).isoformat(),
            }

            # Extract file path for tracking modified files
            if isinstance(tool_input, dict):
                file_path = tool_input.get("file_path") or tool_input.get("path", "")
                if file_path:
                    step["input"] = file_path
                    if tool in ("Write", "Edit"):
                        self._files_modified.add(file_path)
                command = tool_input.get("command", "")
                if command:
                    step["input"] = command[:200]
            elif isinstance(tool_input, str):
                step["input"] = tool_input[:200]

            self._steps.append(step)

        elif et == EventType.AGENT_TOOL_RESULT:
            success = event.payload.get("success", True)
            # Update last step with result
            if self._steps:
                last = self._steps[-1]
                last["success"] = success
                if not success:
                    content = event.payload.get("content", "")
                    last["error_summary"] = str(content)[:300]

        elif et in (EventType.SESSION_COMPLETED, EventType.SESSION_ERROR):
            self._write_trace(event)

    def _write_trace(self, event: Event):
        """Write accumulated trace to file."""
        if not self._steps:
            return

        self._write_trace_internal(
            status=event.payload.get("status", event.event_type.value),
            cost_usd=event.payload.get("cost_usd"),
            input_tokens=event.payload.get("input_tokens", 0),
            output_tokens=event.payload.get("output_tokens", 0),
            duration_ms=event.payload.get(
                "duration_ms",
                int((event.timestamp - self._session_start) * 1000),
            ),
        )

    def _write_trace_internal(
        self, status: str, cost_usd: float | None, duration_ms: int,
        input_tokens: int = 0, output_tokens: int = 0,
    ):
        """Write trace JSON to a uniquely-named file (no overwrite)."""
        if not self._steps:
            return

        ts = datetime.fromtimestamp(self._session_start).strftime("%Y%m%d_%H%M%S")
        # Include session_id to avoid filename collisions across sessions
        filename = f"{self._current_agent}_{ts}_s{self._session_id}.json"
        filepath = self._traces_dir / filename

        trace = {
            "agent": self._current_agent,
            "session_id": self._session_id,
            "timestamp": datetime.fromtimestamp(self._session_start).isoformat(),
            "execution_path": self._steps,
            "files_modified": sorted(self._files_modified),
            "outcome": status,
            "duration_ms": duration_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "total_steps": self._step_counter,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(trace, f, indent=2, ensure_ascii=False)

        # Reset after writing
        self._steps = []
        self._step_counter = 0
        self._files_modified = set()

    async def close(self) -> None:
        # Write any remaining trace data on shutdown
        if self._steps:
            self._write_trace_internal(
                status="shutdown",
                cost_usd=None,
                duration_ms=int((time.time() - self._session_start) * 1000),
            )
