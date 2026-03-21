"""Tests for the execution report generator (F-002)."""

import json
from pathlib import Path

import pytest

from nezha.dag.report import (
    ExecutionReportData,
    SessionRecord,
    generate_report,
    write_report,
)
from nezha.dag.graph import TaskDAG as FeatureDAG


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_features():
    """Simple feature list with mixed statuses."""
    return [
        {
            "id": "F-001",
            "description": "Feature one",
            "acceptance": ["criterion 1"],
            "depends_on": [],
            "passes": True,
        },
        {
            "id": "F-002",
            "description": "Feature two",
            "acceptance": ["criterion 2"],
            "depends_on": [],
            "passes": False,
        },
        {
            "id": "F-003",
            "description": "Feature three",
            "acceptance": ["criterion 3"],
            "depends_on": ["F-001"],
            "passes": False,
        },
    ]


@pytest.fixture
def feature_list_path(tmp_path, simple_features):
    path = tmp_path / "feature_list.json"
    path.write_text(json.dumps(simple_features, indent=2))
    return path


@pytest.fixture
def dag(feature_list_path):
    return FeatureDAG.load(feature_list_path)


@pytest.fixture
def sample_report_data():
    """Report data with a few session records."""
    data = ExecutionReportData(
        start_time="2025-01-01 10:00:00 UTC",
    )
    data.add_session(SessionRecord(
        session_number=1,
        feature_id="F-001",
        is_rework=False,
        duration_ms=5000,
        cost_usd=0.05,
        result="completed",
    ))
    data.add_session(SessionRecord(
        session_number=2,
        feature_id="F-002",
        is_rework=False,
        duration_ms=3000,
        cost_usd=0.03,
        result="failed",
        error="Verification command failed",
    ))
    data.end_time = "2025-01-01 10:05:00 UTC"
    return data


# ---------------------------------------------------------------------------
# SessionRecord tests
# ---------------------------------------------------------------------------

class TestSessionRecord:
    def test_defaults(self):
        record = SessionRecord(session_number=1, feature_id="F-001", is_rework=False)
        assert record.duration_ms == 0
        assert record.cost_usd is None
        assert record.result == ""
        assert record.error == ""

    def test_full_record(self):
        record = SessionRecord(
            session_number=1,
            feature_id="F-001",
            is_rework=True,
            duration_ms=5000,
            cost_usd=0.05,
            result="completed",
            error="",
        )
        assert record.session_number == 1
        assert record.feature_id == "F-001"
        assert record.is_rework is True
        assert record.cost_usd == 0.05


# ---------------------------------------------------------------------------
# ExecutionReportData tests
# ---------------------------------------------------------------------------

class TestExecutionReportData:
    def test_add_session(self):
        data = ExecutionReportData(start_time="2025-01-01 10:00:00 UTC")
        assert len(data.sessions) == 0

        record = SessionRecord(session_number=1, feature_id="F-001", is_rework=False)
        data.add_session(record)
        assert len(data.sessions) == 1
        assert data.sessions[0] is record

    def test_multiple_sessions(self):
        data = ExecutionReportData(start_time="2025-01-01 10:00:00 UTC")
        for i in range(3):
            data.add_session(SessionRecord(
                session_number=i + 1,
                feature_id=f"F-{i+1:03d}",
                is_rework=False,
            ))
        assert len(data.sessions) == 3


# ---------------------------------------------------------------------------
# generate_report tests — Overview section
# ---------------------------------------------------------------------------

class TestReportOverview:
    def test_contains_overview_header(self, sample_report_data, dag):
        report = generate_report(sample_report_data, dag, "all_done")
        assert "## Overview" in report

    def test_contains_completed_count(self, sample_report_data, dag):
        report = generate_report(sample_report_data, dag, "all_done")
        # F-001 is completed, so 1/3
        assert "| Completed | 1/3 |" in report

    def test_contains_blocked_count(self, sample_report_data, dag):
        report = generate_report(sample_report_data, dag, "all_done")
        # F-003 depends on F-001 (completed), F-002 not completed -> F-003 is ready not blocked
        # Actually F-003 depends on F-001 which IS completed, so F-003 is ready
        assert "| Blocked | 0 |" in report

    def test_contains_rework_count(self, sample_report_data, dag):
        report = generate_report(sample_report_data, dag, "all_done")
        assert "| Failed/Rework | 0 |" in report

    def test_contains_total_sessions(self, sample_report_data, dag):
        report = generate_report(sample_report_data, dag, "all_done")
        assert "Total sessions: 2" in report

    def test_contains_total_cost(self, sample_report_data, dag):
        report = generate_report(sample_report_data, dag, "all_done")
        assert "Total cost: $0.0800" in report

    def test_contains_total_time(self, sample_report_data, dag):
        report = generate_report(sample_report_data, dag, "all_done")
        assert "Total time: 8000ms" in report

    def test_contains_exit_reason(self, sample_report_data, dag):
        report = generate_report(sample_report_data, dag, "deadlocked")
        assert "Exit reason: deadlocked" in report


# ---------------------------------------------------------------------------
# generate_report tests — Timeline section
# ---------------------------------------------------------------------------

class TestReportTimeline:
    def test_contains_timeline_header(self, sample_report_data, dag):
        report = generate_report(sample_report_data, dag, "all_done")
        assert "## Session Timeline" in report

    def test_contains_session_rows(self, sample_report_data, dag):
        report = generate_report(sample_report_data, dag, "all_done")
        assert "| 1 | F-001 | new" in report
        assert "| 2 | F-002 | new" in report

    def test_rework_session_type(self, dag):
        data = ExecutionReportData(
            start_time="2025-01-01 10:00:00 UTC",
            end_time="2025-01-01 10:05:00 UTC",
        )
        data.add_session(SessionRecord(
            session_number=1,
            feature_id="F-001",
            is_rework=True,
            duration_ms=1000,
            cost_usd=0.01,
            result="completed",
        ))
        report = generate_report(data, dag, "all_done")
        assert "| 1 | F-001 | rework" in report

    def test_cost_display(self, dag):
        data = ExecutionReportData(
            start_time="t1",
            end_time="t2",
        )
        data.add_session(SessionRecord(
            session_number=1,
            feature_id="F-001",
            is_rework=False,
            cost_usd=0.1234,
        ))
        report = generate_report(data, dag, "all_done")
        assert "$0.1234" in report

    def test_no_cost_display(self, dag):
        data = ExecutionReportData(
            start_time="t1",
            end_time="t2",
        )
        data.add_session(SessionRecord(
            session_number=1,
            feature_id="F-001",
            is_rework=False,
            cost_usd=None,
        ))
        report = generate_report(data, dag, "all_done")
        assert "| - |" in report

    def test_empty_sessions(self, dag):
        data = ExecutionReportData(
            start_time="t1",
            end_time="t2",
        )
        report = generate_report(data, dag, "deadlocked")
        assert "No sessions were executed." in report


# ---------------------------------------------------------------------------
# generate_report tests — Failure Records section
# ---------------------------------------------------------------------------

class TestReportFailures:
    def test_contains_failure_header(self, sample_report_data, dag):
        report = generate_report(sample_report_data, dag, "all_done")
        assert "## Failure Records" in report

    def test_no_failures(self, sample_report_data, dag):
        report = generate_report(sample_report_data, dag, "all_done")
        assert "No failures recorded." in report

    def test_rework_feature_listed(self, tmp_path):
        features = [
            {
                "id": "F-001",
                "description": "Feature one",
                "acceptance": [],
                "depends_on": [],
                "passes": False,
                "rework": True,
                "rework_note": "Test failed: assertion error",
                "rework_count": 2,
            },
        ]
        fl_path = tmp_path / "feature_list.json"
        fl_path.write_text(json.dumps(features))
        dag = FeatureDAG.load(fl_path)

        data = ExecutionReportData(start_time="t1", end_time="t2")
        data.add_session(SessionRecord(
            session_number=1,
            feature_id="F-001",
            is_rework=True,
            result="failed",
            error="assertion error",
        ))

        report = generate_report(data, dag, "max_iterations")
        assert "### F-001" in report
        assert "**Attempts**: 2" in report
        assert "Test failed: assertion error" in report
        assert "Session 1: failed" in report

    def test_skipped_feature_listed(self, tmp_path):
        features = [
            {
                "id": "F-001",
                "description": "Feature one",
                "acceptance": [],
                "depends_on": [],
                "passes": False,
                "rework": False,
                "rework_count": 3,
                "rework_note": "Exhausted",
            },
        ]
        fl_path = tmp_path / "feature_list.json"
        fl_path.write_text(json.dumps(features))
        dag = FeatureDAG.load(fl_path)

        data = ExecutionReportData(start_time="t1", end_time="t2")
        report = generate_report(data, dag, "all_done")
        assert "### F-001" in report
        assert "**Status**: skipped" in report

    def test_session_history_for_failure(self, tmp_path):
        features = [
            {
                "id": "F-001",
                "description": "Feature one",
                "acceptance": [],
                "depends_on": [],
                "passes": False,
                "rework": True,
                "rework_note": "error",
                "rework_count": 1,
            },
        ]
        fl_path = tmp_path / "feature_list.json"
        fl_path.write_text(json.dumps(features))
        dag = FeatureDAG.load(fl_path)

        data = ExecutionReportData(start_time="t1", end_time="t2")
        data.add_session(SessionRecord(
            session_number=1, feature_id="F-001", is_rework=False,
            result="failed", error="compile error",
        ))
        data.add_session(SessionRecord(
            session_number=2, feature_id="F-001", is_rework=True,
            result="failed", error="still broken",
        ))

        report = generate_report(data, dag, "max_iterations")
        assert "Session 1: failed" in report
        assert "compile error" in report
        assert "Session 2: failed" in report
        assert "still broken" in report


# ---------------------------------------------------------------------------
# generate_report tests — Blocked Dependencies section
# ---------------------------------------------------------------------------

class TestReportBlocked:
    def test_contains_blocked_header(self, sample_report_data, dag):
        report = generate_report(sample_report_data, dag, "all_done")
        assert "## Blocked Dependencies" in report

    def test_no_blocked_features(self, sample_report_data, dag):
        report = generate_report(sample_report_data, dag, "all_done")
        assert "No blocked tasks." in report

    def test_blocked_feature_listed(self, tmp_path):
        features = [
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
                "depends_on": ["F-001"],
                "passes": False,
            },
        ]
        fl_path = tmp_path / "feature_list.json"
        fl_path.write_text(json.dumps(features))
        dag = FeatureDAG.load(fl_path)

        data = ExecutionReportData(start_time="t1", end_time="t2")
        report = generate_report(data, dag, "deadlocked")
        assert "**F-002** blocked by: F-001" in report

    def test_blocked_includes_dependency_graph(self, tmp_path):
        features = [
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
                "depends_on": ["F-001"],
                "passes": False,
            },
        ]
        fl_path = tmp_path / "feature_list.json"
        fl_path.write_text(json.dumps(features))
        dag = FeatureDAG.load(fl_path)

        data = ExecutionReportData(start_time="t1", end_time="t2")
        report = generate_report(data, dag, "deadlocked")
        assert "Dependency graph:" in report
        assert "```" in report
        assert "F-001" in report
        assert "F-002" in report


# ---------------------------------------------------------------------------
# write_report tests
# ---------------------------------------------------------------------------

class TestWriteReport:
    def test_writes_file(self, tmp_path, sample_report_data, dag):
        report_path = write_report(sample_report_data, dag, "all_done", tmp_path)
        assert report_path == tmp_path / "execution-report.md"
        assert report_path.exists()

    def test_file_content(self, tmp_path, sample_report_data, dag):
        report_path = write_report(sample_report_data, dag, "all_done", tmp_path)
        content = report_path.read_text(encoding="utf-8")
        assert "# Execution Report" in content
        assert "## Overview" in content
        assert "## Session Timeline" in content
        assert "## Failure Records" in content
        assert "## Blocked Dependencies" in content

    def test_overwrites_existing(self, tmp_path, sample_report_data, dag):
        report_file = tmp_path / "execution-report.md"
        report_file.write_text("old content")

        write_report(sample_report_data, dag, "all_done", tmp_path)
        content = report_file.read_text()
        assert "old content" not in content
        assert "# Execution Report" in content


# ---------------------------------------------------------------------------
# DAG Engine integration: report generation
# ---------------------------------------------------------------------------

class TestDAGEngineReport:
    @pytest.mark.asyncio
    async def test_report_generated_after_run(self, tmp_path):
        """DAG engine generates execution-report.md after run() completes."""
        from nezha.dag.engine import DAGEngine

        features = [
            {
                "id": "F-001",
                "description": "Test feature",
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
            return SessionResult(status="completed", num_turns=1, cost_usd=0.05, duration_ms=2000)

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
        )

        await engine.run("worker.md", max_iterations=1)

        report_path = tmp_path / "execution-report.md"
        assert report_path.exists()

        content = report_path.read_text()
        assert "# Execution Report" in content
        assert "## Overview" in content
        assert "| Completed | 1/1 |" in content

    @pytest.mark.asyncio
    async def test_report_contains_session_timeline(self, tmp_path):
        """Report has timeline with session details."""
        from nezha.dag.engine import DAGEngine

        features = [
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
        ]
        fl_path = tmp_path / "feature_list.json"
        fl_path.write_text(json.dumps(features))

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
            from nezha.engine import SessionResult
            return SessionResult(
                status="completed",
                num_turns=call_count,
                cost_usd=0.01 * call_count,
                duration_ms=1000 * call_count,
            )

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
        )

        await engine.run("worker.md", max_iterations=5)

        content = (tmp_path / "execution-report.md").read_text()
        assert "## Session Timeline" in content
        assert "F-001" in content
        assert "F-002" in content
        assert "completed" in content

    @pytest.mark.asyncio
    async def test_report_on_deadlock(self, tmp_path):
        """Report is generated even when DAG deadlocks.

        Uses a non-circular deadlock: F-002 depends on F-001,
        but F-001 is skipped (rework_count >= 3), so F-002 is blocked.
        """
        from nezha.dag.engine import DAGEngine

        features = [
            {
                "id": "F-001",
                "description": "Feature one",
                "acceptance": [],
                "depends_on": [],
                "passes": False,
                "rework_count": 3,
            },
            {
                "id": "F-002",
                "description": "Feature two",
                "acceptance": [],
                "depends_on": ["F-001"],
                "passes": False,
            },
        ]
        fl_path = tmp_path / "feature_list.json"
        fl_path.write_text(json.dumps(features))

        def mock_session(prompt_path, model_override="", env_override=None):
            from nezha.engine import SessionResult
            return SessionResult(status="completed")

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
        )

        result = await engine.run("worker.md")

        assert result.exit_reason == "deadlocked"

        report_path = tmp_path / "execution-report.md"
        assert report_path.exists()

        content = report_path.read_text()
        assert "Exit reason: deadlocked" in content
        assert "## Blocked Dependencies" in content
        assert "No sessions were executed." in content

    @pytest.mark.asyncio
    async def test_report_with_failed_verification(self, tmp_path):
        """Report records failed sessions correctly."""
        import sys
        from nezha.dag.engine import DAGEngine

        features = [
            {
                "id": "F-001",
                "description": "Test feature",
                "acceptance": [],
                "depends_on": [],
                "passes": False,
            }
        ]
        fl_path = tmp_path / "feature_list.json"
        fl_path.write_text(json.dumps(features))

        def mock_session(prompt_path, model_override="", env_override=None):
            data = json.loads(fl_path.read_text())
            for f in data:
                if f["id"] == "F-001":
                    f["passes"] = True
                    f.pop("rework", None)
                    f.pop("rework_note", None)
            fl_path.write_text(json.dumps(data))
            from nezha.engine import SessionResult
            return SessionResult(status="completed", num_turns=1, cost_usd=0.01, duration_ms=100)

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
            verification_command=f"{sys.executable} -c \"import sys; sys.exit(1)\"",
        )

        await engine.run("worker.md", max_iterations=1)

        content = (tmp_path / "execution-report.md").read_text()
        assert "## Failure Records" in content
        assert "F-001" in content

    @pytest.mark.asyncio
    async def test_report_exit_reason_max_iterations(self, tmp_path):
        """Report shows max_iterations as exit reason."""
        from nezha.dag.engine import DAGEngine

        features = [
            {
                "id": "F-001",
                "description": "Test feature",
                "acceptance": [],
                "depends_on": [],
                "passes": False,
            }
        ]
        fl_path = tmp_path / "feature_list.json"
        fl_path.write_text(json.dumps(features))

        def mock_session(prompt_path, model_override="", env_override=None):
            # Don't update passes — will stay pending
            from nezha.engine import SessionResult
            return SessionResult(status="completed", num_turns=1, cost_usd=0.01, duration_ms=100)

        engine = DAGEngine(
            task_list_path=fl_path,
            workspace=tmp_path,
            run_session_fn=mock_session,
            delay=0,
        )

        result = await engine.run("worker.md", max_iterations=1)

        content = (tmp_path / "execution-report.md").read_text()
        assert "Exit reason: max_iterations" in content
