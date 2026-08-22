"""Compatibility exports for the default Claude Code runtime.

Historically this module contained the Claude Code SDK implementation directly.
The implementation now lives in ``nezha.runtime.claude_code`` so future runtimes
can plug into the same session/result contract while existing imports continue
to work.
"""

from nezha.runtime.claude_code import ClaudeCodeOptions, build_options, run_session
from nezha.runtime.types import SessionEvent, SessionResult

__all__ = [
    "ClaudeCodeOptions",
    "SessionEvent",
    "SessionResult",
    "build_options",
    "run_session",
]
