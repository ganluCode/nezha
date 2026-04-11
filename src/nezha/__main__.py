"""Entry point for: python -m nezha"""

import argparse
import json
import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from nezha import __version__
from nezha.i18n import setup_locale, t as _t


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nezha",
        description="Agent Executor - AI Agent execution unit with multi-model support",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"nezha {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run command
    run_parser = subparsers.add_parser("run", help="Run an agent")
    run_parser.add_argument("agent", help="Agent name (e.g. coding-agent)")
    run_parser.add_argument(
        "--workspace", type=str, default=None,
        help="Override workspace directory",
    )
    run_parser.add_argument(
        "--max-iterations", type=int, default=None,
        help="Maximum iterations for multi-round sessions",
    )
    run_parser.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file (default: executor.yaml)",
    )
    run_parser.add_argument(
        "--feature-id", type=str, default=None,
        help="Run a specific feature by ID (feature queue mode)",
    )
    # Backward compatibility alias
    run_parser.add_argument(
        "--task-id", type=str, default=None,
        help=argparse.SUPPRESS,  # hidden — use --feature-id instead
    )
    run_parser.add_argument(
        "--title", type=str, default=None,
        help="Create a new feature with this title and run it immediately",
    )
    run_parser.add_argument(
        "--input", dest="input_files", metavar="FILE", nargs="+", default=None,
        help="Input files to copy into the new feature (requires --title)",
    )
    run_parser.add_argument(
        "--mode", type=str, default=None,
        help="Execution mode (e.g. 'gardening') — selects an alternate prompt from agent config",
    )
    run_parser.add_argument(
        "--skip-planner", action="store_true",
        help="Skip auto-planner even when task_list.json is missing (let agent figure out the plan)",
    )
    run_parser.add_argument(
        "--at", type=str, default=None, metavar="HH:MM",
        help="Schedule execution at a specific time (e.g. 23:00, 14:30:00)",
    )
    run_parser.add_argument(
        "--delay", type=str, default=None, metavar="DURATION",
        help="Delay execution by a duration (e.g. 30s, 5m, 1h, 1h30m)",
    )
    run_parser.add_argument(
        "--background", action="store_true",
        help="Run in background with output redirected to log file",
    )

    # status command
    status_parser = subparsers.add_parser("status", help="Show executor status")
    status_parser.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # history command
    history_parser = subparsers.add_parser("history", help="Show execution history")
    history_parser.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # logs command
    logs_parser = subparsers.add_parser("logs", help="Show execution logs")
    logs_parser.add_argument(
        "-f", "--follow", action="store_true",
        help="Follow log output (like tail -f)",
    )
    logs_parser.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # rework command
    rework_parser = subparsers.add_parser(
        "rework", help="Mark tasks for rework",
    )
    rework_parser.add_argument("agent", help="Agent name (e.g. coding-agent)")
    rework_parser.add_argument(
        "feature_ids",
        help="Task IDs to rework (comma-separated, e.g. F-003 or F-003,F-005)",
    )
    rework_parser.add_argument("note", help="Rework reason/note")
    rework_parser.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # vibe command
    vibe_parser = subparsers.add_parser(
        "vibe", help="Interactive VibeCoding REPL",
    )
    vibe_parser.add_argument("agent", help="Agent name (e.g. coding-agent)")
    vibe_parser.add_argument(
        "--workspace", type=str, default=None,
        help="Override workspace directory",
    )
    vibe_parser.add_argument(
        "--feature-id", type=str, default=None,
        help="Work on a specific feature's workspace (feature queue mode)",
    )
    # Backward compatibility alias
    vibe_parser.add_argument(
        "--task-id", type=str, default=None,
        help=argparse.SUPPRESS,  # hidden — use --feature-id instead
    )
    vibe_parser.add_argument(
        "--context", type=str, default="latest",
        choices=["all", "latest", "none"],
        help="Context loading mode: all=full history, latest=current task (default), none=clean slate",
    )
    vibe_parser.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # plan command (DAG visualization)
    plan_parser = subparsers.add_parser(
        "plan", help="Show task dependency DAG",
    )
    plan_parser.add_argument("agent", help="Agent name (e.g. coding-agent)")
    plan_parser.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # feature subcommand group (new name)
    feature_parser = subparsers.add_parser("feature", help="Manage agent features (big deliverables)")
    feature_sub = feature_parser.add_subparsers(dest="feature_command", help="Feature commands")

    # feature create
    fc_create = feature_sub.add_parser("create", help="Create a new feature")
    fc_create.add_argument(
        "--title", type=str, default="",
        help="Feature title / user story slug (e.g. 'user auth')",
    )
    fc_create.add_argument(
        "--input", dest="input_files", metavar="FILE", nargs="+", default=None,
        help="Input files to copy into the feature's input/ directory",
    )
    fc_create.add_argument(
        "--priority", type=int, default=50, metavar="N",
        help="Scheduling priority 0-100 (default 50, higher runs first)",
    )
    fc_create.add_argument(
        "--branch", type=str, default="",
        help="Git branch to bind to this feature (default: feat/<feature-id>)",
    )
    fc_create.add_argument(
        "--base-branch", type=str, default="",
        help="Git base branch to create from (default: agent config base_branch). "
             "Use to chain features: e.g. --base-branch feat/<prev-feature-id>",
    )
    fc_create.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )
    fc_create.add_argument(
        "--run-planner", action="store_true",
        help="Run planner-agent after creating feature to generate task_list.json",
    )

    # feature list
    fc_list = feature_sub.add_parser("list", help="List features")
    fc_list.add_argument(
        "--agent", type=str, default=None,
        help="Filter by agent (features with task_list.<agent>.json)",
    )
    fc_list.add_argument(
        "--status", type=str, default=None,
        choices=["pending", "running", "paused", "completed", "partial", "failed"],
        help="Filter by status",
    )
    fc_list.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # feature show
    fc_show = feature_sub.add_parser("show", help="Show feature details")
    fc_show.add_argument("feature_id", help="Feature ID")
    fc_show.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # feature approve
    fc_approve = feature_sub.add_parser("approve", help="Approve a step waiting for review")
    fc_approve.add_argument("feature_id", help="Feature ID")
    fc_approve.add_argument("step_id", help="Step ID to approve")
    fc_approve.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # feature reject
    fc_reject = feature_sub.add_parser("reject", help="Reject a step and send back for rework")
    fc_reject.add_argument("feature_id", help="Feature ID")
    fc_reject.add_argument("step_id", help="Step ID to reject")
    fc_reject.add_argument(
        "--note", type=str, default="",
        help="Rejection reason / feedback for the agent",
    )
    fc_reject.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # feature push
    fc_push = feature_sub.add_parser("push", help="Push feature branch to remote (coding agents)")
    fc_push.add_argument("agent", help="Agent name (needed to locate the git target)")
    fc_push.add_argument("feature_id", help="Feature ID")
    fc_push.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # task subcommand group (backward compatibility alias for "feature")
    task_parser = subparsers.add_parser("task", help="Alias for 'feature' — manage agent features")
    task_sub = task_parser.add_subparsers(dest="task_command", help="Feature commands")

    # task create (alias)
    tc_create = task_sub.add_parser("create", help="Create a new feature")
    tc_create.add_argument(
        "--title", type=str, default="",
        help="Feature title / user story slug (e.g. 'user auth')",
    )
    tc_create.add_argument(
        "--input", dest="input_files", metavar="FILE", nargs="+", default=None,
        help="Input files to copy into the feature's input/ directory",
    )
    tc_create.add_argument(
        "--priority", type=int, default=50, metavar="N",
        help="Scheduling priority 0-100 (default 50, higher runs first)",
    )
    tc_create.add_argument(
        "--branch", type=str, default="",
        help="Git branch to bind to this feature (default: feat/<feature-id>)",
    )
    tc_create.add_argument(
        "--base-branch", type=str, default="",
        help="Git base branch to create from (default: agent config base_branch). "
             "Use to chain features: e.g. --base-branch feat/<prev-feature-id>",
    )
    tc_create.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )
    tc_create.add_argument(
        "--run-planner", action="store_true",
        help="Run planner-agent after creating feature to generate task_list.json",
    )

    # task list (alias)
    tc_list = task_sub.add_parser("list", help="List features")
    tc_list.add_argument(
        "--agent", type=str, default=None,
        help="Filter by agent (features with task_list.<agent>.json)",
    )
    tc_list.add_argument(
        "--status", type=str, default=None,
        choices=["pending", "running", "paused", "completed", "partial", "failed"],
        help="Filter by status",
    )
    tc_list.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # task show (alias)
    tc_show = task_sub.add_parser("show", help="Show feature details")
    tc_show.add_argument("task_id", help="Feature ID")
    tc_show.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # task push (alias)
    tc_push = task_sub.add_parser("push", help="Push feature branch to remote (coding agents)")
    tc_push.add_argument("agent", help="Agent name (needed to locate the git target)")
    tc_push.add_argument("task_id", help="Feature ID")
    tc_push.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # init command — scaffold a new nezha project
    init_parser = subparsers.add_parser(
        "init", help="Scaffold a new nezha project directory",
    )
    init_parser.add_argument(
        "project_dir",
        help="Directory to create (e.g. my-project)",
    )

    # code command — launch Claude Code with agent env + context
    code_parser = subparsers.add_parser(
        "code",
        help="Launch Claude Code (claude CLI) with agent model/env pre-configured and context loaded",
    )
    code_parser.add_argument("agent", help="Agent name (e.g. frontend-agent)")
    code_parser.add_argument(
        "--feature-id", type=str, default=None,
        help="Open a specific feature's workspace (default: latest feature)",
    )
    # Backward compatibility alias
    code_parser.add_argument(
        "--task-id", type=str, default=None,
        help=argparse.SUPPRESS,  # hidden — use --feature-id instead
    )
    code_parser.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # integrate command — merge multiple feature branches into a local integration branch
    integrate_parser = subparsers.add_parser(
        "integrate",
        help="Merge multiple feature branches into a local integration branch",
    )
    integrate_parser.add_argument(
        "tasks", nargs="+", metavar="FEATURE",
        help="Feature indices (from 'feature list') or branch names to merge",
    )
    integrate_parser.add_argument(
        "--branch", "-b", type=str, default=None,
        help="Integration branch name (default: integration/<timestamp>)",
    )
    integrate_parser.add_argument(
        "--base", type=str, default=None,
        help="Base branch to create integration from (default: ai-dev or main)",
    )
    integrate_parser.add_argument(
        "--push", action="store_true",
        help="Push integration branch to remote after merge",
    )
    integrate_parser.add_argument(
        "--repo", type=str, default=None,
        help="Path to git repository (default: auto-detect)",
    )
    integrate_parser.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # project subcommand group
    project_parser = subparsers.add_parser("project", help="Manage project-level configuration")
    project_sub = project_parser.add_subparsers(dest="project_command", help="Project commands")

    # project init
    pc_init = project_sub.add_parser("init", help="Initialize project-level shared knowledge directory")
    pc_init.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # agent-context subcommand group
    ac_parser = subparsers.add_parser(
        "agent-context", help="Manage agent cross-task memory (agent-context.md)",
    )
    ac_sub = ac_parser.add_subparsers(dest="ac_command", help="Agent-context commands")

    # agent-context init
    ac_init = ac_sub.add_parser(
        "init", help="Create an empty agent-context.md in the agent's workspace",
    )
    ac_init.add_argument("agent", help="Agent name")
    ac_init.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # agent-context show
    ac_show = ac_sub.add_parser(
        "show", help="Show the contents of agent-context.md",
    )
    ac_show.add_argument("agent", help="Agent name")
    ac_show.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    # dashboard command
    sp_dashboard = subparsers.add_parser("dashboard", help="Generate HTML dashboard")
    sp_dashboard.add_argument("-c", "--config", default="executor.yaml")
    sp_dashboard.add_argument("-o", "--output", default="", help="Output HTML path")
    sp_dashboard.add_argument("--open", action="store_true", help="Open in browser")

    # pause / resume
    subparsers.add_parser("pause", help="Pause the executor")
    subparsers.add_parser("resume", help="Resume the executor")

    # stop command
    stop_parser = subparsers.add_parser("stop", help="Stop a background agent process")
    stop_parser.add_argument(
        "--config", type=str, default="executor.yaml",
        help="Path to executor config file",
    )

    return parser


def _get_state_dir(config_path: str) -> Path:
    """Resolve state directory from executor config."""
    base = Path(config_path).parent.resolve()
    config = base / config_path if not Path(config_path).is_absolute() else Path(config_path)
    if config.exists():
        import yaml
        with open(config) as f:
            raw = yaml.safe_load(f) or {}
        state_dir = raw.get("state_dir", "./state")
    else:
        state_dir = "./state"
    p = Path(state_dir)
    return p if p.is_absolute() else base / p


def _launch_background(args) -> None:
    """Re-launch the current run command as a detached background process."""
    state_dir = _get_state_dir(args.config)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Build the foreground command (same args minus --background)
    cmd = [sys.executable, "-m", "nezha", "run", args.agent]
    if args.config != "executor.yaml":
        cmd += ["--config", args.config]
    if args.workspace:
        cmd += ["--workspace", args.workspace]
    if args.max_iterations:
        cmd += ["--max-iterations", str(args.max_iterations)]
    feature_id = getattr(args, "feature_id", None) or getattr(args, "task_id", None)
    if feature_id:
        cmd += ["--feature-id", feature_id]
    if args.title:
        cmd += ["--title", args.title]
    if args.input_files:
        for f in args.input_files:
            cmd += ["--input", f]
    if args.mode:
        cmd += ["--mode", args.mode]
    if args.skip_planner:
        cmd += ["--skip-planner"]
    if getattr(args, "at", None):
        cmd += ["--at", args.at]
    if getattr(args, "delay", None):
        cmd += ["--delay", args.delay]
    # NOTE: do NOT include --background to avoid infinite recursion

    # Log file
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = state_dir / "logs" / f"bg_{args.agent}_{ts}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Launch detached process
    with open(log_file, "w") as lf:
        proc = subprocess.Popen(
            cmd,
            stdout=lf,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # detach from parent terminal
        )

    # Write PID file
    pid_file = state_dir / "run.pid"
    pid_file.write_text(json.dumps({
        "pid": proc.pid,
        "agent": args.agent,
        "log": str(log_file),
        "started_at": datetime.now().isoformat(),
        "command": " ".join(cmd),
    }, indent=2))

    print(f"[background] Agent '{args.agent}' started in background (PID: {proc.pid})")
    print(f"[background] Log: {log_file}")
    print(f"[background] PID file: {pid_file}")
    print(f"[background] Use 'nezha stop' to terminate")
    print(f"[background] Use 'nezha logs -f' to follow output")


def _stop_background(config_path: str) -> None:
    """Stop a background agent process by reading PID file."""
    state_dir = _get_state_dir(config_path)
    pid_file = state_dir / "run.pid"

    if not pid_file.exists():
        print("[stop] No background process found (no run.pid file)")
        return

    with open(pid_file) as f:
        data = json.load(f)

    pid = data.get("pid")
    agent = data.get("agent", "unknown")

    if not pid:
        print("[stop] Invalid PID file")
        pid_file.unlink(missing_ok=True)
        return

    # Check if process is still running
    try:
        os.kill(pid, 0)  # signal 0 = check existence
    except ProcessLookupError:
        print(f"[stop] Process {pid} ({agent}) is no longer running")
        pid_file.unlink(missing_ok=True)
        return
    except PermissionError:
        pass  # process exists but we can't signal it — try anyway

    # Send SIGTERM for graceful shutdown
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"[stop] Sent SIGTERM to process {pid} ({agent})")
    except OSError as e:
        print(f"[stop] Failed to stop process {pid}: {e}")
        return

    pid_file.unlink(missing_ok=True)
    log_path = data.get("log", "")
    if log_path:
        print(f"[stop] Log file: {log_path}")


def main():
    # Step 1: fast locale init from env var (covers argparse --help output too)
    _env_locale = os.environ.get("AGENT_EXEC_LANG")
    setup_locale(_env_locale or "en")

    parser = build_parser()
    args = parser.parse_args()

    # Step 2: if env var not set, try executor.yaml locale field
    # This lets existing projects set `locale: zh_CN` in executor.yaml without
    # needing to export AGENT_EXEC_LANG in their shell.
    if not _env_locale and getattr(args, "config", None):
        try:
            from nezha.config import load_executor_config
            _cfg = load_executor_config(args.config)
            if _cfg.locale and _cfg.locale != "en":
                setup_locale(_cfg.locale)
        except Exception:
            pass  # config not found or invalid — keep current locale

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "run":
        # --background: re-launch as detached process and exit
        if args.background:
            _launch_background(args)
            sys.exit(0)

        # Support both --feature-id (new) and --task-id (legacy)
        feature_id = getattr(args, "feature_id", None) or getattr(args, "task_id", None)

        # --title: create a feature inline and run it immediately
        if args.title:
            if feature_id:
                print("Error: --title and --feature-id are mutually exclusive.")
                sys.exit(1)
            from nezha.interface.cli import cmd_feature_create_and_return
            feature_id = cmd_feature_create_and_return(
                title=args.title,
                input_files=args.input_files,
                config_path=args.config,
            )
        elif args.input_files:
            print("Error: --input requires --title to create a new feature.")
            sys.exit(1)

        # Delayed execution: --at / --delay
        if getattr(args, "at", None) and getattr(args, "delay", None):
            print(_t('cli.schedule.error_mutual'))
            sys.exit(1)

        if getattr(args, "at", None) or getattr(args, "delay", None):
            from nezha.delay import (
                parse_at, parse_delay, wait_until_ready, DelayCancel,
            )
            try:
                if args.at:
                    target_time = parse_at(args.at)
                    wait_until_ready(target_time=target_time)
                else:
                    delay_delta = parse_delay(args.delay)
                    wait_until_ready(delay_delta=delay_delta)
            except DelayCancel:
                sys.exit(0)
            except ValueError as e:
                print(f"Error: {e}")
                sys.exit(1)

        from nezha.executor import run
        run(
            agent_name=args.agent,
            config_path=args.config,
            cli_workspace=args.workspace,
            max_iterations=args.max_iterations,
            feature_id=feature_id,
            mode=args.mode,
            skip_planner=args.skip_planner,
        )
    elif args.command == "status":
        from nezha.interface.cli import cmd_status
        cmd_status(config_path=args.config)

    elif args.command == "history":
        from nezha.interface.cli import cmd_history
        cmd_history(config_path=args.config)

    elif args.command == "logs":
        from nezha.interface.cli import cmd_logs
        cmd_logs(config_path=args.config, follow=args.follow)

    elif args.command == "rework":
        from nezha.interface.cli import cmd_rework
        cmd_rework(
            agent_name=args.agent,
            feature_ids=args.feature_ids,
            note=args.note,
            config_path=args.config,
        )

    elif args.command == "vibe":
        from nezha.executor import vibe
        # Support both --feature-id (new) and --task-id (legacy)
        feature_id = getattr(args, "feature_id", None) or getattr(args, "task_id", None)
        vibe(
            agent_name=args.agent,
            config_path=args.config,
            cli_workspace=args.workspace,
            feature_id=feature_id,
            context_mode=args.context,
        )

    elif args.command == "plan":
        from nezha.interface.cli import cmd_plan
        cmd_plan(
            agent_name=args.agent,
            config_path=args.config,
        )

    elif args.command == "feature":
        if args.feature_command is None:
            parser.parse_args(["feature", "--help"])
            sys.exit(0)

        if args.feature_command == "create":
            from nezha.interface.cli import cmd_feature_create
            feature_id = cmd_feature_create(
                title=args.title,
                input_files=args.input_files,
                priority=args.priority,
                branch=args.branch,
                base_branch=args.base_branch,
                config_path=args.config,
            )
            # If --run-planner, automatically run planner-agent to generate task_list.json
            if args.run_planner:
                print(f"\n[feature] Running planner-agent for feature {feature_id}...")
                from nezha.executor import run
                run(
                    agent_name="planner-agent",
                    config_path=args.config,
                    feature_id=feature_id,
                )
        elif args.feature_command == "list":
            from nezha.interface.cli import cmd_feature_list
            cmd_feature_list(
                agent_name=args.agent,
                status=args.status,
                config_path=args.config,
            )
        elif args.feature_command == "show":
            from nezha.interface.cli import cmd_feature_show
            cmd_feature_show(
                feature_id=args.feature_id,
                config_path=args.config,
            )
        elif args.feature_command == "approve":
            from nezha.interface.cli import cmd_feature_approve
            cmd_feature_approve(
                feature_id=args.feature_id,
                step_id=args.step_id,
                config_path=args.config,
            )
        elif args.feature_command == "reject":
            from nezha.interface.cli import cmd_feature_reject
            cmd_feature_reject(
                feature_id=args.feature_id,
                step_id=args.step_id,
                note=args.note,
                config_path=args.config,
            )
        elif args.feature_command == "push":
            from nezha.interface.cli import cmd_feature_push
            cmd_feature_push(
                agent_name=args.agent,
                feature_id=args.feature_id,
                config_path=args.config,
            )

    elif args.command == "task":
        # Backward compatibility — "task" is now an alias for "feature"
        if args.task_command is None:
            parser.parse_args(["task", "--help"])
            sys.exit(0)

        if args.task_command == "create":
            from nezha.interface.cli import cmd_feature_create
            feature_id = cmd_feature_create(
                title=args.title,
                input_files=args.input_files,
                priority=args.priority,
                branch=args.branch,
                base_branch=args.base_branch,
                config_path=args.config,
            )
            if args.run_planner:
                print(f"\n[feature] Running planner-agent for feature {feature_id}...")
                from nezha.executor import run
                run(
                    agent_name="planner-agent",
                    config_path=args.config,
                    feature_id=feature_id,
                )
        elif args.task_command == "list":
            from nezha.interface.cli import cmd_feature_list
            cmd_feature_list(
                agent_name=args.agent,
                status=args.status,
                config_path=args.config,
            )
        elif args.task_command == "show":
            from nezha.interface.cli import cmd_feature_show
            cmd_feature_show(
                feature_id=args.task_id,
                config_path=args.config,
            )
        elif args.task_command == "push":
            from nezha.interface.cli import cmd_feature_push
            cmd_feature_push(
                agent_name=args.agent,
                feature_id=args.task_id,
                config_path=args.config,
            )

    elif args.command == "init":
        from nezha.interface.cli import cmd_init
        cmd_init(project_dir=args.project_dir)

    elif args.command == "code":
        from nezha.interface.cli import cmd_code
        # Support both --feature-id (new) and --task-id (legacy)
        feature_id = getattr(args, "feature_id", None) or getattr(args, "task_id", None)
        cmd_code(
            agent_name=args.agent,
            config_path=args.config,
            feature_id=feature_id,
        )

    elif args.command == "integrate":
        from nezha.interface.cli import cmd_integrate
        cmd_integrate(
            tasks=args.tasks,
            branch=args.branch,
            base=args.base,
            push=args.push,
            repo=args.repo,
            config_path=args.config,
        )

    elif args.command == "project":
        if args.project_command is None:
            parser.parse_args(["project", "--help"])
            sys.exit(0)

        if args.project_command == "init":
            from nezha.interface.cli import cmd_project_init
            cmd_project_init(config_path=args.config)

    elif args.command == "agent-context":
        if args.ac_command is None:
            parser.parse_args(["agent-context", "--help"])
            sys.exit(0)

        if args.ac_command == "init":
            from nezha.interface.cli import cmd_agent_context_init
            cmd_agent_context_init(
                agent_name=args.agent,
                config_path=args.config,
            )
        elif args.ac_command == "show":
            from nezha.interface.cli import cmd_agent_context_show
            cmd_agent_context_show(
                agent_name=args.agent,
                config_path=args.config,
            )

    elif args.command == "dashboard":
        from nezha.interface.cli import cmd_dashboard
        cmd_dashboard(config_path=args.config, output=args.output, open_browser=args.open)

    elif args.command == "stop":
        _stop_background(config_path=args.config)

    else:
        # pause, resume — not yet implemented
        print(_t('cli.main.not_implemented', command=args.command))


if __name__ == "__main__":
    main()
