"""LLM engine wrapper: create sessions and process message streams."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncGenerator

from claude_code_sdk import ClaudeCodeOptions, query
from claude_code_sdk.types import (
    AssistantMessage,
    HookMatcher,
    Message,
    ResultMessage,
    UserMessage,
)

from nezha.config import AgentConfig

# ---------------------------------------------------------------------------
# Monkey-patch: SDK ≤0.0.25 raises MessageParseError on unknown message types
# (e.g. rate_limit_event added in newer CLI versions).  Patch the parser so
# unknown types are silently skipped (return None) instead of crashing.
# ---------------------------------------------------------------------------
try:
    from claude_code_sdk._internal import message_parser as _mp
    from claude_code_sdk._internal import client as _client
    _original_parse = _mp.parse_message

    def _tolerant_parse(data):  # noqa: ANN001
        try:
            return _original_parse(data)
        except Exception:
            logging.getLogger(__name__).info("Skipping unknown SDK message: %s", data.get("type", data) if isinstance(data, dict) else data)
            return None

    # Patch both module-level refs: message_parser.parse_message AND
    # client.parse_message (which was bound at import time via `from ... import`)
    _mp.parse_message = _tolerant_parse
    _client.parse_message = _tolerant_parse
except Exception:
    pass  # SDK internals changed — patch not needed


@dataclass
class SessionEvent:
    """Unified event emitted during a session, for the EventBus to consume."""
    event_type: str  # "thinking" | "tool_call" | "tool_result" | "result"
    data: dict[str, Any]


@dataclass
class SessionResult:
    """Summary of a completed session."""
    status: str  # "completed" | "error"
    duration_ms: int = 0
    num_turns: int = 0
    cost_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    result_text: str = ""
    error: str = ""


def build_options(
    agent_config: AgentConfig,
    workspace: Path,
    security_hook=None,
    env: dict[str, str] | None = None,
    extra_mcp_servers: dict[str, Any] | None = None,
) -> ClaudeCodeOptions:
    """Build ClaudeCodeOptions from agent config.

    MCP server precedence: agent-level overrides global (extra_mcp_servers).
    """
    hooks = None
    if security_hook:
        hooks = {
            "PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[security_hook]),
            ],
        }

    # Merge: global < agent-level (agent wins on conflict), filter empty values
    merged = {**(extra_mcp_servers or {}), **(agent_config.engine.mcp_servers or {})}
    mcp_servers = {k: v for k, v in merged.items() if v}

    return ClaudeCodeOptions(
        model=agent_config.engine.model,
        max_turns=agent_config.engine.max_turns,
        allowed_tools=agent_config.engine.tools,
        mcp_servers=mcp_servers,
        cwd=str(workspace.resolve()),
        hooks=hooks,
        permission_mode="bypassPermissions",
        env=env or {},
    )


async def run_session(
    prompt: str,
    options: ClaudeCodeOptions,
) -> AsyncGenerator[SessionEvent | SessionResult, None]:
    """Run a single Claude Code session, yielding events as they come.

    Usage:
        options = build_options(agent_config, workspace)
        async for event in run_session(prompt, options):
            if isinstance(event, SessionResult):
                print(f"Done: {event.status}")
            else:
                print(f"Event: {event.event_type}")
    """
    try:
        async for msg in query(prompt=prompt, options=options):
            if msg is None:
                continue
            msg_type = type(msg).__name__

            # Skip unknown / non-essential message types
            if msg_type not in ("AssistantMessage", "UserMessage", "ResultMessage"):
                # Log rate limit info messages (informational, not an error)
                if "rate_limit" in msg_type.lower():
                    logging.getLogger(__name__).info("Rate limit info: %s", msg_type)
                continue

            if msg_type == "AssistantMessage" and hasattr(msg, "content"):
                for block in msg.content:
                    block_type = type(block).__name__

                    if block_type == "TextBlock":
                        yield SessionEvent(
                            event_type="thinking",
                            data={"text": block.text},
                        )

                    elif block_type == "ToolUseBlock":
                        yield SessionEvent(
                            event_type="tool_call",
                            data={
                                "tool": block.name,
                                "tool_use_id": block.id,
                                "input": block.input,
                            },
                        )

                    elif block_type == "ThinkingBlock":
                        yield SessionEvent(
                            event_type="thinking",
                            data={"text": f"[thinking] {block.thinking[:200]}..."},
                        )

            elif msg_type == "UserMessage" and hasattr(msg, "content"):
                if isinstance(msg.content, list):
                    for block in msg.content:
                        block_type = type(block).__name__
                        if block_type == "ToolResultBlock":
                            is_error = getattr(block, "is_error", False) or False
                            content = getattr(block, "content", "")
                            yield SessionEvent(
                                event_type="tool_result",
                                data={
                                    "tool_use_id": getattr(block, "tool_use_id", ""),
                                    "success": not is_error,
                                    "content": str(content)[:2000] if content else "",
                                    "is_error": is_error,
                                },
                            )

            elif msg_type == "ResultMessage":
                _usage = msg.usage or {}
                # Detect unrecoverable errors that require graceful stop
                _error_text = (msg.result or "") if msg.is_error else ""
                _is_rate_limited = msg.is_error and any(
                    kw in _error_text.lower()
                    for kw in (
                        "rate limit", "rate_limit", "too many requests", "overloaded",
                        "429", "529",
                        "authentication_error", "invalid authentication", "401",
                    )
                )
                if _is_rate_limited:
                    _status = "rate_limited"
                elif msg.is_error:
                    _status = "error"
                else:
                    _status = "completed"
                yield SessionResult(
                    status=_status,
                    duration_ms=msg.duration_ms,
                    num_turns=msg.num_turns,
                    cost_usd=msg.total_cost_usd,
                    input_tokens=_usage.get("input_tokens", 0) or 0,
                    output_tokens=_usage.get("output_tokens", 0) or 0,
                    result_text=msg.result or "",
                    error=_error_text,
                )
                return

    except Exception as e:
        yield SessionResult(
            status="error",
            error=str(e),
        )
