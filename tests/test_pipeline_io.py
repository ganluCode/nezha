from nezha.config import AgentConfig, IOConfig
from nezha.pipeline.io import scan_input_files


def test_scan_input_files_excludes_runtime_artifacts_by_default(tmp_path):
    (tmp_path / "task.md").write_text("real input", encoding="utf-8")
    (tmp_path / ".codex-last-message.txt").write_text("stale", encoding="utf-8")
    (tmp_path / ".session_manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".dag_context.json").write_text("{}", encoding="utf-8")
    (tmp_path / "exec-plan.md").write_text("old plan", encoding="utf-8")
    (tmp_path / "execution-report.md").write_text("old report", encoding="utf-8")
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")

    files = scan_input_files(AgentConfig(), tmp_path)

    assert [p.name for p in files] == ["task.md"]


def test_scan_input_files_honors_explicit_runtime_artifact_selection(tmp_path):
    (tmp_path / "execution-report.md").write_text("explicit report", encoding="utf-8")
    agent_config = AgentConfig(
        input=IOConfig(files=["execution-report.md"]),
    )

    files = scan_input_files(agent_config, tmp_path)

    assert [p.name for p in files] == ["execution-report.md"]
