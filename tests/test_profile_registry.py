from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stockcrewai.models.profile import ProfileResult
from stockcrewai.pipelines.profile_registry import classify_profiles


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "profiles" / "profile_registry.json"


@pytest.fixture(scope="module")
def profile_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _case(profile_fixture: dict[str, Any], name: str) -> dict[str, Any]:
    return profile_fixture["cases"][name]


@pytest.mark.parametrize(
    "case_name",
    [
        "standard_operating",
        "bank_6020",
        "bank_6022",
        "insurance_6300",
        "insurance_6399",
        "reit",
        "pre_revenue_boolean",
        "pre_revenue_sec",
        "etf",
        "fund",
        "utility",
        "commodity_producer",
        "multi_class",
        "adr",
        "spac",
        "recent_listing",
        "foreign_private_issuer",
        "unknown_empty",
    ],
)
def test_fixture_profiles_are_deterministically_classified(
    profile_fixture: dict[str, Any], case_name: str
) -> None:
    case = _case(profile_fixture, case_name)

    result = classify_profiles(case["source_metadata"])

    assert isinstance(result, ProfileResult)
    assert result.model_dump(mode="json") == case["expected"]


def test_sec_metadata_has_priority_over_conflicting_sic_forms_and_security_metadata(
    profile_fixture: dict[str, Any],
) -> None:
    source_metadata = dict(_case(profile_fixture, "bank_6020")["source_metadata"])
    source_metadata.update(
        {
            "sec_registrant_profile": "standard_operating",
            "sec_reporting_profile": "domestic_us_gaap",
            "sec_security_profile": "common_stock",
            "sic": 6020,
            "filing_forms": ["20-F"],
            "taxonomy": ["ifrs-full", "reit"],
            "security_type": "ADR",
            "security_class": "adr",
            "is_foreign_private_issuer": True,
            "is_investment_company": False,
            "recent_listing": True,
            "ticker": "Bank ETF Holdings",
        }
    )

    result = classify_profiles(source_metadata)

    assert result.issuer_profile.value == "standard_operating"
    assert result.reporting_profile.value == "domestic_us_gaap"
    assert result.security_profile.value == "common_stock"
    assert result.coverage_level.value == "full"


def test_sic_has_priority_over_conflicting_filing_and_taxonomy_metadata(
    profile_fixture: dict[str, Any],
) -> None:
    source_metadata = dict(_case(profile_fixture, "reit")["source_metadata"])
    source_metadata.update(
        {
            "sec_registrant_profile": None,
            "sic": 6022,
            "filing_forms": ["reit"],
            "taxonomy": ["reit"],
        }
    )

    result = classify_profiles(source_metadata)

    assert result.issuer_profile.value == "bank"
    assert "profile_classified_from_sic" in result.reason_codes
    assert "profile_classification_partial" in result.reason_codes


def test_edgartools_operating_company_category_maps_to_standard_operating() -> None:
    result = classify_profiles(
        {
            "sec_business_category": "Operating Company",
            "sic": "3571",
            "filing_forms": ["10-K", "10-Q"],
            "taxonomy": ["us-gaap"],
            "security_type": "common_stock",
            "security_class": "common_stock",
        }
    )

    assert result.issuer_profile.value == "standard_operating"
    assert "profile_classified_from_sec_metadata" in result.reason_codes


def test_special_sic_remains_specialized_when_edgartools_says_operating_company() -> None:
    result = classify_profiles(
        {
            "sec_business_category": "Operating Company",
            "sic": "4931",
            "filing_forms": ["10-K"],
            "taxonomy": ["us-gaap"],
        }
    )

    assert result.issuer_profile.value == "utility"
    assert "profile_classified_from_sic" in result.reason_codes


def test_valid_domestic_sic_and_filing_metadata_identify_standard_operating_without_category() -> None:
    result = classify_profiles(
        {
            "sic": "3571",
            "filing_forms": ["10-K", "10-Q"],
            "taxonomy": ["us-gaap"],
        }
    )

    assert result.issuer_profile.value == "standard_operating"


@pytest.mark.parametrize(
    ("business_category", "expected_security", "expected_issuer"),
    [
        ("ETF", "unsupported_fund_security", "unknown"),
        ("Mutual Fund", "unsupported_fund_security", "unknown"),
        ("Closed-End Fund", "unsupported_fund_security", "unknown"),
        ("BDC", "unsupported_fund_security", "unknown"),
        ("Investment Manager", "unknown", "unknown"),
        ("SPAC", "spac", "unknown"),
    ],
)
def test_edgartools_non_operating_categories_do_not_use_standard_stock_route(
    business_category: str,
    expected_security: str,
    expected_issuer: str,
) -> None:
    result = classify_profiles(
        {
            "sec_business_category": business_category,
            "filing_forms": ["10-K"],
            "taxonomy": ["us-gaap"],
        }
    )

    assert result.security_profile.value == expected_security
    assert result.issuer_profile.value == expected_issuer


def test_investment_manager_category_blocks_domestic_filing_fallback() -> None:
    result = classify_profiles(
        {
            "sec_business_category": "Investment Manager",
            "sic": "6282",
            "filing_forms": ["10-K", "10-Q"],
            "taxonomy": ["us-gaap"],
            "security_type": "common_stock",
        }
    )

    assert result.issuer_profile.value == "unknown"
    assert result.security_profile.value == "common_stock"


def test_classification_evidence_ids_keep_only_supplied_non_empty_string_ids(
    profile_fixture: dict[str, Any],
) -> None:
    source_metadata = dict(_case(profile_fixture, "standard_operating")["source_metadata"])

    result = classify_profiles(source_metadata)

    assert result.classification_evidence_ids == [
        "ev_standard_sec",
        "ev_standard_reporting",
    ]
    assert result.registry_version == "profile-registry:v1"


def test_registry_version_is_fixed_even_when_input_supplies_another_version(
    profile_fixture: dict[str, Any],
) -> None:
    source_metadata = dict(_case(profile_fixture, "bank_6020")["source_metadata"])

    result = classify_profiles(source_metadata)

    assert result.registry_version == "profile-registry:v1"


def test_registry_version_input_cannot_change_any_output(
    profile_fixture: dict[str, Any],
) -> None:
    source_metadata = dict(_case(profile_fixture, "bank_6020")["source_metadata"])
    first = classify_profiles({**source_metadata, "registry_version": "profile-registry:test-a"})
    second = classify_profiles({**source_metadata, "registry_version": "profile-registry:test-b"})

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_unknown_or_invalid_metadata_never_falls_back_to_standard_operating(
    profile_fixture: dict[str, Any],
) -> None:
    source_metadata = dict(_case(profile_fixture, "unknown_empty")["source_metadata"])
    source_metadata.update(
        {
            "sec_registrant_profile": "not-a-profile",
            "sic": "not-a-sic",
            "filing_forms": ["standard_operating"],
            "taxonomy": ["not-a-taxonomy"],
            "security_type": "not-a-security",
            "security_class": "not-a-class",
            "ticker": "Standard Operating Holdings",
            "unexpected": "standard_operating",
        }
    )

    result = classify_profiles(source_metadata)

    assert result.issuer_profile.value == "unknown"
    assert result.security_profile.value == "unknown"
    assert result.reporting_profile.value == "unknown"
    assert result.coverage_level.value == "evidence_only"
    assert "profile_classification_unavailable" in result.reason_codes


def test_partial_profiles_are_not_blocked_by_missing_individual_signals(
    profile_fixture: dict[str, Any],
) -> None:
    source_metadata = dict(_case(profile_fixture, "unknown_empty")["source_metadata"])
    source_metadata.update(
        {
            "sec_registrant_profile": "bank",
            "classification_evidence_ids": ["ev_partial"],
        }
    )

    result = classify_profiles(source_metadata)

    assert result.issuer_profile.value == "bank"
    assert result.security_profile.value == "unknown"
    assert result.reporting_profile.value == "unknown"
    assert result.coverage_level.value == "partial"
    assert result.reason_codes == [
        "profile_classified_from_sec_metadata",
        "profile_classification_partial",
    ]


def test_result_is_json_safe_and_has_no_external_execution(
    profile_fixture: dict[str, Any],
) -> None:
    result = classify_profiles(_case(profile_fixture, "standard_operating")["source_metadata"])

    dumped = result.model_dump(mode="json")

    assert json.loads(json.dumps(dumped)) == dumped
    assert all(isinstance(value, str) for value in dumped.values() if isinstance(value, str))


def test_foreign_filing_signals_override_low_priority_fund_metadata(
    profile_fixture: dict[str, Any],
) -> None:
    source_metadata = dict(_case(profile_fixture, "unknown_empty")["source_metadata"])
    source_metadata.update(
        {
            "filing_forms": ["20-F"],
            "taxonomy": ["ifrs-full"],
            "security_type": "fund",
            "security_class": "ETF",
            "is_investment_company": False,
        }
    )

    result = classify_profiles(source_metadata)

    assert result.reporting_profile.value == "foreign_private_issuer_ifrs"
    assert result.security_profile.value == "unsupported_fund_security"
    assert result.coverage_level.value == "unsupported_security"


@pytest.mark.parametrize("sec_security_profile", ["unknown", "not-a-security-profile"])
def test_fund_security_boundary_survives_unknown_or_invalid_sec_profile(
    profile_fixture: dict[str, Any], sec_security_profile: str
) -> None:
    source_metadata = dict(_case(profile_fixture, "unknown_empty")["source_metadata"])
    source_metadata.update(
        {
            "sec_security_profile": sec_security_profile,
            "security_type": "fund",
            "security_class": "investment company",
        }
    )

    result = classify_profiles(source_metadata)

    assert result.security_profile.value == "unsupported_fund_security"
    assert result.coverage_level.value == "unsupported_security"


def test_valid_common_stock_sec_profile_and_fund_metadata_have_stable_conflict_result(
    profile_fixture: dict[str, Any],
) -> None:
    source_metadata = dict(_case(profile_fixture, "unknown_empty")["source_metadata"])
    source_metadata.update(
        {
            "sec_security_profile": "common_stock",
            "security_type": "fund",
            "security_class": "ETF",
        }
    )

    result = classify_profiles(source_metadata)

    assert result.security_profile.value == "unsupported_fund_security"
    assert result.coverage_level.value == "unsupported_security"
    assert "security_profile_conflict_with_fund_metadata" in result.reason_codes


def test_filing_form_named_like_issuer_profile_is_not_an_issuer_signal(
    profile_fixture: dict[str, Any],
) -> None:
    source_metadata = dict(_case(profile_fixture, "unknown_empty")["source_metadata"])
    source_metadata.update({"filing_forms": ["standard_operating"], "taxonomy": []})

    result = classify_profiles(source_metadata)

    assert result.issuer_profile.value == "unknown"
    assert result.coverage_level.value == "evidence_only"


@pytest.mark.parametrize("listing_age_days", [-1, "not-a-number", True])
def test_invalid_listing_age_does_not_create_recent_listing(
    profile_fixture: dict[str, Any], listing_age_days: Any
) -> None:
    source_metadata = dict(_case(profile_fixture, "unknown_empty")["source_metadata"])
    source_metadata.update(
        {
            "security_type": "common_stock",
            "security_class": "common_stock",
            "recent_listing": True,
            "listing_age_days": listing_age_days,
        }
    )

    result = classify_profiles(source_metadata)

    assert result.security_profile.value == "common_stock"
    assert "profile_metadata_invalid" in result.reason_codes
    assert result.security_profile.value != "recent_listing"
