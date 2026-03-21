"""Event type definitions for the executor event system."""

import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """All event types in the executor lifecycle."""
    # Executor lifecycle
    EXECUTOR_STARTED = "executor.started"
    EXECUTOR_STOPPED = "executor.stopped"

    # Session lifecycle
    SESSION_STARTED = "session.started"
    SESSION_COMPLETED = "session.completed"
    SESSION_ERROR = "session.error"

    # Agent activity (from engine)
    AGENT_THINKING = "agent.thinking"
    AGENT_TOOL_CALL = "agent.tool_call"
    AGENT_TOOL_RESULT = "agent.tool_result"

    # Guard events
    GUARD_PASSED = "guard.passed"
    GUARD_BLOCKED = "guard.blocked"

    # Scheduler events
    SCHEDULER_WAITING = "scheduler.waiting"
    SCHEDULER_TRIGGERED = "scheduler.triggered"

    # Rework events
    FEATURE_REWORK_TRIGGERED = "feature.rework_triggered"
    FEATURE_REWORK_COMPLETED = "feature.rework_completed"
    FEATURE_REWORK_FAILED = "feature.rework_failed"

    # VibeCoding events
    VIBE_SESSION_STARTED = "vibe.session_started"
    VIBE_SESSION_ENDED = "vibe.session_ended"

    # DAG events
    DAG_LOADED = "dag.loaded"
    DAG_FEATURE_STARTED = "dag.feature_started"
    DAG_FEATURE_COMPLETED = "dag.feature_completed"
    DAG_FEATURE_BLOCKED = "dag.feature_blocked"
    DAG_DEADLOCKED = "dag.deadlocked"
    DAG_ALL_COMPLETED = "dag.all_completed"

    # Integration test events
    INTEGRATION_TEST_STARTED = "integration_test.started"
    INTEGRATION_TEST_PASSED = "integration_test.passed"
    INTEGRATION_TEST_FAILED = "integration_test.failed"


@dataclass
class Event:
    """A single event in the executor system.

    All events are serializable to JSON for logging/transport.
    """
    event_type: EventType
    timestamp: float = field(default_factory=time.time)
    executor_id: str = ""
    agent_name: str = ""
    session_id: int = 0
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def create(
        cls,
        event_type: EventType,
        agent_name: str = "",
        session_id: int = 0,
        executor_id: str = "",
        **payload,
    ) -> "Event":
        return cls(
            event_type=event_type,
            executor_id=executor_id,
            agent_name=agent_name,
            session_id=session_id,
            payload=payload,
        )
