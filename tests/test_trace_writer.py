"""Tests for TraceWriterHandler — per-session trace files."""

import asyncio
import json
import time
from pathlib import Path

import pytest

from nezha.events.trace_writer import TraceWriterHandler
from nezha.events.types import Event, EventType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event(event_type: EventType, agent: str = "test-agent", session_id: int = 1,
           payload: dict | None = None, ts: float | None = None) -> Event:
    return Event(
        event_type=event_type,
        agent_name=agent,
        session_id=session_id,
        payload=payload or {},
        timestamp=ts or time.time(),
    )


def _tool_call_event(tool: str, agent: str = "test-agent", session_id: int = 1,
                     input_data: dict | str = "") -> Event:
    return _event(EventType.AGENT_TOOL_CALL, agent, session_id, {
        "tool": tool,
        "input": input_data,
    })


def _tool_result_event(success: bool = True, agent: str = "test-agent",
                       session_id: int = 1) -> Event:
    return _event(EventType.AGENT_TOOL_RESULT, agent, session_id, {
        "success": success,
    })


# ---------------------------------------------------------------------------
# Basic trace writing
# ---------------------------------------------------------------------------

class TestTraceWriterBasic:
    """Test basic trace file creation."""

    def test_single_session_writes_trace(self, tmp_path):
        """A completed session writes one trace file."""
        handler = TraceWriterHandler(tmp_path)
        asyncio.run(handler.handle(_event(EventType.SESSION_STARTED, session_id=1)))
        asyncio.run(handler.handle(_tool_call_event("Read", session_id=1)))
        asyncio.run(handler.handle(_tool_result_event(True, session_id=1)))
        asyncio.run(handler.handle(_event(
            EventType.SESSION_COMPLETED, session_id=1,
            payload={"status": "completed", "cost_usd": 0.5, "duration_ms": 1000},
        )))

        traces = list((tmp_path / "traces").glob("*.json"))
        assert len(traces) == 1
        data = json.loads(traces[0].read_text())
        assert data["agent"] == "test-agent"
        assert data["session_id"] == 1
        assert data["outcome"] == "completed"
        assert data["total_steps"] == 1

    def test_no_steps_no_trace(self, tmp_path):
        """Session with no tool calls does not write a trace."""
        handler = TraceWriterHandler(tmp_path)
        asyncio.run(handler.handle(_event(EventType.SESSION_STARTED, session_id=1)))
        asyncio.run(handler.handle(_event(
            EventType.SESSION_COMPLETED, session_id=1,
            payload={"status": "completed"},
        )))

        traces = list((tmp_path / "traces").glob("*.json"))
        assert len(traces) == 0


# ---------------------------------------------------------------------------
# Per-session trace files (no overwriting)
# ---------------------------------------------------------------------------

class TestPerSessionTraces:
    """Each session gets its own trace file."""

    def test_two_sessions_two_files(self, tmp_path):
        """Two sessions produce two separate trace files."""
        handler = TraceWriterHandler(tmp_path)

        # Session 1
        asyncio.run(handler.handle(_event(EventType.SESSION_STARTED, session_id=1)))
        asyncio.run(handler.handle(_tool_call_event("Read", session_id=1)))
        asyncio.run(handler.handle(_tool_result_event(True, session_id=1)))
        asyncio.run(handler.handle(_event(
            EventType.SESSION_COMPLETED, session_id=1,
            payload={"status": "completed", "cost_usd": 0.3, "duration_ms": 500},
        )))

        # Session 2
        asyncio.run(handler.handle(_event(EventType.SESSION_STARTED, session_id=2)))
        asyncio.run(handler.handle(_tool_call_event("Edit", session_id=2)))
        asyncio.run(handler.handle(_tool_result_event(True, session_id=2)))
        asyncio.run(handler.handle(_tool_call_event("Bash", session_id=2)))
        asyncio.run(handler.handle(_tool_result_event(True, session_id=2)))
        asyncio.run(handler.handle(_event(
            EventType.SESSION_COMPLETED, session_id=2,
            payload={"status": "completed", "cost_usd": 0.7, "duration_ms": 2000},
        )))

        traces = list((tmp_path / "traces").glob("*.json"))
        assert len(traces) == 2

        # Verify each has correct session_id
        session_ids = set()
        for t in traces:
            data = json.loads(t.read_text())
            session_ids.add(data["session_id"])
        assert session_ids == {1, 2}

    def test_session_id_in_filename(self, tmp_path):
        """Trace filename includes _s{session_id}."""
        handler = TraceWriterHandler(tmp_path)
        asyncio.run(handler.handle(_event(EventType.SESSION_STARTED, session_id=3)))
        asyncio.run(handler.handle(_tool_call_event("Read", session_id=3)))
        asyncio.run(handler.handle(_tool_result_event(True, session_id=3)))
        asyncio.run(handler.handle(_event(
            EventType.SESSION_COMPLETED, session_id=3,
            payload={"status": "completed"},
        )))

        traces = list((tmp_path / "traces").glob("*.json"))
        assert len(traces) == 1
        assert "_s3.json" in traces[0].name

    def test_interrupted_session_flushed_on_new_start(self, tmp_path):
        """If SESSION_COMPLETED is missed, data is flushed on next SESSION_STARTED."""
        handler = TraceWriterHandler(tmp_path)

        # Session 1 — no COMPLETED event
        asyncio.run(handler.handle(_event(EventType.SESSION_STARTED, session_id=1)))
        asyncio.run(handler.handle(_tool_call_event("Read", session_id=1)))
        asyncio.run(handler.handle(_tool_result_event(True, session_id=1)))

        # Session 2 starts — should flush session 1 as "interrupted"
        asyncio.run(handler.handle(_event(EventType.SESSION_STARTED, session_id=2)))
        asyncio.run(handler.handle(_tool_call_event("Edit", session_id=2)))
        asyncio.run(handler.handle(_tool_result_event(True, session_id=2)))
        asyncio.run(handler.handle(_event(
            EventType.SESSION_COMPLETED, session_id=2,
            payload={"status": "completed"},
        )))

        traces = sorted((tmp_path / "traces").glob("*.json"))
        assert len(traces) == 2

        # First trace should be "interrupted"
        data1 = json.loads(traces[0].read_text())
        assert data1["session_id"] == 1
        assert data1["outcome"] == "interrupted"

        data2 = json.loads(traces[1].read_text())
        assert data2["session_id"] == 2
        assert data2["outcome"] == "completed"


# ---------------------------------------------------------------------------
# close() flushes remaining data
# ---------------------------------------------------------------------------

class TestTraceWriterClose:
    """Test close() writes remaining trace data."""

    def test_close_flushes(self, tmp_path):
        """close() writes trace for in-progress session."""
        handler = TraceWriterHandler(tmp_path)
        asyncio.run(handler.handle(_event(EventType.SESSION_STARTED, session_id=1)))
        asyncio.run(handler.handle(_tool_call_event("Bash", session_id=1)))
        asyncio.run(handler.handle(_tool_result_event(True, session_id=1)))

        # No COMPLETED event — call close directly
        asyncio.run(handler.close())

        traces = list((tmp_path / "traces").glob("*.json"))
        assert len(traces) == 1
        data = json.loads(traces[0].read_text())
        assert data["outcome"] == "shutdown"

    def test_close_no_data_no_file(self, tmp_path):
        """close() with no pending data does nothing."""
        handler = TraceWriterHandler(tmp_path)
        asyncio.run(handler.close())
        traces = list((tmp_path / "traces").glob("*.json"))
        assert len(traces) == 0


# ---------------------------------------------------------------------------
# Trace content
# ---------------------------------------------------------------------------

class TestTraceContent:
    """Test trace JSON structure and content."""

    def test_files_modified_tracked(self, tmp_path):
        """Write/Edit tool calls are tracked in files_modified."""
        handler = TraceWriterHandler(tmp_path)
        asyncio.run(handler.handle(_event(EventType.SESSION_STARTED, session_id=1)))
        asyncio.run(handler.handle(_tool_call_event(
            "Edit", session_id=1, input_data={"file_path": "/src/main.py"})))
        asyncio.run(handler.handle(_tool_result_event(True, session_id=1)))
        asyncio.run(handler.handle(_tool_call_event(
            "Write", session_id=1, input_data={"file_path": "/src/utils.py"})))
        asyncio.run(handler.handle(_tool_result_event(True, session_id=1)))
        asyncio.run(handler.handle(_tool_call_event(
            "Read", session_id=1, input_data={"file_path": "/src/other.py"})))
        asyncio.run(handler.handle(_tool_result_event(True, session_id=1)))
        asyncio.run(handler.handle(_event(
            EventType.SESSION_COMPLETED, session_id=1,
            payload={"status": "completed"},
        )))

        traces = list((tmp_path / "traces").glob("*.json"))
        data = json.loads(traces[0].read_text())
        assert sorted(data["files_modified"]) == ["/src/main.py", "/src/utils.py"]

    def test_error_tool_result(self, tmp_path):
        """Failed tool calls include error_summary."""
        handler = TraceWriterHandler(tmp_path)
        asyncio.run(handler.handle(_event(EventType.SESSION_STARTED, session_id=1)))
        asyncio.run(handler.handle(_tool_call_event("Bash", session_id=1)))
        asyncio.run(handler.handle(_event(
            EventType.AGENT_TOOL_RESULT, session_id=1,
            payload={"success": False, "content": "Command failed: exit code 1"},
        )))
        asyncio.run(handler.handle(_event(
            EventType.SESSION_COMPLETED, session_id=1,
            payload={"status": "completed"},
        )))

        traces = list((tmp_path / "traces").glob("*.json"))
        data = json.loads(traces[0].read_text())
        step = data["execution_path"][0]
        assert step["success"] is False
        assert "exit code 1" in step["error_summary"]

    def test_session_error_event(self, tmp_path):
        """SESSION_ERROR event also writes trace."""
        handler = TraceWriterHandler(tmp_path)
        asyncio.run(handler.handle(_event(EventType.SESSION_STARTED, session_id=1)))
        asyncio.run(handler.handle(_tool_call_event("Read", session_id=1)))
        asyncio.run(handler.handle(_tool_result_event(True, session_id=1)))
        asyncio.run(handler.handle(_event(
            EventType.SESSION_ERROR, session_id=1,
            payload={"status": "error", "duration_ms": 300},
        )))

        traces = list((tmp_path / "traces").glob("*.json"))
        assert len(traces) == 1
        data = json.loads(traces[0].read_text())
        assert data["outcome"] == "error"
