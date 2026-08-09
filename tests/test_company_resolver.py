from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stockcrewai.models.request import CompanyIdentity, ParsedResearchRequest
from stockcrewai.services.company_resolver import resolve_company


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "profiles" / "company_resolver.json"


@pytest.fixture(scope="module")
def resolver_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _case(fixture: dict[str, Any], name: str) -> dict[str, Any]:
    return fixture["cases"][name]


def _resolve(case: dict[str, Any]) -> CompanyIdentity:
    parsed_request = ParsedResearchRequest.model_validate(case["parsed_request"])
    sec_candidates = [
        CompanyIdentity.model_validate(candidate) for candidate in case["sec_candidates"]
    ]
    result = resolve_company(
        parsed_request,
        sec_candidates,
        case["security_metadata"],
    )
    assert isinstance(result, CompanyIdentity)
    return result


def _aapl_inputs(
    resolver_fixture: dict[str, Any],
) -> tuple[ParsedResearchRequest, list[CompanyIdentity], dict[str, Any]]:
    case = _case(resolver_fixture, "aapl_exact")
    return (
        ParsedResearchRequest.model_validate(case["parsed_request"]),
        [CompanyIdentity.model_validate(case["sec_candidates"][0])],
        dict(case["security_metadata"]),
    )


def test_aapl_exact_match_prefers_authoritative_sec_candidate_fields(
    resolver_fixture: dict[str, Any],
) -> None:
    result = _resolve(_case(resolver_fixture, "aapl_exact"))

    assert result.status == "resolved"
    assert result.reason_code == "sec_exact_match"
    assert result.company_name == "Apple Inc."
    assert result.ticker == "AAPL"
    assert result.cik == "0000320193"
    assert result.exchange == "NASDAQ"
    assert result.security_type == "common_stock"
    assert result.source_reference == "fixture:sec/aapl"


@pytest.mark.parametrize(
    "case_name",
    [
        "parsed_ticker_conflict",
        "multiple_candidates",
        "metadata_signal_conflict",
    ],
)
def test_identity_conflicts_are_ambiguous_without_confidence_selection(
    resolver_fixture: dict[str, Any],
    case_name: str,
) -> None:
    case = _case(resolver_fixture, case_name)
    result = _resolve(case)

    assert result.status == "ambiguous"
    assert result.reason_code == "identity_candidate_conflict"


@pytest.mark.parametrize("case_name", ["unsupported_etf", "unsupported_fund"])
def test_unsupported_security_metadata_is_not_resolved_as_common_stock(
    resolver_fixture: dict[str, Any],
    case_name: str,
) -> None:
    case = _case(resolver_fixture, case_name)
    result = _resolve(case)

    assert result.status == "unsupported"
    assert result.reason_code == "unsupported_security"
    for field, expected in case["expected"].items():
        assert getattr(result, field) == expected


@pytest.mark.parametrize("case_name", ["source_unavailable", "source_error", "no_candidate"])
def test_unavailable_identity_preserves_only_known_facts(
    resolver_fixture: dict[str, Any],
    case_name: str,
) -> None:
    case = _case(resolver_fixture, case_name)
    result = _resolve(case)

    assert result.status == "unavailable"
    assert result.reason_code == case["expected"]["reason_code"]
    for field, expected in case["expected"].items():
        assert getattr(result, field) == expected
    assert result.cik not in {"unknown", "unavailable"}
    assert result.company_name not in {"unknown", "unavailable"}


def test_cik_leading_zero_difference_matches_sec_candidate(
    resolver_fixture: dict[str, Any],
) -> None:
    case = _case(resolver_fixture, "aapl_exact")
    metadata = {**case["security_metadata"], "cik": "320193"}
    result = resolve_company(
        ParsedResearchRequest.model_validate(case["parsed_request"]),
        [CompanyIdentity.model_validate(case["sec_candidates"][0])],
        metadata,
    )

    assert result.status == "resolved"
    assert result.cik == "0000320193"


def test_confidence_does_not_change_status_or_selected_identity(
    resolver_fixture: dict[str, Any],
) -> None:
    case = _case(resolver_fixture, "aapl_exact")
    low_confidence = {
        **case["parsed_request"],
        "confidence": 0.01,
    }
    high_confidence = {
        **case["parsed_request"],
        "confidence": 0.99,
    }
    candidate = CompanyIdentity.model_validate(case["sec_candidates"][0])

    low_result = resolve_company(
        ParsedResearchRequest.model_validate(low_confidence),
        [candidate],
        case["security_metadata"],
    )
    high_result = resolve_company(
        ParsedResearchRequest.model_validate(high_confidence),
        [candidate],
        case["security_metadata"],
    )

    assert low_result.model_dump() == high_result.model_dump()


@pytest.mark.parametrize(
    ("candidate_status", "expected_status", "expected_reason"),
    [
        ("unsupported", "unsupported", "unsupported_security"),
        ("ambiguous", "ambiguous", "identity_candidate_conflict"),
        ("unavailable", "unavailable", "identity_source_unavailable"),
    ],
)
def test_candidate_status_controls_resolution_outcome(
    resolver_fixture: dict[str, Any],
    candidate_status: str,
    expected_status: str,
    expected_reason: str,
) -> None:
    parsed_request, candidates, metadata = _aapl_inputs(resolver_fixture)
    candidates[0] = CompanyIdentity.model_validate(
        {**candidates[0].model_dump(), "status": candidate_status}
    )

    result = resolve_company(parsed_request, candidates, metadata)

    assert result.status == expected_status
    assert result.reason_code == expected_reason


@pytest.mark.parametrize(
    ("field", "conflicting_value"),
    [("exchange", "NYSE"), ("security_type", "preferred_stock")],
)
def test_every_metadata_identity_signal_must_match_candidate(
    resolver_fixture: dict[str, Any],
    field: str,
    conflicting_value: str,
) -> None:
    parsed_request, candidates, metadata = _aapl_inputs(resolver_fixture)
    metadata[field] = conflicting_value

    result = resolve_company(parsed_request, candidates, metadata)

    assert result.status == "ambiguous"
    assert result.reason_code == "identity_candidate_conflict"


@pytest.mark.parametrize("candidate_count", [0, None])
def test_candidate_count_zero_or_missing_blocks_resolution(
    resolver_fixture: dict[str, Any],
    candidate_count: int | None,
) -> None:
    parsed_request, candidates, metadata = _aapl_inputs(resolver_fixture)
    if candidate_count is None:
        metadata.pop("candidate_count")
    else:
        metadata["candidate_count"] = candidate_count

    result = resolve_company(parsed_request, candidates, metadata)

    assert result.status == "ambiguous"
    assert result.reason_code == "identity_candidate_conflict"


def test_candidate_count_greater_than_candidate_list_blocks_resolution(
    resolver_fixture: dict[str, Any],
) -> None:
    parsed_request, candidates, metadata = _aapl_inputs(resolver_fixture)
    metadata["candidate_count"] = 2

    result = resolve_company(parsed_request, candidates, metadata)

    assert result.status == "ambiguous"
    assert result.reason_code == "identity_candidate_conflict"


def test_candidate_count_less_than_candidate_list_blocks_resolution(
    resolver_fixture: dict[str, Any],
) -> None:
    parsed_request, candidates, metadata = _aapl_inputs(resolver_fixture)
    candidates.append(
        CompanyIdentity(
            company_name="Microsoft Corporation",
            ticker="MSFT",
            cik="0000789019",
            exchange="NASDAQ",
            security_type="common_stock",
            source_reference="fixture:sec/msft",
            status="resolved",
            reason_code="fixture_candidate",
        )
    )
    metadata["candidate_count"] = 1

    result = resolve_company(parsed_request, candidates, metadata)

    assert result.status == "ambiguous"
    assert result.reason_code == "identity_candidate_conflict"


def test_missing_metadata_identity_signal_blocks_resolution(
    resolver_fixture: dict[str, Any],
) -> None:
    parsed_request, candidates, metadata = _aapl_inputs(resolver_fixture)
    for field in ("cik", "ticker", "company_name", "exchange", "security_type"):
        metadata[field] = None

    result = resolve_company(parsed_request, candidates, metadata)

    assert result.status == "ambiguous"
    assert result.reason_code == "identity_candidate_conflict"


def test_resolver_results_have_only_company_identity_fields(
    resolver_fixture: dict[str, Any],
) -> None:
    expected_fields = {
        "company_name",
        "ticker",
        "cik",
        "exchange",
        "security_type",
        "source_reference",
        "status",
        "reason_code",
    }

    for case in resolver_fixture["cases"].values():
        assert set(_resolve(case).model_dump()) == expected_fields
