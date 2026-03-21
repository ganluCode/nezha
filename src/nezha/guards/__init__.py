"""Guard implementations: circuit breaker, balance check, time window."""

from nezha.guards.base import BaseGuard, GuardChain, GuardResult, GuardFactory
from nezha.guards.circuit_breaker import CircuitBreakerGuard
from nezha.guards.balance import BalanceCheckGuard
from nezha.guards.time_window import TimeWindowGuard

# Self-register all built-in guards
GuardFactory.register("circuit_breaker", CircuitBreakerGuard)
GuardFactory.register("balance_check", BalanceCheckGuard)
GuardFactory.register("time_window", TimeWindowGuard)

__all__ = [
    "BaseGuard",
    "GuardChain",
    "GuardResult",
    "GuardFactory",
    "CircuitBreakerGuard",
    "BalanceCheckGuard",
    "TimeWindowGuard",
]
