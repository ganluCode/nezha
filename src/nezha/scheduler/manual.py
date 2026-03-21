"""Manual scheduler: one-shot execution triggered by CLI."""

from nezha.config import SchedulerConfig
from nezha.scheduler.base import BaseScheduler


class ManualScheduler(BaseScheduler):
    """Execute once and exit. This is the default for `nezha run`."""

    def __init__(self, config: SchedulerConfig):
        super().__init__(config)
        self._executed = False

    async def start(self, execute_fn, on_failure_judge=None) -> None:
        """Run execute_fn exactly once."""
        self._running = True
        try:
            await execute_fn()
        finally:
            self._running = False
            self._executed = True

    async def wait_for_next(self) -> bool:
        """Manual mode never has a 'next' — returns False immediately."""
        return False
