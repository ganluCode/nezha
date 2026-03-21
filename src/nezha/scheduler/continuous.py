"""Continuous scheduler: loop with configurable delay and adaptive backoff."""

import asyncio

from nezha.config import SchedulerConfig
from nezha.scheduler.base import BaseScheduler


class ContinuousScheduler(BaseScheduler):
    """Execute in a loop with a delay between rounds and exponential backoff.

    Backoff rules:
    - "success"  → reset to base interval
    - "failure"  → exponential backoff: interval * 2^consecutive_failures, capped at max_backoff
    - "no_task"  → same as failure if backoff_on_no_task=True, else use base interval
    """

    def __init__(self, config: SchedulerConfig):
        super().__init__(config)
        self._interval = config.interval
        self._max_backoff = config.max_backoff if config.max_backoff > 0 else float("inf")
        self._backoff_on_no_task = config.backoff_on_no_task
        self._failure_strategy = config.failure_strategy
        self._stop_on_empty = config.stop_on_empty
        self._iteration = 0
        self._consecutive_failures = 0
        self._current_interval = config.interval

    @property
    def iteration(self) -> int:
        return self._iteration

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def current_interval(self) -> float:
        return self._current_interval

    def _update_interval(self, outcome: str | None) -> None:
        """Adjust _current_interval based on the outcome of the last execution."""
        if outcome == "success":
            self._consecutive_failures = 0
            self._current_interval = self._interval
        elif outcome == "failure":
            self._consecutive_failures += 1
            self._current_interval = min(
                self._interval * (2 ** self._consecutive_failures),
                self._max_backoff,
            )
        elif outcome == "no_task":
            if self._backoff_on_no_task:
                self._consecutive_failures += 1
                self._current_interval = min(
                    self._interval * (2 ** self._consecutive_failures),
                    self._max_backoff,
                )
            else:
                # Empty queue is expected; don't penalise, keep base interval
                self._consecutive_failures = 0
                self._current_interval = self._interval
        else:
            # Unknown outcome: treat as success (no backoff)
            self._consecutive_failures = 0
            self._current_interval = self._interval

    async def start(self, execute_fn, on_failure_judge=None) -> None:
        """Start the continuous loop."""
        self._running = True
        print(f"[continuous] Starting with interval={self._interval}s"
              f", max_backoff={self._max_backoff}s"
              f", failure_strategy={self._failure_strategy}")

        while self._running:
            self._iteration += 1
            print(f"\n[continuous] === Iteration {self._iteration} ===")
            try:
                outcome = await execute_fn()  # "success" | "failure" | "no_task"
            except Exception as e:
                print(f"[continuous] Iteration {self._iteration} crashed: {e}")
                import traceback
                traceback.print_exc()
                outcome = "failure"
            print(f"[continuous] Iteration {self._iteration} outcome={outcome}")

            if outcome == "failure":
                if self._failure_strategy == "stop":
                    print("[continuous] Stopping: feature failed/partial "
                          "(failure_strategy=stop)")
                    break
                elif self._failure_strategy == "ai_judge":
                    if on_failure_judge:
                        should_continue = await on_failure_judge()
                        if should_continue:
                            print("[continuous] AI judge → CONTINUE")
                        else:
                            print("[continuous] AI judge → STOP")
                            break
                    else:
                        # No judge callback available, fall back to stop
                        print("[continuous] Stopping: no AI judge available, "
                              "falling back to stop")
                        break
                # "continue" → do nothing, keep looping

            if outcome == "no_task" and self._stop_on_empty:
                print("[continuous] Stopping: no pending features remaining "
                      "(stop_on_empty=true)")
                break

            self._update_interval(outcome)

            should_continue = await self.wait_for_next()
            if not should_continue:
                break

        print(f"[continuous] Stopped after {self._iteration} iterations")

    async def wait_for_next(self) -> bool:
        """Wait for the current (possibly backed-off) interval before the next round."""
        if not self._running:
            return False

        interval = self._current_interval
        if self._consecutive_failures > 0:
            print(f"[continuous] Backoff: {interval:.0f}s "
                  f"(consecutive failures: {self._consecutive_failures})")
        else:
            print(f"[continuous] Next round in {interval:.0f}s... (Ctrl+C to stop)")

        elapsed = 0.0
        while elapsed < interval and self._running:
            sleep_time = min(1.0, interval - elapsed)
            await asyncio.sleep(sleep_time)
            elapsed += sleep_time

        return self._running
