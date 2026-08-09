from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ProfileResult,
    ReportingProfile,
    SecurityProfile,
)


REGISTRY_VERSION = "profile-registry:v1"
_RECENT_LISTING_MAX_AGE_DAYS = 365

_SIC_ISSUER_PROFILES: tuple[tuple[int, int, IssuerProfile], ...] = (
    (6020, 6022, IssuerProfile.BANK),
    (6300, 6399, IssuerProfile.INSURANCE),
    (6798, 6798, IssuerProfile.REIT),
    (4900, 4999, IssuerProfile.UTILITY),
    (1000, 1499, IssuerProfile.COMMODITY_PRODUCER),
)
_FILING_ISSUER_PROFILES = {
    profile.value: profile
    for profile in (
        IssuerProfile.STANDARD_OPERATING,
        IssuerProfile.BANK,
        IssuerProfile.INSURANCE,
        IssuerProfile.REIT,
        IssuerProfile.UTILITY,
        IssuerProfile.COMMODITY_PRODUCER,
        IssuerProfile.PRE_REVENUE,
        IssuerProfile.HOLDING_COMPANY,
    )
}
_FUND_SECURITY_VALUES = frozenset(
    {
        "etf",
        "fund",
        "mutual_fund",
        "closed_end_fund",
        "investment_company",
        "investment_company/fund",
    }
)
_SECURITY_PROFILE_VALUES = {
    "multi_class": SecurityProfile.MULTI_CLASS,
    "adr": SecurityProfile.ADR,
    "spac": SecurityProfile.SPAC,
    "common_stock": SecurityProfile.COMMON_STOCK,
    "common": SecurityProfile.COMMON_STOCK,
}


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _normalized(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    return text.casefold().replace("-", "_").replace(" ", "_")


def _profile_value(
    source_metadata: Mapping[str, Any],
    key: str,
    enum_type: type[Enum],
) -> tuple[bool, Enum | None]:
    if key not in source_metadata:
        return False, None
    value = source_metadata.get(key)
    if isinstance(value, enum_type):
        return True, value
    normalized = _normalized(value)
    if normalized is None:
        return False, None
    for member in enum_type:
        if member.value == normalized:
            return True, member
    return True, None


def _string_values(source_metadata: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = source_metadata.get(key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _parse_sic(value: Any) -> tuple[int | None, bool]:
    if value is None:
        return None, False
    if isinstance(value, bool):
        return None, True
    if isinstance(value, int):
        return value, True
    text = _text(value)
    if text is not None and text.isdigit():
        return int(text), True
    return None, True


def _issuer_from_sic(sic: int) -> IssuerProfile | None:
    for lower, upper, profile in _SIC_ISSUER_PROFILES:
        if lower <= sic <= upper:
            return profile
    return None


def _issuer_from_filing_metadata(source_metadata: Mapping[str, Any]) -> IssuerProfile | None:
    values = _string_values(source_metadata, "taxonomy")
    normalized_values = {_normalized(value) for value in values}
    for profile in _FILING_ISSUER_PROFILES.values():
        if profile.value in normalized_values:
            return profile
    return None


def _classify_issuer(
    source_metadata: Mapping[str, Any],
) -> tuple[IssuerProfile, frozenset[str]]:
    explicit, profile = _profile_value(
        source_metadata, "sec_registrant_profile", IssuerProfile
    )
    if explicit:
        return (
            profile if isinstance(profile, IssuerProfile) else IssuerProfile.UNKNOWN,
            frozenset({"sec"}) if profile is not None else frozenset(),
        )

    if source_metadata.get("has_revenue") is False:
        return IssuerProfile.PRE_REVENUE, frozenset({"sec"})

    sic, has_sic_signal = _parse_sic(source_metadata.get("sic"))
    if has_sic_signal:
        sic_profile = _issuer_from_sic(sic) if sic is not None else None
        return sic_profile or IssuerProfile.UNKNOWN, (
            frozenset({"sic"}) if sic_profile is not None else frozenset()
        )

    filing_profile = _issuer_from_filing_metadata(source_metadata)
    if filing_profile is not None:
        return filing_profile, frozenset({"filing"})
    return IssuerProfile.UNKNOWN, frozenset()


def _has_any_normalized(
    source_metadata: Mapping[str, Any], key: str, values: frozenset[str]
) -> bool:
    return any(
        normalized in values
        for normalized in (_normalized(value) for value in _string_values(source_metadata, key))
    )


def _fund_security_sources(source_metadata: Mapping[str, Any]) -> frozenset[str]:
    sources: set[str] = set()
    if source_metadata.get("is_investment_company") is True:
        sources.add("sec")
    if any(
        _normalized(source_metadata.get(key)) in _FUND_SECURITY_VALUES
        for key in ("security_type", "security_class")
    ):
        sources.add("security")
    return frozenset(sources)


def _has_foreign_filing_signal(source_metadata: Mapping[str, Any]) -> bool:
    return _has_any_normalized(source_metadata, "filing_forms", frozenset({"20_f", "40_f"})) or any(
        (normalized or "").startswith("ifrs")
        for normalized in (
            _normalized(value) for value in _string_values(source_metadata, "taxonomy")
        )
    )


def _has_domestic_filing_signal(source_metadata: Mapping[str, Any]) -> bool:
    return _has_any_normalized(
        source_metadata, "filing_forms", frozenset({"10_k", "10_q", "8_k"})
    ) or any(
        (normalized or "").startswith("us_gaap")
        for normalized in (
            _normalized(value) for value in _string_values(source_metadata, "taxonomy")
        )
    )


def _classify_reporting(
    source_metadata: Mapping[str, Any],
) -> tuple[ReportingProfile, frozenset[str]]:
    explicit, profile = _profile_value(
        source_metadata, "sec_reporting_profile", ReportingProfile
    )
    if explicit:
        return (
            profile if isinstance(profile, ReportingProfile) else ReportingProfile.UNKNOWN,
            frozenset({"sec"}) if profile is not None else frozenset(),
        )

    foreign_filing = _has_foreign_filing_signal(source_metadata)

    if source_metadata.get("is_investment_company") is True:
        return ReportingProfile.INVESTMENT_COMPANY_REPORTING, frozenset({"sec"})

    if source_metadata.get("is_foreign_private_issuer") is True or foreign_filing:
        sources = {"filing"} if foreign_filing else set()
        if source_metadata.get("is_foreign_private_issuer") is True:
            sources.add("sec")
        return ReportingProfile.FOREIGN_PRIVATE_ISSUER_IFRS, frozenset(sources)

    if _has_domestic_filing_signal(source_metadata):
        return ReportingProfile.DOMESTIC_US_GAAP, frozenset({"filing"})

    fund_sources = _fund_security_sources(source_metadata)
    if fund_sources:
        return ReportingProfile.INVESTMENT_COMPANY_REPORTING, fund_sources
    return ReportingProfile.UNKNOWN, frozenset()


def _recent_listing_signal(source_metadata: Mapping[str, Any]) -> bool:
    if _listing_age_metadata_invalid(source_metadata):
        return False
    recent_listing = source_metadata.get("recent_listing")
    if recent_listing is True:
        return True
    if recent_listing is False:
        return False
    age = source_metadata.get("listing_age_days")
    if isinstance(age, bool):
        return False
    if isinstance(age, int):
        return 0 <= age <= _RECENT_LISTING_MAX_AGE_DAYS
    if isinstance(age, str) and age.strip().isdigit():
        return int(age.strip()) <= _RECENT_LISTING_MAX_AGE_DAYS
    return False


def _listing_age_metadata_invalid(source_metadata: Mapping[str, Any]) -> bool:
    if "listing_age_days" not in source_metadata:
        return False
    age = source_metadata.get("listing_age_days")
    if age is None:
        return False
    if isinstance(age, bool):
        return True
    if isinstance(age, int):
        return age < 0
    if isinstance(age, str):
        return not age.strip().isdigit()
    return True


def _classify_security(
    source_metadata: Mapping[str, Any],
) -> tuple[SecurityProfile, frozenset[str]]:
    explicit, profile = _profile_value(
        source_metadata, "sec_security_profile", SecurityProfile
    )

    fund_sources = _fund_security_sources(source_metadata)
    if fund_sources:
        sources = set(fund_sources)
        if isinstance(profile, SecurityProfile) and profile not in {
            SecurityProfile.UNKNOWN,
            SecurityProfile.UNSUPPORTED_FUND_SECURITY,
        }:
            sources.add("sec")
        return SecurityProfile.UNSUPPORTED_FUND_SECURITY, frozenset(sources)

    if explicit:
        return (
            profile if isinstance(profile, SecurityProfile) else SecurityProfile.UNKNOWN,
            frozenset({"sec"}) if profile is not None else frozenset(),
        )

    if _recent_listing_signal(source_metadata):
        return SecurityProfile.RECENT_LISTING, frozenset({"security"})

    normalized_values = {
        _normalized(source_metadata.get(key)) for key in ("security_type", "security_class")
    }
    for value, security_profile in (
        ("multi_class", SecurityProfile.MULTI_CLASS),
        ("adr", SecurityProfile.ADR),
        ("spac", SecurityProfile.SPAC),
    ):
        if value in normalized_values:
            return security_profile, frozenset({"security"})

    if any(value in normalized_values for value in _SECURITY_PROFILE_VALUES):
        return SecurityProfile.COMMON_STOCK, frozenset({"security"})
    return SecurityProfile.UNKNOWN, frozenset()


def _has_security_profile_conflict_with_fund(
    source_metadata: Mapping[str, Any],
) -> bool:
    explicit, profile = _profile_value(
        source_metadata, "sec_security_profile", SecurityProfile
    )
    return bool(_fund_security_sources(source_metadata)) and explicit and isinstance(
        profile, SecurityProfile
    ) and profile not in {
        SecurityProfile.UNKNOWN,
        SecurityProfile.UNSUPPORTED_FUND_SECURITY,
    }


def _evidence_ids(source_metadata: Mapping[str, Any]) -> list[str]:
    value = source_metadata.get("classification_evidence_ids")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def classify_profiles(source_metadata: Mapping[str, Any]) -> ProfileResult:
    """Classify issuer, security, and reporting profiles from supplied metadata only."""
    issuer_profile, issuer_sources = _classify_issuer(source_metadata)
    reporting_profile, reporting_sources = _classify_reporting(source_metadata)
    security_profile, security_sources = _classify_security(source_metadata)

    sources = issuer_sources | reporting_sources | security_sources
    source_reason_codes = {
        "sec": "profile_classified_from_sec_metadata",
        "sic": "profile_classified_from_sic",
        "filing": "profile_classified_from_filing_metadata",
        "security": "profile_classified_from_security_metadata",
    }
    reason_codes = [
        source_reason_codes[source]
        for source in ("sec", "sic", "filing", "security")
        if source in sources
    ]

    if _has_security_profile_conflict_with_fund(source_metadata):
        reason_codes.append("security_profile_conflict_with_fund_metadata")
    if _listing_age_metadata_invalid(source_metadata):
        reason_codes.append("profile_metadata_invalid")

    if security_profile is SecurityProfile.UNSUPPORTED_FUND_SECURITY:
        coverage_level = CoverageLevel.UNSUPPORTED_SECURITY
        reason_codes.append("unsupported_security")
    else:
        known_profiles = (
            issuer_profile is not IssuerProfile.UNKNOWN,
            security_profile is not SecurityProfile.UNKNOWN,
            reporting_profile is not ReportingProfile.UNKNOWN,
        )
        if all(known_profiles):
            coverage_level = CoverageLevel.FULL
        elif any(known_profiles):
            coverage_level = CoverageLevel.PARTIAL
            reason_codes.append("profile_classification_partial")
        else:
            coverage_level = CoverageLevel.EVIDENCE_ONLY
            reason_codes.append("profile_classification_unavailable")

    return ProfileResult(
        issuer_profile=issuer_profile,
        security_profile=security_profile,
        reporting_profile=reporting_profile,
        coverage_level=coverage_level,
        classification_evidence_ids=_evidence_ids(source_metadata),
        reason_codes=reason_codes,
        registry_version=REGISTRY_VERSION,
    )


__all__ = ["REGISTRY_VERSION", "classify_profiles"]
