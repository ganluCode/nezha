"""Guard abstract base class, GuardResult, and GuardChain."""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from nezha.config import GuardConfig


@dataclass
class GuardResult:
    """Result of a guard check."""
    passed: bool
    reason: str = ""
    guard_type: str = ""


class BaseGuard(ABC):
    """Abstract base class for all guards.

    A guard runs a pre-check before each agent execution cycle.
    If any guard in the chain fails, the execution is skipped/paused.
    """

    def __init__(self, config: GuardConfig):
        self.config = config
        self.enabled = config.enabled

    @property
    def guard_type(self) -> str:
        return self.config.type

    @abstractmethod
    async def check(self) -> GuardResult:
        """Run the guard check.

        Returns:
            GuardResult with passed=True if execution should proceed.
        """

    async def on_success(self, **kwargs: Any) -> None:
        """Called after a successful agent execution. Override to reset state.

        Keyword arguments may include ``cost_usd`` for cost tracking.
        """

    async def on_failure(self, error: str = "") -> None:
        """Called after a failed agent execution. Override to track failures."""


class GuardChain:
    """Chain of guards: all must pass for execution to proceed.

    Uses ``asyncio.Lock`` to serialise access so that parallel feature
    executions sharing the same chain instance do not corrupt guard state.
    """

    def __init__(self, guards: list[BaseGuard] | None = None):
        self.guards = guards or []
        self._lock = asyncio.Lock()

    def add(self, guard: BaseGuard):
        self.guards.append(guard)

    async def check_all(self) -> GuardResult:
        """Run all enabled guards. Stops at the first failure.

        Returns:
            GuardResult: passed=True if all guards pass.
        """
        async with self._lock:
            for guard in self.guards:
                if not guard.enabled:
                    continue
                result = await guard.check()
                if not result.passed:
                    return result
            return GuardResult(passed=True)

    async def notify_success(self, **kwargs: Any):
        """Notify all guards of a successful execution.

        Keyword arguments (e.g. ``cost_usd``) are forwarded to each guard.
        """
        async with self._lock:
            for guard in self.guards:
                if guard.enabled:
                    await guard.on_success(**kwargs)

    async def notify_failure(self, error: str = ""):
        """Notify all guards of a failed execution."""
        async with self._lock:
            for guard in self.guards:
                if guard.enabled:
                    await guard.on_failure(error)


class GuardFactory:
    """Create guards from config."""

    _registry: dict[str, type[BaseGuard]] = {}

    @classmethod
    def register(cls, guard_type: str, guard_cls: type[BaseGuard]):
        cls._registry[guard_type] = guard_cls

    @classmethod
    def create(cls, config: GuardConfig) -> BaseGuard:
        guard_cls = cls._registry.get(config.type)
        if not guard_cls:
            available = list(cls._registry.keys())
            raise ValueError(
                f"Unknown guard type: '{config.type}'. "
                f"Available: {available}"
            )
        return guard_cls(config)

    @classmethod
    def create_chain(cls, configs: list[GuardConfig]) -> GuardChain:
        """Create a GuardChain from a list of guard configs."""
        chain = GuardChain()
        for config in configs:
            if config.type:  # Skip empty configs
                guard = cls.create(config)
                chain.add(guard)
        return chain
