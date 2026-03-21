"""File-based input/output handling for agent sessions."""

from pathlib import Path

from nezha.config import AgentConfig


def scan_input_files(agent_config: AgentConfig, workspace: Path) -> list[Path]:
    """Scan the input directory for files the agent should read.

    Returns list of existing input file paths.
    """
    input_cfg = agent_config.input
    input_dir = workspace / input_cfg.path if input_cfg.path else workspace

    if not input_dir.exists():
        return []

    # If specific files are listed, look for those
    if input_cfg.files:
        found = []
        for fname in input_cfg.files:
            fpath = input_dir / fname
            if fpath.exists():
                found.append(fpath)
        return found

    # Otherwise, list all files in input directory
    return sorted(
        f for f in input_dir.iterdir()
        if f.is_file() and f.name != ".gitkeep"
    )


def build_input_context(
    files: list[Path],
    workspace: Path,
    include_content: bool = True,
    max_content_size: int = 50000,
) -> str:
    """Build a context string describing available input files.

    This gets injected into the prompt so the agent knows what to read.

    Args:
        files: List of input file paths
        workspace: Workspace path for relative path calculation
        include_content: If True, include file content (for planners that can't read files)
        max_content_size: Max bytes per file to include (default 50KB). Larger files are truncated.
    """
    if not files:
        return "No input files found in workspace."

    lines = ["Available input files:"]
    for f in files:
        try:
            rel = f.relative_to(workspace)
        except ValueError:
            rel = f
        size = f.stat().st_size
        lines.append(f"  - {rel} ({size} bytes)")

    # Include file contents for planners that can't use Read tool
    if include_content:
        lines.append("\n--- File Contents ---\n")
        total_size = 0
        for f in files:
            try:
                rel = f.relative_to(workspace)
            except ValueError:
                rel = f
            try:
                size = f.stat().st_size
                if size > max_content_size:
                    lines.append(f"### {rel}\n[File too large ({size} bytes), use Read tool to access]\n")
                    continue
                content = f.read_text(encoding="utf-8")
                total_size += size
                lines.append(f"### {rel}\n```\n{content}\n```\n")
            except Exception as e:
                lines.append(f"### {rel}\n[Error reading file: {e}]\n")

    return "\n".join(lines)


def ensure_output_dir(agent_config: AgentConfig, workspace: Path) -> Path:
    """Ensure the output directory exists. Returns the output path."""
    output_cfg = agent_config.output
    output_dir = workspace / output_cfg.path if output_cfg.path else workspace

    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir
