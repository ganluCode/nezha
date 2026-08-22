"""Runtime-aware git identity helpers."""

from __future__ import annotations


def runtime_git_identity(runtime: str) -> tuple[str, str] | None:
    """Return git committer identity for a runtime."""
    normalized = (runtime or "").replace("-", "_").lower()
    if not normalized:
        return None
    if normalized in {"codex", "codex_cli"}:
        return "codex", "codex@nezha.local"
    if normalized in {"claude", "claude_code"}:
        return "claude", "claude@nezha.local"
    if normalized in {"opencode", "opencode_cli"}:
        return "opencode", "opencode@nezha.local"
    return f"nezha-{normalized}", f"nezha-{normalized}@nezha.local"


def apply_runtime_git_identity(
    env: dict[str, str],
    runtime: str,
    *,
    override: bool = False,
) -> dict[str, str]:
    """Apply runtime committer env without changing git author."""
    name_email = runtime_git_identity(runtime)
    if not name_email:
        return env
    name, email = name_email
    result = dict(env)
    if override:
        result["GIT_COMMITTER_NAME"] = name
        result["GIT_COMMITTER_EMAIL"] = email
    else:
        result.setdefault("GIT_COMMITTER_NAME", name)
        result.setdefault("GIT_COMMITTER_EMAIL", email)
    return result
