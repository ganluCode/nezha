"""Tests for nezha.testing.integration module."""

import json
from pathlib import Path

import pytest

from nezha.testing.integration import (
    RunResult,
    CycleResult,
    run_test_command,
    write_test_report,
    _truncate,
)


class TestRunTestCommand:
    """Tests for run_test_command()."""

    def test_passing_command(self, tmp_path):
        result = run_test_command("echo ok", cwd=tmp_path, timeout=10)
        assert result.passed is True
        assert result.exit_code == 0
        assert "ok" in result.output
        assert result.duration_ms >= 0

    def test_failing_command(self, tmp_path):
        result = run_test_command("exit 1", cwd=tmp_path, timeout=10)
        assert result.passed is False
        assert result.exit_code == 1

    def test_timeout(self, tmp_path):
        result = run_test_command("sleep 10", cwd=tmp_path, timeout=1)
        assert result.passed is False
        assert result.exit_code == -1
        assert "timed out" in result.output

    def test_invalid_command(self, tmp_path):
        result = run_test_command("nonexistent_command_xyz", cwd=tmp_path, timeout=5)
        assert result.passed is False

    def test_output_with_stderr(self, tmp_path):
        result = run_test_command("echo out && echo err >&2", cwd=tmp_path, timeout=10)
        assert "out" in result.output
        assert "err" in result.output


class TestWriteTestReport:
    """Tests for write_test_report()."""

    def test_writes_report(self, tmp_path):
        test_result = RunResult(
            passed=False, exit_code=1,
            output="AssertionError: expected 200 got 500",
            duration_ms=1234,
        )
        path = write_test_report(
            workspace=tmp_path,
            cycle=1,
            max_cycles=3,
            test_command="pytest tests/",
            test_result=test_result,
            previous_fixes=[],
        )
        assert path == tmp_path / ".test_report.json"
        assert path.exists()

        report = json.loads(path.read_text())
        assert report["cycle"] == 1
        assert report["max_cycles"] == 3
        assert report["passed"] is False
        assert report["exit_code"] == 1
        assert "AssertionError" in report["output"]
        assert report["test_command"] == "pytest tests/"
        assert report["previous_fixes"] == []
        assert "timestamp" in report

    def test_with_previous_fixes(self, tmp_path):
        test_result = RunResult(passed=False, exit_code=1, output="fail", duration_ms=100)
        previous = [
            {"cycle": 1, "error_summary": "first error", "fix_applied": "session completed"},
        ]
        path = write_test_report(
            workspace=tmp_path, cycle=2, max_cycles=3,
            test_command="mvn test", test_result=test_result,
            previous_fixes=previous,
        )
        report = json.loads(path.read_text())
        assert len(report["previous_fixes"]) == 1
        assert report["previous_fixes"][0]["cycle"] == 1


class TestTruncate:
    """Tests for _truncate() helper."""

    def test_short_text_unchanged(self):
        assert _truncate("hello") == "hello"

    def test_long_text_truncated(self):
        text = "x" * 5000
        result = _truncate(text)
        assert len(result) < 5000
        assert "truncated" in result

    def test_preserves_head_and_tail(self):
        text = "HEAD" + "x" * 5000 + "TAIL"
        result = _truncate(text)
        assert result.startswith("HEAD")
        assert result.endswith("TAIL")


class TestCycleResult:
    """Tests for CycleResult defaults."""

    def test_defaults(self):
        r = CycleResult()
        assert r.passed is False
        assert r.cycles_run == 0
        assert r.total_cost_usd == 0.0
        assert r.exit_reason == ""


class TestConfigParsing:
    """Test PostTaskTestConfig parsing via load_agent_config."""

    def test_parse_post_task_test(self, tmp_path):
        from nezha.config import load_agent_config

        yaml_content = """
agent:
  name: "test-agent"
  category: "coding"

engine:
  model: "claude-sonnet-4-6"

session:
  mode: "multi_round"
  prompts:
    worker: "java/worker.md"

pipeline:
  post_task_test:
    enabled: true
    command: "./mvnw verify"
    max_cycles: 5
    timeout: 300
"""
        config_file = tmp_path / "test-agent.yaml"
        config_file.write_text(yaml_content)

        config = load_agent_config(config_file)
        ptt = config.pipeline.post_task_test
        assert ptt.enabled is True
        assert ptt.command == "./mvnw verify"
        assert ptt.max_cycles == 5
        assert ptt.timeout == 300

    def test_default_when_missing(self, tmp_path):
        from nezha.config import load_agent_config

        yaml_content = """
agent:
  name: "test-agent"
  category: "coding"

engine:
  model: "claude-sonnet-4-6"

session:
  mode: "single_round"
  prompts:
    worker: "coding/worker.md"
"""
        config_file = tmp_path / "test-agent.yaml"
        config_file.write_text(yaml_content)

        config = load_agent_config(config_file)
        ptt = config.pipeline.post_task_test
        assert ptt.enabled is False
        assert ptt.command == ""
        assert ptt.max_cycles == 3
