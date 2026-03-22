"""Tests for JSON repair logic in pipeline/direct_api.py."""

import json
from pathlib import Path

import pytest

from nezha.pipeline.direct_api import _try_fix_json, _fix_unescaped_quotes


# ---------------------------------------------------------------------------
# _try_fix_json: trailing commas
# ---------------------------------------------------------------------------

class TestTrailingCommas:
    def test_trailing_comma_in_array(self):
        bad = '[{"id": "F-001"},]'
        result = _try_fix_json(bad)
        parsed = json.loads(result)
        assert parsed[0]["id"] == "F-001"

    def test_trailing_comma_in_object(self):
        bad = '{"id": "F-001", "passes": false,}'
        result = _try_fix_json(bad)
        parsed = json.loads(result)
        assert parsed["id"] == "F-001"

    def test_multiple_trailing_commas(self):
        bad = '[{"a": 1,}, {"b": 2,},]'
        result = _try_fix_json(bad)
        parsed = json.loads(result)
        assert len(parsed) == 2


# ---------------------------------------------------------------------------
# _try_fix_json: comments
# ---------------------------------------------------------------------------

class TestCommentRemoval:
    def test_single_line_comment(self):
        bad = '[\n  {"id": "F-001"} // first task\n]'
        result = _try_fix_json(bad)
        parsed = json.loads(result)
        assert parsed[0]["id"] == "F-001"

    def test_multi_line_comment(self):
        bad = '[\n  /* this is a task */\n  {"id": "F-001"}\n]'
        result = _try_fix_json(bad)
        parsed = json.loads(result)
        assert parsed[0]["id"] == "F-001"


# ---------------------------------------------------------------------------
# _try_fix_json: unescaped double quotes (Chinese text)
# ---------------------------------------------------------------------------

class TestUnescapedQuotes:
    def test_chinese_quoted_term_in_description(self):
        """PRD with "AI 助理" style quotes should be fixed."""
        bad = '''[
  {
    "id": "F-001",
    "description": "实现"AI 助理"的核心对话功能",
    "acceptance": ["测试通过"],
    "depends_on": [],
    "complexity": "medium",
    "passes": false
  }
]'''
        result = _try_fix_json(bad)
        parsed = json.loads(result)
        assert parsed[0]["id"] == "F-001"
        # The inner quotes should be replaced with Chinese quotes
        desc = parsed[0]["description"]
        assert "AI 助理" in desc
        assert '"' not in desc  # no raw double quotes in value

    def test_multiple_quoted_terms(self):
        """Multiple quoted terms in one line."""
        bad = '''[
  {
    "id": "F-001",
    "description": "实现"代码专家"和"AI 助理"两个角色",
    "acceptance": ["通过"],
    "depends_on": [],
    "complexity": "low",
    "passes": false
  }
]'''
        result = _try_fix_json(bad)
        parsed = json.loads(result)
        desc = parsed[0]["description"]
        assert "代码专家" in desc
        assert "AI 助理" in desc

    def test_already_escaped_quotes_unchanged(self):
        """Already escaped quotes should not be double-escaped."""
        good = r'[{"id": "F-001", "description": "implement \"feature\" here", "passes": false}]'
        result = _try_fix_json(good)
        parsed = json.loads(result)
        assert '"feature"' in parsed[0]["description"] or "feature" in parsed[0]["description"]

    def test_valid_json_not_modified(self):
        """Valid JSON should pass through unchanged."""
        good = '[{"id": "F-001", "description": "simple text", "passes": false}]'
        result = _try_fix_json(good)
        assert json.loads(result) == json.loads(good)

    def test_acceptance_with_quotes(self):
        """Unescaped quotes in acceptance criteria array."""
        bad = '''[
  {
    "id": "F-001",
    "description": "test",
    "acceptance": ["页面显示"欢迎"文字", "按钮标注"提交""],
    "depends_on": [],
    "complexity": "low",
    "passes": false
  }
]'''
        result = _try_fix_json(bad)
        parsed = json.loads(result)
        assert len(parsed[0]["acceptance"]) == 2


# ---------------------------------------------------------------------------
# _fix_unescaped_quotes: iterative repair
# ---------------------------------------------------------------------------

class TestFixUnescapedQuotesIterative:
    def test_single_line_json_with_inner_quotes(self):
        """Single-line JSON with unescaped quotes."""
        bad = '[{"id": "F-001", "description": "实现"测试"功能"}]'
        result = _fix_unescaped_quotes(bad)
        parsed = json.loads(result)
        assert parsed[0]["id"] == "F-001"
        assert "测试" in parsed[0]["description"]

    def test_no_change_when_valid(self):
        good = '[{"id": "F-001", "description": "simple"}]'
        assert _fix_unescaped_quotes(good) == good

    def test_parenthesized_term(self):
        """Handles terms like "白泽（Baize）" with unescaped quotes."""
        bad = '''[{"id": "F-001", "description": "配置"白泽"项目的基础设施"}]'''
        result = _fix_unescaped_quotes(bad)
        parsed = json.loads(result)
        assert "白泽" in parsed[0]["description"]


# ---------------------------------------------------------------------------
# _validate_and_repair_task_list (executor.py)
# ---------------------------------------------------------------------------

class TestValidateAndRepairTaskList:
    def test_valid_file_passes(self, tmp_path):
        from nezha.executor import _validate_and_repair_task_list
        p = tmp_path / "task_list.json"
        p.write_text('[{"id": "F-001", "description": "test"}]')
        assert _validate_and_repair_task_list(p) is True

    def test_empty_array_fails(self, tmp_path):
        from nezha.executor import _validate_and_repair_task_list
        p = tmp_path / "task_list.json"
        p.write_text('[]')
        assert _validate_and_repair_task_list(p) is False

    def test_invalid_json_repaired(self, tmp_path):
        from nezha.executor import _validate_and_repair_task_list
        p = tmp_path / "task_list.json"
        bad = '[{"id": "F-001", "description": "实现"测试"功能"}]'
        p.write_text(bad)
        result = _validate_and_repair_task_list(p)
        assert result is True
        # File should be rewritten with valid JSON
        parsed = json.loads(p.read_text())
        assert parsed[0]["id"] == "F-001"

    def test_unfixable_json_fails(self, tmp_path):
        from nezha.executor import _validate_and_repair_task_list
        p = tmp_path / "task_list.json"
        p.write_text('this is not json at all {{{')
        assert _validate_and_repair_task_list(p) is False


# ---------------------------------------------------------------------------
# TaskDAG.load with repair fallback
# ---------------------------------------------------------------------------

class TestTaskDAGLoadRepair:
    def test_load_valid_json(self, tmp_path):
        from nezha.dag.graph import TaskDAG
        p = tmp_path / "task_list.json"
        p.write_text(json.dumps([
            {"id": "F-001", "description": "test", "depends_on": [], "passes": False}
        ]))
        dag = TaskDAG.load(p)
        assert len(dag._tasks) == 1

    def test_load_with_trailing_comma(self, tmp_path):
        from nezha.dag.graph import TaskDAG
        p = tmp_path / "task_list.json"
        p.write_text('[{"id": "F-001", "description": "test", "depends_on": [], "passes": false},]')
        dag = TaskDAG.load(p)
        assert len(dag._tasks) == 1
        # File should be rewritten
        repaired = json.loads(p.read_text())
        assert len(repaired) == 1
