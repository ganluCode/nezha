"""BaseTool Protocol and ToolResult — deterministic post-session operations."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class ToolResult:
    success: bool
    output: str = ""
    error: str = ""


@runtime_checkable
class BaseTool(Protocol):
    """Protocol for deterministic tools that run after an Agent session.

    Tools are NOT AI — they execute fixed logic (git commands, test runners, etc.)
    with no LLM involvement. They receive the target directory (cwd for operations)
    and an action string to determine what to do.
    """

    def run(self, action: str, cwd: Path, params: dict) -> ToolResult:
        """Execute the tool action.

        Args:
            action:  What to do (e.g. "commit", "push", "run").
            cwd:     Working directory — target for coding agents, task_workspace otherwise.
            params:  Extra key-value parameters from the YAML config.

        Returns:
            ToolResult with success flag and output/error text.
        """
        ...
