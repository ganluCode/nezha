"""Runtime protocol for agent execution backends."""

from __future__ import annotations

from typing import AsyncGenerator, Protocol

from nezha.runtime.types import RuntimeCapabilities, RuntimeContext, SessionEvent, SessionResult


class AgentRuntime(Protocol):
    """Common contract for runtime adapters such as Claude Code or Codex CLI."""

    name: str
    capabilities: RuntimeCapabilities

    def run_session(
        self,
        prompt: str,
        context: RuntimeContext,
    ) -> AsyncGenerator[SessionEvent | SessionResult, None]:
        """Run a single session and yield runtime-neutral events/results."""
        ...
