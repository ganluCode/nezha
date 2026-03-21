"""Built-in agent templates: prompt fallbacks and starter scaffolding files."""
from pathlib import Path

# Root of the templates directory (shipped with the package)
TEMPLATES_DIR = Path(__file__).parent

# Prompts fallback directory — two-layer lookup falls back here
PROMPTS_DIR = TEMPLATES_DIR / "prompts"

# Starter templates for nezha init
AGENTS_DIR = TEMPLATES_DIR / "agents"
