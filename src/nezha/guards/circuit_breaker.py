"""Circuit breaker guard: pause after consecutive failures, recover after cooldown."""

import time

from nezha.config import GuardConfig
from nezha.guards.base import BaseGuard, GuardResult


class CircuitBreakerGuard(BaseGuard):
    """Stop execution after N consecutive failures, recover after cooldown.

    Config params (via GuardConfig.params):
        max_consecutive_errors: int (default 3)
        cooldown_seconds: int (default 600 = 10 minutes)
    """

    def __init__(self, config: GuardConfig):
        super().__init__(config)
        self._max_errors = config.params.get("max_consecutive_errors", 3)
        self._cooldown = config.params.get("cooldown_seconds", 600)
        self._consecutive_errors = 0
        self._tripped_at: float | None = None  # timestamp when circuit opened

    @property
    def is_open(self) -> bool:
        """True if circuit breaker is tripped (blocking execution)."""
        return self._tripped_at is not None

    @property
    def consecutive_errors(self) -> int:
        return self._consecutive_errors

    async def check(self) -> GuardResult:
        """Check if execution should proceed.

        Returns passed=False if circuit is open and cooldown hasn't elapsed.
        """
        if not self.is_open:
            return GuardResult(passed=True, guard_type="circuit_breaker")

        # Check if cooldown has elapsed
        elapsed = time.time() - self._tripped_at
        if elapsed >= self._cooldown:
            print(f"[circuit_breaker] Cooldown elapsed ({self._cooldown}s), resetting")
            self._reset()
            return GuardResult(passed=True, guard_type="circuit_breaker")

        remaining = self._cooldown - elapsed
        return GuardResult(
            passed=False,
            reason=(
                f"Circuit breaker open: {self._consecutive_errors} consecutive errors. "
                f"Cooldown remaining: {remaining:.0f}s"
            ),
            guard_type="circuit_breaker",
        )

    async def on_success(self, **kwargs) -> None:
        """Reset error counter on successful execution."""
        if self._consecutive_errors > 0:
            print(f"[circuit_breaker] Success — resetting error count (was {self._consecutive_errors})")
        self._reset()

    async def on_failure(self, error: str = "") -> None:
        """Increment error counter, trip if threshold reached."""
        self._consecutive_errors += 1
        print(f"[circuit_breaker] Failure #{self._consecutive_errors}/{self._max_errors}")

        if self._consecutive_errors >= self._max_errors:
            self._tripped_at = time.time()
            print(
                f"[circuit_breaker] TRIPPED — {self._consecutive_errors} consecutive errors. "
                f"Cooldown: {self._cooldown}s"
            )

    def _reset(self):
        self._consecutive_errors = 0
        self._tripped_at = None
