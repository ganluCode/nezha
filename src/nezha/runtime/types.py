"""Runtime-neutral session types and capability declarations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from nezha.config import AgentConfig, ExecutorConfig


@dataclass
class SessionEvent:
    """Unified event emitted during a session, for the EventBus to consume."""

    event_type: str  # "thinking" | "tool_call" | "tool_result" | "result"
    data: dict[str, Any]


@dataclass
class SessionResult:
    """Summary of a completed session."""

    status: str  # "completed" | "error" | runtime-specific terminal status
    duration_ms: int = 0
    num_turns: int = 0
    cost_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    result_text: str = ""
    error: str = ""


@dataclass(frozen=True)
class RuntimeCapabilities:
    """Runtime capability flags used by upper layers for graceful degradation."""

    tool_events: bool = False
    token_tracking: bool = False
    cost_tracking: bool = False
    pre_tool_hook: bool = False
    sandbox: bool = False
    mcp: bool = False
    output_schema: bool = False
    resume: bool = False


@dataclass
class RuntimeContext:
    """Runtime invocation context shared by concrete runtime adapters."""

    workspace: Path
    cwd: Path | None = None
    agent_config: AgentConfig | None = None
    executor_config: ExecutorConfig | None = None
    env: dict[str, str] = field(default_factory=dict)
    security_hook: Any | None = None
    extra_mcp_servers: dict[str, Any] = field(default_factory=dict)
    timeout: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
