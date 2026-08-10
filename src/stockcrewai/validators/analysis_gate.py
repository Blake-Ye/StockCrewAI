from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from stockcrewai.models.policy import GateResult, PolicyDecision
from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ProfileResult,
    ReportingProfile,
    SecurityProfile,
)
from stockcrewai.pipelines.metric_registry import policy_version_for_profile


def _reason_codes(fixed_code: str, decisions: Sequence[PolicyDecision]) -> list[str]:
    codes = [fixed_code]
    for decision in decisions:
        if decision.reason_code not in codes:
            codes.append(decision.reason_code)
    return codes


def evaluate_analysis_gate(
    profile: ProfileResult,
    decisions: Sequence[PolicyDecision],
) -> GateResult:
    """Return a deterministic analysis gate from profile coverage and decisions."""
    blocking_decisions = [
        decision
        for decision in decisions
        if decision.status in {"unavailable", "invalid"} and decision.blocking
    ]
    non_blocking_decisions = [
        decision
        for decision in decisions
        if not decision.blocking or decision.status == "not_applicable"
    ]

    status: Literal["ready", "blocked", "evidence_only", "unsupported"]
    if profile.coverage_level is CoverageLevel.UNSUPPORTED_SECURITY:
        status = "unsupported"
        fixed_reason_code = "unsupported_security"
    elif (
        profile.reporting_profile is ReportingProfile.FOREIGN_PRIVATE_ISSUER_IFRS
        and (
            profile.issuer_profile is IssuerProfile.UNKNOWN
            or profile.security_profile is SecurityProfile.UNKNOWN
        )
    ):
        status = "evidence_only"
        fixed_reason_code = "foreign_profile_incomplete"
    elif (
        profile.security_profile in {SecurityProfile.ADR, SecurityProfile.SPAC}
        and profile.reporting_profile is not ReportingProfile.FOREIGN_PRIVATE_ISSUER_IFRS
    ):
        status = "evidence_only"
        fixed_reason_code = "security_profile_policy_unavailable"
    elif profile.coverage_level is CoverageLevel.EVIDENCE_ONLY:
        status = "evidence_only"
        fixed_reason_code = "evidence_only_coverage"
    elif blocking_decisions:
        status = "blocked"
        fixed_reason_code = "analysis_blocked"
    else:
        status = "ready"
        fixed_reason_code = "analysis_ready"

    return GateResult(
        status=status,
        coverage_level=profile.coverage_level,
        blocking_decisions=blocking_decisions,
        non_blocking_decisions=non_blocking_decisions,
        reason_codes=_reason_codes(fixed_reason_code, decisions),
        policy_version=policy_version_for_profile(profile),
    )


__all__ = ["evaluate_analysis_gate"]
