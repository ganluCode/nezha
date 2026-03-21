"""Tests for Task D: templates, two-layer prompt lookup, helper-agent, nezha init."""

import shutil
import sys
from pathlib import Path

import pytest
import yaml

from nezha.config import load_agent_config
from nezha.pipeline.prompt_template import resolve_prompt_path
from nezha.templates import AGENTS_DIR, PROMPTS_DIR, TEMPLATES_DIR


# ---------------------------------------------------------------------------
# F-001: Templates structure
# ---------------------------------------------------------------------------


class TestTemplatesStructure:
    def test_templates_dir_exists(self):
        assert TEMPLATES_DIR.is_dir()

    def test_prompts_dir_exists(self):
        assert PROMPTS_DIR.is_dir()

    def test_agents_dir_exists(self):
        assert AGENTS_DIR.is_dir()

    def test_starter_executor_yaml_exists(self):
        assert (TEMPLATES_DIR / "executor.yaml").is_file()

    def test_starter_coding_agent_yaml_exists(self):
        assert (AGENTS_DIR / "coding-agent.yaml").is_file()

    def test_helper_prompt_exists(self):
        assert (PROMPTS_DIR / "helper" / "worker.md").is_file()

    def test_coding_vibe_prompt_exists(self):
        assert (PROMPTS_DIR / "coding" / "vibe.md").is_file()

    def test_helper_prompt_has_five_scenarios(self):
        content = (PROMPTS_DIR / "helper" / "worker.md").read_text()
        # Check all 5 scenario headers are present
        for n in range(1, 6):
            assert f"SCENARIO {n}" in content

    def test_helper_prompt_mentions_readonly(self):
        content = (PROMPTS_DIR / "helper" / "worker.md").read_text()
        # Confirm read-only constraint is documented
        assert "Read-only" in content or "read-only" in content or "NOT" in content

    def test_coding_vibe_prompt_has_user_instruction(self):
        content = (PROMPTS_DIR / "coding" / "vibe.md").read_text()
        assert "{{user_instruction}}" in content

    def test_starter_executor_yaml_is_valid(self):
        with open(TEMPLATES_DIR / "executor.yaml") as f:
            data = yaml.safe_load(f)
        assert "executor" in data or "workspace" in data  # has at least one section

    def test_starter_coding_agent_yaml_is_valid(self):
        with open(AGENTS_DIR / "coding-agent.yaml") as f:
            data = yaml.safe_load(f)
        assert data["agent"]["name"] == "coding-agent"
        assert data["agent"]["category"] == "coding"


# ---------------------------------------------------------------------------
# F-002: Two-layer prompt lookup
# ---------------------------------------------------------------------------


class TestResolvePromptPath:
    def test_returns_project_path_when_exists(self, tmp_path):
        # Create a prompt in the project prompts dir
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        prompt_file = prompts_dir / "coding" / "worker.md"
        prompt_file.parent.mkdir()
        prompt_file.write_text("project prompt")

        result = resolve_prompt_path(prompts_dir, "coding/worker.md")
        assert result == prompt_file

    def test_falls_back_to_package_templates(self, tmp_path):
        # Project dir exists but does NOT have the prompt
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()

        # helper/worker.md exists in package templates
        result = resolve_prompt_path(prompts_dir, "helper/worker.md")
        assert result == PROMPTS_DIR / "helper" / "worker.md"
        assert result.exists()

    def test_falls_back_for_coding_vibe(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()

        result = resolve_prompt_path(prompts_dir, "coding/vibe.md")
        assert result == PROMPTS_DIR / "coding" / "vibe.md"
        assert result.exists()

    def test_returns_project_path_when_neither_exists(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()

        result = resolve_prompt_path(prompts_dir, "nonexistent/prompt.md")
        # Should return the project-level path (load_and_render will raise FileNotFoundError)
        assert result == prompts_dir / "nonexistent" / "prompt.md"
        assert not result.exists()

    def test_project_layer_takes_priority_over_templates(self, tmp_path):
        # Create a prompt in BOTH project dir and templates (simulated)
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "helper").mkdir()
        project_prompt = prompts_dir / "helper" / "worker.md"
        project_prompt.write_text("custom project prompt")

        result = resolve_prompt_path(prompts_dir, "helper/worker.md")
        # Layer 1 (project) wins
        assert result == project_prompt

    def test_prompts_dir_not_created_by_resolve(self, tmp_path):
        # resolve_prompt_path should NOT create directories
        prompts_dir = tmp_path / "nonexistent_prompts"
        resolve_prompt_path(prompts_dir, "helper/worker.md")
        # prompts_dir itself should NOT be created
        assert not prompts_dir.exists()


# ---------------------------------------------------------------------------
# F-003 + F-004: helper-agent.yaml and prompts/helper/worker.md
# ---------------------------------------------------------------------------


class TestHelperAgent:
    TEMPLATES = Path(__file__).parent.parent / "src" / "nezha" / "templates"

    def test_helper_agent_yaml_exists(self):
        assert (self.TEMPLATES / "agents" / "helper-agent.yaml").is_file()

    def test_helper_agent_config_loads(self):
        config = load_agent_config(self.TEMPLATES / "agents" / "helper-agent.yaml")
        assert config.agent.name == "helper-agent"

    def test_helper_agent_category_is_management(self):
        config = load_agent_config(self.TEMPLATES / "agents" / "helper-agent.yaml")
        assert config.agent.category == "management"

    def test_helper_agent_is_callable(self):
        config = load_agent_config(self.TEMPLATES / "agents" / "helper-agent.yaml")
        assert config.agent.callable

    def test_helper_agent_mode_is_single_round(self):
        config = load_agent_config(self.TEMPLATES / "agents" / "helper-agent.yaml")
        assert config.session.mode == "single_round"

    def test_helper_agent_worker_prompt_configured(self):
        config = load_agent_config(self.TEMPLATES / "agents" / "helper-agent.yaml")
        assert config.session.prompts.get("worker") == "helper/worker.md"

    def test_helper_agent_has_no_target(self):
        config = load_agent_config(self.TEMPLATES / "agents" / "helper-agent.yaml")
        assert config.target is None

    def test_helper_agent_tools_include_write(self):
        """Helper agent has Write for analysis reports but no Edit."""
        config = load_agent_config(self.TEMPLATES / "agents" / "helper-agent.yaml")
        tools = config.engine.tools
        assert "Read" in tools
        assert "Glob" in tools
        assert "Grep" in tools
        assert "Bash" in tools
        assert "Write" in tools
        assert "Edit" not in tools

    def test_helper_prompt_exists(self):
        assert (self.TEMPLATES / "prompts" / "helper" / "worker.md").is_file()

    def test_helper_prompt_has_nine_scenarios(self):
        content = (self.TEMPLATES / "prompts" / "helper" / "worker.md").read_text()
        for n in range(1, 10):
            assert f"SCENARIO {n}" in content


# ---------------------------------------------------------------------------
# F-005: nezha init command
# ---------------------------------------------------------------------------


class TestCmdInit:
    def test_init_creates_project_structure(self, tmp_path):
        from nezha.interface.cli import cmd_init

        project_path = tmp_path / "my-project"
        cmd_init(str(project_path))

        assert project_path.is_dir()
        assert (project_path / "executor.yaml").is_file()
        assert (project_path / "agents").is_dir()
        assert (project_path / "agents" / "coding-agent.yaml").is_file()
        assert (project_path / "prompts").is_dir()
        assert (project_path / "workspace").is_dir()
        assert (project_path / "input").is_dir()
        assert (project_path / ".gitignore").is_file()

    def test_init_executor_yaml_is_valid(self, tmp_path):
        from nezha.interface.cli import cmd_init

        project_path = tmp_path / "my-project"
        cmd_init(str(project_path))

        with open(project_path / "executor.yaml") as f:
            data = yaml.safe_load(f)
        assert data is not None

    def test_init_coding_agent_yaml_loads(self, tmp_path):
        from nezha.interface.cli import cmd_init
        from nezha.config import load_agent_config, load_executor_config, resolve_workspace

        project_path = tmp_path / "my-project"
        cmd_init(str(project_path))

        config = load_agent_config(project_path / "agents" / "coding-agent.yaml")
        assert config.agent.name == "coding-agent"

    def test_init_aborts_if_dir_exists(self, tmp_path):
        from nezha.interface.cli import cmd_init

        project_path = tmp_path / "existing"
        project_path.mkdir()
        # Create a non-empty directory — cmd_init aborts only when dir is non-empty
        (project_path / "some_file.txt").write_text("existing content")

        with pytest.raises(SystemExit):
            cmd_init(str(project_path))

    def test_init_gitignore_ignores_workspace(self, tmp_path):
        from nezha.interface.cli import cmd_init

        project_path = tmp_path / "my-project"
        cmd_init(str(project_path))

        gitignore = (project_path / ".gitignore").read_text()
        assert "workspace/" in gitignore

    def test_init_prompts_dir_is_empty(self, tmp_path):
        from nezha.interface.cli import cmd_init

        project_path = tmp_path / "my-project"
        cmd_init(str(project_path))

        # prompts/ directory exists but is empty (user adds their own prompts)
        prompts_dir = project_path / "prompts"
        assert prompts_dir.is_dir()
        assert list(prompts_dir.iterdir()) == []

    def test_init_two_layer_lookup_works_for_new_project(self, tmp_path):
        """After init, resolve_prompt_path should find helper prompt in package templates."""
        from nezha.interface.cli import cmd_init

        project_path = tmp_path / "my-project"
        cmd_init(str(project_path))

        prompts_dir = project_path / "prompts"
        # No helper/worker.md in project dir → should fall back to package template
        result = resolve_prompt_path(prompts_dir, "helper/worker.md")
        assert result == PROMPTS_DIR / "helper" / "worker.md"
        assert result.exists()
