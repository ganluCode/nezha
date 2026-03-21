"""Cron scheduler: trigger execution based on cron expressions."""

import asyncio
from datetime import datetime, timezone

from nezha.config import SchedulerConfig
from nezha.scheduler.base import BaseScheduler

try:
    from croniter import croniter
except ImportError:
    croniter = None


class CronScheduler(BaseScheduler):
    """Execute on a cron schedule (e.g., '0 2 * * *' = every day at 2am)."""

    def __init__(self, config: SchedulerConfig):
        super().__init__(config)
        if not croniter:
            raise ImportError(
                "croniter is required for cron scheduling. "
                "Install with: pip install croniter"
            )
        if not config.cron:
            raise ValueError("Cron scheduler requires a 'cron' expression in config")

        self._cron_expr = config.cron
        # Validate the expression
        if not croniter.is_valid(self._cron_expr):
            raise ValueError(f"Invalid cron expression: '{self._cron_expr}'")

    async def start(self, execute_fn, on_failure_judge=None) -> None:
        """Start the cron loop: wait for next trigger time, then execute."""
        self._running = True
        print(f"[cron] Started with expression: {self._cron_expr}")
        print(f"[cron] Timezone: {self.config.timezone}")

        while self._running:
            should_run = await self.wait_for_next()
            if not should_run:
                break
            print(f"[cron] Triggering execution at {datetime.now()}")
            await execute_fn()

    async def wait_for_next(self) -> bool:
        """Sleep until the next cron trigger time.

        Returns:
            True if we should execute, False if scheduler was stopped.
        """
        now = datetime.now()
        cron = croniter(self._cron_expr, now)
        next_time = cron.get_next(datetime)
        wait_seconds = (next_time - now).total_seconds()

        print(f"[cron] Next execution at {next_time} (in {wait_seconds:.0f}s)")

        # Sleep in 1-second intervals so we can check _running
        elapsed = 0.0
        while elapsed < wait_seconds and self._running:
            sleep_time = min(1.0, wait_seconds - elapsed)
            await asyncio.sleep(sleep_time)
            elapsed += sleep_time

        return self._running
