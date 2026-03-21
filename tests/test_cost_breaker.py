"""Tests for the cost circuit breaker (F-004)."""

import json
from pathlib import Path

import pytest

from nezha.config import SessionConfig, load_agent_config
from nezha.dag.engine import DAGEngine, DAGExecutionResult
from nezha.engine import SessionResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def single_feature():
    return [
        {
            "id": "F-001",
            "description": "Feature one",
            "acceptance": ["criterion 1"],
            "depends_on": [],
            "passes": False,
        },
    ]


@pytest.fixture
def two_features():
    return [
        {
            "id": "F-001",
            "description": "Feature one",
            "acceptance": ["criterion 1"],
            "depends_on": [],
            "passes": False,
        },
        {
            "id": "F-002",
            "description": "Feature two",
            "acceptance": ["criterion 2"],
            "depends_on": [],
            "passes": False,
        },
    ]


@pytest.fixture
def three_features():
    return [
        {
            "id": "F-001",
            "description": "Feature one",
            "acceptance": [],
            "depends_on": [],
            "passes": False,
        },
        {
            "id": "F-002",
            "description": "Feature two",
            "acceptance": [],
            "depends_on": [],
            "passes": False,
        },
        {
            "id": "F-003",
            "description": "Feature three",
            "acceptance": [],
            "depends_on": [],
            "passes": False,
        },
    ]


def write_features(tmp_path, features):
    fl_path = tmp_path / "task_list.json"
    fl_path.write_text(json.dumps(features, indent=2))
    return fl_path


def make_completing_session(fl_path):
    """Session mock that marks the first non-passing feature as done."""
    def mock_session(prompt_path, model_override="", env_override=None):
        data = json.loads(fl_path.read_text())
        for f in data:
            if not f.get("passes"):
                f["passes"] = True
                break
        fl_path.write_text(json.dumps(data))
        return SessionResult(
            status="completed",
            num_turns=1,
            cost_usd=0.05,
            duration_ms=1000,
        )
    return mock_session


# ---------------------------------------------------------------------------
# SessionConfig tests — field defaults
# ---------------------------------------------------------------------------

class TestSessionConfigFields:
    def test_max_cost_usd_default_none(self):
        config = SessionConfig()
        assert config.max_cost_usd is None

    def test_max_sessions_default_none(self):
        config = SessionConfig()
        assert config.max_sessions is None

    def test_max_cost_usd_set(self):
        config = SessionConfig(max_cost_usd=1.50)
        assert config.max_cost_usd == 1.50

    def test_max_sessions_set(self):
        config = SessionConfig(max_sessions=10)
        assert config.max_sessions == 10


# ---------------------------------------------------------------------------
# Config loading from YAML
# ---------------------------------------------------------------------------

class TestConfigLoading:
    def test_load_with_max_cost_usd(self, tmp_path):
        yaml_content = """
agent:
  name: "test-agent"
session:
  mode: "multi_round"
  max_cost_usd: 2.50
"""
        config_file = tmp_path / "agent.yaml"
        config_file.write_text(yaml_content)
        config = load_agent_config(config_file)
        assert config.session.max_cost_usd == 2.50

    def test_load_with_max_sessions(self, tmp_path):
        yaml_content = """
agent:
  name: "test-agent"
session:
  mode: "multi_round"
  max_sessions: 5
"""
        config_file = tmp_path / "agent.yaml"
        config_file.write_text(yaml_content)
        config = load_agent_config(config_file)
        assert config.session.max_sessions == 5

    def test_load_with_both_limits(self, tmp_path):
        yaml_content = """
agent:
  name: "test-agent"
session:
  mode: "multi_round"
  max_cost_usd: 10.0
  max_sessions: 20
"""
        config_file = tmp_path / "agent.yaml"
        config_file.write_text(yaml_content)
        config = load_agent_config(config_file)
        assert config.session.max_cost_usd == 10.0
        assert config.session.max_sessions == 20

    def test_load_with_null_limits(self, tmp_path):
        yaml_content = """
agent:
  name: "test-agent"
session:
  mode: "multi_round"
  max_cost_usd: null
  max_sessions: null
"""
        config_file = tmp_path / "agent.yaml"
        config_file.write_text(yaml_content)
        config = load_agent_config(config_file)
        assert config.session.max_cost_usd is None
        assert config.session.max_sessions is None

    def test_load_without_limits(self, tmp_path):
        """Backward compatibility: no limits in YAML means None."""
        yaml_content = """
agent:
  name: "test-agent"
session:
  mode: "multi_round"
"""
        config_file = tmp_path / "agent.yaml"
        config_file.write_text(yaml_content)
        config = load_agent_config(config_file)
        assert config.session.max_cost_usd is None
        assert config.session.max_sessions is None


# ---------------------------------------------------------------------------
# DAGEngine — cost accumulation
# ---------------------------------------------------------------------------

class TestCostAccumulation:
    @pytest.mark.asyncio
    async def test_cost_accumulated_across_sessions(self, tmp_path, two_features):
        fl_path = write_features(tmp_path, two_features)

        call_count = 0
        def mock_session(prompt_path, model_override="", env_override=None):
            nonlocal call_count
            call_count += 1
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.10, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
        )

        result = await engine.run("worker.md", max_iterations=5)
        assert result.total_cost_usd == pytest.approx(0.20)
        assert result.sessions_run == 2

    @pytest.mark.asyncio
    async def test_cost_accumulated_with_none_cost(self, tmp_path, single_feature):
        """Sessions with cost_usd=None are treated as 0 cost."""
        fl_path = write_features(tmp_path, single_feature)

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            data[0]["passes"] = True
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=None, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
        )

        result = await engine.run("worker.md")
        assert result.total_cost_usd == 0.0


# ---------------------------------------------------------------------------
# DAGEngine — cost limit circuit breaker
# ---------------------------------------------------------------------------

class TestCostLimitBreaker:
    @pytest.mark.asyncio
    async def test_stops_when_cost_exceeded(self, tmp_path, three_features):
        """Engine stops after cost exceeds max_cost_usd."""
        fl_path = write_features(tmp_path, three_features)

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.10, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            max_cost_usd=0.15,  # Will be exceeded after 2 sessions ($0.20)
        )

        result = await engine.run("worker.md")
        assert result.exit_reason == "cost_limit"
        assert result.sessions_run == 2
        assert result.total_cost_usd == pytest.approx(0.20)

    @pytest.mark.asyncio
    async def test_cost_limit_exact_boundary(self, tmp_path, two_features):
        """Engine stops when cost exactly equals max_cost_usd."""
        fl_path = write_features(tmp_path, two_features)

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.10, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            max_cost_usd=0.10,  # Hit exactly after first session
        )

        result = await engine.run("worker.md")
        assert result.exit_reason == "cost_limit"
        assert result.sessions_run == 1

    @pytest.mark.asyncio
    async def test_no_cost_limit_runs_all(self, tmp_path, two_features):
        """Without max_cost_usd, engine runs until all done."""
        fl_path = write_features(tmp_path, two_features)

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.50, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            max_cost_usd=None,
        )

        result = await engine.run("worker.md")
        assert result.exit_reason == "all_done"
        assert result.sessions_run == 2

    @pytest.mark.asyncio
    async def test_cost_limit_prints_dag_status(self, tmp_path, three_features, capsys):
        """Cost limit prints DAG status and consumed resources."""
        fl_path = write_features(tmp_path, three_features)

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.10, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            max_cost_usd=0.15,
        )

        await engine.run("worker.md")
        captured = capsys.readouterr()
        assert "COST LIMIT REACHED" in captured.out
        assert "$0.2000" in captured.out
        assert "$0.1500" in captured.out
        assert "Sessions run: 2" in captured.out

    @pytest.mark.asyncio
    async def test_cost_limit_emits_event(self, tmp_path, two_features):
        """Cost limit emits dag_cost_limit event."""
        fl_path = write_features(tmp_path, two_features)
        events = []

        async def capture_event(event_type, data):
            events.append((event_type, data))

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.10, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            on_dag_event=capture_event,
            max_cost_usd=0.05,
        )

        await engine.run("worker.md")
        cost_events = [(t, d) for t, d in events if t == "dag_cost_limit"]
        assert len(cost_events) == 1
        assert cost_events[0][1]["total_cost_usd"] == pytest.approx(0.10)
        assert cost_events[0][1]["max_cost_usd"] == 0.05

    @pytest.mark.asyncio
    async def test_cost_limit_generates_report(self, tmp_path, two_features):
        """Report is generated with cost_limit exit reason."""
        fl_path = write_features(tmp_path, two_features)

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.10, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            max_cost_usd=0.05,
        )

        await engine.run("worker.md")
        report_path = tmp_path / "execution-report.md"
        assert report_path.exists()
        content = report_path.read_text()
        assert "Exit reason: cost_limit" in content


# ---------------------------------------------------------------------------
# DAGEngine — session limit circuit breaker
# ---------------------------------------------------------------------------

class TestSessionLimitBreaker:
    @pytest.mark.asyncio
    async def test_stops_when_session_limit_reached(self, tmp_path, three_features):
        """Engine stops after session count reaches max_sessions."""
        fl_path = write_features(tmp_path, three_features)

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.05, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            max_sessions=2,
        )

        result = await engine.run("worker.md")
        assert result.exit_reason == "session_limit"
        assert result.sessions_run == 2

    @pytest.mark.asyncio
    async def test_session_limit_of_one(self, tmp_path, two_features):
        """Engine stops after exactly 1 session when max_sessions=1."""
        fl_path = write_features(tmp_path, two_features)

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.05, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            max_sessions=1,
        )

        result = await engine.run("worker.md")
        assert result.exit_reason == "session_limit"
        assert result.sessions_run == 1

    @pytest.mark.asyncio
    async def test_no_session_limit_runs_all(self, tmp_path, two_features):
        """Without max_sessions, engine runs until all done."""
        fl_path = write_features(tmp_path, two_features)

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.05, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            max_sessions=None,
        )

        result = await engine.run("worker.md")
        assert result.exit_reason == "all_done"
        assert result.sessions_run == 2

    @pytest.mark.asyncio
    async def test_session_limit_prints_dag_status(self, tmp_path, three_features, capsys):
        """Session limit prints DAG status and consumed resources."""
        fl_path = write_features(tmp_path, three_features)

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.05, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            max_sessions=2,
        )

        await engine.run("worker.md")
        captured = capsys.readouterr()
        assert "SESSION LIMIT REACHED" in captured.out
        assert "Sessions run: 2" in captured.out
        assert "(limit: 2)" in captured.out

    @pytest.mark.asyncio
    async def test_session_limit_emits_event(self, tmp_path, two_features):
        """Session limit emits dag_session_limit event."""
        fl_path = write_features(tmp_path, two_features)
        events = []

        async def capture_event(event_type, data):
            events.append((event_type, data))

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.05, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            on_dag_event=capture_event,
            max_sessions=1,
        )

        await engine.run("worker.md")
        session_events = [(t, d) for t, d in events if t == "dag_session_limit"]
        assert len(session_events) == 1
        assert session_events[0][1]["sessions_run"] == 1
        assert session_events[0][1]["max_sessions"] == 1

    @pytest.mark.asyncio
    async def test_session_limit_generates_report(self, tmp_path, two_features):
        """Report is generated with session_limit exit reason."""
        fl_path = write_features(tmp_path, two_features)

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.05, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            max_sessions=1,
        )

        await engine.run("worker.md")
        report_path = tmp_path / "execution-report.md"
        assert report_path.exists()
        content = report_path.read_text()
        assert "Exit reason: session_limit" in content


# ---------------------------------------------------------------------------
# DAGEngine — both limits together
# ---------------------------------------------------------------------------

class TestBothLimits:
    @pytest.mark.asyncio
    async def test_cost_limit_hits_first(self, tmp_path, three_features):
        """When both limits set, cost limit triggers first."""
        fl_path = write_features(tmp_path, three_features)

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.50, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            max_cost_usd=0.40,   # Hit after 1 session ($0.50)
            max_sessions=10,      # Not hit
        )

        result = await engine.run("worker.md")
        assert result.exit_reason == "cost_limit"
        assert result.sessions_run == 1

    @pytest.mark.asyncio
    async def test_session_limit_hits_first(self, tmp_path, three_features):
        """When both limits set, session limit triggers first."""
        fl_path = write_features(tmp_path, three_features)

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.01, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            max_cost_usd=100.0,  # Not hit
            max_sessions=2,      # Hit after 2 sessions
        )

        result = await engine.run("worker.md")
        assert result.exit_reason == "session_limit"
        assert result.sessions_run == 2

    @pytest.mark.asyncio
    async def test_all_done_before_limits(self, tmp_path, single_feature):
        """All features complete before any limit is reached."""
        fl_path = write_features(tmp_path, single_feature)

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            data[0]["passes"] = True
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.01, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            max_cost_usd=100.0,
            max_sessions=100,
        )

        result = await engine.run("worker.md")
        assert result.exit_reason == "all_done"
        assert result.sessions_run == 1


# ---------------------------------------------------------------------------
# DAGEngine — backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    @pytest.mark.asyncio
    async def test_no_limits_behaves_same_as_before(self, tmp_path, two_features):
        """Engine without limits behaves identically to pre-F-004."""
        fl_path = write_features(tmp_path, two_features)

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.05, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
        )

        result = await engine.run("worker.md")
        assert result.exit_reason == "all_done"
        assert result.sessions_run == 2
        assert result.total_cost_usd == pytest.approx(0.10)

    @pytest.mark.asyncio
    async def test_max_iterations_still_works(self, tmp_path, three_features):
        """max_iterations parameter still takes effect."""
        fl_path = write_features(tmp_path, three_features)

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if not f.get("passes"):
                    f["passes"] = True
                    break
            fl_path.write_text(json.dumps(data))
            return SessionResult(
                status="completed", num_turns=1,
                cost_usd=0.01, duration_ms=1000,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            max_sessions=None,  # No session limit
        )

        result = await engine.run("worker.md", max_iterations=1)
        assert result.exit_reason == "max_iterations"
        assert result.sessions_run == 1


# ---------------------------------------------------------------------------
# DAGExecutionResult — total_cost_usd field
# ---------------------------------------------------------------------------

class TestDAGExecutionResultField:
    def test_default_total_cost_usd(self):
        result = DAGExecutionResult()
        assert result.total_cost_usd == 0.0

    def test_exit_reason_includes_new_values(self):
        """New exit reasons are valid."""
        r1 = DAGExecutionResult(exit_reason="cost_limit")
        assert r1.exit_reason == "cost_limit"
        r2 = DAGExecutionResult(exit_reason="session_limit")
        assert r2.exit_reason == "session_limit"
