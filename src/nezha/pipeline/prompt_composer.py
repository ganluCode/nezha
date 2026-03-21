"""Prompt composition: assemble prompts from reusable modules."""

from pathlib import Path

from nezha.pipeline.prompt_template import (
    load_and_render,
    resolve_prompt_path,
)


def compose_prompt(
    config,
    prompts_dir: Path,
    locale: str = "en",
    variables: dict[str, str] | None = None,
) -> str:
    """Assemble a prompt from base template + section modules.

    Each section name maps to modules/{name}.md (e.g. "phases/tdd" -> "modules/phases/tdd.md").
    All parts are resolved via resolve_prompt_path() for 4-layer locale lookup,
    then rendered with variables and concatenated.

    Args:
        config: ComposeConfig with base and sections
        prompts_dir: Project-level prompts directory
        locale: Locale for template lookup (e.g. "en", "zh_CN")
        variables: Template variables for {{variable}} substitution

    Returns:
        Assembled prompt string
    """
    if not config.base:
        raise ValueError("ComposeConfig.base must not be empty")

    parts = []

    # Load and render base template
    base_path = resolve_prompt_path(prompts_dir, config.base, locale)
    parts.append(load_and_render(base_path, variables))

    # Load and render each section module
    for section in config.sections:
        module_path_str = f"modules/{section}.md"
        module_path = resolve_prompt_path(prompts_dir, module_path_str, locale)
        parts.append(load_and_render(module_path, variables))

    return "\n\n---\n\n".join(parts)
