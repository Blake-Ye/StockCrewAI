from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import exchange_calendars as xcals
from exchange_calendars.errors import NoSessionsError


DateInput = date | datetime | str
SessionDirection = Literal["next", "previous", "none"]

_SUPPORTED_EXCHANGES = frozenset({"XNYS", "XNAS"})
_DIRECTIONS = frozenset({"next", "previous", "none"})


def get_exchange_calendar(exchange: str):
    """Return the requested US exchange calendar without exchange fallback."""
    if not isinstance(exchange, str) or exchange not in _SUPPORTED_EXCHANGES:
        raise ValueError("unsupported exchange: expected XNYS or XNAS")
    return xcals.get_calendar(exchange)


def _resolve_timezone(calendar: object, timezone: str | None) -> object:
    if timezone is None:
        return getattr(calendar, "tz")
    if not isinstance(timezone, str) or not timezone.strip():
        raise ValueError("invalid timezone")
    try:
        return ZoneInfo(timezone)
    except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("invalid timezone") from exc


def _local_date(value: DateInput, timezone: object) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("date must be timezone-aware")
        return value.astimezone(timezone).date()  # type: ignore[arg-type]
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid date")

    text = value.strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid date") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("date must be timezone-aware")
    return parsed.astimezone(timezone).date()  # type: ignore[arg-type]


def _calendar_for_range(exchange: str, start: date, end: date):
    calendar = get_exchange_calendar(exchange)
    if start >= calendar.first_session.date() and end <= calendar.last_session.date():
        return calendar
    try:
        return xcals.get_calendar(
            exchange,
            start=start - timedelta(days=7),
            end=end + timedelta(days=7),
        )
    except (NoSessionsError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("date range is outside the exchange calendar") from exc


def normalize_to_session(
    value: DateInput,
    *,
    exchange: str = "XNYS",
    timezone: str | None = None,
    direction: SessionDirection = "previous",
) -> date:
    """Normalize an input date/time to an exchange session label.

    Aware datetimes are converted to ``timezone`` (or the calendar timezone)
    before their local date is selected. Non-session dates use the requested
    direction; ``none`` keeps exchange-calendars' strict behavior.
    """
    if direction not in _DIRECTIONS:
        raise ValueError("invalid direction: expected next, previous, or none")
    calendar = get_exchange_calendar(exchange)
    local_timezone = _resolve_timezone(calendar, timezone)
    local = _local_date(value, local_timezone)
    calendar = _calendar_for_range(exchange, local, local)
    try:
        session = calendar.date_to_session(local.isoformat(), direction=direction)
    except (IndexError, NoSessionsError, TypeError, ValueError) as exc:
        raise ValueError(
            f"date does not represent a session for {exchange}: {local.isoformat()}"
        ) from exc
    return session.date()


def select_sessions(
    values: Iterable[DateInput],
    *,
    exchange: str = "XNYS",
    timezone: str | None = None,
    direction: SessionDirection = "previous",
) -> tuple[date, ...]:
    """Return sorted, unique session labels for the supplied inputs."""
    try:
        inputs = iter(values)
    except TypeError as exc:
        raise ValueError("dates must be iterable") from exc
    selected = {
        normalize_to_session(
            value,
            exchange=exchange,
            timezone=timezone,
            direction=direction,
        )
        for value in inputs
    }
    return tuple(sorted(selected))


def month_end_sessions(
    start: DateInput,
    end: DateInput,
    *,
    exchange: str = "XNYS",
    timezone: str | None = None,
) -> tuple[date, ...]:
    """Return the last available session in each month up to ``end``."""
    calendar = get_exchange_calendar(exchange)
    local_timezone = _resolve_timezone(calendar, timezone)
    start_date = _local_date(start, local_timezone)
    end_date = _local_date(end, local_timezone)
    if start_date > end_date:
        raise ValueError("start date must not be after end date")
    calendar = _calendar_for_range(exchange, start_date, end_date)
    try:
        sessions = calendar.sessions_in_range(start_date.isoformat(), end_date.isoformat())
    except (NoSessionsError, TypeError, ValueError) as exc:
        raise ValueError("date range is outside the exchange calendar") from exc

    last_by_month: dict[tuple[int, int], date] = {}
    for session in sessions:
        session_date = session.date()
        last_by_month[session_date.year, session_date.month] = session_date
    return tuple(last_by_month[key] for key in sorted(last_by_month))


generate_rebalance_dates = month_end_sessions


__all__ = [
    "DateInput",
    "SessionDirection",
    "generate_rebalance_dates",
    "get_exchange_calendar",
    "month_end_sessions",
    "normalize_to_session",
    "select_sessions",
]
