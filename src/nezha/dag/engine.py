"""DAG execution engine: dependency-driven loop with deadlock detection."""

from __future__ import annotations

import json
import inspect
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from nezha.dag.graph import (
    TaskDAG,
    Task,
    STATUS_COMPLETED,
    STATUS_REWORK,
    STATUS_READY,
    STATUS_BLOCKED,
    STATUS_SKIPPED,
)
from nezha.dag.report import (
    ExecutionReportData,
    SessionRecord,
    write_exec_plan,
    write_report,
)
from nezha.dag.verifier import (
    VerificationResult,
    verify_task,
    apply_verification_result,
)


@dataclass
class DAGExecutionResult:
    """Summary of DAG execution."""
    total_tasks: int = 0
    completed: int = 0
    rework_fixed: int = 0
    skipped: int = 0
    blocked: int = 0
    sessions_run: int = 0
    total_cost_usd: float = 0.0
    exit_reason: str = ""  # "all_done" | "deadlocked" | "max_iterations" | "stuck" | "error" | "cost_limit" | "session_limit"


class DAGEngine:
    """Drive task execution based on dependency graph.

    The engine:
    1. Loads task_list.json → builds DAG
    2. Picks next task (rework priority → ready)
    3. Writes .dag_context.json for the worker prompt
    4. Calls run_session_fn to execute one session
    5. Reloads task_list.json → updates DAG → repeats
    6. Stops when: all done, deadlocked, or max iterations reached
    """

    def __init__(
        self,
        task_list_path: Path,
        workspace: Path,
        run_session_fn: Callable[..., "SessionResult"],
        delay: int = 3,
        on_dag_event: Callable | None = None,
        verification_command: str | None = None,
        verification_workspace: Path | None = None,
        max_cost_usd: float | None = None,
        max_sessions: int | None = None,
        integration_prompt_path: str | None = None,
        model_map: dict | None = None,
        agent_strategy=None,
        default_strategy=None,
    ):
        """
        Args:
            task_list_path: Path to task_list.json
            workspace: Workspace directory
            run_session_fn: Callable(prompt_path, runtime_override, model_override, env_override)
                            that runs one isolated session.
                            The DAG engine writes .dag_context.json before calling this.
            delay: Seconds between sessions
            on_dag_event: Optional callback(event_type: str, data: dict)
            verification_command: Optional command to run after each session
                                  (e.g. "python -m pytest"). If None, verification
                                  only checks task_list.json agent report.
            verification_workspace: Working directory for verification_command.
                                    Defaults to workspace.
            max_cost_usd: Total cost limit in USD. None = no limit.
            max_sessions: Total session count limit. None = no limit.
            integration_prompt_path: Optional path to integration prompt. If set, runs
                                     one extra integration session after all tasks complete.
            model_map: Optional dict mapping complexity → ModelMapEntry for model resolution.
            agent_strategy: Agent-specific fallback strategy when model_map misses.
            default_strategy: Fallback runtime/model/env strategy when model_map misses.
        """
        self._task_list_path = task_list_path
        self._workspace = workspace
        self._run_session_fn = run_session_fn
        self._delay = delay
        self._on_dag_event = on_dag_event
        self._verification_command = verification_command
        self._verification_workspace = verification_workspace or workspace
        self._max_cost_usd = max_cost_usd
        self._max_sessions = max_sessions
        self._integration_prompt_path = integration_prompt_path
        self._model_map = model_map or {}
        self._agent_strategy = agent_strategy
        self._default_strategy = default_strategy
        self._dag: TaskDAG | None = None

    def _resolve_runtime_model(self, task: Task) -> tuple[str, str, dict[str, str]]:
        """Resolve runtime, model, and env overrides for a task.

        task.model is legacy/deprecated and intentionally ignored. Runtime/model
        routing belongs to model_map so execution strategy stays in config, not
        planner-generated task data.

        Priority: model_map[complexity] > agent_strategy > default_strategy > empty.
        Returns (runtime_override, model_override, env_override).
        """
        if task.model:
            print(
                f"[DAG] WARNING: task.model is deprecated and ignored for {task.id}; "
                "use executor.yaml model_map instead."
            )
        if task.complexity and task.complexity in self._model_map:
            entry = self._model_map[task.complexity]
            runtime = getattr(entry, "runtime", "") if hasattr(entry, "runtime") else ""
            model = getattr(entry, "model", "") if hasattr(entry, "model") else ""
            env = getattr(entry, "env", {}) if hasattr(entry, "env") else {}
            return runtime, model, env
        if self._agent_strategy:
            runtime = getattr(self._agent_strategy, "runtime", "")
            model = getattr(self._agent_strategy, "model", "")
            env = getattr(self._agent_strategy, "env", {})
            if runtime or model or env:
                return runtime, model, env
        if self._default_strategy:
            runtime = getattr(self._default_strategy, "runtime", "")
            model = getattr(self._default_strategy, "model", "")
            env = getattr(self._default_strategy, "env", {})
            if runtime or model or env:
                return runtime, model, env
        return "", "", {}

    def _run_session_with_strategy(
        self,
        prompt_path: str,
        runtime_override: str = "",
        model_override: str = "",
        env_override: dict[str, str] | None = None,
    ):
        """Invoke the session callback, accepting legacy test callbacks."""
        signature = inspect.signature(self._run_session_fn)
        has_varargs = any(
            p.kind == p.VAR_POSITIONAL
            for p in signature.parameters.values()
        )
        positional_params = [
            p for p in signature.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        if has_varargs or len(positional_params) >= 4:
            return self._run_session_fn(
                prompt_path, runtime_override, model_override, env_override or {},
            )

        # Backward-compatible fallback for old callbacks that still accept
        # (prompt_path, model_override, env_override).
        return self._run_session_fn(prompt_path, model_override, env_override or {})

    def _reload_dag(self) -> TaskDAG:
        """Reload task_list.json and rebuild DAG."""
        self._dag = TaskDAG.load(self._task_list_path)
        return self._dag

    def _write_dag_context(self, target: Task):
        """Write .dag_context.json to workspace for the worker prompt."""
        ctx = self._dag.build_dag_context(target)
        ctx_path = self._workspace / ".dag_context.json"
        ctx_path.write_text(
            json.dumps(ctx, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _clear_rework_flag(self, task_id: str) -> None:
        """Clear rework flag in task_list.json after successful re-execution."""
        try:
            with open(self._task_list_path, encoding="utf-8") as f:
                tasks = json.load(f)
            for task in tasks:
                if task["id"] == task_id:
                    task["rework"] = False
                    break
            with open(self._task_list_path, "w", encoding="utf-8") as f:
                json.dump(tasks, f, indent=2, ensure_ascii=False)
                f.write("\n")
        except Exception:
            pass  # non-critical — graph.py fix ensures correct status anyway

    async def _emit(self, event_type: str, **data):
        """Emit a DAG event if callback is set."""
        if self._on_dag_event:
            await self._on_dag_event(event_type, data)

    async def run(
        self,
        worker_prompt_path: str,
        max_iterations: int | None = None,
    ) -> DAGExecutionResult:
        """Run the DAG execution loop.

        Args:
            worker_prompt_path: Prompt template path for worker sessions
            max_iterations: Max sessions to run (None = unlimited)

        Returns:
            DAGExecutionResult with execution summary
        """
        result = DAGExecutionResult()
        report_data = ExecutionReportData(
            start_time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        )
        iteration = 0
        # Stuck detection: track consecutive assignments of the same task
        consecutive_same: dict[str, int] = {}  # task_id -> consecutive count
        last_target_id: str | None = None
        MAX_CONSECUTIVE = 3  # Max times same task assigned without progress

        # Initial load
        dag = self._reload_dag()
        s = dag.summary()
        result.total_tasks = s["total"]

        await self._emit("dag_loaded", summary=s)

        # Print initial DAG
        print(f"\n[DAG] Task Execution DAG")
        print("=" * 60)
        print(dag.format_tree())
        print("=" * 60)
        print(f"  Completed: {s['counts'][STATUS_COMPLETED]}/{s['total']}")
        ready_ids = s["by_status"][STATUS_READY]
        if ready_ids:
            print(f"  Ready: {len(ready_ids)} ({', '.join(ready_ids)})")
        rework_ids = s["by_status"][STATUS_REWORK]
        if rework_ids:
            print(f"  Rework: {len(rework_ids)} ({', '.join(rework_ids)})")
        blocked_ids = s["by_status"][STATUS_BLOCKED]
        if blocked_ids:
            print(f"  Blocked: {len(blocked_ids)}")
        print()

        # Generate initial exec-plan.md
        write_exec_plan(dag, self._workspace)

        while True:
            iteration += 1

            if max_iterations and iteration > max_iterations:
                print(f"\n[DAG] Reached max iterations ({max_iterations})")
                result.exit_reason = "max_iterations"
                break

            # Reload DAG each iteration (Agent may have modified task_list.json)
            dag = self._reload_dag()

            # Check completion
            if dag.is_all_done():
                s = dag.summary()
                result.completed = s["counts"][STATUS_COMPLETED]
                result.skipped = s["counts"][STATUS_SKIPPED]
                result.exit_reason = "all_done"
                await self._emit("dag_all_completed", summary=s)
                print(f"\n[DAG] All tasks completed!")

                # Post-DAG integration session (if configured)
                if self._integration_prompt_path:
                    print(f"\n{'=' * 60}")
                    print(f"  INTEGRATION SESSION: Verify end-to-end wiring")
                    print(f"{'=' * 60}\n")
                    time.sleep(self._delay)
                    int_result = self._run_session_with_strategy(
                        self._integration_prompt_path, "", "", {},
                    )
                    result.sessions_run += 1
                    result.total_cost_usd += int_result.cost_usd or 0
                    _int_tokens = int_result.input_tokens + int_result.output_tokens
                    print(f"\n[DAG] Integration session: {int_result.status} | "
                          f"tokens={_int_tokens} | "
                          f"cost=${int_result.cost_usd or 0:.4f}")
                    report_data.add_session(SessionRecord(
                        session_number=result.sessions_run,
                        feature_id="integration",
                        is_rework=False,
                        duration_ms=int_result.duration_ms,
                        cost_usd=int_result.cost_usd,
                        input_tokens=int_result.input_tokens,
                        output_tokens=int_result.output_tokens,
                        result=int_result.status,
                        error=int_result.error,
                    ))
                break

            # Pick next target: rework first, then ready
            target: Task | None = None

            rework_list = dag.get_rework_tasks()
            if rework_list:
                target = rework_list[0]
                session_type = "REWORK"
                await self._emit(
                    "dag_feature_started",
                    feature_id=target.id,
                    is_rework=True,
                )
            else:
                ready_list = dag.get_ready_tasks()
                if ready_list:
                    target = ready_list[0]
                    session_type = "TASK"
                    await self._emit(
                        "dag_feature_started",
                        feature_id=target.id,
                        is_rework=False,
                    )

            if target is None:
                # Deadlocked — but first check if it's due to stuck detection
                result.exit_reason = "deadlocked"
                await self._emit("dag_deadlocked", blocked=len(dag.get_blocked_tasks()))

                print(f"\n[DAG] DEADLOCKED — no executable tasks")
                print(f"[DAG] Blocked tasks:")
                for bi in dag.get_blocked_tasks():
                    print(f"  {bi.task_id}: blocked by {', '.join(bi.blocked_by)}")
                print(f"\n[DAG] To unblock:")
                print(f"  - Use 'nezha vibe <agent>' to fix blocked tasks")
                print(f"  - Use 'nezha rework <agent> <id> <note>' to mark for rework")
                print(f"  - Then re-run 'nezha run <agent>'")
                break

            # Stuck detection: same task assigned consecutively without progress
            if target.id == last_target_id:
                consecutive_same[target.id] = consecutive_same.get(target.id, 1) + 1
            else:
                consecutive_same[target.id] = 1
                last_target_id = target.id

            if consecutive_same.get(target.id, 0) > MAX_CONSECUTIVE:
                print(f"\n[DAG] STUCK DETECTED — {target.id} assigned "
                      f"{consecutive_same[target.id]} times without progress")
                print(f"[DAG] The agent is not updating task_list.json correctly.")
                print(f"[DAG] Skipping {target.id} to try other tasks...")

                # Mark as skipped in memory to try other tasks
                # Find other ready/rework tasks
                alternatives = [
                    f for f in (rework_list if rework_list else ready_list)
                    if f.id != target.id
                ]
                if alternatives:
                    target = alternatives[0]
                    session_type = "REWORK" if target.rework else "TASK"
                    consecutive_same[target.id] = consecutive_same.get(target.id, 0) + 1
                    last_target_id = target.id
                    print(f"[DAG] Trying alternative: {target.id}")
                else:
                    print(f"[DAG] No alternative tasks available. Stopping.")
                    result.exit_reason = "stuck"
                    break

            # Write DAG context for this session
            self._write_dag_context(target)

            print(f"\n{'=' * 60}")
            print(f"  SESSION {iteration}: {session_type} — {target.id}")
            print(f"  {target.description}")
            if target.rework:
                rn = target.rework_note
                if isinstance(rn, dict):
                    print(f"  Rework attempt: #{rn.get('attempt', '?')}")
                    if rn.get("block_reason"):
                        print(f"  Block reason: {rn['block_reason']}")
                    if rn.get("tried"):
                        print(f"  Previously tried: {str(rn['tried'])[:120]}")
                elif rn:
                    print(f"  Rework note: {rn}")
            print(f"{'=' * 60}\n")

            # Run session (empty overrides mean use agent/executor defaults)
            resolved_runtime, resolved_model, resolved_env = self._resolve_runtime_model(target)
            if resolved_runtime or resolved_model:
                print(
                    f"[DAG] Runtime route for {target.id}: "
                    f"runtime={resolved_runtime or '(agent default)'}, "
                    f"model={resolved_model or '(agent default)'}"
                )
            session_result = self._run_session_with_strategy(
                worker_prompt_path, resolved_runtime, resolved_model, resolved_env,
            )
            result.sessions_run += 1
            result.total_cost_usd += session_result.cost_usd or 0

            # Print session summary
            _tokens = session_result.input_tokens + session_result.output_tokens
            print(f"\n{'-' * 60}")
            print(f"  Session {iteration}: {session_result.status} | "
                  f"turns={session_result.num_turns} | "
                  f"tokens={_tokens} | "
                  f"cost=${session_result.cost_usd or 0:.4f} | "
                  f"time={session_result.duration_ms}ms")
            print(f"{'-' * 60}")

            if session_result.status == "error":
                print(f"[DAG] Session error: {session_result.error}")

            # Rate limited — stop DAG immediately
            if session_result.status == "rate_limited":
                print(f"[DAG] Rate limit detected — stopping DAG execution")
                result.exit_reason = "rate_limited"
                break

            # --- Record session for report ---
            # Determine result after verification (updated below if needed)
            session_record = SessionRecord(
                session_number=iteration,
                feature_id=target.id,
                is_rework=target.rework,
                duration_ms=session_result.duration_ms,
                cost_usd=session_result.cost_usd,
                input_tokens=session_result.input_tokens,
                output_tokens=session_result.output_tokens,
                result=session_result.status,
                error=session_result.error,
            )

            # --- Verification layer ---
            verification = verify_task(
                task_id=target.id,
                task_list_path=self._task_list_path,
                verification_command=self._verification_command,
                workspace=self._verification_workspace,
            )

            await self._emit(
                "dag.feature_verified",
                feature_id=target.id,
                passed=verification.passed,
                agent_reported_pass=verification.agent_reported_pass,
                command_run=verification.command_run,
                command_passed=verification.command_passed,
                command_output_tail=verification.command_output_tail,
                reason=verification.reason,
            )

            if not verification.passed:
                print(f"[DAG] Verification FAILED for {target.id}: {verification.reason}")
                if verification.command_output:
                    # Show first few lines of output
                    lines = verification.command_output.strip().split("\n")
                    preview = "\n".join(lines[:5])
                    if len(lines) > 5:
                        preview += f"\n  ... ({len(lines) - 5} more lines)"
                    print(f"[DAG] Command output:\n  {preview}")
                apply_verification_result(verification, self._task_list_path)
            else:
                print(f"[DAG] Verification passed for {target.id}: {verification.reason}")
                apply_verification_result(verification, self._task_list_path)

            # Check if the target task was completed after session
            dag_after = self._reload_dag()
            status_after = dag_after.get_status(target.id)

            # Update session record based on final status
            if status_after == STATUS_COMPLETED:
                session_record.result = "completed"
            elif not verification.passed:
                session_record.result = "failed"
                if not session_record.error:
                    session_record.error = verification.reason
            report_data.add_session(session_record)

            # Update exec-plan.md with latest DAG state
            write_exec_plan(dag_after, self._workspace)

            if status_after == STATUS_COMPLETED:
                # Reset stuck counter on success
                consecutive_same.pop(target.id, None)
                if target.rework:
                    result.rework_fixed += 1
                    # Clear rework flag in task_list.json to prevent re-scheduling
                    self._clear_rework_flag(target.id)
                await self._emit(
                    "dag_feature_completed",
                    feature_id=target.id,
                    was_rework=target.rework,
                )
                # Show newly unblocked tasks
                downstream = dag_after.get_ready_tasks()
                newly_ready = [
                    f.id for f in downstream
                    if f.id not in [r.id for r in dag.get_ready_tasks()]
                    and f.id != target.id
                ]
                if newly_ready:
                    print(f"[DAG] Unblocked: {', '.join(newly_ready)}")
            elif status_after in (STATUS_REWORK, STATUS_BLOCKED):
                blocked_downstream = dag_after.get_downstream(target.id)
                if blocked_downstream:
                    await self._emit(
                        "dag_feature_blocked",
                        feature_id=target.id,
                        blocked_downstream=blocked_downstream,
                    )
                    print(f"[DAG] {target.id} not completed → "
                          f"blocking {len(blocked_downstream)} downstream tasks")

            # --- Cost circuit breaker ---
            if self._max_cost_usd is not None and result.total_cost_usd >= self._max_cost_usd:
                result.exit_reason = "cost_limit"
                s_limit = dag_after.summary()
                print(f"\n[DAG] COST LIMIT REACHED")
                print(f"  Total cost: ${result.total_cost_usd:.4f} "
                      f"(limit: ${self._max_cost_usd:.4f})")
                print(f"  Sessions run: {result.sessions_run}")
                print(f"  Completed: {s_limit['counts'][STATUS_COMPLETED]}/{s_limit['total']}")
                ready_r = s_limit["by_status"][STATUS_READY]
                rework_r = s_limit["by_status"][STATUS_REWORK]
                if ready_r:
                    print(f"  Ready: {', '.join(ready_r)}")
                if rework_r:
                    print(f"  Rework: {', '.join(rework_r)}")
                await self._emit(
                    "dag_cost_limit",
                    total_cost_usd=result.total_cost_usd,
                    max_cost_usd=self._max_cost_usd,
                    sessions_run=result.sessions_run,
                )
                break

            # --- Session count circuit breaker ---
            if self._max_sessions is not None and result.sessions_run >= self._max_sessions:
                result.exit_reason = "session_limit"
                s_limit = dag_after.summary()
                print(f"\n[DAG] SESSION LIMIT REACHED")
                print(f"  Sessions run: {result.sessions_run} "
                      f"(limit: {self._max_sessions})")
                print(f"  Total cost: ${result.total_cost_usd:.4f}")
                print(f"  Completed: {s_limit['counts'][STATUS_COMPLETED]}/{s_limit['total']}")
                ready_r = s_limit["by_status"][STATUS_READY]
                rework_r = s_limit["by_status"][STATUS_REWORK]
                if ready_r:
                    print(f"  Ready: {', '.join(ready_r)}")
                if rework_r:
                    print(f"  Rework: {', '.join(rework_r)}")
                await self._emit(
                    "dag_session_limit",
                    sessions_run=result.sessions_run,
                    max_sessions=self._max_sessions,
                    total_cost_usd=result.total_cost_usd,
                )
                break

            # Delay before next session
            if max_iterations is None or iteration < max_iterations:
                s_after = dag_after.summary()
                remaining = (
                    s_after["counts"][STATUS_READY]
                    + s_after["counts"][STATUS_REWORK]
                )
                if remaining > 0:
                    print(f"\n[DAG] Next session in {self._delay}s... "
                          f"({remaining} tasks remaining)")
                    time.sleep(self._delay)

        # Final summary
        final_dag = self._reload_dag()
        s_final = final_dag.summary()
        result.completed = s_final["counts"][STATUS_COMPLETED]
        result.blocked = s_final["counts"][STATUS_BLOCKED]
        result.skipped = s_final["counts"][STATUS_SKIPPED]

        # Generate execution report
        report_data.end_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        report_path = write_report(
            report_data=report_data,
            dag=final_dag,
            exit_reason=result.exit_reason,
            workspace=self._workspace,
        )
        print(f"\n[DAG] Execution report: {report_path}")

        return result
