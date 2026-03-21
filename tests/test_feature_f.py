"""Tests for Task F: agent-context.md cross-task memory injection."""

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nezha.pipeline.knowledge import (
    AGENT_CONTEXT_FILE,
    MAX_AGENT_CONTEXT_CHARS,
    load_agent_context,
)


# ---------------------------------------------------------------------------
# F-001: load_agent_context()
# ---------------------------------------------------------------------------


class TestLoadAgentContext:
    def test_returns_empty_when_no_file(self, tmp_path):
        result = load_agent_context(tmp_path)
        assert result == ""

    def test_returns_section_when_file_exists(self, tmp_path):
        (tmp_path / "agent-context.md").write_text(
            "## Previous Tasks\n- Completed F-001", encoding="utf-8"
        )
        result = load_agent_context(tmp_path)
        assert "## AGENT MEMORY" in result
        assert "Previous Tasks" in result

    def test_section_includes_source_label(self, tmp_path):
        (tmp_path / "agent-context.md").write_text("Some memory", encoding="utf-8")
        result = load_agent_context(tmp_path)
        assert f"_Source: {AGENT_CONTEXT_FILE}_" in result

    def test_returns_empty_for_empty_file(self, tmp_path):
        (tmp_path / "agent-context.md").write_text("", encoding="utf-8")
        result = load_agent_context(tmp_path)
        assert result == ""

    def test_returns_empty_for_whitespace_only_file(self, tmp_path):
        (tmp_path / "agent-context.md").write_text("   \n\n  ", encoding="utf-8")
        result = load_agent_context(tmp_path)
        assert result == ""

    def test_truncates_at_max_chars(self, tmp_path):
        long_content = "x" * (MAX_AGENT_CONTEXT_CHARS + 500)
        (tmp_path / "agent-context.md").write_text(long_content, encoding="utf-8")
        result = load_agent_context(tmp_path)
        assert "Truncated" in result
        assert len(result) < len(long_content) + 300  # output much shorter than input

    def test_no_truncation_within_limit(self, tmp_path):
        content = "A" * 100
        (tmp_path / "agent-context.md").write_text(content, encoding="utf-8")
        result = load_agent_context(tmp_path)
        assert "Truncated" not in result
        assert "A" * 100 in result

    def test_content_stripped_in_output(self, tmp_path):
        (tmp_path / "agent-context.md").write_text(
            "\n\n  Memory here  \n\n", encoding="utf-8"
        )
        result = load_agent_context(tmp_path)
        assert "Memory here" in result

    def test_max_chars_constant_is_reasonable(self):
        assert MAX_AGENT_CONTEXT_CHARS > 0
        assert MAX_AGENT_CONTEXT_CHARS <= 20000

    def test_agent_context_file_constant(self):
        assert AGENT_CONTEXT_FILE == "agent-context.md"


# ---------------------------------------------------------------------------
# F-002: session.py parameter signatures
# ---------------------------------------------------------------------------


class TestSessionSignatures:
    def test_run_single_round_has_agent_workspace_param(self):
        from nezha.pipeline.session import run_single_round

        sig = inspect.signature(run_single_round)
        assert "agent_workspace" in sig.parameters
        param = sig.parameters["agent_workspace"]
        assert param.default is None

    def test_run_multi_round_has_agent_workspace_param(self):
        from nezha.pipeline.session import run_multi_round

        sig = inspect.signature(run_multi_round)
        assert "agent_workspace" in sig.parameters
        param = sig.parameters["agent_workspace"]
        assert param.default is None

    def test_run_vibe_session_has_agent_workspace_param(self):
        from nezha.pipeline.session import run_vibe_session

        sig = inspect.signature(run_vibe_session)
        assert "agent_workspace" in sig.parameters
        param = sig.parameters["agent_workspace"]
        assert param.default is None

    def test_run_vibe_session_has_context_mode_param(self):
        from nezha.pipeline.session import run_vibe_session

        sig = inspect.signature(run_vibe_session)
        assert "context_mode" in sig.parameters
        assert sig.parameters["context_mode"].default == "latest"


# ---------------------------------------------------------------------------
# F-002: executor.py passes agent_workspace to session calls
# ---------------------------------------------------------------------------


class TestExecutorPassesAgentWorkspace:
    """Verify executor.py passes agent_workspace=workspace to session functions."""

    def test_execute_agent_passes_agent_workspace_multi_round(self):
        """run_multi_round should receive agent_workspace."""
        from nezha import executor

        src = inspect.getsource(executor.execute_agent)
        # Should pass agent_workspace= to run_multi_round
        assert "agent_workspace=workspace" in src

    def test_execute_agent_passes_agent_workspace_single_round(self):
        """run_single_round should receive agent_workspace."""
        from nezha import executor

        src = inspect.getsource(executor.execute_agent)
        assert "agent_workspace=workspace" in src

    def test_vibe_preserves_agent_workspace_before_task_id_override(self):
        """vibe() should save agent_workspace before workspace is overridden by task_id."""
        from nezha import executor

        src = inspect.getsource(executor.vibe)
        assert "agent_workspace = workspace" in src
        assert "agent_workspace=agent_workspace" in src

    def test_vibe_passes_agent_workspace_to_run_vibe_session(self):
        """run_vibe_session call in vibe() should include agent_workspace."""
        from nezha import executor

        src = inspect.getsource(executor.vibe)
        assert "agent_workspace=agent_workspace" in src


# ---------------------------------------------------------------------------
# F-003: CLI commands
# ---------------------------------------------------------------------------


class TestCmdAgentContextInit:
    def test_creates_agent_context_file(self, tmp_path, capsys):
        workspace = tmp_path / "workspace" / "test-agent"
        workspace.mkdir(parents=True)

        with patch(
            "nezha.interface.cli._resolve_agent_workspace",
            return_value=workspace,
        ):
            from nezha.interface.cli import cmd_agent_context_init

            cmd_agent_context_init("test-agent")

        filepath = workspace / "agent-context.md"
        assert filepath.exists()
        out = capsys.readouterr().out
        assert "Created" in out

    def test_init_file_has_correct_content(self, tmp_path):
        workspace = tmp_path / "workspace" / "test-agent"
        workspace.mkdir(parents=True)

        with patch(
            "nezha.interface.cli._resolve_agent_workspace",
            return_value=workspace,
        ):
            from nezha.interface.cli import cmd_agent_context_init

            cmd_agent_context_init("test-agent")

        content = (workspace / "agent-context.md").read_text(encoding="utf-8")
        assert "Agent Memory" in content

    def test_skips_if_file_already_exists(self, tmp_path, capsys):
        workspace = tmp_path / "workspace" / "test-agent"
        workspace.mkdir(parents=True)
        existing = workspace / "agent-context.md"
        existing.write_text("# Existing content", encoding="utf-8")

        with patch(
            "nezha.interface.cli._resolve_agent_workspace",
            return_value=workspace,
        ):
            from nezha.interface.cli import cmd_agent_context_init

            cmd_agent_context_init("test-agent")

        # File should still have original content
        assert existing.read_text(encoding="utf-8") == "# Existing content"
        out = capsys.readouterr().out
        assert "already exists" in out

    def test_creates_workspace_dir_if_missing(self, tmp_path):
        workspace = tmp_path / "workspace" / "new-agent"
        # workspace does NOT exist yet

        with patch(
            "nezha.interface.cli._resolve_agent_workspace",
            return_value=workspace,
        ):
            from nezha.interface.cli import cmd_agent_context_init

            cmd_agent_context_init("new-agent")

        assert (workspace / "agent-context.md").exists()


class TestCmdAgentContextShow:
    def test_shows_file_content(self, tmp_path, capsys):
        workspace = tmp_path / "workspace" / "test-agent"
        workspace.mkdir(parents=True)
        (workspace / "agent-context.md").write_text(
            "# My memory\n- Task F done", encoding="utf-8"
        )

        with patch(
            "nezha.interface.cli._resolve_agent_workspace",
            return_value=workspace,
        ):
            from nezha.interface.cli import cmd_agent_context_show

            cmd_agent_context_show("test-agent")

        out = capsys.readouterr().out
        assert "My memory" in out
        assert "Task F done" in out

    def test_shows_error_when_file_missing(self, tmp_path, capsys):
        workspace = tmp_path / "workspace" / "test-agent"
        workspace.mkdir(parents=True)

        with patch(
            "nezha.interface.cli._resolve_agent_workspace",
            return_value=workspace,
        ):
            from nezha.interface.cli import cmd_agent_context_show

            cmd_agent_context_show("test-agent")

        out = capsys.readouterr().out
        assert "No agent-context.md" in out or "not found" in out.lower()

    def test_show_includes_file_path(self, tmp_path, capsys):
        workspace = tmp_path / "workspace" / "test-agent"
        workspace.mkdir(parents=True)
        (workspace / "agent-context.md").write_text("memory", encoding="utf-8")

        with patch(
            "nezha.interface.cli._resolve_agent_workspace",
            return_value=workspace,
        ):
            from nezha.interface.cli import cmd_agent_context_show

            cmd_agent_context_show("test-agent")

        out = capsys.readouterr().out
        assert "agent-context.md" in out


# ---------------------------------------------------------------------------
# F-003: __main__.py has agent-context subcommand
# ---------------------------------------------------------------------------


class TestAgentContextCLIParser:
    def _get_parser(self):
        from nezha.__main__ import build_parser
        return build_parser()

    def test_agent_context_command_exists(self):
        parser = self._get_parser()
        # Should not raise
        args = parser.parse_args(["agent-context", "init", "my-agent"])
        assert args.command == "agent-context"
        assert args.ac_command == "init"
        assert args.agent == "my-agent"

    def test_agent_context_show_command(self):
        parser = self._get_parser()
        args = parser.parse_args(["agent-context", "show", "my-agent"])
        assert args.command == "agent-context"
        assert args.ac_command == "show"
        assert args.agent == "my-agent"

    def test_agent_context_init_default_config(self):
        parser = self._get_parser()
        args = parser.parse_args(["agent-context", "init", "my-agent"])
        assert args.config == "executor.yaml"

    def test_agent_context_init_custom_config(self):
        parser = self._get_parser()
        args = parser.parse_args(
            ["agent-context", "init", "my-agent", "--config", "custom.yaml"]
        )
        assert args.config == "custom.yaml"

    def test_agent_context_show_default_config(self):
        parser = self._get_parser()
        args = parser.parse_args(["agent-context", "show", "my-agent"])
        assert args.config == "executor.yaml"
