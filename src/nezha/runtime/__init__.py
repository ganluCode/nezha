"""Runtime adapters for agent execution backends."""

from __future__ import annotations

from nezha.runtime.base import AgentRuntime
from nezha.runtime.claude_code import ClaudeCodeRuntime
from nezha.runtime.codex_cli import CodexCliRuntime
from nezha.runtime.types import (
    RuntimeCapabilities,
    RuntimeContext,
    SessionEvent,
    SessionResult,
)


def get_runtime(runtime: str = "claude_code") -> AgentRuntime:
    """Return a runtime adapter by name."""
    normalized = (runtime or "claude_code").replace("-", "_").lower()
    if normalized in {"claude", "claude_code"}:
        return ClaudeCodeRuntime()
    if normalized in {"codex", "codex_cli"}:
        return CodexCliRuntime()
    raise ValueError(f"Unknown runtime: {runtime}")


__all__ = [
    "AgentRuntime",
    "ClaudeCodeRuntime",
    "CodexCliRuntime",
    "RuntimeCapabilities",
    "RuntimeContext",
    "SessionEvent",
    "SessionResult",
    "get_runtime",
]
