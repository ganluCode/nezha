"""Scheduler abstract base class and factory."""

from abc import ABC, abstractmethod
from typing import Any

from nezha.config import SchedulerConfig


class BaseScheduler(ABC):
    """Abstract base class for all schedulers.

    A scheduler controls WHEN an agent session is triggered.
    - Manual: one-shot, triggered by CLI command
    - Continuous: loop with configurable delay between rounds
    - Cron: triggered by cron expression on a schedule
    """

    def __init__(self, config: SchedulerConfig):
        self.config = config
        self._running = False

    @property
    def mode(self) -> str:
        """Scheduler mode identifier."""
        return self.config.mode

    @abstractmethod
    async def start(self, execute_fn, on_failure_judge=None) -> None:
        """Start the scheduler.

        Args:
            execute_fn: Async callable that runs one agent execution cycle.
                        Signature: async () -> Any
            on_failure_judge: Optional async callable invoked when a feature
                              fails and failure_strategy is 'ai_judge'.
                              Signature: async () -> bool (True = continue)
        """

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        self._running = False

    @abstractmethod
    async def wait_for_next(self) -> bool:
        """Wait until the next execution should happen.

        Returns:
            True if execution should proceed, False if scheduler is stopping.
        """


class SchedulerFactory:
    """Create a scheduler from config."""

    _registry: dict[str, type[BaseScheduler]] = {}

    @classmethod
    def register(cls, mode: str, scheduler_cls: type[BaseScheduler]):
        cls._registry[mode] = scheduler_cls

    @classmethod
    def create(cls, config: SchedulerConfig) -> BaseScheduler:
        scheduler_cls = cls._registry.get(config.mode)
        if not scheduler_cls:
            available = list(cls._registry.keys())
            raise ValueError(
                f"Unknown scheduler mode: '{config.mode}'. "
                f"Available: {available}"
            )
        return scheduler_cls(config)
