"""Tests for delayed execution parsing and logic."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from nezha.delay import parse_delay, parse_at, format_countdown, DelayCancel


class TestParseDelay:
    def test_seconds(self):
        assert parse_delay("30s") == timedelta(seconds=30)

    def test_minutes(self):
        assert parse_delay("5m") == timedelta(minutes=5)

    def test_hours(self):
        assert parse_delay("2h") == timedelta(hours=2)

    def test_composite_hm(self):
        assert parse_delay("1h30m") == timedelta(hours=1, minutes=30)

    def test_composite_hms(self):
        assert parse_delay("2h15m30s") == timedelta(hours=2, minutes=15, seconds=30)

    def test_composite_ms(self):
        assert parse_delay("5m10s") == timedelta(minutes=5, seconds=10)

    def test_whitespace(self):
        assert parse_delay("  30s  ") == timedelta(seconds=30)

    def test_invalid_no_unit(self):
        with pytest.raises(ValueError):
            parse_delay("30")

    def test_invalid_letters(self):
        with pytest.raises(ValueError):
            parse_delay("30x")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            parse_delay("")

    def test_zero_not_allowed(self):
        with pytest.raises(ValueError):
            parse_delay("0s")


class TestParseAt:
    def test_future_time_today(self):
        fake_now = datetime(2026, 3, 3, 10, 0, 0)
        with patch("nezha.delay.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.combine = datetime.combine
            mock_dt.strptime = datetime.strptime
            result = parse_at("23:00")
            assert result == datetime(2026, 3, 3, 23, 0, 0)

    def test_past_time_rolls_to_tomorrow(self):
        fake_now = datetime(2026, 3, 3, 23, 30, 0)
        with patch("nezha.delay.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.combine = datetime.combine
            mock_dt.strptime = datetime.strptime
            result = parse_at("10:00")
            assert result == datetime(2026, 3, 4, 10, 0, 0)

    def test_with_seconds(self):
        fake_now = datetime(2026, 3, 3, 10, 0, 0)
        with patch("nezha.delay.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.combine = datetime.combine
            mock_dt.strptime = datetime.strptime
            result = parse_at("23:00:30")
            assert result == datetime(2026, 3, 3, 23, 0, 30)

    def test_invalid_format(self):
        with pytest.raises(ValueError):
            parse_at("noon")

    def test_invalid_time(self):
        with pytest.raises(ValueError):
            parse_at("25:00")


class TestFormatCountdown:
    def test_seconds_only(self):
        assert format_countdown(45) == "45s"

    def test_minutes_and_seconds(self):
        assert format_countdown(125) == "2m 5s"

    def test_hours_minutes_seconds(self):
        assert format_countdown(3725) == "1h 2m 5s"

    def test_exact_hour(self):
        assert format_countdown(3600) == "1h 0m 0s"

    def test_zero(self):
        assert format_countdown(0) == "0s"
