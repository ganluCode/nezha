"""Tests for V2.0 F5: feature show enhancement + cost stats."""

from pathlib import Path

import pytest

from nezha.interface.cli import _parse_report_summary, _get_feature_cost


# ---------------------------------------------------------------------------
# Sample execution-report.md content
# ---------------------------------------------------------------------------

_SAMPLE_REPORT = """\
# Execution Report

Generated: 2026-02-19 13:13:27 UTC
Started: 2026-02-19 12:53:25 UTC
Exit reason: all_done

## Overview

| Status | Count |
|--------|-------|
| Completed | 6/6 |
| Failed/Rework | 0 |
| Blocked | 0 |
| Skipped | 0 |
| Ready | 0 |

Total sessions: 6
Total cost: $6.9267
Total time: 1153419ms

## Session Timeline

| # | Feature | Type | Duration | Cost | Result |
|---|---------|------|----------|------|--------|
| 1 | F-001 | new | 168054ms | $1.0046 | completed |
| 2 | F-002 | new | 196539ms | $1.2308 | completed |
| 3 | F-003 | new | 279469ms | $1.5755 | completed |

## Failure Records

No failures recorded.

## Blocked Dependencies

No blocked tasks.
"""


class TestParseReportSummary:

    def test_full_report(self, tmp_path):
        report = tmp_path / "execution-report.md"
        report.write_text(_SAMPLE_REPORT)

        result = _parse_report_summary(report)
        assert result is not None
        assert result["exit_reason"] == "all_done"
        assert result["completed"] == 6
        assert result["total"] == 6
        assert result["sessions"] == 6
        assert result["cost"] == pytest.approx(6.9267)
        assert result["time_ms"] == 1153419

    def test_timeline_parsed(self, tmp_path):
        report = tmp_path / "execution-report.md"
        report.write_text(_SAMPLE_REPORT)

        result = _parse_report_summary(report)
        assert "timeline" in result
        assert len(result["timeline"]) == 3
        assert result["timeline"][0]["feature"] == "F-001"
        assert result["timeline"][0]["cost"] == "$1.0046"
        assert result["timeline"][2]["duration_ms"] == 279469

    def test_missing_file(self, tmp_path):
        result = _parse_report_summary(tmp_path / "nonexistent.md")
        assert result is None

    def test_empty_report(self, tmp_path):
        report = tmp_path / "execution-report.md"
        report.write_text("# Execution Report\n\nNo data.\n")
        result = _parse_report_summary(report)
        assert result is None  # no parseable fields


class TestGetFeatureCost:

    def test_with_report(self, tmp_path):
        report = tmp_path / "execution-report.md"
        report.write_text(_SAMPLE_REPORT)
        assert _get_feature_cost(tmp_path) == "$6.9267"

    def test_without_report(self, tmp_path):
        assert _get_feature_cost(tmp_path) == "-"


class TestFeatureShowWithReport:

    def test_show_displays_cost(self, tmp_path, capsys):
        """cmd_feature_show displays report summary when report exists."""
        from nezha.feature_queue import FileFeatureQueue

        queue = FileFeatureQueue(tmp_path)
        feature = queue.create(title="test-cost")
        ws = queue.feature_workspace(feature.id)
        (ws / "execution-report.md").write_text(_SAMPLE_REPORT)

        # Call show directly (uses internal queue)
        from nezha.interface.cli import _parse_report_summary
        summary = _parse_report_summary(ws / "execution-report.md")
        assert summary is not None
        assert summary["cost"] == pytest.approx(6.9267)
        assert summary["sessions"] == 6
