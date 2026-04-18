"""GitTool — deterministic git operations: commit, push, create-pr."""

import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

from nezha.tools.base import ToolResult


class GitTool:
    """Runs git commands in the target directory.

    Supported actions:
        commit   — git add -A && git commit -m <message>
        push     — git push origin <branch>
        create-pr — gh pr create (GitHub) or Gitee REST API (auto-detected from remote URL)
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
        message = params.get("message", "chore: auto-commit by nezha")
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

    def _get_remote_url(self, cwd: Path, remote: str = "origin") -> str:
        result = subprocess.run(
            ["git", "remote", "get-url", remote],
            cwd=cwd, capture_output=True, text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def _detect_platform(self, remote_url: str) -> str:
        """Detect git platform from remote URL. Returns 'gitee' or 'github'."""
        if "gitee.com" in remote_url:
            return "gitee"
        return "github"

    def _parse_owner_repo(self, remote_url: str) -> tuple[str, str]:
        """Parse owner and repo name from remote URL (SSH or HTTPS)."""
        # SSH: git@gitee.com:owner/repo.git
        # HTTPS: https://gitee.com/owner/repo.git
        match = re.search(r"[:/]([^/]+)/([^/]+?)(?:\.git)?$", remote_url)
        if not match:
            raise ValueError(f"Cannot parse owner/repo from remote URL: {remote_url}")
        return match.group(1), match.group(2)

    def _create_pr(self, cwd: Path, params: dict) -> ToolResult:
        title = params.get("title", "Auto PR by nezha")
        body = params.get("body", "")
        base = params.get("base", "main")
        try:
            remote_url = self._get_remote_url(cwd)
            platform = self._detect_platform(remote_url)

            if platform == "gitee":
                return self._create_pr_gitee(cwd, remote_url, title, body, base)
            else:
                return self._create_pr_github(cwd, title, body, base)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _create_pr_github(self, cwd: Path, title: str, body: str, base: str) -> ToolResult:
        cmd = ["gh", "pr", "create", "--title", title, "--base", base]
        if body:
            cmd += ["--body", body]
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        if result.returncode != 0:
            return ToolResult(success=False, error=result.stderr)
        return ToolResult(success=True, output=result.stdout.strip())

    def _create_pr_gitee(self, cwd: Path, remote_url: str, title: str, body: str, base: str) -> ToolResult:
        token = os.environ.get("GITEE_TOKEN", "")
        if not token:
            return ToolResult(success=False, error="GITEE_TOKEN not set in environment")

        owner, repo = self._parse_owner_repo(remote_url)

        # Get current branch as head
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd, capture_output=True, text=True,
        )
        head = result.stdout.strip() if result.returncode == 0 else "main"

        payload = json.dumps({
            "access_token": token,
            "title": title,
            "body": body,
            "head": head,
            "base": base,
        }).encode()

        url = f"https://gitee.com/api/v5/repos/{owner}/{repo}/pulls"
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        pr_url = data.get("html_url", "")
        return ToolResult(success=True, output=f"PR created: {pr_url}")
