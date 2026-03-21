"""Tool system: deterministic operations invoked after Agent sessions (post_tools)."""

from nezha.tools.base import BaseTool, ToolResult
from nezha.tools.git_tool import GitTool
from nezha.tools.test_tool import TestTool

_REGISTRY: dict[str, type[BaseTool]] = {
    "git-tool": GitTool,
    "test-tool": TestTool,
}


def create_tool(name: str) -> BaseTool:
    """Instantiate a tool by name."""
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown tool: '{name}'. Available: {list(_REGISTRY)}")
    return cls()


__all__ = ["BaseTool", "ToolResult", "GitTool", "TestTool", "create_tool"]
