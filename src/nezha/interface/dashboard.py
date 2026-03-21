"""Dashboard generator: produces a static HTML report of feature status and token usage."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

_STATUS_COLORS = {
    "completed": "#27ae60",
    "running":   "#2980b9",
    "failed":    "#e74c3c",
    "pending":   "#95a5a6",
    "partial":   "#f39c12",
    "paused":    "#8e44ad",
}

_STATUS_ICONS = {
    "completed": "✓",
    "running":   "►",
    "failed":    "✗",
    "pending":   "○",
    "partial":   "◑",
    "paused":    "‖",
}


def _status_color(status: str) -> str:
    return _STATUS_COLORS.get(status, "#95a5a6")


def _status_icon(status: str) -> str:
    return _STATUS_ICONS.get(status, "?")


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def _collect_features(workspace_base: Path) -> list[dict]:
    """Read all features from workspace_base/features/ directory."""
    import yaml

    features_dir = workspace_base / "features"
    if not features_dir.exists():
        # Fallback to legacy tasks/ directory
        features_dir = workspace_base / "tasks"
    if not features_dir.exists():
        return []

    features = []
    for feature_dir in sorted(features_dir.iterdir()):
        if not feature_dir.is_dir():
            continue
        yaml_path = feature_dir / "feature.yaml"
        if not yaml_path.exists():
            continue
        try:
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            continue

        # Read execution report if available
        report_path = feature_dir / "execution-report.md"
        from nezha.interface.cli import _parse_report_summary
        report = _parse_report_summary(report_path)

        features.append({
            "id": data.get("id", feature_dir.name),
            "title": data.get("title", ""),
            "status": data.get("status", "pending"),
            "created_at": data.get("created_at", ""),
            "started_at": data.get("started_at", ""),
            "completed_at": data.get("completed_at", ""),
            "error": data.get("error", ""),
            "metadata": data.get("metadata", {}),
            "steps": data.get("steps", []),
            "report": report,
        })

    return features


def _compute_summary(features: list[dict]) -> dict:
    """Compute aggregated summary statistics across all features."""
    total = len(features)
    completed = sum(1 for f in features if f["status"] == "completed")
    total_cost = 0.0
    total_sessions = 0
    total_tokens_str = "-"

    for f in features:
        report = f.get("report") or {}
        if "cost" in report:
            try:
                total_cost += float(report["cost"])
            except (TypeError, ValueError):
                pass
        if "sessions" in report:
            try:
                total_sessions += int(report["sessions"])
            except (TypeError, ValueError):
                pass
    # Aggregate tokens display — use first non-empty report's format as reference
    # (tokens are already pre-formatted like "1.2M" in the report)
    token_reports = [f.get("report") for f in features if f.get("report") and "tokens" in f["report"]]
    if token_reports:
        total_tokens_str = f"{len(token_reports)} features"

    return {
        "total": total,
        "completed": completed,
        "completed_pct": int(completed / total * 100) if total else 0,
        "total_cost": total_cost,
        "total_sessions": total_sessions,
        "total_tokens": total_tokens_str,
    }


# ---------------------------------------------------------------------------
# HTML building blocks
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f5f7fa;
    color: #333;
    line-height: 1.5;
}
header {
    background: #1a1a2e;
    color: #fff;
    padding: 20px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
}
header h1 { font-size: 1.5rem; font-weight: 700; letter-spacing: 0.5px; }
header .subtitle { font-size: 0.8rem; color: #aab; margin-top: 2px; }
.container { max-width: 1200px; margin: 0 auto; padding: 24px 16px; }
.cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 28px;
}
.card {
    background: #fff;
    border-radius: 8px;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
    padding: 20px 24px;
}
.card .label { font-size: 0.78rem; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }
.card .value { font-size: 2rem; font-weight: 700; color: #1a1a2e; margin-top: 4px; }
.card .sub { font-size: 0.8rem; color: #aaa; margin-top: 2px; }
section { background: #fff; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,.08); padding: 20px 24px; margin-bottom: 24px; }
section h2 { font-size: 1rem; font-weight: 600; margin-bottom: 16px; color: #1a1a2e; }
table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
thead th {
    text-align: left; padding: 8px 12px;
    border-bottom: 2px solid #eee;
    color: #888; font-weight: 600;
    font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.4px;
}
tbody tr { border-bottom: 1px solid #f0f0f0; }
tbody tr:last-child { border-bottom: none; }
tbody td { padding: 10px 12px; vertical-align: middle; }
tbody tr:hover { background: #fafbfc; }
.badge {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 2px 10px; border-radius: 12px; font-size: 0.78rem; font-weight: 600;
    color: #fff;
}
.feature-id { font-family: monospace; font-size: 0.82rem; color: #555; }
.feature-title { font-weight: 500; }
.bar-container { height: 8px; background: #f0f0f0; border-radius: 4px; min-width: 60px; max-width: 200px; }
.bar-fill { height: 100%; border-radius: 4px; transition: width 0.3s; }
.no-data { color: #bbb; font-style: italic; text-align: center; padding: 32px; }
footer { text-align: center; color: #bbb; font-size: 0.78rem; padding: 24px; }
"""


def _render_badge(status: str) -> str:
    color = _status_color(status)
    icon = _status_icon(status)
    return (
        f'<span class="badge" style="background:{color}">'
        f'{icon} {status}'
        f'</span>'
    )


def _format_tokens(report: dict | None) -> str:
    if report and "tokens" in report:
        return report["tokens"]
    return "-"


def _format_cost(report: dict | None) -> str:
    if report and "cost" in report:
        return f"${report['cost']:.4f}"
    return "-"


def _format_sessions(report: dict | None) -> str:
    if report and "sessions" in report:
        return str(report["sessions"])
    return "-"


def _format_duration(report: dict | None) -> str:
    if report and "time_ms" in report:
        ms = report["time_ms"]
        try:
            ms = int(ms)
        except (TypeError, ValueError):
            return "-"
        if ms < 60_000:
            return f"{ms / 1000:.1f}s"
        elif ms < 3_600_000:
            return f"{ms / 60_000:.1f}m"
        else:
            return f"{ms / 3_600_000:.1f}h"
    return "-"


def _format_date(ts: str) -> str:
    if not ts:
        return "-"
    # Truncate to date+time without microseconds
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)[:16]


def _render_summary_cards(summary: dict) -> str:
    pct = summary["completed_pct"]
    return f"""
<div class="cards">
  <div class="card">
    <div class="label">Total Features</div>
    <div class="value">{summary['total']}</div>
  </div>
  <div class="card">
    <div class="label">Completed</div>
    <div class="value">{summary['completed']}</div>
    <div class="sub">{pct}% of total</div>
  </div>
  <div class="card">
    <div class="label">Total Sessions</div>
    <div class="value">{summary['total_sessions']}</div>
  </div>
</div>
"""


def _render_features_table(features: list[dict]) -> str:
    if not features:
        return '<p class="no-data">No features found.</p>'

    rows = []
    for f in features:
        status = f.get("status", "pending")
        report = f.get("report")
        rows.append(f"""
    <tr>
      <td>{_render_badge(status)}</td>
      <td class="feature-id">{f['id']}</td>
      <td class="feature-title">{f['title'] or '-'}</td>
      <td>{_format_tokens(report)}</td>
      <td>{_format_sessions(report)}</td>
      <td>{_format_duration(report)}</td>
      <td>{_format_date(f.get('created_at', ''))}</td>
    </tr>""")

    return f"""
<table>
  <thead>
    <tr>
      <th>Status</th>
      <th>ID</th>
      <th>Title</th>
      <th>Tokens</th>
      <th>Sessions</th>
      <th>Duration</th>
      <th>Created</th>
    </tr>
  </thead>
  <tbody>{''.join(rows)}
  </tbody>
</table>
"""


def _render_tokens_chart(features: list[dict]) -> str:
    """Render a simple CSS bar chart comparing per-feature token usage."""
    token_data = []
    for f in features:
        report = f.get("report")
        if report and "tokens" in report:
            token_data.append((f["id"], f["title"], report["tokens"], report.get("sessions", 0)))

    if not token_data:
        return '<p class="no-data">No token data available.</p>'

    bars = []
    for fid, title, tokens, sessions in token_data:
        color = "#2980b9"
        label = title or fid
        bars.append(f"""
  <div style="margin-bottom:12px">
    <div style="display:flex;justify-content:space-between;margin-bottom:4px;font-size:.82rem">
      <span style="font-weight:500;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;max-width:300px" title="{label}">{label}</span>
      <span style="font-family:monospace;color:#888;margin-left:8px">{tokens} tokens · {sessions} sessions</span>
    </div>
  </div>""")

    return "".join(bars)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_dashboard(
    workspace_base: Path,
    state_dir: Path | None = None,
) -> str:
    """Generate a static HTML dashboard showing feature status and costs.

    Args:
        workspace_base: Root workspace directory (contains features/ subdirectory)
        state_dir: Optional state directory; used to read executor_status.json

    Returns:
        HTML string of the generated dashboard.
    """
    features = _collect_features(workspace_base)
    summary = _compute_summary(features)

    # Optionally read executor status
    executor_status: dict = {}
    if state_dir:
        status_file = state_dir / "executor_status.json"
        if status_file.exists():
            try:
                with open(status_file, encoding="utf-8") as f:
                    executor_status = json.load(f) or {}
            except Exception:
                pass

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Executor status banner (optional)
    executor_banner = ""
    if executor_status:
        exec_status = executor_status.get("status", "unknown")
        exec_agent = executor_status.get("current_agent", "-")
        exec_color = _status_color(exec_status)
        executor_banner = f"""
<section>
  <h2>Executor Status</h2>
  <p>
    <span class="badge" style="background:{exec_color}">{exec_status}</span>
    &nbsp; Current agent: <strong>{exec_agent}</strong>
  </p>
</section>
"""

    features_table = _render_features_table(features)
    tokens_chart = _render_tokens_chart(features)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Agent Executor Dashboard</title>
  <style>
{_CSS}
  </style>
</head>
<body>
<header>
  <div>
    <h1>Agent Executor Dashboard</h1>
    <div class="subtitle">Generated: {generated_at}</div>
  </div>
  <div style="font-size:.85rem;color:#aab">Workspace: {workspace_base}</div>
</header>
<div class="container">
  {_render_summary_cards(summary)}
  {executor_banner}
  <section>
    <h2>Features</h2>
    {features_table}
  </section>
  <section>
    <h2>Tokens by Feature</h2>
    {tokens_chart}
  </section>
</div>
<footer>Agent Executor &mdash; {generated_at}</footer>
</body>
</html>
"""
    return html


def write_dashboard(
    workspace_base: Path,
    output_path: Path,
    state_dir: Path | None = None,
) -> Path:
    """Generate the dashboard HTML and write it to output_path.

    Args:
        workspace_base: Root workspace directory
        output_path: Destination file path for the HTML file
        state_dir: Optional state directory for executor_status.json

    Returns:
        The output_path that was written.
    """
    html = generate_dashboard(workspace_base, state_dir=state_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
