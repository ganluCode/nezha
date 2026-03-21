"""Balance check guard: pause if API credit balance is too low."""

import asyncio
import time

from nezha.config import GuardConfig
from nezha.guards.base import BaseGuard, GuardResult


class _SharedCostTracker:
    """Process-level cost accumulator shared across parallel features.

    All ``BalanceCheckGuard`` instances that set ``max_cost_usd`` will
    register their spend here.  The tracker is safe for concurrent
    ``asyncio`` coroutines (single event-loop).
    """

    def __init__(self):
        self._total: float = 0.0
        self._lock = asyncio.Lock()

    async def add(self, amount: float) -> float:
        """Add *amount* and return updated total."""
        async with self._lock:
            self._total += amount
            return self._total

    async def total(self) -> float:
        async with self._lock:
            return self._total

    async def reset(self) -> None:
        async with self._lock:
            self._total = 0.0


# Singleton — shared across all guard instances in the process.
_shared_cost = _SharedCostTracker()


class BalanceCheckGuard(BaseGuard):
    """Check API credit balance before execution.

    Currently a placeholder — Anthropic doesn't expose a public balance API.
    When available, this guard will query the balance and block if too low.

    Config params (via GuardConfig.params):
        min_balance_usd: float (default 5.0)
        check_interval: int (default 300 = 5 minutes)
        max_cost_usd:   float (default 0 = disabled)
            When > 0, blocks execution once accumulated session cost
            across all parallel features exceeds this budget.
    """

    def __init__(self, config: GuardConfig):
        super().__init__(config)
        self._min_balance = config.params.get("min_balance_usd", 5.0)
        self._check_interval = config.params.get("check_interval", 300)
        self._max_cost = config.params.get("max_cost_usd", 0.0)
        self._last_check: float = 0
        self._last_balance: float | None = None

    async def check(self) -> GuardResult:
        """Check if balance is sufficient.

        Uses cached value if within check_interval to avoid excessive API calls.
        Degrades gracefully (passes) if balance check fails.
        Also checks accumulated cost against ``max_cost_usd`` budget.
        """
        # Budget check (shared across all parallel features)
        if self._max_cost > 0:
            total = await _shared_cost.total()
            if total >= self._max_cost:
                return GuardResult(
                    passed=False,
                    reason=(
                        f"Cost budget exceeded: ${total:.4f} >= "
                        f"${self._max_cost:.2f}"
                    ),
                    guard_type="balance_check",
                )

        now = time.time()

        # Use cached result if recent
        if (now - self._last_check) < self._check_interval and self._last_balance is not None:
            if self._last_balance < self._min_balance:
                return GuardResult(
                    passed=False,
                    reason=f"Balance ${self._last_balance:.2f} below minimum ${self._min_balance:.2f}",
                    guard_type="balance_check",
                )
            return GuardResult(passed=True, guard_type="balance_check")

        # Try to fetch balance
        balance = await self._fetch_balance()
        self._last_check = now

        if balance is None:
            # Degrade gracefully: if we can't check, allow execution
            print("[balance] Could not check balance — degrading to pass")
            return GuardResult(passed=True, guard_type="balance_check")

        self._last_balance = balance
        if balance < self._min_balance:
            return GuardResult(
                passed=False,
                reason=f"Balance ${balance:.2f} below minimum ${self._min_balance:.2f}",
                guard_type="balance_check",
            )

        return GuardResult(passed=True, guard_type="balance_check")

    async def on_success(self, **kwargs) -> None:
        """Record session cost in the shared accumulator."""
        cost_usd = kwargs.get("cost_usd", 0.0)
        if cost_usd > 0:
            total = await _shared_cost.add(cost_usd)
            if self._max_cost > 0:
                print(f"[balance] Cost so far: ${total:.4f} / ${self._max_cost:.2f}")

    async def _fetch_balance(self) -> float | None:
        """Fetch the current API credit balance.

        TODO: Implement when Anthropic provides a balance API.
        For now, returns None (degrades to pass).
        """
        # Placeholder: no public API to check Anthropic balance yet.
        # When available, implement:
        #   import httpx
        #   async with httpx.AsyncClient() as client:
        #       resp = await client.get("https://api.anthropic.com/v1/billing/balance",
        #                               headers={"x-api-key": api_key})
        #       return resp.json()["balance_usd"]
        return None
