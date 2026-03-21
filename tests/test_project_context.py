"""Tests for project context functionality (F-006).

Covers:
- load_project_context() from knowledge.py
- session.py run_single_round signature includes project_dir parameter
"""

import inspect
from pathlib import Path

import pytest
import yaml

from nezha.pipeline.knowledge import load_project_context


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project_dir(tmp_path):
    """Create and return a project directory path (does NOT create the dir)."""
    return tmp_path / "project"


@pytest.fixture
def full_project_dir(tmp_path):
    """Create a fully populated project directory with all expected files."""
    d = tmp_path / "project"
    d.mkdir()

    # project.yaml
    (d / "project.yaml").write_text(
        yaml.dump({"name": "test-project", "description": "A test", "repo": "https://example.com"}),
        encoding="utf-8",
    )

    # tech_stack.yaml
    (d / "tech_stack.yaml").write_text(
        yaml.dump({"language": "Python", "framework": "FastAPI"}),
        encoding="utf-8",
    )

    # standards/*.md
    standards = d / "standards"
    standards.mkdir()
    (standards / "coding.md").write_text("Use type hints everywhere.", encoding="utf-8")
    (standards / "testing.md").write_text("100% coverage required.", encoding="utf-8")

    # knowledge/CLAUDE.md
    knowledge = d / "knowledge"
    knowledge.mkdir()
    (knowledge / "CLAUDE.md").write_text("# Project Knowledge\n\nBe helpful.", encoding="utf-8")

    # roadmap.md
    (d / "roadmap.md").write_text("# Roadmap\n\n## Current\n\n- Feature A\n\n## Backlog\n\n- Feature B", encoding="utf-8")

    return d


# ---------------------------------------------------------------------------
# load_project_context — non-existent directory returns empty string
# ---------------------------------------------------------------------------

class TestLoadProjectContextNonExistent:
    """AC: load_project_context(不存在目录) 返回空串."""

    def test_nonexistent_dir_returns_empty(self, project_dir):
        """Returns empty string when directory does not exist."""
        assert not project_dir.exists()
        result = load_project_context(project_dir)
        assert result == ""

    def test_file_instead_of_dir_returns_empty(self, tmp_path):
        """Returns empty string when path is a file, not a directory."""
        f = tmp_path / "project"
        f.write_text("not a dir")
        result = load_project_context(f)
        assert result == ""


# ---------------------------------------------------------------------------
# load_project_context — only project.yaml
# ---------------------------------------------------------------------------

class TestLoadProjectContextOnlyProjectYaml:
    """AC: load_project_context(只有 project.yaml) 仅输出项目信息段."""

    def test_only_project_yaml_returns_project_info(self, project_dir):
        """Only project.yaml present → output contains Project Info section only."""
        project_dir.mkdir()
        (project_dir / "project.yaml").write_text(
            yaml.dump({"name": "my-app", "description": "My App", "repo": "https://github.com/test"}),
            encoding="utf-8",
        )
        result = load_project_context(project_dir)
        assert "## Project Info" in result
        assert "my-app" in result
        assert "My App" in result

    def test_only_project_yaml_no_other_sections(self, project_dir):
        """Only project.yaml present → no Tech Stack, Standards, Knowledge, or Roadmap sections."""
        project_dir.mkdir()
        (project_dir / "project.yaml").write_text(
            yaml.dump({"name": "solo"}),
            encoding="utf-8",
        )
        result = load_project_context(project_dir)
        assert "## Project Info" in result
        assert "## Tech Stack" not in result
        assert "## Coding Standards" not in result
        assert "## Project Knowledge" not in result
        assert "## Roadmap" not in result

    def test_empty_project_yaml_returns_empty(self, project_dir):
        """Empty project.yaml → no output (no sections)."""
        project_dir.mkdir()
        (project_dir / "project.yaml").write_text("", encoding="utf-8")
        result = load_project_context(project_dir)
        assert result == ""

    def test_invalid_yaml_returns_empty(self, project_dir):
        """Invalid YAML in project.yaml → no output (graceful handling)."""
        project_dir.mkdir()
        (project_dir / "project.yaml").write_text(": : invalid: [yaml", encoding="utf-8")
        result = load_project_context(project_dir)
        assert "## Project Info" not in result


# ---------------------------------------------------------------------------
# load_project_context — full directory
# ---------------------------------------------------------------------------

class TestLoadProjectContextFullDir:
    """AC: load_project_context(完整目录) 包含所有段落."""

    def test_full_dir_has_project_info(self, full_project_dir):
        result = load_project_context(full_project_dir)
        assert "## Project Info" in result
        assert "test-project" in result

    def test_full_dir_has_tech_stack(self, full_project_dir):
        result = load_project_context(full_project_dir)
        assert "## Tech Stack" in result
        assert "Python" in result

    def test_full_dir_has_coding_standards(self, full_project_dir):
        result = load_project_context(full_project_dir)
        assert "## Coding Standards" in result
        assert "type hints" in result

    def test_full_dir_has_project_knowledge(self, full_project_dir):
        result = load_project_context(full_project_dir)
        assert "## Project Knowledge" in result
        assert "Be helpful" in result

    def test_full_dir_has_roadmap(self, full_project_dir):
        result = load_project_context(full_project_dir)
        assert "## Roadmap" in result
        assert "Feature A" in result

    def test_full_dir_all_sections_present(self, full_project_dir):
        """All five sections are present in one output."""
        result = load_project_context(full_project_dir)
        for heading in ["## Project Info", "## Tech Stack", "## Coding Standards", "## Project Knowledge", "## Roadmap"]:
            assert heading in result, f"Missing section: {heading}"


# ---------------------------------------------------------------------------
# load_project_context — standards/ merging
# ---------------------------------------------------------------------------

class TestLoadProjectContextStandardsMerge:
    """AC: load_project_context(standards/ 下多个 .md) 全部合并."""

    def test_multiple_standards_merged(self, full_project_dir):
        """All .md files in standards/ are merged into the output."""
        result = load_project_context(full_project_dir)
        assert "type hints" in result  # from coding.md
        assert "100% coverage" in result  # from testing.md

    def test_standards_each_has_subheading(self, full_project_dir):
        """Each standards file gets its own ### subheading (file stem)."""
        result = load_project_context(full_project_dir)
        assert "### coding" in result
        assert "### testing" in result

    def test_standards_sorted_alphabetically(self, tmp_path):
        """Standards files are merged in sorted order."""
        d = tmp_path / "project"
        d.mkdir()
        standards = d / "standards"
        standards.mkdir()
        (standards / "b_style.md").write_text("Style B content", encoding="utf-8")
        (standards / "a_naming.md").write_text("Naming A content", encoding="utf-8")

        result = load_project_context(d)
        # a_naming should appear before b_style
        idx_a = result.index("Naming A content")
        idx_b = result.index("Style B content")
        assert idx_a < idx_b

    def test_empty_standards_dir_no_section(self, tmp_path):
        """Empty standards/ directory → no Coding Standards section."""
        d = tmp_path / "project"
        d.mkdir()
        (d / "standards").mkdir()
        result = load_project_context(d)
        assert "## Coding Standards" not in result

    def test_standards_only_gitkeep_no_section(self, tmp_path):
        """standards/ with only .gitkeep (no .md) → no Coding Standards section."""
        d = tmp_path / "project"
        d.mkdir()
        standards = d / "standards"
        standards.mkdir()
        (standards / ".gitkeep").write_text("", encoding="utf-8")
        result = load_project_context(d)
        assert "## Coding Standards" not in result

    def test_standards_empty_md_files_skipped(self, tmp_path):
        """Empty .md files in standards/ are skipped."""
        d = tmp_path / "project"
        d.mkdir()
        standards = d / "standards"
        standards.mkdir()
        (standards / "empty.md").write_text("", encoding="utf-8")
        (standards / "whitespace.md").write_text("   \n  ", encoding="utf-8")
        result = load_project_context(d)
        assert "## Coding Standards" not in result


# ---------------------------------------------------------------------------
# session.py — run_single_round signature includes project_dir
# ---------------------------------------------------------------------------

class TestSessionRunSingleRoundSignature:
    """AC: session.py run_single_round 签名包含 project_dir 参数."""

    def test_run_single_round_has_project_dir_param(self):
        """run_single_round function signature includes project_dir parameter."""
        from nezha.pipeline.session import run_single_round
        sig = inspect.signature(run_single_round)
        assert "project_dir" in sig.parameters

    def test_run_single_round_project_dir_default_none(self):
        """project_dir parameter defaults to None."""
        from nezha.pipeline.session import run_single_round
        sig = inspect.signature(run_single_round)
        param = sig.parameters["project_dir"]
        assert param.default is None

    def test_run_multi_round_has_project_dir_param(self):
        """run_multi_round function signature also includes project_dir parameter."""
        from nezha.pipeline.session import run_multi_round
        sig = inspect.signature(run_multi_round)
        assert "project_dir" in sig.parameters

    def test_run_vibe_session_has_project_dir_param(self):
        """run_vibe_session function signature also includes project_dir parameter."""
        from nezha.pipeline.session import run_vibe_session
        sig = inspect.signature(run_vibe_session)
        assert "project_dir" in sig.parameters

    def test_session_imports_load_project_context(self):
        """session.py imports load_project_context from knowledge module."""
        from nezha.pipeline import session
        source = Path(session.__file__).read_text()
        assert "load_project_context" in source
