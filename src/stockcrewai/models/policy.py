from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ReportingProfile,
    SecurityProfile,
)


_NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class Applicability(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    NOT_APPLICABLE = "not_applicable"


class GateEffect(str, Enum):
    BLOCKING = "blocking"
    NON_BLOCKING = "non_blocking"


class MetricPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: _NonEmptyString
    issuer_profile: IssuerProfile
    security_profile: SecurityProfile
    reporting_profile: ReportingProfile
    applicability: Applicability
    required_evidence: list[_NonEmptyString] = Field(default_factory=list)
    formula_id: _NonEmptyString
    period_basis: _NonEmptyString
    unit_policy: _NonEmptyString
    gate_effect: GateEffect
    reason_code: _NonEmptyString
    policy_version: _NonEmptyString


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: _NonEmptyString
    status: Literal["available", "unavailable", "not_applicable", "invalid"]
    evidence_ids: list[_NonEmptyString] = Field(default_factory=list)
    calculation_ids: list[_NonEmptyString] = Field(default_factory=list)
    reason_code: _NonEmptyString
    blocking: bool


class GateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "blocked", "evidence_only", "unsupported"]
    coverage_level: CoverageLevel
    blocking_decisions: list[PolicyDecision] = Field(default_factory=list)
    non_blocking_decisions: list[PolicyDecision] = Field(default_factory=list)
    reason_codes: list[_NonEmptyString] = Field(default_factory=list)
    policy_version: _NonEmptyString
