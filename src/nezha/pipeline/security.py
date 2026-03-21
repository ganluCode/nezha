"""Bash command security hook: allowlist-based validation."""

import re
import shlex

# Default allowed commands for development tasks
DEFAULT_ALLOWED_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "grep",
    "cp", "mkdir", "chmod", "pwd",
    "npm", "node", "npx",
    "git",
    "ps", "lsof", "sleep", "pkill",
    "pip", "python", "python3", "uv",
}

# Commands needing extra validation
SENSITIVE_COMMANDS = {"pkill", "chmod", "rm"}

# Allowed process names for pkill
ALLOWED_PKILL_TARGETS = {"node", "npm", "npx", "vite", "next", "uvicorn", "python"}


def extract_commands(command_string: str) -> list[str]:
    """Extract base command names from a shell command string."""
    # Split on &&, ||, ;
    segments = re.split(r"\s*(?:&&|\|\|)\s*", command_string)
    result = []
    for seg in segments:
        for sub in re.split(r"(?<![\'\"]);(?![\'\"]\s*)", seg):
            sub = sub.strip()
            if not sub:
                continue
            # Handle pipes
            for piped in sub.split("|"):
                piped = piped.strip()
                if not piped:
                    continue
                try:
                    tokens = shlex.split(piped)
                except ValueError:
                    tokens = piped.split()
                if tokens:
                    cmd = tokens[0].split("/")[-1]  # basename
                    result.append(cmd)
    return result


def _validate_pkill(command_segment: str) -> tuple[bool, str]:
    """Validate pkill: only allow killing dev-related processes."""
    try:
        tokens = shlex.split(command_segment)
    except ValueError:
        return False, "Could not parse pkill command"

    args = [t for t in tokens[1:] if not t.startswith("-")]
    if not args:
        return False, "pkill requires a process name"

    target = args[-1].split()[0] if " " in args[-1] else args[-1]
    if target in ALLOWED_PKILL_TARGETS:
        return True, ""
    return False, f"pkill only allowed for: {ALLOWED_PKILL_TARGETS}"


def _validate_chmod(command_segment: str) -> tuple[bool, str]:
    """Validate chmod: only allow +x."""
    try:
        tokens = shlex.split(command_segment)
    except ValueError:
        return False, "Could not parse chmod command"

    for token in tokens[1:]:
        if token.startswith("-"):
            return False, "chmod flags not allowed"

    mode = tokens[1] if len(tokens) > 1 else None
    if mode and not re.match(r"^[ugoa]*\+x$", mode):
        return False, f"chmod only allowed with +x mode, got: {mode}"
    return True, ""


# Patterns that indicate dangerous rm targets (absolute or home-relative paths)
_RM_DANGEROUS_PATTERNS = re.compile(
    r"^(/|~|\$HOME|\$\{HOME\}|\$USER)"  # absolute path or home dir
)


def _validate_rm(command_segment: str) -> tuple[bool, str]:
    """Validate rm: only allow relative paths within the project directory.

    Blocks:
    - Absolute paths (/Users/..., /home/..., /)
    - Home directory (~, $HOME)
    - Path traversal escaping the project (../../...)
    - Bare filesystem roots (-rf /)
    """
    try:
        tokens = shlex.split(command_segment)
    except ValueError:
        return False, "Could not parse rm command"

    # Collect non-flag arguments (the paths to delete)
    paths = [t for t in tokens[1:] if not t.startswith("-")]

    if not paths:
        return False, "rm requires at least one path argument"

    for path in paths:
        # Block absolute paths and home-relative expansions
        if _RM_DANGEROUS_PATTERNS.match(path):
            return False, f"rm: absolute or home-relative paths are not allowed: {path}"

        # Block path traversal that starts with ..
        if path.startswith(".."):
            return False, f"rm: path traversal outside project is not allowed: {path}"

        # Block paths containing /.. sequences that could escape the project
        if "/.." in path:
            return False, f"rm: path traversal outside project is not allowed: {path}"

        # Block shell glob on root-like targets (e.g. rm -rf *)
        # Allow * only as a suffix within a relative path (e.g. dist/*, src/*.js)
        if path == "*" or path == "**":
            return (
                False,
                "rm: bare glob '*' is too broad — use a specific path like 'dist/*'",
            )

    return True, ""


def create_security_hook(allowed_commands: set[str] | None = None):
    """Create a PreToolUse security hook with the given allowlist.

    Args:
        allowed_commands: Set of allowed command names. Uses defaults if None.

    Returns:
        Async hook function compatible with the LLM engine SDK.
    """
    cmds = allowed_commands or DEFAULT_ALLOWED_COMMANDS

    async def hook(input_data, tool_use_id=None, context=None):
        if input_data.get("tool_name") != "Bash":
            return {}

        command = input_data.get("tool_input", {}).get("command", "")
        if not command:
            return {}

        commands = extract_commands(command)
        if not commands:
            return {
                "decision": "block",
                "reason": f"Could not parse command: {command}",
            }

        for cmd in commands:
            if cmd not in cmds:
                return {
                    "decision": "block",
                    "reason": f"Command '{cmd}' is not in the allowed list",
                }

            if cmd in SENSITIVE_COMMANDS:
                if cmd == "pkill":
                    ok, reason = _validate_pkill(command)
                    if not ok:
                        return {"decision": "block", "reason": reason}
                elif cmd == "chmod":
                    ok, reason = _validate_chmod(command)
                    if not ok:
                        return {"decision": "block", "reason": reason}
                elif cmd == "rm":
                    ok, reason = _validate_rm(command)
                    if not ok:
                        return {"decision": "block", "reason": reason}

        return {}

    return hook
