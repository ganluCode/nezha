"""TestTool — run a test suite and report pass/fail."""

import subprocess
from pathlib import Path

from nezha.tools.base import ToolResult


class TestTool:
    """Executes a test command and returns success based on exit code.

    Supported actions:
        run   — execute the configured command (e.g. "python -m pytest")

    YAML params:
        command:  The test command to run (overrides agent-level verification.command).
        timeout:  Seconds before the command is killed (default: 300).
    """

    def run(self, action: str, cwd: Path, params: dict) -> ToolResult:
        if action == "run":
            return self._run(cwd, params)
        else:
            return ToolResult(success=False, error=f"Unknown test-tool action: '{action}'")

    # ------------------------------------------------------------------

    def _run(self, cwd: Path, params: dict) -> ToolResult:
        command = params.get("command", "python -m pytest")
        timeout = int(params.get("timeout", 300))
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout + result.stderr
            if result.returncode == 0:
                return ToolResult(success=True, output=output.strip())
            else:
                return ToolResult(success=False, output=output.strip(),
                                  error=f"Tests failed (exit {result.returncode})")
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error=f"Test command timed out after {timeout}s")
        except Exception as e:
            return ToolResult(success=False, error=str(e))
