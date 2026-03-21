"""Prompt template loading with variable injection."""

import re
from pathlib import Path

# Built-in templates prompts dir (shipped with the package — fallback layer 2)
_TEMPLATES_PROMPTS = Path(__file__).parent.parent / "templates" / "prompts"


def resolve_prompt_path(
    prompts_dir: Path,
    prompt_path: str,
    locale: str = "en",
) -> Path:
    """Four-layer prompt lookup with locale support.

    For locale="zh_CN" and prompt_path="frontend/worker.md", the lookup order is:
      1. <prompts_dir>/frontend/worker.zh.md   (project-level locale-specific)
      2. <prompts_dir>/frontend/worker.md       (project-level default)
      3. <package>/templates/prompts/frontend/worker.zh.md  (built-in locale-specific)
      4. <package>/templates/prompts/frontend/worker.md     (built-in default)

    For locale="en" (default), skips locale-specific steps → same as original two-layer lookup.

    Returns the resolved Path. Falls back to the layer-2 path if nothing is found
    (load_and_render will then raise FileNotFoundError with a clear message).
    """
    p = Path(prompt_path)

    # Build locale-specific filename: "worker.md" → "worker.zh.md"
    lang = locale.split("_")[0]  # "zh_CN" → "zh", "en" → "en"
    localized_path = str(p.with_stem(f"{p.stem}.{lang}")) if lang != "en" else None

    candidates = []
    if localized_path:
        candidates.append(prompts_dir / localized_path)           # 1: project locale
    candidates.append(prompts_dir / prompt_path)                   # 2: project default
    if localized_path:
        candidates.append(_TEMPLATES_PROMPTS / localized_path)    # 3: built-in locale
    candidates.append(_TEMPLATES_PROMPTS / prompt_path)           # 4: built-in default

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return prompts_dir / prompt_path  # let load_and_render raise a descriptive error


def load_prompt(template_path: str | Path) -> str:
    """Load a prompt template from file."""
    path = Path(template_path)
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, variables: dict[str, str] | None = None) -> str:
    """Render a prompt template by replacing {{variable}} placeholders.

    Args:
        template: The prompt template string
        variables: Dict of variable names to values

    Returns:
        Rendered prompt with variables substituted
    """
    if not variables:
        return template

    def replacer(match):
        key = match.group(1).strip()
        return variables.get(key, match.group(0))  # keep original if not found

    return re.sub(r"\{\{(.+?)\}\}", replacer, template)


def load_and_render(
    template_path: str | Path,
    variables: dict[str, str] | None = None,
) -> str:
    """Load a prompt template and render it with variables."""
    template = load_prompt(template_path)
    return render_prompt(template, variables)
