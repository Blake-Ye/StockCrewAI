from __future__ import annotations

import pytest

from stockcrewai.models.profile import ProfileResult
from stockcrewai.pipelines.profile_registry import classify_profiles


def _ordinary_metadata(*, sic: str = "3571") -> dict[str, object]:
    return {
        "sic": sic,
        "filing_forms": ["10-K", "10-Q", "8-K"],
        "taxonomy": ["us-gaap"],
        "security_type": "common_stock",
        "security_class": "common_stock",
        "is_foreign_private_issuer": False,
        "is_investment_company": False,
        "has_revenue": True,
        "classification_evidence_ids": ["ev_profile_metadata"],
    }


def test_ordinary_company_is_classified_for_complete_research() -> None:
    result = classify_profiles(_ordinary_metadata())

    assert isinstance(result, ProfileResult)
    assert result.issuer_profile.value == "standard_operating"
    assert result.security_profile.value == "common_stock"
    assert result.reporting_profile.value == "domestic_us_gaap"
    assert result.coverage_level.value == "full"
    assert result.is_ordinary_scope is True
    assert result.ordinary_scope_reason_code == "ordinary_scope_allowed"


@pytest.mark.parametrize(
    ("sic", "issuer_profile"),
    [
        ("6020", "bank"),
        ("6300", "insurance"),
        ("6798", "reit"),
        ("4911", "utility"),
        ("1000", "commodity_producer"),
    ],
)
def test_special_sic_profiles_are_blocked_from_ordinary_scope(
    sic: str, issuer_profile: str
) -> None:
    result = classify_profiles(_ordinary_metadata(sic=sic))

    assert result.issuer_profile.value == issuer_profile
    assert result.is_ordinary_scope is False
    assert result.ordinary_scope_reason_code == f"ordinary_scope_issuer_{issuer_profile}"
    assert "profile_classified_from_sic" in result.reason_codes


def test_etf_is_blocked_from_ordinary_scope() -> None:
    result = classify_profiles(
        {
            "sic": None,
            "filing_forms": ["N-1A"],
            "taxonomy": ["investment_company"],
            "security_type": "ETF",
            "security_class": "etf",
            "is_foreign_private_issuer": False,
            "is_investment_company": True,
            "has_revenue": None,
            "classification_evidence_ids": ["ev_etf_security"],
        }
    )

    assert result.security_profile.value == "unsupported_fund_security"
    assert result.coverage_level.value == "unsupported_security"
    assert result.is_ordinary_scope is False
    assert (
        result.ordinary_scope_reason_code
        == "ordinary_scope_security_unsupported_fund_security"
    )
    assert "unsupported_security" in result.reason_codes
