"""GitTool — deterministic git operations: commit, push, create-pr."""

import subprocess
from pathlib import Path

from nezha.tools.base import ToolResult


class GitTool:
    """Runs git commands in the target directory.

    Supported actions:
        commit   — git add -A && git commit -m <message>
        push     — git push origin <branch>
        create-pr — gh pr create (requires GitHub CLI)
    """

    def run(self, action: str, cwd: Path, params: dict) -> ToolResult:
        if action == "commit":
            return self._commit(cwd, params)
        elif action == "push":
            return self._push(cwd, params)
        elif action == "create-pr":
            return self._create_pr(cwd, params)
        else:
            return ToolResult(success=False, error=f"Unknown git-tool action: '{action}'")

    # ------------------------------------------------------------------

    def _commit(self, cwd: Path, params: dict) -> ToolResult:
        message = params.get("message", "chore: auto-commit by agent-executor")
        try:
            # Stage all changes
            result = subprocess.run(
                ["git", "add", "-A"],
                cwd=cwd, capture_output=True, text=True,
            )
            if result.returncode != 0:
                return ToolResult(success=False, error=result.stderr)

            # Check if there's anything to commit
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=cwd, capture_output=True, text=True,
            )
            if not status.stdout.strip():
                return ToolResult(success=True, output="Nothing to commit, working tree clean")

            result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=cwd, capture_output=True, text=True,
            )
            if result.returncode != 0:
                return ToolResult(success=False, error=result.stderr)
            return ToolResult(success=True, output=result.stdout.strip())
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _push(self, cwd: Path, params: dict) -> ToolResult:
        branch = params.get("branch", "")
        remote = params.get("remote", "origin")
        try:
            if not branch:
                # Get current branch
                result = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=cwd, capture_output=True, text=True,
                )
                branch = result.stdout.strip()

            result = subprocess.run(
                ["git", "push", remote, branch],
                cwd=cwd, capture_output=True, text=True,
            )
            if result.returncode != 0:
                return ToolResult(success=False, error=result.stderr)
            return ToolResult(success=True, output=result.stdout.strip() or result.stderr.strip())
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _create_pr(self, cwd: Path, params: dict) -> ToolResult:
        title = params.get("title", "Auto PR by agent-executor")
        body = params.get("body", "")
        base = params.get("base", "main")
        try:
            cmd = ["gh", "pr", "create", "--title", title, "--base", base]
            if body:
                cmd += ["--body", body]
            result = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True,
            )
            if result.returncode != 0:
                return ToolResult(success=False, error=result.stderr)
            return ToolResult(success=True, output=result.stdout.strip())
        except Exception as e:
            return ToolResult(success=False, error=str(e))
