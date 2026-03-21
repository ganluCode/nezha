"""Time window guard: only allow execution during specified hours."""

from datetime import datetime

from nezha.config import GuardConfig
from nezha.guards.base import BaseGuard, GuardResult


class TimeWindowGuard(BaseGuard):
    """Only allow execution during a configured time window.

    Useful for limiting agent execution to off-peak hours (e.g., overnight).

    Config params (via GuardConfig.params):
        allow: str — time range like "00:00-08:00"
        timezone: str — timezone name (default: Asia/Shanghai)
    """

    def __init__(self, config: GuardConfig):
        super().__init__(config)
        self._allow_range = config.params.get("allow", "00:00-08:00")
        self._timezone = config.params.get("timezone", "Asia/Shanghai")

        # Parse the range
        parts = self._allow_range.split("-")
        if len(parts) != 2:
            raise ValueError(f"Invalid time window format: '{self._allow_range}'. Expected 'HH:MM-HH:MM'")

        self._start_hour, self._start_min = self._parse_time(parts[0])
        self._end_hour, self._end_min = self._parse_time(parts[1])

    @staticmethod
    def _parse_time(time_str: str) -> tuple[int, int]:
        """Parse 'HH:MM' into (hour, minute)."""
        parts = time_str.strip().split(":")
        if len(parts) != 2:
            raise ValueError(f"Invalid time format: '{time_str}'. Expected 'HH:MM'")
        return int(parts[0]), int(parts[1])

    def _in_window(self, now: datetime) -> bool:
        """Check if the given time falls within the allowed window."""
        current = now.hour * 60 + now.minute
        start = self._start_hour * 60 + self._start_min
        end = self._end_hour * 60 + self._end_min

        if start <= end:
            # Normal range: e.g. 08:00-17:00
            return start <= current < end
        else:
            # Overnight range: e.g. 22:00-06:00
            return current >= start or current < end

    async def check(self) -> GuardResult:
        """Check if current time is within the allowed window."""
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(self._timezone)
            now = datetime.now(tz)
        except (ImportError, KeyError):
            # Fallback to local time if timezone not available
            now = datetime.now()

        if self._in_window(now):
            return GuardResult(passed=True, guard_type="time_window")

        return GuardResult(
            passed=False,
            reason=(
                f"Outside allowed time window ({self._allow_range} {self._timezone}). "
                f"Current time: {now.strftime('%H:%M')}"
            ),
            guard_type="time_window",
        )
