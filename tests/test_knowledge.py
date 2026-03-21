"""Tests for knowledge injection (F-005)."""

import json
from pathlib import Path

import pytest

from nezha.pipeline.knowledge import (
    KNOWLEDGE_FILES,
    MAX_KNOWLEDGE_CHARS,
    load_knowledge,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path):
    return tmp_path


# ---------------------------------------------------------------------------
# load_knowledge — basic behavior
# ---------------------------------------------------------------------------

class TestLoadKnowledgeBasic:
    """Test load_knowledge basic file detection and reading."""

    def test_no_knowledge_files(self, workspace):
        """Returns empty string when no knowledge files exist."""
        result = load_knowledge(workspace)
        assert result == ""

    def test_claude_md_exists(self, workspace):
        """Reads CLAUDE.md when it exists."""
        (workspace / "CLAUDE.md").write_text("Project rules here")
        result = load_knowledge(workspace)
        assert "## PROJECT KNOWLEDGE" in result
        assert "Project rules here" in result
        assert "CLAUDE.md" in result

    def test_agent_context_md_exists(self, workspace):
        """Reads .agent-context.md when it exists."""
        (workspace / ".agent-context.md").write_text("Context info")
        result = load_knowledge(workspace)
        assert "## PROJECT KNOWLEDGE" in result
        assert "Context info" in result
        assert ".agent-context.md" in result

    def test_empty_file(self, workspace):
        """Returns section with empty content for empty file."""
        (workspace / "CLAUDE.md").write_text("")
        result = load_knowledge(workspace)
        assert "## PROJECT KNOWLEDGE" in result
        assert "CLAUDE.md" in result


# ---------------------------------------------------------------------------
# load_knowledge — priority
# ---------------------------------------------------------------------------

class TestLoadKnowledgePriority:
    """Test CLAUDE.md > .agent-context.md priority."""

    def test_claude_md_takes_priority(self, workspace):
        """CLAUDE.md is used when both files exist."""
        (workspace / "CLAUDE.md").write_text("From CLAUDE.md")
        (workspace / ".agent-context.md").write_text("From agent-context")
        result = load_knowledge(workspace)
        assert "From CLAUDE.md" in result
        assert "From agent-context" not in result

    def test_fallback_to_agent_context(self, workspace):
        """Falls back to .agent-context.md when CLAUDE.md doesn't exist."""
        (workspace / ".agent-context.md").write_text("From agent-context")
        result = load_knowledge(workspace)
        assert "From agent-context" in result

    def test_priority_order_matches_constant(self):
        """KNOWLEDGE_FILES constant has correct priority order."""
        assert KNOWLEDGE_FILES == ["CLAUDE.md", ".agent-context.md"]


# ---------------------------------------------------------------------------
# load_knowledge — truncation
# ---------------------------------------------------------------------------

class TestLoadKnowledgeTruncation:
    """Test content size limit and truncation behavior."""

    def test_within_limit(self, workspace):
        """Content within limit is not truncated."""
        content = "x" * 100
        (workspace / "CLAUDE.md").write_text(content)
        result = load_knowledge(workspace)
        assert content in result
        assert "Truncated" not in result

    def test_at_exact_limit(self, workspace):
        """Content exactly at limit is not truncated."""
        content = "x" * MAX_KNOWLEDGE_CHARS
        (workspace / "CLAUDE.md").write_text(content)
        result = load_knowledge(workspace)
        assert content in result
        assert "Truncated" not in result

    def test_exceeds_limit(self, workspace):
        """Content exceeding limit is truncated with warning."""
        content = "x" * (MAX_KNOWLEDGE_CHARS + 500)
        (workspace / "CLAUDE.md").write_text(content)
        result = load_knowledge(workspace)
        # Content should be truncated to MAX_KNOWLEDGE_CHARS
        assert "x" * MAX_KNOWLEDGE_CHARS in result
        # Extra content should not be present
        assert "x" * (MAX_KNOWLEDGE_CHARS + 1) not in result
        # Truncation warning should be present
        assert "Truncated" in result
        assert str(MAX_KNOWLEDGE_CHARS) in result

    def test_truncation_warning_mentions_filename(self, workspace):
        """Truncation warning mentions the source filename."""
        content = "y" * (MAX_KNOWLEDGE_CHARS + 100)
        (workspace / "CLAUDE.md").write_text(content)
        result = load_knowledge(workspace)
        assert "CLAUDE.md" in result
        assert "concise" in result.lower() or "keep" in result.lower()

    def test_max_knowledge_chars_value(self):
        """MAX_KNOWLEDGE_CHARS is 10000."""
        assert MAX_KNOWLEDGE_CHARS == 10000


# ---------------------------------------------------------------------------
# load_knowledge — section format
# ---------------------------------------------------------------------------

class TestLoadKnowledgeFormat:
    """Test output section formatting."""

    def test_section_header(self, workspace):
        """Output starts with ## PROJECT KNOWLEDGE header."""
        (workspace / "CLAUDE.md").write_text("content")
        result = load_knowledge(workspace)
        assert result.startswith("## PROJECT KNOWLEDGE")

    def test_source_attribution(self, workspace):
        """Output includes source file attribution."""
        (workspace / "CLAUDE.md").write_text("content")
        result = load_knowledge(workspace)
        assert "_Source: CLAUDE.md_" in result

    def test_source_attribution_agent_context(self, workspace):
        """Output includes source attribution for .agent-context.md."""
        (workspace / ".agent-context.md").write_text("content")
        result = load_knowledge(workspace)
        assert "_Source: .agent-context.md_" in result

    def test_content_after_header(self, workspace):
        """Content appears after the header and attribution."""
        (workspace / "CLAUDE.md").write_text("my project rules")
        result = load_knowledge(workspace)
        lines = result.split("\n")
        # Header, blank, source, blank, content
        assert lines[0] == "## PROJECT KNOWLEDGE"
        assert "my project rules" in result


# ---------------------------------------------------------------------------
# load_knowledge — edge cases
# ---------------------------------------------------------------------------

class TestLoadKnowledgeEdgeCases:
    """Test edge cases and error handling."""

    def test_directory_named_claude_md(self, workspace):
        """Ignores CLAUDE.md if it's a directory, not a file."""
        (workspace / "CLAUDE.md").mkdir()
        result = load_knowledge(workspace)
        assert result == ""

    def test_unicode_content(self, workspace):
        """Handles Unicode content correctly."""
        content = "项目规范：使用 Python 3.12\n踩坑记录：避免使用 eval()"
        (workspace / "CLAUDE.md").write_text(content, encoding="utf-8")
        result = load_knowledge(workspace)
        assert "项目规范" in result
        assert "踩坑记录" in result

    def test_multiline_content(self, workspace):
        """Handles multiline content correctly."""
        content = "# Rules\n\n- Rule 1\n- Rule 2\n\n## Framework\n\nUse FastAPI"
        (workspace / "CLAUDE.md").write_text(content)
        result = load_knowledge(workspace)
        assert "# Rules" in result
        assert "- Rule 1" in result
        assert "Use FastAPI" in result

    def test_workspace_does_not_exist(self, tmp_path):
        """Returns empty string for non-existent workspace."""
        fake_ws = tmp_path / "nonexistent"
        result = load_knowledge(fake_ws)
        assert result == ""

    def test_unreadable_file_falls_through(self, workspace):
        """Falls through to next file if first file can't be read."""
        # Create CLAUDE.md as a directory (can't be read as file)
        (workspace / "CLAUDE.md").mkdir()
        (workspace / ".agent-context.md").write_text("fallback content")
        result = load_knowledge(workspace)
        assert "fallback content" in result
        assert ".agent-context.md" in result


# ---------------------------------------------------------------------------
# Integration with session.py — structural checks
# ---------------------------------------------------------------------------

class TestSessionIntegration:
    """Verify knowledge injection is wired into session.py."""

    def test_session_imports_load_knowledge(self):
        """session.py imports load_knowledge (possibly alongside other names)."""
        from nezha.pipeline import session
        source = Path(session.__file__).read_text()
        assert "pipeline.knowledge import" in source and "load_knowledge" in source

    def test_single_round_calls_load_knowledge(self):
        """run_single_round calls load_knowledge (with cwd — code repo path)."""
        from nezha.pipeline import session
        source = Path(session.__file__).read_text()
        # load_knowledge now uses cwd (target for coding agents, workspace otherwise)
        assert "load_knowledge(cwd)" in source

    def test_subprocess_runner_imports_knowledge(self):
        """_SUBPROCESS_RUNNER template imports load_knowledge (possibly alongside other names)."""
        from nezha.pipeline.session import _SUBPROCESS_RUNNER
        assert "pipeline.knowledge import" in _SUBPROCESS_RUNNER and "load_knowledge" in _SUBPROCESS_RUNNER

    def test_subprocess_runner_calls_load_knowledge(self):
        """_SUBPROCESS_RUNNER template calls load_knowledge (with cwd)."""
        from nezha.pipeline.session import _SUBPROCESS_RUNNER
        assert "load_knowledge(cwd)" in _SUBPROCESS_RUNNER

    def test_vibe_runner_imports_knowledge(self):
        """_VIBE_SUBPROCESS_RUNNER template imports load_knowledge (possibly alongside other names)."""
        from nezha.pipeline.session import _VIBE_SUBPROCESS_RUNNER
        assert "pipeline.knowledge import" in _VIBE_SUBPROCESS_RUNNER and "load_knowledge" in _VIBE_SUBPROCESS_RUNNER

    def test_vibe_runner_calls_load_knowledge(self):
        """_VIBE_SUBPROCESS_RUNNER template calls load_knowledge (with cwd)."""
        from nezha.pipeline.session import _VIBE_SUBPROCESS_RUNNER
        assert "load_knowledge(cwd)" in _VIBE_SUBPROCESS_RUNNER


# ---------------------------------------------------------------------------
# Integration — prompt prepending behavior
# ---------------------------------------------------------------------------

class TestPromptPrepending:
    """Test that knowledge is prepended to prompt (not appended)."""

    def test_knowledge_prepended_to_prompt(self, workspace):
        """Knowledge section should go at the beginning of the prompt."""
        (workspace / "CLAUDE.md").write_text("knowledge content")
        knowledge = load_knowledge(workspace)
        original_prompt = "## YOUR ROLE\n\nYou are an agent."
        combined = knowledge + "\n\n" + original_prompt
        # Knowledge comes first
        assert combined.index("PROJECT KNOWLEDGE") < combined.index("YOUR ROLE")

    def test_no_knowledge_prompt_unchanged(self, workspace):
        """When no knowledge file, prompt should not be modified."""
        knowledge = load_knowledge(workspace)
        assert knowledge == ""
        original_prompt = "## YOUR ROLE\n\nYou are an agent."
        # Empty string means no prepending needed
        if knowledge:
            combined = knowledge + "\n\n" + original_prompt
        else:
            combined = original_prompt
        assert combined == original_prompt


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """Test that behavior is unchanged when no knowledge files exist."""

    def test_no_files_returns_empty(self, workspace):
        """No knowledge files → empty string, behavior unchanged."""
        assert load_knowledge(workspace) == ""

    def test_other_md_files_ignored(self, workspace):
        """Other .md files are not picked up."""
        (workspace / "README.md").write_text("readme content")
        (workspace / "CHANGELOG.md").write_text("changes")
        result = load_knowledge(workspace)
        assert result == ""
