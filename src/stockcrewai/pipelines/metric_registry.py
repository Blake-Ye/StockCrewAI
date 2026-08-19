from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, NamedTuple

from stockcrewai.models.evidence import (
    CalculationRecord,
    EvidenceRecord,
    ValidationStatus,
)
from stockcrewai.models.policy import (
    Applicability,
    GateEffect,
    MetricPolicy,
    PolicyDecision,
)
from stockcrewai.models.profile import (
    IssuerProfile,
    ProfileResult,
)


POLICY_VERSION = "metric-policy:v1"


class _MetricSpec(NamedTuple):
    metric_id: str
    applicability: Applicability
    required_evidence: tuple[str, ...]
    formula_id: str
    period_basis: str
    unit_policy: str
    gate_effect: GateEffect
    reason_code: str
    policy_version: str = POLICY_VERSION


def _spec(
    metric_id: str,
    applicability: Applicability,
    required_evidence: tuple[str, ...],
    *,
    gate_effect: GateEffect,
    reason_code: str,
    formula_id: str | None = None,
    period_basis: str = "ttm_or_latest_fiscal_period",
    unit_policy: str = "decimal_ratio_or_per_share",
    policy_version: str = POLICY_VERSION,
) -> _MetricSpec:
    return _MetricSpec(
        metric_id,
        applicability,
        required_evidence,
        formula_id or f"{metric_id}:v1",
        period_basis,
        unit_policy,
        gate_effect,
        reason_code,
        policy_version,
    )


_REQUIRED = Applicability.REQUIRED
_OPTIONAL = Applicability.OPTIONAL
_NOT_APPLICABLE = Applicability.NOT_APPLICABLE
_BLOCKING = GateEffect.BLOCKING
_NON_BLOCKING = GateEffect.NON_BLOCKING


_POLICY_TABLE: dict[IssuerProfile, tuple[_MetricSpec, ...]] = {
    IssuerProfile.STANDARD_OPERATING: (
        _spec(
            "revenue_growth",
            _REQUIRED,
            ("revenue_current", "revenue_prior"),
            gate_effect=_BLOCKING,
            reason_code="required_revenue_growth",
        ),
        _spec(
            "operating_margin",
            _REQUIRED,
            ("operating_income", "revenue"),
            gate_effect=_BLOCKING,
            reason_code="required_operating_margin",
        ),
        _spec(
            "pe_ratio",
            _OPTIONAL,
            ("market_price", "diluted_eps"),
            gate_effect=_NON_BLOCKING,
            reason_code="optional_pe_ratio",
        ),
        _spec(
            "fcf_yield",
            _OPTIONAL,
            ("free_cash_flow", "market_cap"),
            gate_effect=_NON_BLOCKING,
            reason_code="optional_fcf_yield",
        ),
    ),

}

def _policy(profile: ProfileResult, spec: _MetricSpec) -> MetricPolicy:
    return MetricPolicy(
        metric_id=spec.metric_id,
        issuer_profile=profile.issuer_profile,
        security_profile=profile.security_profile,
        reporting_profile=profile.reporting_profile,
        applicability=spec.applicability,
        required_evidence=list(spec.required_evidence),
        formula_id=spec.formula_id,
        period_basis=spec.period_basis,
        unit_policy=spec.unit_policy,
        gate_effect=spec.gate_effect,
        reason_code=spec.reason_code,
        policy_version=spec.policy_version,
    )


def policy_version_for_profile(profile: ProfileResult | IssuerProfile) -> str:
    """Return the fixed metric policy version for a resolved issuer profile."""
    return POLICY_VERSION

def resolve_metric_policies(profile: ProfileResult) -> tuple[MetricPolicy, ...]:
    """Return the fixed metric policy rows applicable to one resolved profile."""
    if profile.issuer_profile is not IssuerProfile.STANDARD_OPERATING:
        return ()
    return tuple(
        _policy(profile, spec)
        for spec in _POLICY_TABLE[IssuerProfile.STANDARD_OPERATING]
    )
DecisionStatus = Literal["available", "unavailable", "not_applicable", "invalid"]


def _decision(
    policy: MetricPolicy,
    status: DecisionStatus,
    reason_code: str,
    *,
    evidence_ids: Sequence[str] = (),
    calculation_ids: Sequence[str] = (),
    blocking: bool = False,
) -> PolicyDecision:
    return PolicyDecision(
        metric_id=policy.metric_id,
        status=status,
        evidence_ids=list(evidence_ids),
        calculation_ids=list(calculation_ids),
        reason_code=reason_code,
        blocking=blocking,
    )


def _missing_decision(policy: MetricPolicy, status: DecisionStatus, reason_code: str) -> PolicyDecision:
    return _decision(
        policy,
        status,
        reason_code,
        blocking=policy.gate_effect is GateEffect.BLOCKING,
    )


def evaluate_policy_decisions(
    policies: Sequence[MetricPolicy],
    evidence: Sequence[EvidenceRecord],
    calculations: Sequence[CalculationRecord],
) -> tuple[PolicyDecision, ...]:
    """Evaluate policies from typed records without creating new provenance IDs."""
    evidence_allowlist = {
        record.evidence_id
        for record in evidence
        if record.validation_status is ValidationStatus.VALID
    }
    decisions: list[PolicyDecision] = []

    for policy in policies:
        if policy.applicability is Applicability.NOT_APPLICABLE:
            decisions.append(_decision(policy, "not_applicable", policy.reason_code))
            continue

        formula_matches = [
            calculation
            for calculation in calculations
            if calculation.formula_id == policy.formula_id
        ]
        valid_matches = [
            calculation
            for calculation in formula_matches
            if calculation.validation_status is ValidationStatus.VALID
            and calculation.result is not None
            and set(calculation.input_evidence_ids).issubset(evidence_allowlist)
        ]
        if valid_matches:
            calculation = min(valid_matches, key=lambda item: item.calculation_id)
            result = calculation.result
            assert result is not None
            if policy.metric_id == "pe_ratio" and result < 0:
                decisions.append(_decision(policy, "not_applicable", "negative_eps"))
            else:
                decisions.append(
                    _decision(
                        policy,
                        "available",
                        "validated_calculation",
                        evidence_ids=calculation.input_evidence_ids,
                        calculation_ids=[calculation.calculation_id],
                    )
                )
            continue

        if any(calculation.validation_status is ValidationStatus.INVALID for calculation in formula_matches):
            decisions.append(_missing_decision(policy, "invalid", "calculation_invalid"))
            continue
        if any(calculation.result is None for calculation in formula_matches):
            decisions.append(_missing_decision(policy, "invalid", "calculation_result_missing"))
            continue
        if any(
            calculation.validation_status is ValidationStatus.VALID
            for calculation in formula_matches
        ):
            decisions.append(
                _missing_decision(policy, "invalid", "calculation_evidence_unallowlisted")
            )
            continue
        if formula_matches:
            decisions.append(_missing_decision(policy, "unavailable", "calculation_not_validated"))
            continue

        reason_code = policy.reason_code if policy.reason_code == "share_class_unreconciled" else (
            "required_calculation_missing"
            if policy.applicability is Applicability.REQUIRED
            else "optional_calculation_missing"
        )
        decisions.append(_missing_decision(policy, "unavailable", reason_code))

    return tuple(decisions)


__all__ = [
    "POLICY_VERSION",
    "evaluate_policy_decisions",
    "policy_version_for_profile",
    "resolve_metric_policies",
]
