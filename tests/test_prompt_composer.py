"""Tests for Phase A: PromptComposer — composable prompt modules."""

from pathlib import Path

import pytest
import yaml

from nezha.config import ComposeConfig, load_agent_config
from nezha.pipeline.prompt_composer import compose_prompt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_prompts_dir(tmp_path: Path) -> Path:
    """Create a prompts directory under tmp_path."""
    d = tmp_path / "prompts"
    d.mkdir()
    return d


def _write_file(path: Path, content: str) -> None:
    """Create parent dirs and write content to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. test_compose_basic
# ---------------------------------------------------------------------------


class TestComposeBasic:
    def test_compose_basic(self, tmp_path):
        """Base + one module joined by separator."""
        prompts = _make_prompts_dir(tmp_path)
        _write_file(prompts / "coding" / "base.md", "# BASE")
        _write_file(prompts / "modules" / "phases" / "tdd.md", "## TDD MODULE")

        config = ComposeConfig(base="coding/base.md", sections=["phases/tdd"])
        result = compose_prompt(config, prompts)

        assert "# BASE" in result
        assert "## TDD MODULE" in result
        assert "\n\n---\n\n" in result

    def test_compose_separator_between_parts(self, tmp_path):
        """Verify exact separator between base and module."""
        prompts = _make_prompts_dir(tmp_path)
        _write_file(prompts / "coding" / "base.md", "BASE_CONTENT")
        _write_file(prompts / "modules" / "phases" / "tdd.md", "TDD_CONTENT")

        config = ComposeConfig(base="coding/base.md", sections=["phases/tdd"])
        result = compose_prompt(config, prompts)

        assert result == "BASE_CONTENT\n\n---\n\nTDD_CONTENT"


# ---------------------------------------------------------------------------
# 2. test_compose_multi_sections
# ---------------------------------------------------------------------------


class TestComposeMultiSections:
    def test_compose_multi_sections_order_preserved(self, tmp_path):
        """Multiple sections are joined in declared order."""
        prompts = _make_prompts_dir(tmp_path)
        _write_file(prompts / "coding" / "base.md", "BASE")
        _write_file(prompts / "modules" / "phases" / "context-acquisition.md", "CONTEXT")
        _write_file(prompts / "modules" / "phases" / "tdd.md", "TDD")
        _write_file(prompts / "modules" / "phases" / "regression.md", "REGRESSION")

        config = ComposeConfig(
            base="coding/base.md",
            sections=["phases/context-acquisition", "phases/tdd", "phases/regression"],
        )
        result = compose_prompt(config, prompts)

        parts = result.split("\n\n---\n\n")
        assert len(parts) == 4
        assert parts[0] == "BASE"
        assert parts[1] == "CONTEXT"
        assert parts[2] == "TDD"
        assert parts[3] == "REGRESSION"

    def test_compose_three_sections(self, tmp_path):
        """Three sections produce correct part count."""
        prompts = _make_prompts_dir(tmp_path)
        _write_file(prompts / "mybase.md", "B")
        _write_file(prompts / "modules" / "s1.md", "S1")
        _write_file(prompts / "modules" / "s2.md", "S2")
        _write_file(prompts / "modules" / "s3.md", "S3")

        config = ComposeConfig(base="mybase.md", sections=["s1", "s2", "s3"])
        result = compose_prompt(config, prompts)

        assert result.count("\n\n---\n\n") == 3


# ---------------------------------------------------------------------------
# 3. test_compose_with_variables
# ---------------------------------------------------------------------------


class TestComposeWithVariables:
    def test_compose_variables_in_base(self, tmp_path):
        """Variables are substituted in the base template."""
        prompts = _make_prompts_dir(tmp_path)
        _write_file(prompts / "base.md", "Workspace: {{workspace}}")
        _write_file(prompts / "modules" / "mod.md", "Project: {{project_name}}")

        config = ComposeConfig(base="base.md", sections=["mod"])
        result = compose_prompt(
            config, prompts,
            variables={"workspace": "/ws/path", "project_name": "my-project"},
        )

        assert "Workspace: /ws/path" in result
        assert "Project: my-project" in result

    def test_compose_variables_in_sections(self, tmp_path):
        """Variables are substituted in section modules."""
        prompts = _make_prompts_dir(tmp_path)
        _write_file(prompts / "base.md", "BASE")
        _write_file(prompts / "modules" / "phases" / "tdd.md",
                    "Run: `{{test_command}}`")

        config = ComposeConfig(base="base.md", sections=["phases/tdd"])
        result = compose_prompt(
            config, prompts,
            variables={"test_command": "pytest tests/ -x -v"},
        )

        assert "Run: `pytest tests/ -x -v`" in result

    def test_compose_unknown_variable_kept_as_is(self, tmp_path):
        """Unknown {{var}} placeholders are kept unchanged."""
        prompts = _make_prompts_dir(tmp_path)
        _write_file(prompts / "base.md", "Hello {{unknown_var}}")

        config = ComposeConfig(base="base.md", sections=[])
        result = compose_prompt(config, prompts, variables={"workspace": "/ws"})

        assert "{{unknown_var}}" in result

    def test_compose_no_variables_dict(self, tmp_path):
        """None variables leaves placeholders unchanged."""
        prompts = _make_prompts_dir(tmp_path)
        _write_file(prompts / "base.md", "Workspace: {{workspace}}")

        config = ComposeConfig(base="base.md", sections=[])
        result = compose_prompt(config, prompts, variables=None)

        assert "{{workspace}}" in result


# ---------------------------------------------------------------------------
# 4. test_compose_locale
# ---------------------------------------------------------------------------


class TestComposeLocale:
    def test_compose_locale_zh_prefers_localized_file(self, tmp_path):
        """With locale=zh_CN, prefers .zh.md files over .md."""
        prompts = _make_prompts_dir(tmp_path)
        _write_file(prompts / "base.md", "BASE EN")
        _write_file(prompts / "base.zh.md", "BASE ZH")
        _write_file(prompts / "modules" / "mod.md", "MOD EN")
        _write_file(prompts / "modules" / "mod.zh.md", "MOD ZH")

        config = ComposeConfig(base="base.md", sections=["mod"])
        result = compose_prompt(config, prompts, locale="zh_CN")

        assert "BASE ZH" in result
        assert "MOD ZH" in result
        assert "BASE EN" not in result
        assert "MOD EN" not in result

    def test_compose_locale_en_uses_default_files(self, tmp_path):
        """With locale=en, uses default .md files."""
        prompts = _make_prompts_dir(tmp_path)
        _write_file(prompts / "base.md", "BASE EN")
        _write_file(prompts / "base.zh.md", "BASE ZH")

        config = ComposeConfig(base="base.md", sections=[])
        result = compose_prompt(config, prompts, locale="en")

        assert "BASE EN" in result
        assert "BASE ZH" not in result

    def test_compose_locale_falls_back_to_default_if_no_localized(self, tmp_path):
        """If no .zh.md exists, falls back to .md."""
        prompts = _make_prompts_dir(tmp_path)
        _write_file(prompts / "base.md", "BASE ONLY EN")

        config = ComposeConfig(base="base.md", sections=[])
        result = compose_prompt(config, prompts, locale="zh_CN")

        assert "BASE ONLY EN" in result


# ---------------------------------------------------------------------------
# 5. test_compose_missing_section_raises
# ---------------------------------------------------------------------------


class TestComposeMissingSection:
    def test_compose_missing_section_raises_file_not_found(self, tmp_path):
        """A section file that does not exist raises FileNotFoundError."""
        prompts = _make_prompts_dir(tmp_path)
        _write_file(prompts / "base.md", "BASE")

        config = ComposeConfig(base="base.md", sections=["phases/nonexistent"])
        with pytest.raises(FileNotFoundError):
            compose_prompt(config, prompts)

    def test_compose_missing_base_raises_value_error(self, tmp_path):
        """Empty base string raises ValueError."""
        prompts = _make_prompts_dir(tmp_path)

        config = ComposeConfig(base="", sections=[])
        with pytest.raises(ValueError, match="base must not be empty"):
            compose_prompt(config, prompts)

    def test_compose_missing_base_file_raises_file_not_found(self, tmp_path):
        """A base file that does not exist raises FileNotFoundError."""
        prompts = _make_prompts_dir(tmp_path)

        config = ComposeConfig(base="coding/nonexistent.md", sections=[])
        with pytest.raises(FileNotFoundError):
            compose_prompt(config, prompts)


# ---------------------------------------------------------------------------
# 6. test_compose_empty_sections
# ---------------------------------------------------------------------------


class TestComposeEmptySections:
    def test_compose_empty_sections_returns_only_base(self, tmp_path):
        """Empty sections list returns only base content (no separator)."""
        prompts = _make_prompts_dir(tmp_path)
        _write_file(prompts / "base.md", "JUST BASE")

        config = ComposeConfig(base="base.md", sections=[])
        result = compose_prompt(config, prompts)

        assert result == "JUST BASE"
        assert "---" not in result

    def test_compose_default_sections_is_empty(self):
        """ComposeConfig default sections list is empty."""
        config = ComposeConfig(base="something.md")
        assert config.sections == []


# ---------------------------------------------------------------------------
# 7. test_compose_project_override
# ---------------------------------------------------------------------------


class TestComposeProjectOverride:
    def test_project_level_base_overrides_builtin(self, tmp_path):
        """Project-level prompts/ overrides built-in templates (layer 1 > layer 4)."""
        prompts = _make_prompts_dir(tmp_path)
        # Write a project-level file that shadows the built-in coding/base.md
        _write_file(prompts / "coding" / "base.md", "PROJECT BASE OVERRIDE")

        config = ComposeConfig(base="coding/base.md", sections=[])
        result = compose_prompt(config, prompts)

        assert "PROJECT BASE OVERRIDE" in result

    def test_project_level_module_overrides_builtin(self, tmp_path):
        """Project-level module overrides the built-in template module."""
        prompts = _make_prompts_dir(tmp_path)
        _write_file(prompts / "coding" / "base.md", "BASE")
        # This would shadow any built-in modules/phases/tdd.md
        _write_file(prompts / "modules" / "phases" / "tdd.md", "PROJECT TDD OVERRIDE")

        config = ComposeConfig(base="coding/base.md", sections=["phases/tdd"])
        result = compose_prompt(config, prompts)

        assert "PROJECT TDD OVERRIDE" in result

    def test_builtin_module_used_when_no_project_override(self, tmp_path):
        """Built-in module is used when no project-level override exists."""
        prompts = _make_prompts_dir(tmp_path)
        _write_file(prompts / "coding" / "base.md", "BASE")

        # modules/phases/context-acquisition.md exists as a built-in template
        config = ComposeConfig(
            base="coding/base.md",
            sections=["phases/context-acquisition"],
        )
        result = compose_prompt(config, prompts)

        # Built-in module content should appear
        assert "CONTEXT" in result or "dag_context" in result or ".dag_context.json" in result


# ---------------------------------------------------------------------------
# 8. test_compose_config_parsing
# ---------------------------------------------------------------------------


class TestComposeConfigParsing:
    def test_compose_config_parsed_from_yaml(self, tmp_path):
        """Load a YAML with compose config and verify ComposeConfig is parsed."""
        agent_yaml = tmp_path / "agent.yaml"
        agent_yaml.write_text(
            """
agent:
  name: test-agent
  category: coding
session:
  mode: multi_round
  prompts:
    worker: coding/worker.md
  compose:
    worker:
      base: coding/base.md
      sections:
        - phases/context-acquisition
        - stacks/java-spring
""",
            encoding="utf-8",
        )

        config = load_agent_config(agent_yaml)

        assert "worker" in config.session.compose
        compose = config.session.compose["worker"]
        assert isinstance(compose, ComposeConfig)
        assert compose.base == "coding/base.md"
        assert compose.sections == ["phases/context-acquisition", "stacks/java-spring"]

    def test_compose_config_empty_when_not_in_yaml(self, tmp_path):
        """Agent YAML without compose section has empty compose dict."""
        agent_yaml = tmp_path / "agent.yaml"
        agent_yaml.write_text(
            """
agent:
  name: simple-agent
  category: coding
session:
  mode: multi_round
  prompts:
    worker: coding/worker.md
""",
            encoding="utf-8",
        )

        config = load_agent_config(agent_yaml)
        assert config.session.compose == {}

    def test_compose_config_multiple_keys(self, tmp_path):
        """Multiple compose keys (worker, vibe) are all parsed."""
        agent_yaml = tmp_path / "agent.yaml"
        agent_yaml.write_text(
            """
agent:
  name: multi-agent
  category: coding
session:
  prompts:
    worker: coding/worker.md
  compose:
    worker:
      base: coding/base.md
      sections:
        - phases/tdd
    vibe:
      base: coding/vibe-base.md
      sections: []
""",
            encoding="utf-8",
        )

        config = load_agent_config(agent_yaml)

        assert "worker" in config.session.compose
        assert "vibe" in config.session.compose
        assert config.session.compose["worker"].base == "coding/base.md"
        assert config.session.compose["worker"].sections == ["phases/tdd"]
        assert config.session.compose["vibe"].base == "coding/vibe-base.md"
        assert config.session.compose["vibe"].sections == []

    def test_compose_config_sections_defaults_to_empty_list(self, tmp_path):
        """A compose entry with only base (no sections) defaults sections to []."""
        agent_yaml = tmp_path / "agent.yaml"
        agent_yaml.write_text(
            """
agent:
  name: simple-agent
session:
  compose:
    worker:
      base: coding/base.md
""",
            encoding="utf-8",
        )

        config = load_agent_config(agent_yaml)
        assert config.session.compose["worker"].sections == []


# ---------------------------------------------------------------------------
# 9. test_session_fallback_no_compose
# ---------------------------------------------------------------------------


class TestSessionFallbackNoCompose:
    def test_agent_without_compose_has_empty_compose(self, tmp_path):
        """Agent config without compose section has empty compose dict (backward compat)."""
        agent_yaml = tmp_path / "agent.yaml"
        agent_yaml.write_text(
            """
agent:
  name: old-agent
  category: coding
session:
  mode: multi_round
  prompts:
    worker: java/worker.md
""",
            encoding="utf-8",
        )

        config = load_agent_config(agent_yaml)
        # compose should default to empty dict — no KeyError
        assert config.session.compose == {}
        # Compose lookup for "worker" returns None (falsy)
        compose = config.session.compose.get("worker")
        assert compose is None

    def test_compose_config_dataclass_defaults(self):
        """ComposeConfig has correct defaults."""
        c = ComposeConfig()
        assert c.base == ""
        assert c.sections == []

    def test_compose_config_with_base_only(self):
        """ComposeConfig with only base set."""
        c = ComposeConfig(base="coding/base.md")
        assert c.base == "coding/base.md"
        assert c.sections == []

    def test_compose_config_with_sections(self):
        """ComposeConfig with base and sections set."""
        c = ComposeConfig(base="coding/base.md", sections=["phases/tdd", "stacks/python"])
        assert c.base == "coding/base.md"
        assert c.sections == ["phases/tdd", "stacks/python"]

    def test_existing_agents_config_still_loads(self):
        """Existing agent configs (without compose) still load without error."""
        base_dir = Path(__file__).parent.parent
        for yaml_path in (base_dir / "agents").glob("*.yaml"):
            config = load_agent_config(yaml_path)
            # compose should be empty dict (not raise AttributeError)
            assert hasattr(config.session, "compose")
            assert isinstance(config.session.compose, dict)
