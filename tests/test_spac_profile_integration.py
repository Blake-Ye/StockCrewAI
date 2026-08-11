from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from stockcrewai.models.evidence import EvidenceRecord, MarketPriceRecord
from stockcrewai.models.policy import Applicability, GateEffect
from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ProfileResult,
    ReportingProfile,
    SecurityProfile,
)
from stockcrewai.pipelines.evidence_pipeline import build_profile_policy_context
from stockcrewai.pipelines.metric_registry import (
    policy_version_for_profile,
    resolve_metric_policies,
)
from stockcrewai.quant.factors import compute_factor_observations
from stockcrewai.reporting.context import ReportContext, build_report_context
from stockcrewai.reporting.renderer import (
    build_deterministic_report_draft,
    render_validated_report,
)
from stockcrewai.tools.verdict_tool import DeterministicVerdictTool


FIXTURE = Path(__file__).parent / "fixtures" / "profiles" / "spac" / "complete_pre_merger.json"
SPAC_METRIC_IDS = (
    "spac_trust_cash",
    "spac_warrant_dilution_ratio",
    "spac_pro_forma_shares",
    "spac_cash_per_pro_forma_share",
)
SPAC_FORMULAS = {
    "spac_trust_cash": "spac-trust-cash-direct-v1",
    "spac_warrant_dilution_ratio": "spac-warrant-dilution-ratio-v1",
    "spac_pro_forma_shares": "spac-pro-forma-shares-v1",
    "spac_cash_per_pro_forma_share": "spac-cash-per-pro-forma-share-v1",
}


def _fixture() -> dict[str, Any]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    profile = dict(payload["profile_input"])
    profile.update(
        {
            "profile_version": "spac-profile:v1",
            "policy_version": "metric-policy:spac:v1",
            "issuer_profile": "standard_operating",
            "reporting_profile": "domestic_us_gaap",
            "coverage_level": "full",
        }
    )
    payload["profile_input"] = profile
    return payload


def _typed_records(
    fixture: dict[str, Any],
) -> tuple[tuple[EvidenceRecord, ...], tuple[MarketPriceRecord, ...]]:
    return (
        tuple(EvidenceRecord.model_validate(item) for item in fixture["evidence_records"]),
        tuple(
            MarketPriceRecord.model_validate(item)
            for item in fixture["market_price_records"]
        ),
    )


def _policy_context(fixture: dict[str, Any] | None = None) -> dict[str, Any]:
    fixture = fixture or _fixture()
    evidence_records, market_price_records = _typed_records(fixture)
    return build_profile_policy_context(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )


def _market_only_fixture() -> dict[str, Any]:
    fixture = _fixture()
    fixture["evidence_records"] = []
    return fixture


@pytest.mark.parametrize("coverage", [CoverageLevel.FULL, CoverageLevel.PARTIAL])
def test_spac_registry_publishes_only_typed_structure_policies(
    coverage: CoverageLevel,
) -> None:
    profile = ProfileResult(
        issuer_profile=IssuerProfile.STANDARD_OPERATING,
        security_profile=SecurityProfile.SPAC,
        reporting_profile=ReportingProfile.DOMESTIC_US_GAAP,
        coverage_level=coverage,
        registry_version="profile-registry:test-input",
    )

    policies = resolve_metric_policies(profile)

    assert tuple(policy.metric_id for policy in policies) == SPAC_METRIC_IDS
    assert all(policy.applicability is Applicability.OPTIONAL for policy in policies)
    assert all(policy.gate_effect is GateEffect.NON_BLOCKING for policy in policies)
    assert {policy.metric_id: policy.formula_id for policy in policies} == SPAC_FORMULAS
    assert all(policy.policy_version == "metric-policy:spac:v1" for policy in policies)
    assert policy_version_for_profile(profile) == "metric-policy:spac:v1"
    assert policy_version_for_profile(
        profile.model_copy(update={"reporting_profile": ReportingProfile.FOREIGN_PRIVATE_ISSUER_IFRS})
    ) == "metric-policy:spac:v1"
    assert not {
        "revenue_growth",
        "operating_margin",
        "pe_ratio",
        "fcf_yield",
    }.intersection(policy.metric_id for policy in policies)

    assert resolve_metric_policies(
        profile.model_copy(update={"coverage_level": CoverageLevel.EVIDENCE_ONLY})
    ) == ()


def test_spac_typed_pipeline_is_evidence_only_and_preserves_provenance() -> None:
    context = _policy_context()
    decisions = {item["metric_id"]: item for item in context["policy_decisions"]}
    calculations = {
        item["formula_id"]: item for item in context["calculation_records"]
    }

    assert context["profile"]["security_profile"] == "spac"
    assert context["profile_version"] == "spac-profile:v1"
    assert context["policy_version"] == "metric-policy:spac:v1"
    assert context["profile_envelope"] == {
        "status": "valid",
        "reason_code": "typed_profile_envelope_valid",
    }
    assert context["gate"]["status"] == "evidence_only"
    assert tuple(item["metric_id"] for item in context["policies"]) == SPAC_METRIC_IDS
    assert tuple(decisions) == SPAC_METRIC_IDS
    assert all(item["status"] == "available" for item in decisions.values())
    assert context["values"] == {
        "spac_trust_cash": "1200",
        "spac_warrant_dilution_ratio": "0.25",
        "spac_pro_forma_shares": "125",
        "spac_cash_per_pro_forma_share": "9.6",
    }
    assert decisions["spac_trust_cash"]["calculation_ids"] == []
    assert decisions["spac_trust_cash"]["evidence_ids"] == ["ev_spac_trust_cash"]
    assert set(calculations) == set(SPAC_FORMULAS.values()) - {
        "spac-trust-cash-direct-v1"
    }
    assert all(
        decision["blocking"] is False
        for decision in decisions.values()
    )
    assert {record["evidence_id"] for record in context["evidence_records"]} == {
        "ev_spac_trust_cash",
        "ev_spac_basic_shares",
        "ev_spac_warrants",
    }
    assert len(context["market_price_records"]) == 2


def test_spac_market_only_profile_envelope_is_unavailable() -> None:
    context = _policy_context(_market_only_fixture())

    assert context["profile_envelope"] == {
        "status": "unavailable",
        "reason_code": "typed_profile_envelope_required",
    }
    assert all(value is None for value in context["values"].values())
    assert context["calculation_records"] == []
    assert context["evidence_records"] == []
    assert len(context["market_price_records"]) == 2


def test_spac_invalid_typed_envelope_is_unavailable_but_remains_evidence_only() -> None:
    fixture = _fixture()
    profile = deepcopy(fixture["profile_input"])
    profile["profile_version"] = "spac-profile:v0"
    evidence_records, market_price_records = _typed_records(fixture)

    context = build_profile_policy_context(
        profile=profile,
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )

    assert context["profile_envelope"] == {
        "status": "unavailable",
        "reason_code": "typed_profile_envelope_required",
    }
    assert all(value is None for value in context["values"].values())
    assert context["calculation_records"] == []
    assert all(
        decision["status"] == "unavailable"
        for decision in context["policy_decisions"]
    )
    assert context["gate"]["status"] == "evidence_only"


def test_spac_report_context_and_renderer_show_only_structure_metrics() -> None:
    fixture = _fixture()
    policy_context = _policy_context(fixture)
    report_context = ReportContext.model_validate(
        build_report_context(
            company={"name": "Synthetic SPAC", "ticker": "SPAC"},
            deterministic_verdict={
                "status": "insufficient_data",
                "overall_rating": "insufficient_data",
                "risk_level": "insufficient_data",
                "triggered_rules": ["spac_security_structure_evidence_only"],
            },
            validated_claims=[],
            valuation={
                "status": "not_applicable",
                "readiness": "not_applicable",
                "validation_status": "unvalidated",
                "reason_code": "spac_security_structure_not_applicable",
                "calculations": [],
            },
            historical_valuation={
                "status": "not_applicable",
                "readiness": "not_applicable",
                "validation_status": "unvalidated",
                "reason_code": "spac_security_structure_not_applicable",
                "calculations": [],
            },
            reverse_dcf={
                "status": "not_applicable",
                "readiness": "not_applicable",
                "validation_status": "unvalidated",
                "reason_code": "spac_security_structure_not_applicable",
                "calculations": [],
            },
            policy_context=policy_context,
        )
    )

    assert report_context.profile_metrics is not None
    assert report_context.profile_metrics["profile_version"] == "spac-profile:v1"
    assert report_context.profile_metrics["metric_ids"] == list(SPAC_METRIC_IDS)
    report_metrics = {metric.metric_id: metric for metric in report_context.metrics}
    assert set(report_metrics) == set(SPAC_METRIC_IDS)
    assert report_metrics["spac_trust_cash"].provenance_type == "direct_evidence"
    assert report_metrics["spac_trust_cash"].calculation_id is None
    assert report_metrics["spac_trust_cash"].evidence_ids == ["ev_spac_trust_cash"]
    for metric_id in SPAC_METRIC_IDS[1:]:
        assert report_metrics[metric_id].provenance_type == "calculation"
        assert report_metrics[metric_id].calculation_id
        assert report_metrics[metric_id].evidence_ids
        assert report_metrics[metric_id].validation_status == "valid"

    report = render_validated_report(
        report_context=report_context,
        report_draft=build_deterministic_report_draft(),
    )
    for text in (
        "### SPAC 证券结构指标",
        "SPAC 信托现金",
        "SPAC 认股权证稀释率",
        "SPAC 备考股数",
        "SPAC 每备考股信托现金",
        "1200.00 USD",
        "25.00%",
        "125.00 shares",
        "9.60 USD/share",
        "SPAC evidence-only",
        "不构成评级",
    ):
        assert text in report
    assert "当前估值" not in report
    assert "历史估值" not in report
    assert "反向 DCF" not in report
    assert "财务趋势" not in report
    assert "P/E" not in report
    assert "FCF Yield" not in report


def test_spac_verdict_precedes_all_ordinary_logic() -> None:
    result = DeterministicVerdictTool().run(
        validation_status="valid",
        valuation={"readiness": "ready", "validation_status": "valid"},
        historical_valuation={"status": "ok", "validation_status": "valid"},
        reverse_dcf={"status": "ok", "validation_status": "valid"},
        risk_input={"status": "available", "risk_level": "low"},
        policy_context={
            "profile": {"security_profile": "spac"},
            "gate": {"status": "ready"},
        },
    )

    assert result.status == "insufficient_data"
    assert result.policy_defined is False
    assert result.is_investment_rating is False
    assert result.overall_rating == "insufficient_data"
    assert result.summary_code == "SPAC_EVIDENCE_ONLY"
    assert result.triggered_rules == ["spac_security_structure_evidence_only"]
    assert result.reasons == ["spac_security_structure_evidence_only"]


def test_spac_quant_observations_are_not_applicable_without_provenance() -> None:
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "quant" / "factors" / "snapshots.json").read_text(
            encoding="utf-8"
        )
    )
    standard = next(item for item in payload["cases"] if item["name"] == "standard_operating")
    from stockcrewai.models.quant import PointInTimeSnapshot

    snapshot = PointInTimeSnapshot.model_validate(standard["snapshot"]).model_copy(
        update={"security_profile": SecurityProfile.SPAC}
    )

    observations = compute_factor_observations([snapshot], "factor-formulas-v1")

    assert len(observations) == 17
    assert all(item.status == "not_applicable" for item in observations)
    assert all(item.reason_code == "security_profile_not_applicable" for item in observations)
    assert all(item.raw_value is None for item in observations)
    assert all(item.evidence_ids == [] and item.calculation_ids == [] for item in observations)
