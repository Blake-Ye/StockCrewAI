from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from stockcrewai.models.policy import (
    Applicability,
    GateEffect,
    GateResult,
    MetricPolicy,
    PolicyDecision,
)
from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ReportingProfile,
    SecurityProfile,
)


def _standard_policy_payload() -> dict[str, object]:
    return {
        "metric_id": "free_cash_flow",
        "issuer_profile": IssuerProfile.STANDARD_OPERATING,
        "security_profile": SecurityProfile.COMMON_STOCK,
        "reporting_profile": ReportingProfile.DOMESTIC_US_GAAP,
        "applicability": Applicability.REQUIRED,
        "required_evidence": ["ev_operating_cash_flow", "ev_capex"],
        "formula_id": "fcf_v1",
        "period_basis": "ttm",
        "unit_policy": "usd",
        "gate_effect": GateEffect.BLOCKING,
        "reason_code": "required_for_standard_operating",
        "policy_version": "metric-policy-v1",
    }


def _standard_decision_payload() -> dict[str, object]:
    return {
        "metric_id": "free_cash_flow",
        "status": "available",
        "evidence_ids": ["ev_fcf"],
        "calculation_ids": ["calc_fcf"],
        "reason_code": "validated",
        "blocking": True,
    }


def _standard_gate_payload() -> dict[str, object]:
    return {
        "status": "ready",
        "coverage_level": CoverageLevel.FULL,
        "blocking_decisions": [PolicyDecision(**_standard_decision_payload())],
        "non_blocking_decisions": [],
        "reason_codes": ["all_required_metrics_available"],
        "policy_version": "metric-policy-v1",
    }


@pytest.mark.parametrize(
    ("enum_type", "expected_values"),
    [
        (Applicability, ("required", "optional", "not_applicable")),
        (GateEffect, ("blocking", "non_blocking")),
    ],
)
def test_policy_enum_values_are_json_strings(enum_type: type[object], expected_values: tuple[str, ...]) -> None:
    assert tuple(member.value for member in enum_type) == expected_values  # type: ignore[attr-defined]
    assert all(json.dumps(member.value).startswith('"') for member in enum_type)  # type: ignore[attr-defined]


def test_standard_policy_decision_and_gate_are_json_serializable() -> None:
    policy = MetricPolicy(**_standard_policy_payload())
    decision = PolicyDecision(**_standard_decision_payload())
    gate = GateResult(**_standard_gate_payload())

    assert policy.model_dump(mode="json") == {
        "metric_id": "free_cash_flow",
        "issuer_profile": "standard_operating",
        "security_profile": "common_stock",
        "reporting_profile": "domestic_us_gaap",
        "applicability": "required",
        "required_evidence": ["ev_operating_cash_flow", "ev_capex"],
        "formula_id": "fcf_v1",
        "period_basis": "ttm",
        "unit_policy": "usd",
        "gate_effect": "blocking",
        "reason_code": "required_for_standard_operating",
        "policy_version": "metric-policy-v1",
    }
    assert decision.model_dump(mode="json") == {
        "metric_id": "free_cash_flow",
        "status": "available",
        "evidence_ids": ["ev_fcf"],
        "calculation_ids": ["calc_fcf"],
        "reason_code": "validated",
        "blocking": True,
    }
    assert json.loads(gate.model_dump_json()) == gate.model_dump(mode="json")


def test_policy_decision_blocking_is_explicit_and_not_inferred_from_reason_text() -> None:
    decision = PolicyDecision(
        metric_id="free_cash_flow",
        status="unavailable",
        reason_code="text says blocking but structured decision is non_blocking",
        blocking=False,
    )

    assert decision.blocking is False


@pytest.mark.parametrize(
    ("model_type", "payload"),
    [
        (MetricPolicy, _standard_policy_payload()),
        (PolicyDecision, _standard_decision_payload()),
        (GateResult, _standard_gate_payload()),
    ],
)
def test_policy_models_forbid_extra_fields(model_type: type[object], payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        model_type(**{**payload, "unexpected": "value"})  # type: ignore[operator]


@pytest.mark.parametrize("field_name", ["issuer_profile", "security_profile", "reporting_profile", "applicability", "gate_effect"])
def test_metric_policy_rejects_invalid_enum_values(field_name: str) -> None:
    payload = _standard_policy_payload()
    payload[field_name] = "not-a-valid-enum"

    with pytest.raises(ValidationError):
        MetricPolicy(**payload)


def test_policy_decision_rejects_invalid_status() -> None:
    payload = _standard_decision_payload()
    payload["status"] = "not-a-status"

    with pytest.raises(ValidationError):
        PolicyDecision(**payload)


@pytest.mark.parametrize("field_name", ["status", "coverage_level"])
def test_gate_result_rejects_invalid_status_or_coverage(field_name: str) -> None:
    payload = _standard_gate_payload()
    payload[field_name] = "not-a-valid-value"

    with pytest.raises(ValidationError):
        GateResult(**payload)


@pytest.mark.parametrize(
    ("model_type", "payload_factory", "field_name", "invalid_value"),
    [
        (MetricPolicy, _standard_policy_payload, "metric_id", ""),
        (MetricPolicy, _standard_policy_payload, "required_evidence", [""]),
        (MetricPolicy, _standard_policy_payload, "formula_id", ""),
        (MetricPolicy, _standard_policy_payload, "reason_code", ""),
        (MetricPolicy, _standard_policy_payload, "policy_version", ""),
        (PolicyDecision, _standard_decision_payload, "metric_id", ""),
        (PolicyDecision, _standard_decision_payload, "evidence_ids", [""]),
        (PolicyDecision, _standard_decision_payload, "calculation_ids", [""]),
        (PolicyDecision, _standard_decision_payload, "reason_code", ""),
        (GateResult, _standard_gate_payload, "policy_version", ""),
        (GateResult, _standard_gate_payload, "reason_codes", [""]),
    ],
)
def test_policy_models_reject_empty_ids_reasons_and_versions(
    model_type: type[object],
    payload_factory: object,
    field_name: str,
    invalid_value: object,
) -> None:
    payload = payload_factory()  # type: ignore[operator]
    payload[field_name] = invalid_value

    with pytest.raises(ValidationError):
        model_type(**payload)  # type: ignore[operator]


def test_policy_model_list_defaults_are_independent() -> None:
    policy_payload = _standard_policy_payload()
    policy_payload.pop("required_evidence")
    first_policy = MetricPolicy(**policy_payload)
    second_policy = MetricPolicy(**policy_payload)
    first_policy.required_evidence.append("ev_one")

    decision_payload = _standard_decision_payload()
    decision_payload.pop("evidence_ids")
    decision_payload.pop("calculation_ids")
    first_decision = PolicyDecision(**decision_payload)
    second_decision = PolicyDecision(**decision_payload)
    first_decision.evidence_ids.append("ev_one")
    first_decision.calculation_ids.append("calc_one")

    gate_payload = _standard_gate_payload()
    gate_payload.pop("blocking_decisions")
    gate_payload.pop("non_blocking_decisions")
    gate_payload.pop("reason_codes")
    first_gate = GateResult(**gate_payload)
    second_gate = GateResult(**gate_payload)
    first_gate.reason_codes.append("reason_one")

    assert second_policy.required_evidence == []
    assert second_decision.evidence_ids == []
    assert second_decision.calculation_ids == []
    assert second_gate.blocking_decisions == []
    assert second_gate.non_blocking_decisions == []
    assert second_gate.reason_codes == []


@pytest.mark.parametrize(
    ("model_type", "expected_fields"),
    [
        (
            MetricPolicy,
            {
                "metric_id",
                "issuer_profile",
                "security_profile",
                "reporting_profile",
                "applicability",
                "required_evidence",
                "formula_id",
                "period_basis",
                "unit_policy",
                "gate_effect",
                "reason_code",
                "policy_version",
            },
        ),
        (
            PolicyDecision,
            {"metric_id", "status", "evidence_ids", "calculation_ids", "reason_code", "blocking"},
        ),
        (
            GateResult,
            {
                "status",
                "coverage_level",
                "blocking_decisions",
                "non_blocking_decisions",
                "reason_codes",
                "policy_version",
            },
        ),
    ],
)
def test_policy_json_schema_exposes_fields_and_forbids_extra(
    model_type: type[object], expected_fields: set[str]
) -> None:
    schema = model_type.model_json_schema()  # type: ignore[attr-defined]

    assert set(schema["properties"]) == expected_fields
    assert schema["additionalProperties"] is False


def test_policy_json_schema_exposes_status_values() -> None:
    decision_schema = PolicyDecision.model_json_schema()
    gate_schema = GateResult.model_json_schema()

    assert set(decision_schema["properties"]["status"]["enum"]) == {
        "available",
        "unavailable",
        "not_applicable",
        "invalid",
    }
    assert set(gate_schema["properties"]["status"]["enum"]) == {
        "ready",
        "blocked",
        "evidence_only",
        "unsupported",
    }
