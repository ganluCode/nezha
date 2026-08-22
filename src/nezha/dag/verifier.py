"""Verification layer: independently verify task completion after each session."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class VerificationResult:
    """Result of verifying a task after a session."""
    task_id: str
    passed: bool
    agent_reported_pass: bool  # What the agent set in task_list.json
    command_passed: bool | None = None  # None if no command configured
    command_output: str = ""
    command_run: bool = False
    command_output_tail: str = ""
    reason: str = ""

    # Legacy alias for backward compatibility with callers using feature_id
    @property
    def feature_id(self) -> str:
        return self.task_id


def verify_task(
    task_id: str,
    task_list_path: Path,
    verification_command: str | None = None,
    workspace: Path | None = None,
    timeout: int = 300,
) -> VerificationResult:
    """Verify a task after session completion.

    Checks:
    1. Did the agent update task_list.json (set passes: true)?
    2. If verification_command is configured, run it and check exit code.

    The task passes verification if:
    - No verification command is configured and the agent reported passes: true
    - Or a verification command is configured and it succeeded

    This preserves the legacy self-report path for tasks without external
    checks, while letting Nezha own metadata recovery when a sandboxed runtime
    completes the code/test work but cannot write task_list.json itself.

    Args:
        task_id: The task ID to verify
        task_list_path: Path to task_list.json
        verification_command: Optional command to run (e.g. "python -m pytest")
        workspace: Working directory for the command
        timeout: Command timeout in seconds
    """
    # Step 1: Check if agent updated task_list.json
    agent_reported_pass = _check_agent_report(task_id, task_list_path)

    # Step 2: Run verification command if configured
    command_passed = None
    command_output = ""

    command_run = bool(verification_command)
    if verification_command:
        command_passed, command_output = _run_verification_command(
            verification_command,
            workspace or task_list_path.parent,
            timeout,
        )
    command_output_tail = _tail(command_output)

    # Step 3: Determine overall pass/fail
    passed, reason = _determine_result(
        task_id, agent_reported_pass, command_passed
    )

    return VerificationResult(
        task_id=task_id,
        passed=passed,
        agent_reported_pass=agent_reported_pass,
        command_passed=command_passed,
        command_output=command_output,
        command_run=command_run,
        command_output_tail=command_output_tail,
        reason=reason,
    )


# Legacy alias for backward compatibility
def verify_feature(
    feature_id: str,
    feature_list_path: Path,
    verification_command: str | None = None,
    workspace: Path | None = None,
    timeout: int = 300,
) -> VerificationResult:
    """Deprecated: use verify_task() instead."""
    return verify_task(
        task_id=feature_id,
        task_list_path=feature_list_path,
        verification_command=verification_command,
        workspace=workspace,
        timeout=timeout,
    )


def apply_verification_result(
    result: VerificationResult,
    task_list_path: Path,
) -> None:
    """Apply verification result to task_list.json.

    If verification passed, mark the task completed and clear rework state.
    If verification failed, mark the task for rework.
    """
    if result.passed:
        _mark_task_passed(result.task_id, task_list_path)
        return

    with open(task_list_path, encoding="utf-8") as f:
        tasks = json.load(f)

    for task in tasks:
        if task["id"] == result.task_id:
            new_count = task.get("rework_count", 0) + 1
            existing_note = task.get("rework_note", "")
            verification_note = _verification_note(result)
            block_reason = _format_block_reason(result)
            if isinstance(existing_note, dict):
                # Preserve agent's tried/not_tried/related_files, update block_reason
                new_note: str | dict = {
                    **existing_note,
                    "block_reason": block_reason,
                    "verification": verification_note,
                    "attempt": new_count,
                }
            else:
                # First failure or plain-text legacy note — start structured
                new_note = {
                    "attempt": new_count,
                    "tried": str(existing_note) if existing_note else "",
                    "not_tried": "",
                    "related_files": [],
                    "block_reason": block_reason,
                    "verification": verification_note,
                }
            task["passes"] = False
            task["rework"] = True
            task["rework_note"] = new_note
            task["rework_count"] = new_count
            break

    with open(task_list_path, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _mark_task_passed(task_id: str, task_list_path: Path) -> None:
    """Mark a task as passed and clear stale rework flags."""
    with open(task_list_path, encoding="utf-8") as f:
        tasks = json.load(f)

    changed = False
    for task in tasks:
        if task["id"] == task_id:
            if task.get("passes") is not True:
                task["passes"] = True
                changed = True
            if task.get("rework") is not False:
                task["rework"] = False
                changed = True
            if task.get("rework_note"):
                task.pop("rework_note", None)
                changed = True
            break

    if changed:
        with open(task_list_path, "w", encoding="utf-8") as f:
            json.dump(tasks, f, indent=2, ensure_ascii=False)
            f.write("\n")


def _check_agent_report(task_id: str, task_list_path: Path) -> bool:
    """Check if the agent set passes: true for the task."""
    try:
        with open(task_list_path, encoding="utf-8") as f:
            tasks = json.load(f)
        for task in tasks:
            if task["id"] == task_id:
                return task.get("passes", False)
    except (json.JSONDecodeError, FileNotFoundError, KeyError):
        pass
    return False


def _tail(text: str, limit: int = 2000) -> str:
    """Return a compact tail for notes/events."""
    if not text:
        return ""
    return text if len(text) <= limit else text[-limit:]


def _verification_note(result: VerificationResult) -> dict:
    """Structured verifier data written into rework_note."""
    return {
        "agent_reported_pass": result.agent_reported_pass,
        "command_run": result.command_run or result.command_passed is not None,
        "command_passed": result.command_passed,
        "command_output_tail": result.command_output_tail or _tail(result.command_output),
        "reason": result.reason,
    }


def _format_block_reason(result: VerificationResult) -> str:
    """Human-readable reason for the next rework session."""
    tail = result.command_output_tail or _tail(result.command_output, limit=800)
    if tail:
        return f"{result.reason}\n\nVerifier output tail:\n{tail}"
    return result.reason


def _run_verification_command(
    command: str,
    cwd: Path,
    timeout: int,
) -> tuple[bool, str]:
    """Run verification command and return (passed, output)."""
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = proc.stdout + proc.stderr
        # Truncate output to avoid excessive storage
        if len(output) > 2000:
            output = output[:1000] + "\n...(truncated)...\n" + output[-1000:]
        return proc.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"Verification command timed out after {timeout}s"
    except Exception as e:
        return False, f"Verification command error: {e}"


def _determine_result(
    task_id: str,
    agent_reported_pass: bool,
    command_passed: bool | None,
) -> tuple[bool, str]:
    """Determine overall verification result.

    Returns (passed, reason).
    """
    if command_passed is None:
        # No verification command configured — trust agent report
        if agent_reported_pass:
            return True, "Agent reported pass (no verification command)"
        return False, "Agent did not report passes: true"

    if not command_passed:
        return False, "Verification command failed"

    if agent_reported_pass:
        return True, "Agent reported pass and verification command succeeded"
    return True, "Verification command succeeded; Nezha will mark task passed"
