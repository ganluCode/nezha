"""Post-task integration test cycle: test → fix → re-test loop.

After the DAG completes (all features pass unit tests), the executor can run
an integration/E2E test command.  If it fails, a fix session is triggered
using the coding-agent with a specialised fix prompt, then the test is re-run.
The cycle repeats up to ``max_cycles`` times.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Result data classes
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    """Result of a single deterministic test command execution."""
    passed: bool
    exit_code: int
    output: str
    duration_ms: int


@dataclass
class CycleResult:
    """Aggregate result of the entire test→fix cycle."""
    passed: bool = False
    cycles_run: int = 0
    total_cost_usd: float = 0.0
    exit_reason: str = ""          # tests_passed | max_cycles | fix_error | command_error
    last_output: str = ""


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

_MAX_OUTPUT_LEN = 3000


def run_test_command(
    command: str,
    cwd: Path,
    timeout: int = 600,
) -> RunResult:
    """Run an integration test command deterministically.

    Returns a structured result with pass/fail, output, and timing.
    Output is truncated to keep the report LLM-friendly.
    """
    start = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        output = (proc.stdout or "") + (proc.stderr or "")
        output = _truncate(output)
        return RunResult(
            passed=proc.returncode == 0,
            exit_code=proc.returncode,
            output=output,
            duration_ms=duration_ms,
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - start) * 1000)
        return RunResult(
            passed=False,
            exit_code=-1,
            output=f"Test command timed out after {timeout}s",
            duration_ms=duration_ms,
        )
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        return RunResult(
            passed=False,
            exit_code=-1,
            output=f"Test command error: {e}",
            duration_ms=duration_ms,
        )


# ---------------------------------------------------------------------------
# Test report
# ---------------------------------------------------------------------------

def write_test_report(
    workspace: Path,
    cycle: int,
    max_cycles: int,
    test_command: str,
    test_result: RunResult,
    previous_fixes: list[dict],
) -> Path:
    """Write ``.test_report.json`` for the fix agent to read.

    The report is compact enough for an LLM to consume while providing
    sufficient context for intelligent fixing.  ``previous_fixes`` prevents
    the agent from repeating the same failed approach.
    """
    report = {
        "cycle": cycle,
        "max_cycles": max_cycles,
        "test_command": test_command,
        "passed": test_result.passed,
        "exit_code": test_result.exit_code,
        "output": test_result.output,
        "duration_ms": test_result.duration_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "previous_fixes": previous_fixes,
    }
    path = workspace / ".test_report.json"
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate(text: str) -> str:
    """Truncate long output keeping head and tail for context."""
    if len(text) <= _MAX_OUTPUT_LEN:
        return text
    half = _MAX_OUTPUT_LEN // 2
    return text[:half] + "\n...(truncated)...\n" + text[-half:]
