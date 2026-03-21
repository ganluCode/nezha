"""Tests for cmd_project_init (F-004)."""

from pathlib import Path

import pytest
import yaml

from nezha.interface.cli import cmd_project_init


@pytest.fixture
def executor_yaml(tmp_path):
    """Create a minimal executor.yaml in tmp_path and return its path."""
    config = {
        "executor": {"name": "test"},
        "workspace": {"base": "./workspace", "strategy": "per_agent"},
    }
    config_file = tmp_path / "executor.yaml"
    config_file.write_text(yaml.dump(config), encoding="utf-8")
    return str(config_file)


@pytest.fixture
def project_dir(tmp_path):
    """Return the expected project directory path."""
    return tmp_path / "workspace" / "project"


class TestProjectInitCreatesStructure:
    """Test that cmd_project_init creates the expected directory structure."""

    def test_creates_project_dir(self, executor_yaml, project_dir):
        cmd_project_init(config_path=executor_yaml)
        assert project_dir.is_dir()

    def test_creates_project_yaml(self, executor_yaml, project_dir):
        cmd_project_init(config_path=executor_yaml)
        f = project_dir / "project.yaml"
        assert f.is_file()
        content = f.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        # Should contain name, description, repo as keys (values empty)
        assert "name" in data or "name" in content
        assert "description" in content
        assert "repo" in content

    def test_creates_tech_stack_yaml(self, executor_yaml, project_dir):
        cmd_project_init(config_path=executor_yaml)
        f = project_dir / "tech_stack.yaml"
        assert f.is_file()

    def test_creates_standards_gitkeep(self, executor_yaml, project_dir):
        cmd_project_init(config_path=executor_yaml)
        f = project_dir / "standards" / ".gitkeep"
        assert f.is_file()

    def test_creates_knowledge_claude_md(self, executor_yaml, project_dir):
        cmd_project_init(config_path=executor_yaml)
        f = project_dir / "knowledge" / "CLAUDE.md"
        assert f.is_file()
        content = f.read_text(encoding="utf-8")
        assert "# Project Knowledge" in content

    def test_creates_roadmap_md(self, executor_yaml, project_dir):
        cmd_project_init(config_path=executor_yaml)
        f = project_dir / "roadmap.md"
        assert f.is_file()
        content = f.read_text(encoding="utf-8")
        assert "# Roadmap" in content
        assert "## Current" in content
        assert "## Backlog" in content

    def test_creates_prd_template(self, executor_yaml, project_dir):
        cmd_project_init(config_path=executor_yaml)
        f = project_dir / "prd-template.md"
        assert f.is_file()
        content = f.read_text(encoding="utf-8")
        assert "PRD Template" in content
        assert "Functional Requirements" in content

    def test_creates_prd_template_zh(self, executor_yaml, project_dir):
        cmd_project_init(config_path=executor_yaml)
        f = project_dir / "prd-template.zh.md"
        assert f.is_file()
        content = f.read_text(encoding="utf-8")
        assert "PRD 模板" in content
        assert "功能需求" in content


class TestProjectYamlTemplate:
    """Test project.yaml contains the required field template."""

    def test_has_name_field(self, executor_yaml, project_dir):
        cmd_project_init(config_path=executor_yaml)
        content = (project_dir / "project.yaml").read_text()
        assert "name:" in content

    def test_has_description_field(self, executor_yaml, project_dir):
        cmd_project_init(config_path=executor_yaml)
        content = (project_dir / "project.yaml").read_text()
        assert "description:" in content

    def test_has_repo_field(self, executor_yaml, project_dir):
        cmd_project_init(config_path=executor_yaml)
        content = (project_dir / "project.yaml").read_text()
        assert "repo:" in content


class TestSkipIfExists:
    """Test that existing project directory is not overwritten."""

    def test_skips_when_dir_exists(self, executor_yaml, project_dir, capsys):
        # Pre-create the project directory with a custom file
        project_dir.mkdir(parents=True, exist_ok=True)
        marker = project_dir / "custom_file.txt"
        marker.write_text("user data")

        cmd_project_init(config_path=executor_yaml)

        # Should print skip message
        captured = capsys.readouterr()
        assert "already exists" in captured.out

        # Custom file should still be there (not overwritten)
        assert marker.read_text() == "user data"

        # Template files should NOT be created (skipped entirely)
        assert not (project_dir / "project.yaml").exists()

    def test_prints_skip_message(self, executor_yaml, project_dir, capsys):
        project_dir.mkdir(parents=True, exist_ok=True)
        cmd_project_init(config_path=executor_yaml)
        captured = capsys.readouterr()
        assert "already exists" in captured.out
        assert "Skipping" in captured.out


class TestPrintOutput:
    """Test output messages on successful creation."""

    def test_prints_success_message(self, executor_yaml, project_dir, capsys):
        cmd_project_init(config_path=executor_yaml)
        captured = capsys.readouterr()
        assert "Initialized" in captured.out
        assert "project.yaml" in captured.out
        assert "tech_stack.yaml" in captured.out
        assert "CLAUDE.md" in captured.out
        assert "roadmap.md" in captured.out
        assert "prd-template.md" in captured.out
        assert "prd-template.zh.md" in captured.out


class TestConfigParsing:
    """Test that workspace.base is correctly resolved from executor.yaml."""

    def test_absolute_workspace_base(self, tmp_path):
        abs_ws = tmp_path / "abs_workspace"
        config = {
            "executor": {"name": "test"},
            "workspace": {"base": str(abs_ws)},
        }
        config_file = tmp_path / "executor.yaml"
        config_file.write_text(yaml.dump(config), encoding="utf-8")

        cmd_project_init(config_path=str(config_file))
        assert (abs_ws / "project" / "project.yaml").is_file()

    def test_relative_workspace_base(self, executor_yaml, project_dir):
        cmd_project_init(config_path=executor_yaml)
        assert (project_dir / "project.yaml").is_file()
