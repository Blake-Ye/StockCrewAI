from __future__ import annotations

import json
from enum import Enum

import pytest
from pydantic import ValidationError

from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ProfileResult,
    ReportingProfile,
    SecurityProfile,
)


def _standard_profile_payload() -> dict[str, object]:
    return {
        "issuer_profile": IssuerProfile.STANDARD_OPERATING,
        "security_profile": SecurityProfile.COMMON_STOCK,
        "reporting_profile": ReportingProfile.DOMESTIC_US_GAAP,
        "coverage_level": CoverageLevel.FULL,
        "classification_evidence_ids": ["ev_profile_001"],
        "reason_codes": ["profile_classified"],
        "registry_version": "profile-registry-v1",
    }


@pytest.mark.parametrize(
    ("enum_type", "expected_values"),
    [
        (
            IssuerProfile,
            (
                "standard_operating",
                "bank",
                "insurance",
                "reit",
                "utility",
                "commodity_producer",
                "pre_revenue",
                "holding_company",
                "unknown",
            ),
        ),
        (
            SecurityProfile,
            (
                "common_stock",
                "multi_class",
                "adr",
                "spac",
                "recent_listing",
                "unsupported_fund_security",
                "unknown",
            ),
        ),
        (
            ReportingProfile,
            (
                "domestic_us_gaap",
                "foreign_private_issuer_ifrs",
                "investment_company_reporting",
                "unknown",
            ),
        ),
        (
            CoverageLevel,
            ("full", "partial", "evidence_only", "unsupported_security"),
        ),
    ],
)
def test_profile_enum_values_are_json_strings(
    enum_type: type[Enum], expected_values: tuple[str, ...]
) -> None:
    assert tuple(member.value for member in enum_type) == expected_values
    assert all(json.dumps(member.value).startswith('"') for member in enum_type)


def test_standard_profile_is_json_serializable_with_stable_values() -> None:
    result = ProfileResult(**_standard_profile_payload())

    dumped = result.model_dump(mode="json")

    assert dumped == {
        "issuer_profile": "standard_operating",
        "security_profile": "common_stock",
        "reporting_profile": "domestic_us_gaap",
        "coverage_level": "full",
        "classification_evidence_ids": ["ev_profile_001"],
        "reason_codes": ["profile_classified"],
        "registry_version": "profile-registry-v1",
    }
    assert json.loads(result.model_dump_json()) == dumped


def test_profile_result_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ProfileResult(**{**_standard_profile_payload(), "unexpected": "value"})


@pytest.mark.parametrize("field_name", ["issuer_profile", "security_profile", "reporting_profile", "coverage_level"])
def test_profile_result_rejects_invalid_enum_values(field_name: str) -> None:
    payload = _standard_profile_payload()
    payload[field_name] = "not-a-valid-enum"

    with pytest.raises(ValidationError):
        ProfileResult(**payload)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("classification_evidence_ids", [""]),
        ("reason_codes", [""]),
        ("registry_version", ""),
    ],
)
def test_profile_result_rejects_empty_ids_reasons_and_version(
    field_name: str, invalid_value: object
) -> None:
    payload = _standard_profile_payload()
    payload[field_name] = invalid_value

    with pytest.raises(ValidationError):
        ProfileResult(**payload)


def test_profile_result_list_defaults_are_independent() -> None:
    first = ProfileResult(
        issuer_profile=IssuerProfile.UNKNOWN,
        security_profile=SecurityProfile.UNKNOWN,
        reporting_profile=ReportingProfile.UNKNOWN,
        coverage_level=CoverageLevel.EVIDENCE_ONLY,
        registry_version="profile-registry-v1",
    )
    second = ProfileResult(
        issuer_profile=IssuerProfile.UNKNOWN,
        security_profile=SecurityProfile.UNKNOWN,
        reporting_profile=ReportingProfile.UNKNOWN,
        coverage_level=CoverageLevel.EVIDENCE_ONLY,
        registry_version="profile-registry-v1",
    )

    first.classification_evidence_ids.append("ev_one")
    first.reason_codes.append("reason_one")

    assert second.classification_evidence_ids == []
    assert second.reason_codes == []


def test_profile_result_json_schema_exposes_fields_and_forbids_extra() -> None:
    schema = ProfileResult.model_json_schema()

    assert set(schema["properties"]) == {
        "issuer_profile",
        "security_profile",
        "reporting_profile",
        "coverage_level",
        "classification_evidence_ids",
        "reason_codes",
        "registry_version",
    }
    assert schema["additionalProperties"] is False
