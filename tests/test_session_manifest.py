"""Tests for session manifest — prompt injection audit log."""

import json
from pathlib import Path

import pytest

from nezha.pipeline.session import _write_session_manifest


# ---------------------------------------------------------------------------
# Basic manifest writing
# ---------------------------------------------------------------------------

class TestSessionManifestBasic:
    """Test _write_session_manifest produces correct JSON."""

    def test_writes_manifest_file(self, tmp_path):
        """Manifest is written to workspace/.session_manifest.json."""
        _write_session_manifest(
            workspace=tmp_path,
            agent_name="python-agent",
            model="claude-sonnet-4-6",
            cwd=tmp_path / "repo",
            input_files=[],
            knowledge="",
            agent_ctx="",
            project_context=None,
            project_dir=None,
            prompt_total_chars=1000,
        )
        manifest_path = tmp_path / ".session_manifest.json"
        assert manifest_path.exists()
        data = json.loads(manifest_path.read_text())
        assert data["agent"] == "python-agent"
        assert data["model"] == "claude-sonnet-4-6"
        assert data["prompt_context"]["prompt_total_chars"] == 1000

    def test_manifest_has_timestamp(self, tmp_path):
        """Manifest includes ISO timestamp."""
        _write_session_manifest(
            workspace=tmp_path, agent_name="a", model="m",
            cwd=tmp_path, input_files=[], knowledge="", agent_ctx="",
            project_context=None, project_dir=None, prompt_total_chars=0,
        )
        data = json.loads((tmp_path / ".session_manifest.json").read_text())
        assert "timestamp" in data
        # Should be ISO format
        assert "T" in data["timestamp"]


# ---------------------------------------------------------------------------
# Input files
# ---------------------------------------------------------------------------

class TestManifestInputFiles:
    """Test input file recording."""

    def test_records_input_files(self, tmp_path):
        """Input files are listed with name and size."""
        f1 = tmp_path / "spec.md"
        f1.write_text("# Spec\nHello world")
        f2 = tmp_path / "data.json"
        f2.write_text('{"key": "value"}')

        _write_session_manifest(
            workspace=tmp_path, agent_name="a", model="m",
            cwd=tmp_path, input_files=[f1, f2], knowledge="", agent_ctx="",
            project_context=None, project_dir=None, prompt_total_chars=100,
        )
        data = json.loads((tmp_path / ".session_manifest.json").read_text())
        files = data["prompt_context"]["input_files"]
        assert len(files) == 2
        assert files[0]["name"] == "spec.md"
        assert files[0]["size"] == f1.stat().st_size
        assert files[1]["name"] == "data.json"

    def test_empty_input_files(self, tmp_path):
        """Empty input files list produces empty array."""
        _write_session_manifest(
            workspace=tmp_path, agent_name="a", model="m",
            cwd=tmp_path, input_files=[], knowledge="", agent_ctx="",
            project_context=None, project_dir=None, prompt_total_chars=0,
        )
        data = json.loads((tmp_path / ".session_manifest.json").read_text())
        assert data["prompt_context"]["input_files"] == []


# ---------------------------------------------------------------------------
# Knowledge and context
# ---------------------------------------------------------------------------

class TestManifestContext:
    """Test knowledge, agent_ctx, and project_context recording."""

    def test_knowledge_recorded(self, tmp_path):
        """Knowledge chars are recorded when present."""
        _write_session_manifest(
            workspace=tmp_path, agent_name="a", model="m",
            cwd=tmp_path, input_files=[], knowledge="x" * 3000, agent_ctx="",
            project_context=None, project_dir=None, prompt_total_chars=3000,
        )
        data = json.loads((tmp_path / ".session_manifest.json").read_text())
        k = data["prompt_context"]["knowledge"]
        assert k["source"] == "CLAUDE.md"
        assert k["chars"] == 3000

    def test_no_knowledge_is_none(self, tmp_path):
        """Empty knowledge → null in JSON."""
        _write_session_manifest(
            workspace=tmp_path, agent_name="a", model="m",
            cwd=tmp_path, input_files=[], knowledge="", agent_ctx="",
            project_context=None, project_dir=None, prompt_total_chars=0,
        )
        data = json.loads((tmp_path / ".session_manifest.json").read_text())
        assert data["prompt_context"]["knowledge"] is None

    def test_agent_context_recorded(self, tmp_path):
        """Agent context chars are recorded when present."""
        _write_session_manifest(
            workspace=tmp_path, agent_name="a", model="m",
            cwd=tmp_path, input_files=[], knowledge="", agent_ctx="ctx" * 100,
            project_context=None, project_dir=None, prompt_total_chars=300,
        )
        data = json.loads((tmp_path / ".session_manifest.json").read_text())
        ac = data["prompt_context"]["agent_context"]
        assert ac["source"] == "agent-context.md"
        assert ac["chars"] == 300

    def test_project_context_with_files(self, tmp_path):
        """Project context includes source dir and file list."""
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / "standards").mkdir()
        (proj / "standards" / "ARCH.md").write_text("# Architecture")
        (proj / "project.yaml").write_text("name: test")

        _write_session_manifest(
            workspace=tmp_path, agent_name="a", model="m",
            cwd=tmp_path, input_files=[], knowledge="", agent_ctx="",
            project_context="context" * 100, project_dir=proj,
            prompt_total_chars=600,
        )
        data = json.loads((tmp_path / ".session_manifest.json").read_text())
        pc = data["prompt_context"]["project_context"]
        assert str(proj) in pc["source"]
        assert pc["chars"] == 700
        assert "standards/ARCH.md" in pc["files"]
        assert "project.yaml" in pc["files"]

    def test_no_project_context_is_none(self, tmp_path):
        """No project_dir → project_context is null."""
        _write_session_manifest(
            workspace=tmp_path, agent_name="a", model="m",
            cwd=tmp_path, input_files=[], knowledge="", agent_ctx="",
            project_context=None, project_dir=None, prompt_total_chars=0,
        )
        data = json.loads((tmp_path / ".session_manifest.json").read_text())
        assert data["prompt_context"]["project_context"] is None


# ---------------------------------------------------------------------------
# Structural checks — wired into session.py
# ---------------------------------------------------------------------------

class TestManifestIntegration:
    """Verify manifest writing is wired into session.py."""

    def test_single_round_calls_manifest(self):
        """run_single_round calls _write_session_manifest."""
        from nezha.pipeline import session
        source = Path(session.__file__).read_text()
        assert "_write_session_manifest(" in source

    def test_subprocess_runner_writes_manifest(self):
        """_SUBPROCESS_RUNNER template writes .session_manifest.json."""
        from nezha.pipeline.session import _SUBPROCESS_RUNNER
        assert ".session_manifest.json" in _SUBPROCESS_RUNNER
