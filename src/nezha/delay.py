"""Delayed execution support: --at and --delay for nezha run."""

import re
import signal
import sys
import time
from datetime import datetime, timedelta

from nezha.i18n import t


class DelayCancel(Exception):
    """Raised when user cancels a wait with Ctrl+C."""


def parse_delay(value: str) -> timedelta:
    """Parse a human-friendly duration string into a timedelta.

    Supported formats:
        30s, 5m, 1h, 1h30m, 2h15m30s, 5m10s

    Raises:
        ValueError: If the format is not recognized.
    """
    pattern = r'^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$'
    match = re.fullmatch(pattern, value.strip())
    if not match or not any(match.groups()):
        raise ValueError(
            t('cli.schedule.invalid_delay', value=value)
        )
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    delta = timedelta(hours=hours, minutes=minutes, seconds=seconds)
    if delta.total_seconds() <= 0:
        raise ValueError(t('cli.schedule.delay_zero'))
    return delta


def parse_at(value: str) -> datetime:
    """Parse an --at time string into a target datetime.

    Supported formats:
        23:00       -> today at 23:00 (or tomorrow if already past)
        23:00:30    -> today at 23:00:30 (or tomorrow if already past)

    Raises:
        ValueError: If the format is not recognized.
    """
    now = datetime.now()
    parsed_time = None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed_time = datetime.strptime(value.strip(), fmt).time()
            break
        except ValueError:
            continue
    if parsed_time is None:
        raise ValueError(t('cli.schedule.invalid_at', value=value))

    target = datetime.combine(now.date(), parsed_time)
    if target <= now:
        target += timedelta(days=1)
    return target


def format_countdown(seconds: float) -> str:
    """Format remaining seconds as a human-readable countdown string."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s"
    else:
        h, remainder = divmod(seconds, 3600)
        m, s = divmod(remainder, 60)
        return f"{h}h {m}m {s}s"


def wait_until_ready(
    target_time: datetime | None = None,
    delay_delta: timedelta | None = None,
) -> None:
    """Block until the target time, showing a countdown.

    Exactly one of target_time or delay_delta must be provided.

    Raises:
        DelayCancel: If user presses Ctrl+C during the wait.
    """
    if delay_delta:
        target_time = datetime.now() + delay_delta

    if target_time is None:
        return

    remaining = (target_time - datetime.now()).total_seconds()
    if remaining <= 0:
        return

    target_str = target_time.strftime("%H:%M:%S")
    print(t('cli.schedule.waiting', time=target_str, countdown=format_countdown(remaining)))
    print(t('cli.schedule.cancel_hint'))
    print()

    _cancelled = False

    def _on_sigint(sig, frame):
        nonlocal _cancelled
        _cancelled = True

    old_handler = signal.signal(signal.SIGINT, _on_sigint)

    try:
        while True:
            remaining = (target_time - datetime.now()).total_seconds()
            if remaining <= 0:
                break
            if _cancelled:
                print(f"\n{t('cli.schedule.cancelled')}")
                raise DelayCancel()

            countdown = format_countdown(remaining)
            sys.stderr.write(f"\r{t('cli.schedule.countdown', remaining=countdown)}   ")
            sys.stderr.flush()

            time.sleep(min(1.0, remaining))

        sys.stderr.write(f"\r{t('cli.schedule.starting')}              \n")
        sys.stderr.flush()
    finally:
        signal.signal(signal.SIGINT, old_handler)
