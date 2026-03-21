"""Scheduler implementations: manual, cron, continuous."""

from nezha.scheduler.base import BaseScheduler, SchedulerFactory
from nezha.scheduler.manual import ManualScheduler
from nezha.scheduler.cron import CronScheduler
from nezha.scheduler.continuous import ContinuousScheduler

# Self-register all built-in schedulers
SchedulerFactory.register("manual", ManualScheduler)
SchedulerFactory.register("cron", CronScheduler)
SchedulerFactory.register("continuous", ContinuousScheduler)

__all__ = [
    "BaseScheduler",
    "SchedulerFactory",
    "ManualScheduler",
    "CronScheduler",
    "ContinuousScheduler",
]
