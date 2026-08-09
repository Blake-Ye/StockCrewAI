from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from importlib import import_module
from typing import Any

import exchange_calendars as xcals
import pytest
from hypothesis import given, strategies as st

from stockcrewai.models.evidence import (
    EvidenceRecord,
    MarketPriceRecord,
    ValidationStatus,
)
from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ProfileResult,
    ReportingProfile,
    SecurityProfile,
)


_CALENDAR_MODULE: Any | None = None
try:
    _CALENDAR_MODULE = import_module("stockcrewai.quant.calendar")
except ModuleNotFoundError:
    pass


def _calendar_api() -> Any:
    if _CALENDAR_MODULE is None:
        pytest.fail("stockcrewai.quant.calendar 公共 API 尚未实现")
    return _CALENDAR_MODULE


def test_only_xnys_and_xnas_are_supported_without_exchange_fallback() -> None:
    api = _calendar_api()

    assert api.get_exchange_calendar("XNYS").name == "XNYS"
    assert api.get_exchange_calendar("XNAS").is_session(date(2024, 7, 5))

    with pytest.raises(ValueError, match="unsupported exchange"):
        api.get_exchange_calendar("NYSE")


@pytest.mark.parametrize("exchange", ["XNYS", "XNAS"])
def test_normalize_session_handles_weekend_and_exchange_holiday(exchange: str) -> None:
    api = _calendar_api()

    assert api.normalize_to_session(
        date(2024, 7, 4), exchange=exchange, direction="previous"
    ) == date(2024, 7, 3)
    assert api.normalize_to_session(
        date(2024, 7, 4), exchange=exchange, direction="next"
    ) == date(2024, 7, 5)
    assert api.normalize_to_session(
        date(2024, 7, 6), exchange=exchange, direction="previous"
    ) == date(2024, 7, 5)


def test_timezone_boundary_is_converted_before_session_normalization() -> None:
    api = _calendar_api()
    instant = datetime(2024, 7, 5, 0, 30, tzinfo=timezone.utc)

    assert api.normalize_to_session(
        instant, exchange="XNYS", timezone="America/New_York", direction="previous"
    ) == date(2024, 7, 3)
    assert api.normalize_to_session(
        instant, exchange="XNYS", timezone="UTC", direction="previous"
    ) == date(2024, 7, 5)
    assert api.normalize_to_session(
        "2024-07-05T00:30:00Z",
        exchange="XNYS",
        timezone="America/New_York",
        direction="previous",
    ) == date(2024, 7, 3)


def test_invalid_date_timezone_and_direction_have_stable_value_errors() -> None:
    api = _calendar_api()

    with pytest.raises(ValueError, match="timezone-aware"):
        api.normalize_to_session(datetime(2024, 7, 5, 12), exchange="XNYS")
    with pytest.raises(ValueError, match="invalid timezone"):
        api.normalize_to_session(
            date(2024, 7, 5), exchange="XNYS", timezone="Mars/Olympus"
        )
    with pytest.raises(ValueError, match="direction"):
        api.normalize_to_session(date(2024, 7, 5), exchange="XNYS", direction="sideways")
    with pytest.raises(ValueError, match="does not represent a session"):
        api.normalize_to_session(date(2024, 7, 4), exchange="XNYS", direction="none")


@given(
    st.lists(
        st.dates(min_value=date(2024, 1, 1), max_value=date(2026, 12, 31)),
        max_size=30,
    )
)
def test_selected_sessions_are_invariant_to_input_order_and_duplicates(
    values: list[date],
) -> None:
    api = _calendar_api()

    selected = api.select_sessions(values, exchange="XNYS", direction="previous")
    reordered = api.select_sessions(
        list(reversed(values)) + values[:5], exchange="XNYS", direction="previous"
    )

    assert selected == reordered
    assert list(selected) == sorted(set(selected))


@pytest.mark.parametrize("exchange", ["XNYS", "XNAS"])
@given(
    st.lists(
        st.dates(min_value=date(2024, 1, 1), max_value=date(2026, 12, 31)),
        max_size=20,
    )
)
def test_selected_output_dates_are_sessions_of_the_requested_calendar(
    exchange: str, values: list[date]
) -> None:
    api = _calendar_api()
    selected = api.select_sessions(values, exchange=exchange, direction="previous")
    calendar = xcals.get_calendar(exchange)

    assert all(calendar.is_session(value) for value in selected)


@given(
    st.dates(min_value=date(2024, 1, 1), max_value=date(2026, 12, 31)),
)
def test_month_end_sessions_never_exceed_the_requested_cutoff(end: date) -> None:
    api = _calendar_api()
    start = date(2024, 1, 1)

    sessions = api.month_end_sessions(start, end, exchange="XNYS") if end >= start else ()

    assert all(value <= end for value in sessions)
    assert list(sessions) == sorted(set(sessions))


@pytest.mark.parametrize("exchange", ["XNYS", "XNAS"])
def test_month_end_includes_a_valid_half_day_session_deterministically(exchange: str) -> None:
    api = _calendar_api()
    sessions = api.month_end_sessions(
        date(2024, 11, 1), date(2024, 11, 30), exchange=exchange
    )
    calendar = xcals.get_calendar(exchange)

    assert sessions == (date(2024, 11, 29),)
    assert calendar.is_session(sessions[0])
    assert calendar.session_close(sessions[0]).tz_convert("America/New_York").time() == time(
        13, 0
    )


def _point_in_time_module() -> Any:
    try:
        return import_module("stockcrewai.quant.point_in_time")
    except ModuleNotFoundError:
        pytest.skip("point_in_time builder 由 WP05-S05 代理提供")


@given(st.integers(min_value=0, max_value=30))
def test_point_in_time_builder_excludes_future_filing_and_price_cutoffs(
    days_after_epoch: int,
) -> None:
    module = _point_in_time_module()
    builder = getattr(module, "build_point_in_time_snapshot")
    as_of = datetime(2024, 1, 2, 15, 0, tzinfo=timezone.utc) + timedelta(
        days=days_after_epoch
    )
    profile = {
        "cik": "0000320193",
        "ticker": "AAPL",
        "profile": ProfileResult(
            issuer_profile=IssuerProfile.STANDARD_OPERATING,
            security_profile=SecurityProfile.COMMON_STOCK,
            reporting_profile=ReportingProfile.DOMESTIC_US_GAAP,
            coverage_level=CoverageLevel.FULL,
            registry_version="profile-v1",
        ),
    }
    evidence = [
        EvidenceRecord(
            evidence_id="ev_before",
            source_reference="offline://before",
            as_of=as_of - timedelta(hours=1),
            filed_at=(as_of - timedelta(days=1)).date(),
            period_start=date(2023, 1, 1),
            period_end=date(2023, 12, 31),
            unit="USD",
            currency="USD",
            value=1,
            validation_status=ValidationStatus.VALID,
        ),
        EvidenceRecord(
            evidence_id="ev_after",
            source_reference="offline://after",
            as_of=as_of + timedelta(hours=1),
            filed_at=(as_of + timedelta(days=1)).date(),
            period_start=date(2023, 1, 1),
            period_end=date(2023, 12, 31),
            unit="USD",
            currency="USD",
            value=2,
            validation_status=ValidationStatus.VALID,
        ),
    ]
    prices = [
        MarketPriceRecord(
            evidence_id="price_before",
            ticker="AAPL",
            price=1,
            currency="USD",
            price_timestamp=as_of - timedelta(minutes=1),
            source_reference="offline://price-before",
            adjustment_basis="raw",
            validation_status=ValidationStatus.VALID,
        ),
        MarketPriceRecord(
            evidence_id="price_after",
            ticker="AAPL",
            price=2,
            currency="USD",
            price_timestamp=as_of + timedelta(minutes=1),
            source_reference="offline://price-after",
            adjustment_basis="raw",
            validation_status=ValidationStatus.VALID,
        ),
    ]

    snapshot = builder(
        as_of=as_of,
        profile=profile,
        evidence=evidence,
        calculations=[],
        prices=prices,
        builder_version="snapshot-builder-v1",
    )

    assert "ev_after" not in snapshot.available_evidence_ids
    assert "price_after" not in snapshot.available_evidence_ids
