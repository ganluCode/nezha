"""Tests for ContinuousScheduler adaptive backoff logic."""

import pytest

from nezha.config import SchedulerConfig
from nezha.scheduler.continuous import ContinuousScheduler


def make_scheduler(interval=10, max_backoff=3600, backoff_on_no_task=True):
    config = SchedulerConfig(
        mode="continuous",
        interval=interval,
        max_backoff=max_backoff,
        backoff_on_no_task=backoff_on_no_task,
    )
    return ContinuousScheduler(config)


class TestUpdateInterval:
    def test_success_resets_to_base(self):
        s = make_scheduler(interval=10)
        s._consecutive_failures = 3
        s._current_interval = 80
        s._update_interval("success")
        assert s._current_interval == 10
        assert s._consecutive_failures == 0

    def test_failure_doubles_interval(self):
        s = make_scheduler(interval=10)
        s._update_interval("failure")
        assert s._consecutive_failures == 1
        assert s._current_interval == 20  # 10 * 2^1

    def test_failure_exponential(self):
        s = make_scheduler(interval=10)
        s._update_interval("failure")  # 20
        s._update_interval("failure")  # 40
        s._update_interval("failure")  # 80
        assert s._consecutive_failures == 3
        assert s._current_interval == 80

    def test_failure_capped_at_max_backoff(self):
        s = make_scheduler(interval=10, max_backoff=50)
        s._consecutive_failures = 9
        s._update_interval("failure")
        assert s._current_interval == 50

    def test_no_task_backs_off_when_enabled(self):
        s = make_scheduler(interval=10, backoff_on_no_task=True)
        s._update_interval("no_task")
        assert s._consecutive_failures == 1
        assert s._current_interval == 20

    def test_no_task_no_backoff_when_disabled(self):
        s = make_scheduler(interval=10, backoff_on_no_task=False)
        s._update_interval("no_task")
        assert s._consecutive_failures == 0
        assert s._current_interval == 10

    def test_success_after_failures_resets(self):
        s = make_scheduler(interval=10)
        s._update_interval("failure")
        s._update_interval("failure")
        s._update_interval("success")
        assert s._consecutive_failures == 0
        assert s._current_interval == 10

    def test_unknown_outcome_treated_as_success(self):
        s = make_scheduler(interval=10)
        s._consecutive_failures = 5
        s._update_interval(None)
        assert s._consecutive_failures == 0
        assert s._current_interval == 10

    def test_max_backoff_zero_means_no_cap(self):
        s = make_scheduler(interval=10, max_backoff=0)
        s._consecutive_failures = 19
        s._update_interval("failure")
        # 10 * 2^20 = 10485760, no cap
        assert s._current_interval == 10 * (2 ** 20)

    def test_initial_state(self):
        s = make_scheduler(interval=5)
        assert s.consecutive_failures == 0
        assert s.current_interval == 5
