from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


_NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class IssuerProfile(str, Enum):
    STANDARD_OPERATING = "standard_operating"
    BANK = "bank"
    INSURANCE = "insurance"
    REIT = "reit"
    UTILITY = "utility"
    COMMODITY_PRODUCER = "commodity_producer"
    PRE_REVENUE = "pre_revenue"
    HOLDING_COMPANY = "holding_company"
    UNKNOWN = "unknown"


class SecurityProfile(str, Enum):
    COMMON_STOCK = "common_stock"
    MULTI_CLASS = "multi_class"
    ADR = "adr"
    SPAC = "spac"
    RECENT_LISTING = "recent_listing"
    UNSUPPORTED_FUND_SECURITY = "unsupported_fund_security"
    UNKNOWN = "unknown"


class ReportingProfile(str, Enum):
    DOMESTIC_US_GAAP = "domestic_us_gaap"
    FOREIGN_PRIVATE_ISSUER_IFRS = "foreign_private_issuer_ifrs"
    INVESTMENT_COMPANY_REPORTING = "investment_company_reporting"
    UNKNOWN = "unknown"


class CoverageLevel(str, Enum):
    FULL = "full"
    PARTIAL = "partial"
    EVIDENCE_ONLY = "evidence_only"
    UNSUPPORTED_SECURITY = "unsupported_security"


_ORDINARY_SECURITY_PROFILES = frozenset(
    {SecurityProfile.COMMON_STOCK, SecurityProfile.MULTI_CLASS}
)
_ORDINARY_SCOPE_EVIDENCE_REASON = "ordinary_scope_evidence_verified"


class ProfileResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issuer_profile: IssuerProfile
    security_profile: SecurityProfile
    reporting_profile: ReportingProfile
    coverage_level: CoverageLevel
    classification_evidence_ids: list[_NonEmptyString] = Field(default_factory=list)
    reason_codes: list[_NonEmptyString] = Field(default_factory=list)
    registry_version: _NonEmptyString

    @property
    def ordinary_scope_reason_code(self) -> str:
        """Return the stable reason for allowing or blocking ordinary scope."""
        if self.security_profile is SecurityProfile.UNSUPPORTED_FUND_SECURITY:
            return "ordinary_scope_security_unsupported_fund_security"
        if self.issuer_profile is not IssuerProfile.STANDARD_OPERATING:
            if self.issuer_profile is not IssuerProfile.UNKNOWN:
                return f"ordinary_scope_issuer_{self.issuer_profile.value}"
        if self.reporting_profile is not ReportingProfile.DOMESTIC_US_GAAP:
            if self.reporting_profile is not ReportingProfile.UNKNOWN:
                return f"ordinary_scope_reporting_{self.reporting_profile.value}"
        if self.security_profile not in _ORDINARY_SECURITY_PROFILES:
            if self.security_profile is not SecurityProfile.UNKNOWN:
                return f"ordinary_scope_security_{self.security_profile.value}"
        if self.issuer_profile is not IssuerProfile.STANDARD_OPERATING:
            return f"ordinary_scope_issuer_{self.issuer_profile.value}"
        if self.reporting_profile is not ReportingProfile.DOMESTIC_US_GAAP:
            return f"ordinary_scope_reporting_{self.reporting_profile.value}"
        if self.security_profile not in _ORDINARY_SECURITY_PROFILES:
            return f"ordinary_scope_security_{self.security_profile.value}"
        if (
            self.coverage_level is not CoverageLevel.FULL
            or not self.classification_evidence_ids
            or _ORDINARY_SCOPE_EVIDENCE_REASON not in self.reason_codes
        ):
            return "ordinary_scope_evidence_missing"
        return "ordinary_scope_allowed"

    @property
    def is_ordinary_scope(self) -> bool:
        """Whether this profile is eligible for the ordinary-company mainline."""
        return self.ordinary_scope_reason_code == "ordinary_scope_allowed"
