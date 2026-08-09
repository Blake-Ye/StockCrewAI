from __future__ import annotations

import inspect

import pytest

from stockcrewai.models.policy import PolicyDecision
from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ProfileResult,
    ReportingProfile,
    SecurityProfile,
)
from stockcrewai.validators.analysis_gate import evaluate_analysis_gate


def _profile(
    coverage: CoverageLevel,
    *,
    security: SecurityProfile = SecurityProfile.COMMON_STOCK,
    reason_codes: list[str] | None = None,
) -> ProfileResult:
    return ProfileResult(
        issuer_profile=IssuerProfile.STANDARD_OPERATING,
        security_profile=security,
        reporting_profile=ReportingProfile.DOMESTIC_US_GAAP,
        coverage_level=coverage,
        reason_codes=reason_codes or [],
        registry_version="profile-registry:caller-version",
    )


def _decision(
    metric_id: str,
    status: str,
    *,
    reason_code: str,
    blocking: bool,
) -> PolicyDecision:
    return PolicyDecision(
        metric_id=metric_id,
        status=status,  # type: ignore[arg-type]
        reason_code=reason_code,
        blocking=blocking,
    )


def test_blocked_gate_lists_only_blocking_unavailable_or_invalid_decisions() -> None:
    decisions = [
        _decision(
            "revenue_growth",
            "unavailable",
            reason_code="required_calculation_missing",
            blocking=True,
        ),
        _decision(
            "pe_ratio",
            "not_applicable",
            reason_code="negative_eps",
            blocking=False,
        ),
    ]

    gate = evaluate_analysis_gate(_profile(CoverageLevel.FULL), decisions)

    assert gate.status == "blocked"
    assert gate.coverage_level is CoverageLevel.FULL
    assert [decision.metric_id for decision in gate.blocking_decisions] == [
        "revenue_growth"
    ]
    assert [decision.metric_id for decision in gate.non_blocking_decisions] == ["pe_ratio"]
    assert "negative_eps" in gate.reason_codes
    assert "required_calculation_missing" in gate.reason_codes
    assert gate.policy_version == "metric-policy:v1"


def test_ready_gate_accepts_full_or_partial_coverage_without_blocking_decisions() -> None:
    decisions = [
        _decision(
            "revenue_growth",
            "available",
            reason_code="validated_calculation",
            blocking=False,
        ),
        _decision(
            "pe_ratio",
            "unavailable",
            reason_code="optional_calculation_missing",
            blocking=False,
        ),
    ]

    for coverage in (CoverageLevel.FULL, CoverageLevel.PARTIAL):
        gate = evaluate_analysis_gate(_profile(coverage), decisions)

        assert gate.status == "ready"
        assert gate.blocking_decisions == []
        assert gate.non_blocking_decisions == decisions


def test_evidence_only_and_unsupported_security_have_dedicated_gate_statuses() -> None:
    decisions = [
        _decision(
            "revenue_growth",
            "unavailable",
            reason_code="required_calculation_missing",
            blocking=True,
        )
    ]

    evidence_only = evaluate_analysis_gate(_profile(CoverageLevel.EVIDENCE_ONLY), decisions)
    unsupported = evaluate_analysis_gate(
        ProfileResult(
            issuer_profile=IssuerProfile.UNKNOWN,
            security_profile=SecurityProfile.UNSUPPORTED_FUND_SECURITY,
            reporting_profile=ReportingProfile.INVESTMENT_COMPANY_REPORTING,
            coverage_level=CoverageLevel.UNSUPPORTED_SECURITY,
            registry_version="profile-registry:caller-version",
        ),
        decisions,
    )

    assert evidence_only.status == "evidence_only"
    assert evidence_only.coverage_level is CoverageLevel.EVIDENCE_ONLY
    assert unsupported.status == "unsupported"
    assert unsupported.coverage_level is CoverageLevel.UNSUPPORTED_SECURITY


@pytest.mark.parametrize("security", [SecurityProfile.ADR, SecurityProfile.SPAC])
def test_adr_and_spac_without_policy_are_evidence_only(security: SecurityProfile) -> None:
    gate = evaluate_analysis_gate(_profile(CoverageLevel.FULL, security=security), [])

    assert gate.status == "evidence_only"
    assert gate.reason_codes == ["security_profile_policy_unavailable"]
    assert gate.blocking_decisions == []


def test_gate_signature_has_no_warning_or_limitation_text_input() -> None:
    parameters = inspect.signature(evaluate_analysis_gate).parameters

    assert tuple(parameters) == ("profile", "decisions")
    assert "warnings" not in parameters
    assert "limitations" not in parameters


def test_profile_reason_text_does_not_change_gate_dump() -> None:
    decisions = [
        _decision(
            "revenue_growth",
            "available",
            reason_code="validated_calculation",
            blocking=False,
        )
    ]
    first = evaluate_analysis_gate(
        _profile(CoverageLevel.FULL, reason_codes=["warning: first limitations text"]),
        decisions,
    )
    second = evaluate_analysis_gate(
        _profile(CoverageLevel.FULL, reason_codes=["warning: different limitations text"]),
        decisions,
    )

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
