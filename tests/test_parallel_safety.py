"""Tests for V2.1-F3 parallel execution safety enhancements.

Covers:
1. StateWriter per-feature file isolation
2. FileLogger per-feature log filenames
3. GuardChain asyncio.Lock concurrency safety
4. BalanceCheckGuard shared cost accumulator
"""

import asyncio
import json
import time
from pathlib import Path

import pytest

from nezha.events.types import Event, EventType
from nezha.events.state_writer import StateWriterHandler
from nezha.events.file_logger import FileLoggerHandler
from nezha.guards.base import GuardChain, GuardResult, BaseGuard
from nezha.guards.balance import BalanceCheckGuard, _shared_cost
from nezha.guards.circuit_breaker import CircuitBreakerGuard
from nezha.config import GuardConfig


# ---------------------------------------------------------------------------
# 1. StateWriter per-feature isolation
# ---------------------------------------------------------------------------

class TestStateWriterPerFeature:

    def test_default_path_without_feature_id(self, tmp_path):
        """Without feature_id, writes to the original path."""
        status_path = tmp_path / "executor_status.json"
        handler = StateWriterHandler(status_path, tmp_path)
        assert handler._status_path == status_path

    def test_per_feature_path(self, tmp_path):
        """With feature_id, writes to executor_status_{fid}.json."""
        status_path = tmp_path / "executor_status.json"
        handler = StateWriterHandler(status_path, tmp_path, feature_id="feat-42")
        expected = tmp_path / "executor_status_feat-42.json"
        assert handler._status_path == expected

    @pytest.mark.asyncio
    async def test_parallel_writes_no_conflict(self, tmp_path):
        """Two handlers with different feature_ids write separate files."""
        h1 = StateWriterHandler(tmp_path / "status.json", tmp_path, feature_id="a")
        h2 = StateWriterHandler(tmp_path / "status.json", tmp_path, feature_id="b")

        event = Event.create(
            EventType.EXECUTOR_STARTED,
            agent_name="test-agent",
        )
        await h1.handle(event)
        await h2.handle(event)

        assert (tmp_path / "status_a.json").exists()
        assert (tmp_path / "status_b.json").exists()

        data_a = json.loads((tmp_path / "status_a.json").read_text())
        data_b = json.loads((tmp_path / "status_b.json").read_text())
        assert data_a["status"] == "running"
        assert data_b["status"] == "running"


# ---------------------------------------------------------------------------
# 2. FileLogger per-feature log filenames
# ---------------------------------------------------------------------------

class TestFileLoggerPerFeature:

    @pytest.mark.asyncio
    async def test_default_log_filename(self, tmp_path):
        """Without feature_id, log filename is {agent}_{ts}.log."""
        handler = FileLoggerHandler(tmp_path)
        event = Event.create(
            EventType.SESSION_STARTED,
            agent_name="my-agent",
        )
        await handler.handle(event)
        await handler.close()

        logs = list(tmp_path.glob("*.log"))
        assert len(logs) == 1
        assert logs[0].name.startswith("my-agent_")
        # No feature_id in name
        parts = logs[0].stem.split("_")
        # agent_YYYYMMDD_HHMMSS -> 3 parts
        assert len(parts) == 3

    @pytest.mark.asyncio
    async def test_per_feature_log_filename(self, tmp_path):
        """With feature_id, log filename includes it."""
        handler = FileLoggerHandler(tmp_path, feature_id="feat-99")
        event = Event.create(
            EventType.SESSION_STARTED,
            agent_name="my-agent",
        )
        await handler.handle(event)
        await handler.close()

        logs = list(tmp_path.glob("*.log"))
        assert len(logs) == 1
        assert "feat-99" in logs[0].name
        assert logs[0].name.startswith("my-agent_feat-99_")

    @pytest.mark.asyncio
    async def test_parallel_loggers_separate_files(self, tmp_path):
        """Two loggers with different feature_ids create separate files."""
        h1 = FileLoggerHandler(tmp_path, feature_id="f1")
        h2 = FileLoggerHandler(tmp_path, feature_id="f2")

        event = Event.create(EventType.SESSION_STARTED, agent_name="agent")
        await h1.handle(event)
        await h2.handle(event)
        await h1.close()
        await h2.close()

        logs = list(tmp_path.glob("*.log"))
        assert len(logs) == 2
        names = {l.name for l in logs}
        assert any("f1" in n for n in names)
        assert any("f2" in n for n in names)


# ---------------------------------------------------------------------------
# 3. GuardChain asyncio.Lock concurrency safety
# ---------------------------------------------------------------------------

class _SlowGuard(BaseGuard):
    """A guard that takes time to check, for concurrency testing."""

    def __init__(self, delay: float = 0.05):
        config = GuardConfig(type="slow", enabled=True)
        super().__init__(config)
        self._delay = delay
        self.check_count = 0
        self.success_count = 0

    async def check(self) -> GuardResult:
        self.check_count += 1
        await asyncio.sleep(self._delay)
        return GuardResult(passed=True, guard_type="slow")

    async def on_success(self, **kwargs) -> None:
        self.success_count += 1
        await asyncio.sleep(self._delay)


class TestGuardChainConcurrency:

    @pytest.mark.asyncio
    async def test_lock_serialises_concurrent_checks(self):
        """Concurrent check_all calls are serialised by the lock."""
        guard = _SlowGuard(delay=0.02)
        chain = GuardChain([guard])

        # Run 5 concurrent check_all calls
        results = await asyncio.gather(
            *[chain.check_all() for _ in range(5)]
        )
        assert all(r.passed for r in results)
        assert guard.check_count == 5

    @pytest.mark.asyncio
    async def test_lock_serialises_concurrent_notify(self):
        """Concurrent notify_success calls are serialised."""
        guard = _SlowGuard(delay=0.02)
        chain = GuardChain([guard])

        await asyncio.gather(
            *[chain.notify_success() for _ in range(5)]
        )
        assert guard.success_count == 5

    @pytest.mark.asyncio
    async def test_kwargs_forwarded_to_on_success(self):
        """notify_success forwards kwargs to guard.on_success."""
        config = GuardConfig(type="balance_check", enabled=True,
                             params={"max_cost_usd": 100.0})
        guard = BalanceCheckGuard(config)
        chain = GuardChain([guard])

        # Reset shared tracker
        await _shared_cost.reset()
        await chain.notify_success(cost_usd=1.5)

        total = await _shared_cost.total()
        assert total == pytest.approx(1.5)
        await _shared_cost.reset()


# ---------------------------------------------------------------------------
# 4. BalanceCheckGuard shared cost accumulator
# ---------------------------------------------------------------------------

class TestSharedCostTracker:

    @pytest.mark.asyncio
    async def test_accumulates_cost(self):
        """Cost tracker adds up across calls."""
        await _shared_cost.reset()
        await _shared_cost.add(1.0)
        await _shared_cost.add(2.5)
        total = await _shared_cost.total()
        assert total == pytest.approx(3.5)
        await _shared_cost.reset()

    @pytest.mark.asyncio
    async def test_reset(self):
        await _shared_cost.reset()
        await _shared_cost.add(10.0)
        await _shared_cost.reset()
        total = await _shared_cost.total()
        assert total == 0.0

    @pytest.mark.asyncio
    async def test_concurrent_adds(self):
        """Concurrent adds are safe."""
        await _shared_cost.reset()
        await asyncio.gather(
            *[_shared_cost.add(1.0) for _ in range(100)]
        )
        total = await _shared_cost.total()
        assert total == pytest.approx(100.0)
        await _shared_cost.reset()


class TestBalanceBudgetGuard:

    @pytest.mark.asyncio
    async def test_no_budget_limit_by_default(self):
        """Without max_cost_usd, cost budget check is skipped."""
        config = GuardConfig(type="balance_check", enabled=True)
        guard = BalanceCheckGuard(config)
        result = await guard.check()
        assert result.passed

    @pytest.mark.asyncio
    async def test_budget_blocks_when_exceeded(self):
        """When accumulated cost >= max_cost_usd, check fails."""
        await _shared_cost.reset()
        config = GuardConfig(type="balance_check", enabled=True,
                             params={"max_cost_usd": 5.0})
        guard = BalanceCheckGuard(config)

        # Simulate accumulated cost
        await _shared_cost.add(5.5)
        result = await guard.check()
        assert not result.passed
        assert "budget exceeded" in result.reason.lower()
        await _shared_cost.reset()

    @pytest.mark.asyncio
    async def test_budget_passes_when_under(self):
        """When accumulated cost < max_cost_usd, check passes."""
        await _shared_cost.reset()
        config = GuardConfig(type="balance_check", enabled=True,
                             params={"max_cost_usd": 10.0})
        guard = BalanceCheckGuard(config)

        await _shared_cost.add(3.0)
        result = await guard.check()
        assert result.passed
        await _shared_cost.reset()

    @pytest.mark.asyncio
    async def test_on_success_accumulates_cost(self):
        """on_success with cost_usd adds to shared tracker."""
        await _shared_cost.reset()
        config = GuardConfig(type="balance_check", enabled=True,
                             params={"max_cost_usd": 10.0})
        guard = BalanceCheckGuard(config)

        await guard.on_success(cost_usd=2.0)
        await guard.on_success(cost_usd=3.0)
        total = await _shared_cost.total()
        assert total == pytest.approx(5.0)
        await _shared_cost.reset()

    @pytest.mark.asyncio
    async def test_parallel_features_share_cost(self):
        """Multiple guard instances share the same cost tracker."""
        await _shared_cost.reset()
        config = GuardConfig(type="balance_check", enabled=True,
                             params={"max_cost_usd": 10.0})
        g1 = BalanceCheckGuard(config)
        g2 = BalanceCheckGuard(config)

        await g1.on_success(cost_usd=4.0)
        await g2.on_success(cost_usd=4.0)

        # Both see the total
        total = await _shared_cost.total()
        assert total == pytest.approx(8.0)

        # Still under budget
        r1 = await g1.check()
        assert r1.passed

        # Push over budget
        await g2.on_success(cost_usd=3.0)
        r2 = await g1.check()
        assert not r2.passed
        await _shared_cost.reset()
