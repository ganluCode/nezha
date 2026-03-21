"""Knowledge injection: load project knowledge files into prompts."""

from pathlib import Path

import yaml

# Maximum characters allowed from knowledge file
MAX_KNOWLEDGE_CHARS = 10000

# Files to check, in priority order (first found wins)
KNOWLEDGE_FILES = ["CLAUDE.md", ".agent-context.md"]


def load_knowledge(workspace: Path) -> str:
    """Load project knowledge from workspace root.

    Checks for CLAUDE.md first, then .agent-context.md. If found, returns
    the content wrapped in a '## PROJECT KNOWLEDGE' section. If the content
    exceeds MAX_KNOWLEDGE_CHARS, it is truncated with a warning.

    Args:
        workspace: Workspace root directory.

    Returns:
        Formatted knowledge section string, or empty string if no file found.
    """
    for filename in KNOWLEDGE_FILES:
        filepath = workspace / filename
        if filepath.is_file():
            try:
                content = filepath.read_text(encoding="utf-8")
            except Exception:
                continue

            truncated = False
            if len(content) > MAX_KNOWLEDGE_CHARS:
                content = content[:MAX_KNOWLEDGE_CHARS]
                truncated = True

            section = f"## PROJECT KNOWLEDGE\n\n_Source: {filename}_\n\n{content}"
            if truncated:
                section += (
                    f"\n\n_[Truncated: content exceeded {MAX_KNOWLEDGE_CHARS} characters. "
                    f"Please keep {filename} concise.]_"
                )

            return section

    return ""


# Maximum characters allowed from agent-context file
MAX_AGENT_CONTEXT_CHARS = 8000

# The agent-context filename (lives at the agent's workspace root)
AGENT_CONTEXT_FILE = "agent-context.md"


def load_agent_context(agent_workspace: Path) -> str:
    """Load the agent's cross-task memory from agent-context.md.

    The file lives at the agent workspace root (NOT inside a task subdirectory).
    This gives the agent accumulated knowledge from all its previous tasks.

    Args:
        agent_workspace: The agent's workspace root directory (e.g. workspace/evolve-agent/).

    Returns:
        Formatted '## AGENT MEMORY' section, or empty string if file not found.
    """
    filepath = agent_workspace / AGENT_CONTEXT_FILE
    if not filepath.is_file():
        return ""

    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return ""

    if not content.strip():
        return ""

    truncated = False
    if len(content) > MAX_AGENT_CONTEXT_CHARS:
        content = content[:MAX_AGENT_CONTEXT_CHARS]
        truncated = True

    section = f"## AGENT MEMORY\n\n_Source: {AGENT_CONTEXT_FILE}_\n\n{content.strip()}"
    if truncated:
        section += (
            f"\n\n_[Truncated: agent-context.md exceeded {MAX_AGENT_CONTEXT_CHARS} characters. "
            f"Please keep it concise.]_"
        )

    return section


def _read_file_safe(path: Path) -> str | None:
    """Read a file, returning None if it doesn't exist or can't be read."""
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def load_project_context(project_dir: Path) -> str:
    """Load project-level context files and return formatted Markdown.

    Reads configuration and documentation files from the project directory
    and assembles them into a structured Markdown prompt fragment.

    Files checked (in order):
        - project.yaml — project metadata (name, description, repo)
        - tech_stack.yaml — technology stack definitions
        - standards/*.md — coding standards (all .md files merged)
        - knowledge/CLAUDE.md — project-specific AI guidance
        - roadmap.md — project roadmap

    Args:
        project_dir: Path to the project directory.

    Returns:
        Formatted Markdown string with section headings,
        or empty string if the directory doesn't exist.
    """
    if not project_dir.is_dir():
        return ""

    sections: list[str] = []

    # --- project.yaml ---
    project_yaml = _read_file_safe(project_dir / "project.yaml")
    if project_yaml is not None:
        try:
            data = yaml.safe_load(project_yaml)
        except yaml.YAMLError:
            data = None
        if data and isinstance(data, dict):
            lines = ["## Project Info", ""]
            for key, value in data.items():
                lines.append(f"- **{key}**: {value}")
            sections.append("\n".join(lines))

    # --- tech_stack.yaml ---
    tech_stack_yaml = _read_file_safe(project_dir / "tech_stack.yaml")
    if tech_stack_yaml is not None:
        try:
            data = yaml.safe_load(tech_stack_yaml)
        except yaml.YAMLError:
            data = None
        if data and isinstance(data, dict):
            lines = ["## Tech Stack", ""]
            for key, value in data.items():
                if isinstance(value, list):
                    lines.append(f"- **{key}**: {', '.join(str(v) for v in value)}")
                elif isinstance(value, dict):
                    lines.append(f"- **{key}**:")
                    for k2, v2 in value.items():
                        lines.append(f"  - {k2}: {v2}")
                else:
                    lines.append(f"- **{key}**: {value}")
            sections.append("\n".join(lines))

    # --- standards/*.md ---
    standards_dir = project_dir / "standards"
    if standards_dir.is_dir():
        md_files = sorted(standards_dir.glob("*.md"))
        if md_files:
            parts = ["## Coding Standards", ""]
            for md_file in md_files:
                content = _read_file_safe(md_file)
                if content is not None and content.strip():
                    parts.append(f"### {md_file.stem}")
                    parts.append("")
                    parts.append(content.strip())
                    parts.append("")
            if len(parts) > 2:  # has actual content beyond header
                sections.append("\n".join(parts).rstrip())

    # --- knowledge/CLAUDE.md ---
    claude_md = _read_file_safe(project_dir / "knowledge" / "CLAUDE.md")
    if claude_md is not None and claude_md.strip():
        sections.append(f"## Project Knowledge\n\n{claude_md.strip()}")

    # --- roadmap.md ---
    roadmap_md = _read_file_safe(project_dir / "roadmap.md")
    if roadmap_md is not None and roadmap_md.strip():
        sections.append(f"## Roadmap\n\n{roadmap_md.strip()}")

    if not sections:
        return ""

    return "\n\n".join(sections)
