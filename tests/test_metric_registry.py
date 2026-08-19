from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from stockcrewai.models.evidence import (
    CalculationRecord,
    EvidenceRecord,
    ValidationStatus,
)
from stockcrewai.models.policy import Applicability, MetricPolicy
from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ProfileResult,
    ReportingProfile,
    SecurityProfile,
)
from stockcrewai.pipelines.metric_registry import (
    POLICY_VERSION,
    evaluate_policy_decisions,
    resolve_metric_policies,
)


def _profile(
    issuer: IssuerProfile,
    *,
    security: SecurityProfile = SecurityProfile.COMMON_STOCK,
    reporting: ReportingProfile = ReportingProfile.DOMESTIC_US_GAAP,
    coverage: CoverageLevel = CoverageLevel.FULL,
    reason_codes: list[str] | None = None,
) -> ProfileResult:
    return ProfileResult(
        issuer_profile=issuer,
        security_profile=security,
        reporting_profile=reporting,
        coverage_level=coverage,
        reason_codes=reason_codes or [],
        registry_version="profile-registry:test-input",
    )


def _evidence(
    evidence_id: str = "ev_input",
    *,
    validation_status: ValidationStatus = ValidationStatus.VALID,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        source_reference=f"fixture:{evidence_id}",
        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        filed_at=date(2026, 1, 1),
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        unit="usd",
        currency="USD",
        value=Decimal("1"),
        validation_status=validation_status,
    )


def _calculation(
    calculation_id: str,
    formula_id: str,
    *,
    result: Decimal | None = Decimal("1"),
    validation_status: ValidationStatus = ValidationStatus.VALID,
    input_evidence_ids: list[str] | None = None,
) -> CalculationRecord:
    return CalculationRecord(
        calculation_id=calculation_id,
        formula_id=formula_id,
        input_evidence_ids=input_evidence_ids or ["ev_input"],
        source_reference=f"fixture:{calculation_id}",
        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        result=result,
        unit="ratio",
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        validation_status=validation_status,
    )


@pytest.mark.parametrize(
    ("issuer", "expected_metrics", "expected_applicability"),
    [
        (
            IssuerProfile.STANDARD_OPERATING,
            ("revenue_growth", "operating_margin", "pe_ratio", "fcf_yield"),
            {
                "revenue_growth": Applicability.REQUIRED,
                "operating_margin": Applicability.REQUIRED,
                "pe_ratio": Applicability.OPTIONAL,
                "fcf_yield": Applicability.OPTIONAL,
            },
        ),
    ],
)
def test_policy_matrix_is_profile_aware(
    issuer: IssuerProfile,
    expected_metrics: tuple[str, ...],
    expected_applicability: dict[str, Applicability],
) -> None:
    profile = _profile(issuer)

    policies = resolve_metric_policies(profile)

    assert tuple(policy.metric_id for policy in policies) == expected_metrics
    assert {policy.metric_id: policy.applicability for policy in policies} == expected_applicability


def test_every_policy_is_complete_versioned_aligned_and_unique() -> None:
    profile = _profile(IssuerProfile.STANDARD_OPERATING)
    policies = resolve_metric_policies(profile)

    assert len({policy.metric_id for policy in policies}) == len(policies)
    for policy in policies:
        assert isinstance(policy, MetricPolicy)
        assert policy.model_dump(mode="json")
        assert policy.policy_version == POLICY_VERSION == "metric-policy:v1"
        assert policy.issuer_profile is profile.issuer_profile
        assert policy.security_profile is profile.security_profile
        assert policy.reporting_profile is profile.reporting_profile
        assert policy.metric_id
        assert policy.required_evidence
        assert policy.formula_id
        assert policy.period_basis
        assert policy.unit_policy
        assert policy.reason_code


def test_registry_does_not_accept_or_infer_a_policy_version() -> None:
    first = resolve_metric_policies(_profile(IssuerProfile.STANDARD_OPERATING))
    second = resolve_metric_policies(
        ProfileResult(
            issuer_profile=IssuerProfile.STANDARD_OPERATING,
            security_profile=SecurityProfile.COMMON_STOCK,
            reporting_profile=ReportingProfile.DOMESTIC_US_GAAP,
            coverage_level=CoverageLevel.FULL,
            registry_version="another-version",
        )
    )

    assert [policy.model_dump(mode="json") for policy in first] == [
        policy.model_dump(mode="json") for policy in second
    ]


@pytest.mark.parametrize(
    "issuer",
    [
        IssuerProfile.BANK,
        IssuerProfile.INSURANCE,
        IssuerProfile.REIT,
        IssuerProfile.UTILITY,
        IssuerProfile.COMMODITY_PRODUCER,
        IssuerProfile.PRE_REVENUE,
        IssuerProfile.HOLDING_COMPANY,
        IssuerProfile.UNKNOWN,
    ],
)
def test_non_standard_issuer_does_not_publish_policies(issuer: IssuerProfile) -> None:
    assert resolve_metric_policies(_profile(issuer)) == ()


def test_valid_calculation_is_the_only_minimal_available_provenance() -> None:
    policy = resolve_metric_policies(_profile(IssuerProfile.STANDARD_OPERATING))[0]
    calculation = _calculation("calc-revenue", policy.formula_id)

    decisions = evaluate_policy_decisions([policy], [_evidence()], [calculation])

    assert decisions[0].status == "available"
    assert decisions[0].calculation_ids == ["calc-revenue"]
    assert decisions[0].evidence_ids == ["ev_input"]
    assert decisions[0].blocking is False


@pytest.mark.parametrize(
    "evidence_status", [ValidationStatus.UNVALIDATED, ValidationStatus.INVALID]
)
def test_valid_calculation_requires_valid_input_evidence(
    evidence_status: ValidationStatus,
) -> None:
    policy = resolve_metric_policies(_profile(IssuerProfile.STANDARD_OPERATING))[0]
    calculation = _calculation("calc-revenue", policy.formula_id)

    decision = evaluate_policy_decisions(
        [policy], [_evidence(validation_status=evidence_status)], [calculation]
    )[0]

    assert decision.status == "invalid"
    assert decision.reason_code == "calculation_evidence_unallowlisted"
    assert decision.evidence_ids == []
    assert decision.calculation_ids == []
    assert decision.blocking is True


def test_isolated_evidence_cannot_become_an_available_calculation() -> None:
    policy = resolve_metric_policies(_profile(IssuerProfile.STANDARD_OPERATING))[0]

    decision = evaluate_policy_decisions([policy], [_evidence()], [])[0]

    assert decision.status == "unavailable"
    assert decision.evidence_ids == []
    assert decision.calculation_ids == []
    assert decision.blocking is True


def test_missing_required_calculation_is_blocking_and_optional_missing_is_not() -> None:
    policies = resolve_metric_policies(_profile(IssuerProfile.STANDARD_OPERATING))

    decisions = evaluate_policy_decisions(policies, [], [])

    assert decisions[0].status == "unavailable"
    assert decisions[0].blocking is True
    assert decisions[2].status == "unavailable"
    assert decisions[2].blocking is False


def test_invalid_calculation_is_invalid_and_required_blocking() -> None:
    policy = resolve_metric_policies(_profile(IssuerProfile.STANDARD_OPERATING))[0]
    calculation = _calculation(
        "calc-invalid",
        policy.formula_id,
        result=None,
        validation_status=ValidationStatus.INVALID,
    )

    decision = evaluate_policy_decisions([policy], [_evidence()], [calculation])[0]

    assert decision.status == "invalid"
    assert decision.reason_code == "calculation_invalid"
    assert decision.blocking is True


def test_duplicate_valid_calculations_choose_lexicographically_smallest_id() -> None:
    policy = resolve_metric_policies(_profile(IssuerProfile.STANDARD_OPERATING))[0]
    calculations = [
        _calculation("calc-z", policy.formula_id),
        _calculation("calc-a", policy.formula_id),
    ]

    decision = evaluate_policy_decisions([policy], [_evidence()], calculations)[0]

    assert decision.calculation_ids == ["calc-a"]


def test_negative_pe_is_not_applicable_but_negative_fcf_yield_is_available() -> None:
    policies = resolve_metric_policies(_profile(IssuerProfile.STANDARD_OPERATING))
    pe_policy = next(policy for policy in policies if policy.metric_id == "pe_ratio")
    fcf_policy = next(policy for policy in policies if policy.metric_id == "fcf_yield")

    decisions = evaluate_policy_decisions(
        [pe_policy, fcf_policy],
        [_evidence()],
        [
            _calculation("calc-pe", pe_policy.formula_id, result=Decimal("-1")),
            _calculation("calc-fcf", fcf_policy.formula_id, result=Decimal("-1")),
        ],
    )

    assert decisions[0].status == "not_applicable"
    assert decisions[0].reason_code == "negative_eps"
    assert decisions[0].blocking is False
    assert decisions[1].status == "available"
    assert decisions[1].calculation_ids == ["calc-fcf"]
    assert decisions[1].blocking is False
