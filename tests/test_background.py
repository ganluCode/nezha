"""Tests for background execution: --background flag, stop command, status PID display."""

import json
import os
import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest


# ---------------------------------------------------------------------------
# _get_state_dir
# ---------------------------------------------------------------------------

class TestGetStateDir:
    def test_default_state_dir(self, tmp_path):
        """No executor.yaml → defaults to ./state."""
        from nezha.__main__ import _get_state_dir
        result = _get_state_dir(str(tmp_path / "executor.yaml"))
        assert result == tmp_path / "state"

    def test_custom_state_dir(self, tmp_path):
        """executor.yaml with state_dir → uses custom path."""
        import yaml
        config = tmp_path / "executor.yaml"
        config.write_text(yaml.dump({"state_dir": "./custom_state"}))
        from nezha.__main__ import _get_state_dir
        result = _get_state_dir(str(config))
        assert result == tmp_path / "custom_state"


# ---------------------------------------------------------------------------
# _launch_background
# ---------------------------------------------------------------------------

class TestLaunchBackground:
    def _make_args(self, tmp_path, **overrides):
        """Create a minimal args namespace for _launch_background."""
        defaults = {
            "agent": "test-agent",
            "config": str(tmp_path / "executor.yaml"),
            "workspace": None,
            "max_iterations": None,
            "feature_id": None,
            "task_id": None,
            "title": None,
            "input_files": None,
            "mode": None,
            "skip_planner": False,
            "at": None,
            "delay": None,
            "background": True,
        }
        defaults.update(overrides)
        import argparse
        return argparse.Namespace(**defaults)

    @patch("subprocess.Popen")
    def test_launch_creates_pid_file(self, mock_popen, tmp_path):
        """Background launch creates run.pid with correct content."""
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc

        from nezha.__main__ import _launch_background
        args = self._make_args(tmp_path)
        _launch_background(args)

        pid_file = tmp_path / "state" / "run.pid"
        assert pid_file.exists()
        data = json.loads(pid_file.read_text())
        assert data["pid"] == 12345
        assert data["agent"] == "test-agent"
        assert "started_at" in data
        assert "command" in data

    @patch("subprocess.Popen")
    def test_launch_creates_log_file(self, mock_popen, tmp_path):
        """Background launch creates log directory and file."""
        mock_proc = MagicMock()
        mock_proc.pid = 99
        mock_popen.return_value = mock_proc

        from nezha.__main__ import _launch_background
        args = self._make_args(tmp_path)
        _launch_background(args)

        log_dir = tmp_path / "state" / "logs"
        assert log_dir.exists()
        log_files = list(log_dir.glob("bg_test-agent_*.log"))
        assert len(log_files) == 1

    @patch("subprocess.Popen")
    def test_launch_passes_correct_command(self, mock_popen, tmp_path):
        """Popen is called with correct command args (no --background)."""
        mock_proc = MagicMock()
        mock_proc.pid = 1
        mock_popen.return_value = mock_proc

        from nezha.__main__ import _launch_background
        args = self._make_args(tmp_path, feature_id="feat-001", mode="gardening")
        _launch_background(args)

        call_args = mock_popen.call_args
        cmd = call_args[0][0]
        assert "--background" not in cmd
        assert "test-agent" in cmd
        assert "--feature-id" in cmd
        assert "feat-001" in cmd
        assert "--mode" in cmd
        assert "gardening" in cmd

    @patch("subprocess.Popen")
    def test_launch_detaches_session(self, mock_popen, tmp_path):
        """Popen is called with start_new_session=True."""
        mock_proc = MagicMock()
        mock_proc.pid = 1
        mock_popen.return_value = mock_proc

        from nezha.__main__ import _launch_background
        args = self._make_args(tmp_path)
        _launch_background(args)

        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs["start_new_session"] is True

    @patch("subprocess.Popen")
    def test_launch_with_title_and_input(self, mock_popen, tmp_path):
        """--title and --input args are forwarded correctly."""
        mock_proc = MagicMock()
        mock_proc.pid = 1
        mock_popen.return_value = mock_proc

        from nezha.__main__ import _launch_background
        args = self._make_args(tmp_path, title="My Feature", input_files=["a.md", "b.md"])
        _launch_background(args)

        cmd = mock_popen.call_args[0][0]
        assert "--title" in cmd
        assert "My Feature" in cmd
        assert cmd.count("--input") == 2


# ---------------------------------------------------------------------------
# _stop_background
# ---------------------------------------------------------------------------

class TestStopBackground:
    def test_stop_no_pid_file(self, tmp_path, capsys):
        """No run.pid → prints message and returns."""
        import yaml
        config = tmp_path / "executor.yaml"
        config.write_text(yaml.dump({"state_dir": str(tmp_path / "state")}))

        from nezha.__main__ import _stop_background
        _stop_background(str(config))

        output = capsys.readouterr().out
        assert "No background process found" in output

    def test_stop_process_not_running(self, tmp_path, capsys):
        """PID file exists but process is dead → cleanup and message."""
        import yaml
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        pid_file = state_dir / "run.pid"
        pid_file.write_text(json.dumps({"pid": 999999, "agent": "test"}))

        config = tmp_path / "executor.yaml"
        config.write_text(yaml.dump({"state_dir": str(state_dir)}))

        from nezha.__main__ import _stop_background
        _stop_background(str(config))

        output = capsys.readouterr().out
        assert "no longer running" in output
        assert not pid_file.exists()

    @patch("os.kill")
    def test_stop_sends_sigterm(self, mock_kill, tmp_path, capsys):
        """Process running → sends SIGTERM and removes PID file."""
        import yaml
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        pid_file = state_dir / "run.pid"
        pid_file.write_text(json.dumps({"pid": 42, "agent": "evolve-agent", "log": "/tmp/test.log"}))

        config = tmp_path / "executor.yaml"
        config.write_text(yaml.dump({"state_dir": str(state_dir)}))

        # First call (signal 0 check) succeeds, second call (SIGTERM) succeeds
        mock_kill.side_effect = [None, None]

        from nezha.__main__ import _stop_background
        _stop_background(str(config))

        output = capsys.readouterr().out
        assert "SIGTERM" in output
        assert "42" in output
        mock_kill.assert_any_call(42, signal.SIGTERM)
        assert not pid_file.exists()

    def test_stop_invalid_pid_file(self, tmp_path, capsys):
        """Invalid PID file → cleanup."""
        import yaml
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        pid_file = state_dir / "run.pid"
        pid_file.write_text(json.dumps({"agent": "test"}))  # no pid field

        config = tmp_path / "executor.yaml"
        config.write_text(yaml.dump({"state_dir": str(state_dir)}))

        from nezha.__main__ import _stop_background
        _stop_background(str(config))

        output = capsys.readouterr().out
        assert "Invalid PID" in output
        assert not pid_file.exists()


# ---------------------------------------------------------------------------
# status command shows background info
# ---------------------------------------------------------------------------

class TestStatusBackground:
    @patch("os.kill")
    def test_status_shows_background_running(self, mock_kill, tmp_path, capsys):
        """status command shows background process info when running."""
        import yaml
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        pid_file = state_dir / "run.pid"
        pid_file.write_text(json.dumps({
            "pid": 1234,
            "agent": "evolve-agent",
            "started_at": "2026-03-11T10:00:00",
            "log": "/tmp/bg.log",
        }))

        config = tmp_path / "executor.yaml"
        config.write_text(yaml.dump({"state_dir": str(state_dir)}))

        mock_kill.return_value = None  # process exists

        from nezha.interface.cli import cmd_status
        cmd_status(str(config))

        output = capsys.readouterr().out
        assert "background" in output.lower()
        assert "1234" in output
        assert "evolve-agent" in output

    def test_status_cleans_stale_pid(self, tmp_path, capsys):
        """status cleans up PID file when process is no longer running."""
        import yaml
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        pid_file = state_dir / "run.pid"
        pid_file.write_text(json.dumps({
            "pid": 999999,
            "agent": "test-agent",
            "started_at": "2026-03-11T10:00:00",
            "log": "/tmp/bg.log",
        }))

        config = tmp_path / "executor.yaml"
        config.write_text(yaml.dump({"state_dir": str(state_dir)}))

        from nezha.interface.cli import cmd_status
        cmd_status(str(config))

        output = capsys.readouterr().out
        assert "no longer running" in output
        assert not pid_file.exists()


# ---------------------------------------------------------------------------
# CLI integration: --background flag is recognized
# ---------------------------------------------------------------------------

class TestCLIBackgroundFlag:
    def test_parser_has_background_flag(self):
        """run subcommand accepts --background."""
        from nezha.__main__ import build_parser
        parser = build_parser()
        args = parser.parse_args(["run", "test-agent", "--background"])
        assert args.background is True

    def test_parser_default_no_background(self):
        """Default: --background is False."""
        from nezha.__main__ import build_parser
        parser = build_parser()
        args = parser.parse_args(["run", "test-agent"])
        assert args.background is False

    def test_stop_command_parsed(self):
        """stop subcommand is recognized."""
        from nezha.__main__ import build_parser
        parser = build_parser()
        args = parser.parse_args(["stop"])
        assert args.command == "stop"
