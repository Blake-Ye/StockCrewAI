from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from stockcrewai import pipeline_support
from stockcrewai.crews.report.crew import (
    build_deterministic_report_draft,
    build_narrative_context,
    build_report_context,
    render_validated_report,
)
from stockcrewai.main import ResearchFlow
from stockcrewai.models.policy import PolicyDecision
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
from stockcrewai.tools.verdict_tool import DeterministicVerdictTool


def _bank_profile() -> ProfileResult:
    return ProfileResult(
        issuer_profile=IssuerProfile.BANK,
        security_profile=SecurityProfile.COMMON_STOCK,
        reporting_profile=ReportingProfile.DOMESTIC_US_GAAP,
        coverage_level=CoverageLevel.FULL,
        registry_version="profile-registry:v1",
    )


def _bank_policy_context() -> dict[str, Any]:
    profile = _bank_profile()
    policies = resolve_metric_policies(profile)
    decisions = evaluate_policy_decisions(policies, [], [])
    gate = pipeline_support._profile_policy_gate(profile, decisions)
    return {
        "profile": profile.model_dump(mode="json"),
        "coverage_level": profile.coverage_level.value,
        "policies": [policy.model_dump(mode="json") for policy in policies],
        "policy_decisions": [decision.model_dump(mode="json") for decision in decisions],
        "policy_version": POLICY_VERSION,
        "gate": gate.model_dump(mode="json"),
    }


def _valuation_policy_context() -> dict[str, Any]:
    context = _bank_policy_context()
    context["policy_decisions"] = [
        *context["policy_decisions"],
        PolicyDecision(
            metric_id="historical_valuation",
            status="not_applicable",
            reason_code="insufficient_history",
            blocking=False,
        ).model_dump(mode="json"),
        PolicyDecision(
            metric_id="reverse_dcf",
            status="not_applicable",
            reason_code="policy_not_applicable",
            blocking=False,
        ).model_dump(mode="json"),
    ]
    return context


def _bank_valuation_policy_context() -> dict[str, Any]:
    return {
        "profile": _bank_profile().model_dump(mode="json"),
        "policy_version": POLICY_VERSION,
        "policy_decisions": [
            PolicyDecision(
                metric_id="pe_ratio",
                status="not_applicable",
                reason_code="not_applicable_bank_pe_ratio",
                blocking=False,
            ).model_dump(mode="json"),
            PolicyDecision(
                metric_id="fcf_yield",
                status="not_applicable",
                reason_code="not_applicable_bank_fcf_yield",
                blocking=False,
            ).model_dump(mode="json"),
        ],
    }


def _complete_valuation_inputs() -> dict[str, Any]:
    return {
        "valuation_result": {
            "readiness": "ready",
            "validation_status": "valid",
            "market_price": "100",
            "market_price_evidence_id": "ev_price",
            "price_timestamp": "2026-08-06T15:30:00Z",
            "currency": "USD",
            "source_reference": "market:test",
            "calculations": [
                {
                    "calculation_id": "calc_pe_ratio",
                    "formula_id": "pe_ratio",
                    "display_result": "40.00",
                    "raw_result": "40",
                    "unit": "multiple",
                    "input_evidence_ids": ["ev_price", "ev_eps"],
                    "source_reference": "market:test",
                    "status": "available",
                    "validation_status": "valid",
                },
                {
                    "calculation_id": "calc_fcf_yield",
                    "formula_id": "fcf_yield",
                    "display_result": "0.01",
                    "raw_result": "0.01",
                    "unit": "percent",
                    "input_evidence_ids": ["ev_price", "ev_fcf"],
                    "source_reference": "market:test",
                    "status": "available",
                    "validation_status": "valid",
                },
            ],
        },
        "historical_valuation_result": {
            "status": "ok",
            "validation_status": "valid",
            "calculation_id": "calc_historical_pe",
            "input_evidence_ids": ["ev_history"],
            "current_value": "20",
            "five_year_median": "15",
            "percentile_25": "10",
            "percentile_75": "25",
            "current_percentile": "80",
            "current_date": "2026-08-06",
            "source_reference": "history:test",
            "series": [{"date": "2026-08-06", "pe_ratio": "20"}],
        },
        "reverse_dcf_result": {
            "status": "ok",
            "validation_status": "valid",
            "calculation_id": "calc_reverse_dcf_growth",
            "input_evidence_ids": ["ev_fcf"],
            "implied_growth": "0.12",
            "source_reference": "dcf:test",
            "as_of": "2026-08-06",
            "base_fcf": "100",
            "period_basis": "TTM",
            "forecast_years": 5,
            "discount_rate": "0.10",
            "terminal_growth": "0.03",
        },
        "validated_evidence_ids": [
            "ev_price",
            "ev_eps",
            "ev_fcf",
            "ev_history",
        ],
        "validated_calculation_ids": [
            "calc_pe_ratio",
            "calc_fcf_yield",
            "calc_historical_pe",
            "calc_reverse_dcf_growth",
        ],
    }


def _valuation_source_metadata() -> dict[str, Any]:
    return {
        "facts": {
            evidence_id: {
                "evidence_id": evidence_id,
                "source_reference": "fixture:test",
                "as_of": "2026-08-06",
            }
            for evidence_id in ("ev_price", "ev_eps", "ev_fcf", "ev_history")
        }
    }


def test_legacy_profile_is_adapted_to_shared_profile_policy_gate() -> None:
    context = pipeline_support.build_profile_policy_context(
        profile={
            "issuer_type": "bank",
            "security_type": "common_stock",
            "reporting_profile": "us_sec",
        },
        source_metadata={},
        facts={},
        calculations=[],
    )

    assert context["profile"]["issuer_profile"] == "bank"
    assert context["profile"]["coverage_level"] == "full"
    assert context["policy_version"] == POLICY_VERSION
    assert context["gate"]["status"] == "blocked"
    blocking = context["gate"]["blocking_decisions"]
    assert blocking
    assert all(item["metric_id"] and item["reason_code"] for item in blocking)


def test_flow_routes_from_profile_gate_and_exposes_one_json_safe_context() -> None:
    context = _bank_policy_context()
    flow = ResearchFlow()
    flow.state.profile = context["profile"]
    flow.state.policy_context = context
    flow._pipeline_state = {
        "profile": context["profile"],
        "policy_context": context,
        "facts": {},
        "calculations": [],
        "validated_evidence_ids": [],
        "validated_calculation_ids": [],
    }
    flow._risk_input = {"status": "unavailable"}
    flow._validation_result = SimpleNamespace(status="valid", validated=True)

    assert flow.route_analysis() == "analysis_blocked"

    result = flow._flow_result()
    assert result["profile"]["coverage_level"] == "full"
    assert result["policy_context"]["policy_version"] == POLICY_VERSION
    assert result["policy_context"]["gate"]["status"] == "blocked"
    assert result["policy_context"]["gate"]["blocking_decisions"]


def test_verdict_does_not_require_policy_not_applicable_metrics() -> None:
    policy_context = {
        "policy_version": POLICY_VERSION,
        "policy_decisions": [
            PolicyDecision(
                metric_id="historical_valuation",
                status="not_applicable",
                reason_code="insufficient_history",
                blocking=False,
            ).model_dump(mode="json"),
            PolicyDecision(
                metric_id="reverse_dcf",
                status="not_applicable",
                reason_code="policy_not_applicable",
                blocking=False,
            ).model_dump(mode="json"),
        ],
    }

    result = DeterministicVerdictTool().run(
        validation_status="valid",
        valuation={"readiness": "ready", "validation_status": "valid"},
        historical_valuation={"status": "unavailable"},
        reverse_dcf={"status": "unavailable"},
        risk_input={"status": "available", "risk_level": "medium"},
        policy_context=policy_context,
    )

    assert result.status == "ready"


def test_bank_profile_verdict_skips_unpublished_or_not_applicable_valuation() -> None:
    result = DeterministicVerdictTool().run(
        validation_status="valid",
        valuation={},
        historical_valuation={},
        reverse_dcf={},
        risk_input={"status": "available", "risk_level": "medium"},
        policy_context=_bank_valuation_policy_context(),
    )

    assert result.status == "ready"


def test_explicit_not_applicable_valuation_policy_filters_valuation_claims() -> None:
    valuation_inputs = _complete_valuation_inputs()
    valuation_inputs["policy_context"] = _valuation_policy_context()

    claims = pipeline_support.build_deterministic_valuation_claims(valuation_inputs)

    assert claims == []


def test_verdict_ignores_explicit_not_applicable_pe_and_fcf() -> None:
    valuation_inputs = _complete_valuation_inputs()
    verdict = DeterministicVerdictTool().run(
        validation_status="valid",
        valuation=valuation_inputs["valuation_result"],
        historical_valuation=valuation_inputs["historical_valuation_result"],
        reverse_dcf=valuation_inputs["reverse_dcf_result"],
        risk_input={"status": "available", "risk_level": "medium"},
        policy_context=_valuation_policy_context(),
    )

    assert verdict.status == "ready"
    assert verdict.overall_rating == "reasonable"
    assert verdict.triggered_rules == ["balanced_valuation"]


def test_report_context_preserves_market_price_but_filters_not_applicable_valuation() -> None:
    valuation_inputs = _complete_valuation_inputs()
    context = build_report_context(
        company={"name": "Example Bank", "ticker": "EXB"},
        deterministic_verdict={"status": "ready"},
        valuation=valuation_inputs["valuation_result"],
        historical_valuation=valuation_inputs["historical_valuation_result"],
        reverse_dcf=valuation_inputs["reverse_dcf_result"],
        source_metadata=_valuation_source_metadata(),
        policy_context=_valuation_policy_context(),
    )

    metric_ids = {metric["metric_id"] for metric in context["metrics"]}
    assert "market_price" in metric_ids
    assert not metric_ids & {
        "pe_ratio",
        "fcf_yield",
        "historical_pe_current",
        "historical_pe_median",
        "historical_pe_percentile_25",
        "historical_pe_percentile_75",
        "historical_percentile",
        "reverse_dcf_implied_growth",
    }
    assert context["historical_valuation"] == {}
    assert context["reverse_dcf"] == {}


def test_legacy_report_context_omits_profile_metadata_and_keeps_null_calculation_id() -> None:
    valuation_inputs = _complete_valuation_inputs()
    for policy_context in (None, {}):
        context = build_report_context(
            company={"name": "Example Holdings", "ticker": "EXM"},
            deterministic_verdict={"status": "ready"},
            valuation=valuation_inputs["valuation_result"],
            source_metadata=_valuation_source_metadata(),
            policy_context=policy_context,
        )

        assert not {
            "profile",
            "coverage_level",
            "policy_version",
        } & context.keys()
        market_price = next(
            metric for metric in context["metrics"] if metric["metric_id"] == "market_price"
        )
        assert "calculation_id" in market_price
        assert market_price["calculation_id"] is None


def test_report_context_and_renderer_show_profile_coverage_and_policy_version() -> None:
    context = build_report_context(
        company={"name": "Example Bank", "ticker": "EXB"},
        deterministic_verdict={"status": "insufficient_data"},
        policy_context=_bank_policy_context(),
    )

    narrative = build_narrative_context(context)
    report = render_validated_report(context, build_deterministic_report_draft())

    assert context["coverage_level"] == "full"
    assert context["profile"]["issuer_profile"] == "bank"
    assert context["policy_version"] == POLICY_VERSION
    assert narrative["coverage_level"] == "full"
    assert narrative["policy_version"] == POLICY_VERSION
    assert "覆盖范围：full" in report
    assert "Profile：issuer=bank" in report
    assert f"Policy version：{POLICY_VERSION}" in report
