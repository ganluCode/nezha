"""Tests for V2.1: Dashboard, Helper enhancements, parallel execution config."""

import json
from pathlib import Path

import pytest
import yaml

from nezha.config import SchedulerConfig, load_agent_config


# ---------------------------------------------------------------------------
# V2.1-F2: Dashboard
# ---------------------------------------------------------------------------

class TestDashboard:

    def _create_feature_dir(self, workspace: Path, feature_id: str, status: str = "completed", cost: float | None = None):
        """Create a minimal feature directory with feature.yaml and optional report."""
        features_dir = workspace / "features" / feature_id
        features_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "id": feature_id,
            "title": feature_id,
            "status": status,
            "created_at": "2026-03-01T10:00:00+08:00",
        }
        with open(features_dir / "feature.yaml", "w") as f:
            yaml.dump(data, f)

        if cost is not None:
            report = (
                f"# Execution Report\n\n"
                f"Exit reason: all_done\n\n"
                f"## Overview\n\n"
                f"| Status | Count |\n|--------|-------|\n| Completed | 1/1 |\n\n"
                f"Total sessions: 1\nTotal cost: ${cost:.4f}\nTotal time: 60000ms\n\n"
                f"## Session Timeline\n\n"
                f"| # | Feature | Type | Duration | Cost | Result |\n"
                f"|---|---------|------|----------|------|--------|\n"
                f"| 1 | F-001 | new | 60000ms | ${cost:.4f} | completed |\n"
            )
            (features_dir / "execution-report.md").write_text(report)

        return features_dir

    def test_generate_dashboard_empty(self, tmp_path):
        """Dashboard with no features generates valid HTML."""
        from nezha.interface.dashboard import generate_dashboard
        (tmp_path / "features").mkdir()
        html = generate_dashboard(tmp_path)
        assert "<html" in html
        assert "Dashboard" in html

    def test_generate_dashboard_with_features(self, tmp_path):
        """Dashboard includes feature data."""
        from nezha.interface.dashboard import generate_dashboard
        self._create_feature_dir(tmp_path, "feat-1", "completed", cost=1.5)
        self._create_feature_dir(tmp_path, "feat-2", "pending")
        html = generate_dashboard(tmp_path)
        assert "feat-1" in html
        assert "feat-2" in html
        assert "$1.50" in html or "1.5" in html

    def test_write_dashboard(self, tmp_path):
        """write_dashboard creates the HTML file."""
        from nezha.interface.dashboard import write_dashboard
        (tmp_path / "features").mkdir()
        self._create_feature_dir(tmp_path, "feat-1", "completed")
        out = tmp_path / "output" / "dashboard.html"
        result = write_dashboard(tmp_path, out)
        assert result == out
        assert out.exists()
        assert "<html" in out.read_text()

    def test_compute_summary(self, tmp_path):
        """Summary computation aggregates correctly."""
        from nezha.interface.dashboard import _collect_features, _compute_summary
        self._create_feature_dir(tmp_path, "a", "completed", cost=2.0)
        self._create_feature_dir(tmp_path, "b", "completed", cost=3.0)
        self._create_feature_dir(tmp_path, "c", "pending")
        features = _collect_features(tmp_path)
        summary = _compute_summary(features)
        assert summary["total"] == 3
        assert summary["completed"] == 2
        assert summary["total_cost"] == pytest.approx(5.0)

    def test_dashboard_with_state_dir(self, tmp_path):
        """Dashboard reads executor_status.json when state_dir provided."""
        from nezha.interface.dashboard import generate_dashboard
        (tmp_path / "features").mkdir()
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        status = {"status": "idle", "current_agent": None, "last_updated": "2026-03-01T10:00:00"}
        (state_dir / "executor_status.json").write_text(json.dumps(status))
        html = generate_dashboard(tmp_path, state_dir=state_dir)
        assert "idle" in html.lower() or "Idle" in html


# ---------------------------------------------------------------------------
# V2.1-F1: Helper agent
# ---------------------------------------------------------------------------

class TestHelperUniversal:

    TEMPLATES = Path(__file__).resolve().parent.parent / "src" / "nezha" / "templates"

    def test_helper_prompt_has_operational_scenarios(self):
        """Helper prompt includes scenarios 6-9 (operational)."""
        content = (self.TEMPLATES / "prompts" / "helper" / "worker.md").read_text()
        assert "SCENARIO 6" in content  # Feature Management
        assert "SCENARIO 7" in content  # Execution Control
        assert "SCENARIO 8" in content  # Cost & Reporting
        assert "SCENARIO 9" in content  # Git & Integration

    def test_helper_prompt_mentions_agent_exec(self):
        """Helper prompt references nezha CLI commands."""
        content = (self.TEMPLATES / "prompts" / "helper" / "worker.md").read_text()
        assert "nezha" in content

    def test_helper_zh_prompt_exists(self):
        """Chinese helper prompt exists."""
        assert (self.TEMPLATES / "prompts" / "helper" / "worker.zh.md").is_file()

    def test_helper_agent_is_callable(self):
        config = load_agent_config(self.TEMPLATES / "agents" / "helper-agent.yaml")
        assert config.agent.callable is True

    def test_helper_agent_has_write_tool(self):
        config = load_agent_config(self.TEMPLATES / "agents" / "helper-agent.yaml")
        assert "Write" in config.engine.tools


# ---------------------------------------------------------------------------
# V2.1-F3: Parallel execution config
# ---------------------------------------------------------------------------

class TestParallelConfig:

    def test_scheduler_config_concurrency_default(self):
        """Concurrency defaults to 1."""
        config = SchedulerConfig()
        assert config.concurrency == 1

    def test_scheduler_config_concurrency_set(self):
        """Concurrency can be set from config."""
        config = SchedulerConfig(concurrency=4)
        assert config.concurrency == 4

    def test_concurrency_loaded_from_yaml(self, tmp_path):
        """Concurrency field is loaded from executor.yaml."""
        from nezha.config import load_executor_config
        config_data = {
            "scheduler": {"mode": "continuous", "concurrency": 3},
        }
        config_path = tmp_path / "executor.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)
        config = load_executor_config(str(config_path))
        assert config.scheduler.concurrency == 3

    def test_concurrency_defaults_when_not_in_yaml(self, tmp_path):
        """Missing concurrency field defaults to 1."""
        from nezha.config import load_executor_config
        config_data = {"scheduler": {"mode": "manual"}}
        config_path = tmp_path / "executor.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)
        config = load_executor_config(str(config_path))
        assert config.scheduler.concurrency == 1
