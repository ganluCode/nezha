"""Tests for the verification layer (F-001)."""

import json
import sys
from pathlib import Path

import pytest

from nezha.dag.verifier import (
    VerificationResult,
    verify_feature,
    verify_task,
    apply_verification_result,
    _check_agent_report,
    _run_verification_command,
    _determine_result,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def feature_list_path(tmp_path):
    """Create a temporary feature_list.json."""
    features = [
        {
            "id": "F-001",
            "description": "Test feature 1",
            "acceptance": ["criterion 1"],
            "depends_on": [],
            "passes": False,
        },
        {
            "id": "F-002",
            "description": "Test feature 2",
            "acceptance": ["criterion 2"],
            "depends_on": [],
            "passes": True,
        },
    ]
    path = tmp_path / "feature_list.json"
    path.write_text(json.dumps(features, indent=2))
    return path


@pytest.fixture
def workspace(tmp_path):
    return tmp_path


# ---------------------------------------------------------------------------
# _check_agent_report tests
# ---------------------------------------------------------------------------

class TestCheckAgentReport:
    def test_agent_reported_false(self, feature_list_path):
        assert _check_agent_report("F-001", feature_list_path) is False

    def test_agent_reported_true(self, feature_list_path):
        assert _check_agent_report("F-002", feature_list_path) is True

    def test_unknown_feature(self, feature_list_path):
        assert _check_agent_report("F-999", feature_list_path) is False

    def test_missing_file(self, tmp_path):
        assert _check_agent_report("F-001", tmp_path / "nonexistent.json") is False

    def test_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json")
        assert _check_agent_report("F-001", path) is False


# ---------------------------------------------------------------------------
# _run_verification_command tests
# ---------------------------------------------------------------------------

class TestRunVerificationCommand:
    def test_successful_command(self, workspace):
        passed, output = _run_verification_command(
            f"{sys.executable} -c \"print('ok')\"",
            workspace,
            timeout=30,
        )
        assert passed is True
        assert "ok" in output

    def test_failing_command(self, workspace):
        passed, output = _run_verification_command(
            f"{sys.executable} -c \"import sys; print('fail'); sys.exit(1)\"",
            workspace,
            timeout=30,
        )
        assert passed is False
        assert "fail" in output

    def test_timeout(self, workspace):
        passed, output = _run_verification_command(
            f"{sys.executable} -c \"import time; time.sleep(10)\"",
            workspace,
            timeout=1,
        )
        assert passed is False
        assert "timed out" in output

    def test_output_truncation(self, workspace):
        # Generate >2000 chars output
        cmd = f"{sys.executable} -c \"print('x' * 3000)\""
        passed, output = _run_verification_command(cmd, workspace, timeout=30)
        assert passed is True
        assert "truncated" in output


# ---------------------------------------------------------------------------
# _determine_result tests
# ---------------------------------------------------------------------------

class TestDetermineResult:
    def test_agent_not_reported(self):
        passed, reason = _determine_result("F-001", False, None)
        assert passed is False
        assert "did not report" in reason

    def test_agent_pass_no_command(self):
        passed, reason = _determine_result("F-001", True, None)
        assert passed is True
        assert "no verification command" in reason

    def test_agent_pass_command_pass(self):
        passed, reason = _determine_result("F-001", True, True)
        assert passed is True
        assert "succeeded" in reason

    def test_agent_pass_command_fail(self):
        passed, reason = _determine_result("F-001", True, False)
        assert passed is False
        assert "command failed" in reason

    def test_agent_fail_command_pass(self):
        passed, reason = _determine_result("F-001", False, True)
        assert passed is False
        assert "did not report" in reason


# ---------------------------------------------------------------------------
# verify_feature integration tests
# ---------------------------------------------------------------------------

class TestVerifyFeature:
    def test_no_command_agent_pass(self, feature_list_path):
        result = verify_feature("F-002", feature_list_path)
        assert result.passed is True
        assert result.agent_reported_pass is True
        assert result.command_passed is None

    def test_no_command_agent_fail(self, feature_list_path):
        result = verify_feature("F-001", feature_list_path)
        assert result.passed is False
        assert result.agent_reported_pass is False
        assert result.command_passed is None

    def test_with_passing_command(self, feature_list_path, workspace):
        result = verify_feature(
            "F-002",
            feature_list_path,
            verification_command=f"{sys.executable} -c \"print('ok')\"",
            workspace=workspace,
        )
        assert result.passed is True
        assert result.command_passed is True

    def test_with_failing_command(self, feature_list_path, workspace):
        result = verify_feature(
            "F-002",
            feature_list_path,
            verification_command=f"{sys.executable} -c \"import sys; sys.exit(1)\"",
            workspace=workspace,
        )
        assert result.passed is False
        assert result.agent_reported_pass is True
        assert result.command_passed is False

    def test_agent_fail_with_command(self, feature_list_path, workspace):
        # Agent didn't report pass, command doesn't even matter
        result = verify_feature(
            "F-001",
            feature_list_path,
            verification_command=f"{sys.executable} -c \"print('ok')\"",
            workspace=workspace,
        )
        assert result.passed is False
        assert result.agent_reported_pass is False


# ---------------------------------------------------------------------------
# apply_verification_result tests
# ---------------------------------------------------------------------------

class TestApplyVerificationResult:
    def test_passed_no_changes(self, feature_list_path):
        result = VerificationResult(
            task_id="F-002",
            passed=True,
            agent_reported_pass=True,
        )
        apply_verification_result(result, feature_list_path)

        with open(feature_list_path) as f:
            features = json.load(f)

        f002 = next(f for f in features if f["id"] == "F-002")
        assert f002["passes"] is True
        assert f002.get("rework") is not True

    def test_failed_marks_rework(self, feature_list_path):
        result = VerificationResult(
            task_id="F-002",
            passed=False,
            agent_reported_pass=True,
            command_passed=False,
            reason="Verification command failed",
        )
        apply_verification_result(result, feature_list_path)

        with open(feature_list_path) as f:
            features = json.load(f)

        f002 = next(f for f in features if f["id"] == "F-002")
        assert f002["passes"] is False
        assert f002["rework"] is True
        # rework_note is now a structured dict
        rn = f002["rework_note"]
        assert isinstance(rn, dict)
        assert "Verification command failed" in rn["block_reason"]
        assert rn["attempt"] == 1
        assert f002["rework_count"] == 1

    def test_rework_count_increments(self, feature_list_path):
        # Set initial rework_count
        with open(feature_list_path) as f:
            features = json.load(f)
        for feat in features:
            if feat["id"] == "F-002":
                feat["rework_count"] = 2
        with open(feature_list_path, "w") as f:
            json.dump(features, f, indent=2)

        result = VerificationResult(
            task_id="F-002",
            passed=False,
            agent_reported_pass=True,
            command_passed=False,
            reason="Verification command failed",
        )
        apply_verification_result(result, feature_list_path)

        with open(feature_list_path) as f:
            features = json.load(f)

        f002 = next(f for f in features if f["id"] == "F-002")
        assert f002["rework_count"] == 3


# ---------------------------------------------------------------------------
# Config tests: VerificationConfig loading
# ---------------------------------------------------------------------------

class TestVerificationConfig:
    def test_default_verification_config(self):
        from nezha.config import VerificationConfig
        config = VerificationConfig()
        assert config.command is None

    def test_load_agent_config_with_verification(self, tmp_path):
        from nezha.config import load_agent_config

        yaml_content = """
agent:
  name: "test-agent"
  description: "Test"

engine:
  model: "claude-sonnet-4-5-20250929"

session:
  mode: "multi_round"
  prompts:
    worker: "worker.md"

verification:
  command: "python -m pytest"
"""
        config_path = tmp_path / "test-agent.yaml"
        config_path.write_text(yaml_content)

        config = load_agent_config(config_path)
        assert config.verification.command == "python -m pytest"

    def test_load_agent_config_without_verification(self, tmp_path):
        from nezha.config import load_agent_config

        yaml_content = """
agent:
  name: "test-agent"
  description: "Test"

engine:
  model: "claude-sonnet-4-5-20250929"

session:
  mode: "single_round"
  prompts:
    worker: "worker.md"
"""
        config_path = tmp_path / "test-agent.yaml"
        config_path.write_text(yaml_content)

        config = load_agent_config(config_path)
        assert config.verification.command is None

    def test_load_agent_config_verification_null(self, tmp_path):
        from nezha.config import load_agent_config

        yaml_content = """
agent:
  name: "test-agent"
  description: "Test"

engine:
  model: "claude-sonnet-4-5-20250929"

session:
  mode: "single_round"
  prompts:
    worker: "worker.md"

verification:
  command: null
"""
        config_path = tmp_path / "test-agent.yaml"
        config_path.write_text(yaml_content)

        config = load_agent_config(config_path)
        assert config.verification.command is None


# ---------------------------------------------------------------------------
# DAG Engine integration tests (verification in the loop)
# ---------------------------------------------------------------------------

class TestDAGEngineVerification:
    """Test that verification is wired into the DAG engine correctly."""

    @pytest.mark.asyncio
    async def test_engine_with_no_verification_command(self, tmp_path):
        """Without verification command, passing features stay passed."""
        from nezha.dag.engine import DAGEngine

        features = [
            {
                "id": "F-001",
                "description": "Test",
                "acceptance": [],
                "depends_on": [],
                "passes": False,
            }
        ]
        fl_path = tmp_path / "feature_list.json"
        fl_path.write_text(json.dumps(features))

        def mock_session(prompt_path, model_override="", env_override=None):
            # Simulate agent updating feature_list.json
            data = json.loads(fl_path.read_text())
            data[0]["passes"] = True
            fl_path.write_text(json.dumps(data))

            from nezha.engine import SessionResult
            return SessionResult(status="completed", num_turns=1, cost_usd=0.01, duration_ms=100)

        events = []
        async def on_event(event_type, data):
            events.append((event_type, data))

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            on_dag_event=on_event,
            verification_command=None,
        )

        result = await engine.run("worker.md", max_iterations=1)
        assert result.completed == 1

        # Check that verification event was emitted
        verify_events = [e for e in events if e[0] == "dag.feature_verified"]
        assert len(verify_events) == 1
        assert verify_events[0][1]["passed"] is True

    @pytest.mark.asyncio
    async def test_engine_verification_command_fails(self, tmp_path):
        """When verification command fails, feature is marked for rework."""
        from nezha.dag.engine import DAGEngine

        features = [
            {
                "id": "F-001",
                "description": "Test",
                "acceptance": [],
                "depends_on": [],
                "passes": False,
            }
        ]
        fl_path = tmp_path / "feature_list.json"
        fl_path.write_text(json.dumps(features))

        call_count = 0
        def mock_session(prompt_path, model_override="", env_override=None):
            nonlocal call_count
            call_count += 1
            # Agent reports success
            data = json.loads(fl_path.read_text())
            for f in data:
                if f["id"] == "F-001":
                    f["passes"] = True
                    f.pop("rework", None)
                    f.pop("rework_note", None)
            fl_path.write_text(json.dumps(data))

            from nezha.engine import SessionResult
            return SessionResult(status="completed", num_turns=1, cost_usd=0.01, duration_ms=100)

        events = []
        async def on_event(event_type, data):
            events.append((event_type, data))

        # Use a command that always fails
        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            on_dag_event=on_event,
            verification_command=f"{sys.executable} -c \"import sys; sys.exit(1)\"",
        )

        result = await engine.run("worker.md", max_iterations=1)

        # Feature should NOT be completed because verification failed
        verify_events = [e for e in events if e[0] == "dag.feature_verified"]
        assert len(verify_events) == 1
        assert verify_events[0][1]["passed"] is False

        # feature_list.json should have rework marked
        data = json.loads(fl_path.read_text())
        f001 = next(f for f in data if f["id"] == "F-001")
        assert f001["passes"] is False
        assert f001["rework"] is True
        # rework_note is now a structured dict
        rn = f001["rework_note"]
        assert isinstance(rn, dict)
        assert "Verification command failed" in rn["block_reason"]

    @pytest.mark.asyncio
    async def test_engine_agent_didnt_update(self, tmp_path):
        """When agent doesn't update passes, verification marks rework."""
        from nezha.dag.engine import DAGEngine

        features = [
            {
                "id": "F-001",
                "description": "Test",
                "acceptance": [],
                "depends_on": [],
                "passes": False,
            }
        ]
        fl_path = tmp_path / "feature_list.json"
        fl_path.write_text(json.dumps(features))

        def mock_session(prompt_path, model_override="", env_override=None):
            # Agent does NOT update feature_list.json
            from nezha.engine import SessionResult
            return SessionResult(status="completed", num_turns=1, cost_usd=0.01, duration_ms=100)

        events = []
        async def on_event(event_type, data):
            events.append((event_type, data))

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            on_dag_event=on_event,
            verification_command=None,
        )

        result = await engine.run("worker.md", max_iterations=1)

        verify_events = [e for e in events if e[0] == "dag.feature_verified"]
        assert len(verify_events) == 1
        assert verify_events[0][1]["passed"] is False
        assert verify_events[0][1]["agent_reported_pass"] is False


# ---------------------------------------------------------------------------
# Backward compatibility test
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_no_verification_config_defaults_to_none(self):
        from nezha.config import AgentConfig
        config = AgentConfig()
        assert config.verification.command is None

    @pytest.mark.asyncio
    async def test_dag_engine_without_verification_param(self, tmp_path):
        """DAGEngine works without verification_command param (backward compat)."""
        from nezha.dag.engine import DAGEngine

        features = [
            {
                "id": "F-001",
                "description": "Test",
                "acceptance": [],
                "depends_on": [],
                "passes": False,
            }
        ]
        fl_path = tmp_path / "feature_list.json"
        fl_path.write_text(json.dumps(features))

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            data[0]["passes"] = True
            fl_path.write_text(json.dumps(data))
            from nezha.engine import SessionResult
            return SessionResult(status="completed", num_turns=1, cost_usd=0.01, duration_ms=100)

        # No verification_command param — should work fine
        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
        )

        result = await engine.run("worker.md", max_iterations=1)
        assert result.completed == 1
