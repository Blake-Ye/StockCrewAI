from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from stockcrewai.flow import ResearchFlow
from stockcrewai.crews.report.crew import (
    build_deterministic_report_draft,
    build_report_context,
    render_validated_report,
)
from stockcrewai.models.evidence import CalculationRecord, EvidenceRecord, MarketPriceRecord, ValidationStatus
from stockcrewai.models.profile import CoverageLevel, IssuerProfile, ProfileResult, ReportingProfile, SecurityProfile
from stockcrewai.pipelines.evidence_pipeline import build_profile_policy_context
from stockcrewai.pipelines.metric_registry import resolve_metric_policies


AS_OF = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _profile(*, security: SecurityProfile = SecurityProfile.ADR) -> ProfileResult:
    return ProfileResult(
        issuer_profile=IssuerProfile.STANDARD_OPERATING,
        security_profile=security,
        reporting_profile=ReportingProfile.FOREIGN_PRIVATE_ISSUER_IFRS,
        coverage_level=CoverageLevel.FULL,
        registry_version="profile-registry:v1",
    )


def _evidence(evidence_id: str, value: str, *, unit: str = "USD", currency: str = "USD") -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        source_reference=f"sec:test/{evidence_id}",
        as_of=AS_OF,
        filed_at=date(2026, 2, 20),
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        unit=unit,
        currency=currency,
        value=value,
        validation_status=ValidationStatus.VALID,
    )


def test_foreign_policy_rows_are_overlayed_without_domestic_adr_early_return() -> None:
    policies = resolve_metric_policies(_profile())

    assert [policy.metric_id for policy in policies][-3:] == [
        "adr_ratio",
        "adr_equivalent_shares",
        "adr_market_cap",
    ]
    assert {policy.policy_version for policy in policies[-3:]} == {
        "metric-policy:foreign-issuer:v1"
    }
    assert [policy.formula_id for policy in policies[-3:]] == [
        "foreign-adr-ratio-direct-v1",
        "foreign-adr-equivalent-shares-v1",
        "foreign-adr-market-cap-v1",
    ]


def test_foreign_missing_ratio_keeps_gate_ready_when_required_company_metrics_are_valid() -> None:
    revenue = _evidence("ev_revenue", "1000")
    prior_revenue = _evidence("ev_revenue_prior", "900")
    operating_income = _evidence("ev_operating_income", "100")
    ordinary = _evidence(
        "ev_ordinary_shares",
        "1000",
        unit="shares",
        currency="shares",
    )
    market_price = MarketPriceRecord(
        evidence_id="ev_adr_price",
        ticker="FPI",
        price="100",
        currency="USD",
        price_timestamp=AS_OF,
        source_reference="market:test/FPI",
        adjustment_basis="raw",
        validation_status=ValidationStatus.VALID,
    )
    calculations = [
        CalculationRecord(
            calculation_id="calc_revenue_growth",
            formula_id="revenue_growth:v1",
            input_evidence_ids=[revenue.evidence_id, prior_revenue.evidence_id],
            source_reference="derived:revenue_growth:v1",
            as_of=AS_OF,
            result="0.111111111111111111",
            unit="ratio",
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            validation_status=ValidationStatus.VALID,
        ),
        CalculationRecord(
            calculation_id="calc_operating_margin",
            formula_id="operating_margin:v1",
            input_evidence_ids=[operating_income.evidence_id, revenue.evidence_id],
            source_reference="derived:operating_margin:v1",
            as_of=AS_OF,
            result="0.1",
            unit="ratio",
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            validation_status=ValidationStatus.VALID,
        ),
    ]
    profile_input = {
        "profile_version": "foreign-issuer-profile:v1",
        "policy_version": "metric-policy:foreign-issuer:v1",
        "issuer_profile": "standard_operating",
        "security_profile": "adr",
        "reporting_profile": "foreign_private_issuer_ifrs",
        "coverage_level": "full",
        "as_of": AS_OF.isoformat(),
        "metric_inputs": {"ordinary_shares_outstanding": ordinary.evidence_id},
    }

    context = build_profile_policy_context(
        profile=profile_input,
        source_metadata={"filing_forms": ["20-F"], "taxonomy": ["ifrs-full"]},
        facts={},
        calculations=calculations,
        evidence_records=(revenue, prior_revenue, operating_income, ordinary),
        market_price_records=(market_price,),
    )
    decisions = {item["metric_id"]: item for item in context["policy_decisions"]}

    assert context["gate"]["status"] == "ready"
    assert decisions["adr_ratio"]["status"] == "unavailable"
    assert decisions["adr_ratio"]["blocking"] is False
    assert decisions["adr_market_cap"]["status"] == "unavailable"
    assert context["profile_version"] == "foreign-issuer-profile:v1"
    assert context["foreign_metadata"]["adr_ratio_reason_code"] == "adr_ratio_missing"

    flow = ResearchFlow()
    flow._profile_input = context["profile"]
    flow.state.profile = context["profile"]
    flow._profile_evidence_records = (revenue, prior_revenue, operating_income, ordinary)
    flow._profile_market_price_records = (market_price,)
    flow._pipeline_state = {
        "facts": {},
        "calculations": calculations,
    }
    flow._refresh_profile_policy_context(market_price.model_dump(mode="json"))
    assert flow.state.policy_context["gate"]["status"] == "ready"
    assert flow.route_analysis() == "analysis_ready"


def test_foreign_common_stock_overlay_marks_adr_rows_not_applicable() -> None:
    policies = resolve_metric_policies(_profile(security=SecurityProfile.COMMON_STOCK))

    assert [policy.metric_id for policy in policies][-3:] == [
        "adr_ratio",
        "adr_equivalent_shares",
        "adr_market_cap",
    ]
    assert all(policy.applicability.value == "not_applicable" for policy in policies[-3:])


def test_foreign_common_stock_context_is_not_applicable_without_adr_fabrication() -> None:
    context = build_profile_policy_context(
        profile=_profile(security=SecurityProfile.COMMON_STOCK),
        source_metadata={"filing_forms": ["20-F"], "taxonomy": ["ifrs-full"]},
        facts={},
        calculations=[],
        market_price_records=(_foreign_market_price(),),
    )

    assert context["foreign_metadata"]["adr_ratio_status"] == "not_applicable"
    assert (
        context["foreign_metadata"]["adr_ratio_reason_code"]
        == "foreign_adr_not_applicable"
    )
    assert context["values"]["adr_ratio"] is None


def test_foreign_unknown_classification_is_evidence_only_without_adr_fabrication() -> None:
    tsm_revenue = _evidence("ev_tsm_revenue", "1000")
    unknown_foreign_profile = ProfileResult(
        issuer_profile=IssuerProfile.UNKNOWN,
        security_profile=SecurityProfile.UNKNOWN,
        reporting_profile=ReportingProfile.FOREIGN_PRIVATE_ISSUER_IFRS,
        coverage_level=CoverageLevel.PARTIAL,
        registry_version="profile-registry:v1",
    )

    context = build_profile_policy_context(
        profile=unknown_foreign_profile,
        source_metadata={"filing_forms": ["20-F"], "taxonomy": ["ifrs-full"]},
        facts={},
        calculations=[],
        evidence_records=(tsm_revenue,),
        market_price_records=(),
    )

    assert context["gate"]["status"] == "evidence_only"
    assert "foreign_profile_incomplete" in context["gate"]["reason_codes"]
    assert context["foreign_metadata"]["adr_ratio_status"] == "unavailable"
    assert (
        context["foreign_metadata"]["adr_ratio_reason_code"]
        == "security_profile_unknown"
    )
    assert context["policies"] == []
    assert context["policy_decisions"] == []
    assert context["values"] == {}
    assert context["calculation_records"] == []
    assert context["evidence_records"][0]["evidence_id"] == "ev_tsm_revenue"


def test_foreign_unknown_flow_uses_sec_metadata_activation_without_legacy_valuation_gate() -> None:
    profile = ProfileResult(
        issuer_profile=IssuerProfile.UNKNOWN,
        security_profile=SecurityProfile.UNKNOWN,
        reporting_profile=ReportingProfile.FOREIGN_PRIVATE_ISSUER_IFRS,
        coverage_level=CoverageLevel.PARTIAL,
        registry_version="profile-registry:v1",
    )
    policy_context = build_profile_policy_context(
        profile=profile,
        source_metadata={"filing_forms": ["20-F"], "taxonomy": ["ifrs-full"]},
        facts={},
        calculations=[],
    )
    policy_context["policy_activation"] = "legacy_analysis_gate"
    valuation_tool = Mock()
    historical_valuation_tool = Mock()
    reverse_dcf_tool = Mock()
    flow = ResearchFlow(
        market_price_data=_foreign_market_price_data(),
        valuation_tool=valuation_tool,
        historical_valuation_tool=historical_valuation_tool,
        reverse_dcf_tool=reverse_dcf_tool,
    )
    flow.state.profile = policy_context["profile"]
    flow.state.policy_context = policy_context
    flow._pipeline_state = {
        "profile": policy_context["profile"],
        "policy_context": policy_context,
        "facts": {},
        "calculations": [],
    }

    valuation = flow.prepare_valuation(flow._pipeline_state)

    assert flow.state.policy_context["policy_activation"] == "sec_metadata"
    assert flow.state.policy_context["gate"]["status"] == "evidence_only"
    assert flow.route_analysis(valuation) == "analysis_ready"
    assert flow.state.required_data == []
    for tool in (valuation_tool, historical_valuation_tool, reverse_dcf_tool):
        tool.run.assert_not_called()


def test_foreign_evidence_only_claim_gate_accepts_empty_valuation_task() -> None:
    profile = ProfileResult(
        issuer_profile=IssuerProfile.UNKNOWN,
        security_profile=SecurityProfile.UNKNOWN,
        reporting_profile=ReportingProfile.FOREIGN_PRIVATE_ISSUER_IFRS,
        coverage_level=CoverageLevel.PARTIAL,
        registry_version="profile-registry:v1",
    )
    flow = ResearchFlow()
    flow.state.profile = profile.model_dump(mode="json")
    flow.state.policy_context = {
        "profile": flow.state.profile,
        "gate": {"status": "evidence_only"},
    }
    flow.state.valuation = {
        "status": "not_applicable",
        "reason_code": "foreign_currency_fx_not_implemented",
    }
    flow.state.historical_valuation = {
        "status": "not_applicable",
        "reason_code": "foreign_currency_fx_not_implemented",
    }
    flow.state.reverse_dcf = {
        "status": "not_applicable",
        "reason_code": "foreign_currency_fx_not_implemented",
    }
    flow._analysis_inputs = {
        "financial_analysis_input": {
            "validated_evidence_ids": ["ev_financial"],
        }
    }
    flow._risk_input = {"validated_filing_ids": ["ev_risk"]}
    flow._valuation_analysis_input = {
        "validated_evidence_ids": [],
        "validated_calculation_ids": ["calc_financial"],
    }
    analysis_result = SimpleNamespace(
        tasks_output=[
            SimpleNamespace(
                raw=json.dumps(
                    {
                        "claims": [
                            {
                                "claim_id": "claim_financial",
                                "category": "financial_quality",
                                "statement": "财务事实有已验证证据支持。",
                                "evidence_ids": ["ev_financial"],
                                "calculation_ids": ["calc_financial"],
                                "confidence": 0.8,
                            },
                            {
                                "claim_id": "claim_financial_trend",
                                "category": "financial_trend",
                                "statement": "财务趋势有已验证证据支持。",
                                "evidence_ids": ["ev_financial"],
                                "calculation_ids": ["calc_financial"],
                                "confidence": 0.8,
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            ),
            SimpleNamespace(
                raw=json.dumps(
                    {
                        "claims": [
                            {
                                "claim_id": "claim_risk",
                                "category": "risk",
                                "statement": "风险章节有已验证证据支持。",
                                "evidence_ids": ["ev_risk"],
                                "calculation_ids": [],
                                "confidence": 0.8,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ),
            SimpleNamespace(raw=json.dumps({"claims": []})),
        ]
    )

    assert flow.route_claims(analysis_result) == "claims_ready"
    assert flow.state.required_data == []
    assert {claim["category"] for claim in flow.state.analysis} == {
        "financial_quality",
        "financial_trend",
        "risk",
    }


def test_foreign_evidence_only_report_route_accepts_no_valuation_claims() -> None:
    profile = ProfileResult(
        issuer_profile=IssuerProfile.UNKNOWN,
        security_profile=SecurityProfile.UNKNOWN,
        reporting_profile=ReportingProfile.FOREIGN_PRIVATE_ISSUER_IFRS,
        coverage_level=CoverageLevel.PARTIAL,
        registry_version="profile-registry:v1",
    )
    report_crew = Mock()
    report_crew.kickoff.return_value = SimpleNamespace(
        raw=json.dumps(
            build_deterministic_report_draft().model_dump(mode="json"),
            ensure_ascii=False,
        )
    )
    flow = ResearchFlow(report_crew=report_crew)
    flow.state.profile = profile.model_dump(mode="json")
    flow.state.policy_context = {
        "profile": flow.state.profile,
        "gate": {"status": "evidence_only"},
    }
    flow.state.analysis = [
        {
            "claim_id": "claim_financial_quality",
            "category": "financial_quality",
            "statement": "财务事实有已验证证据支持。",
            "evidence_ids": ["ev_financial"],
            "calculation_ids": ["calc_financial"],
            "confidence": 0.8,
        },
        {
            "claim_id": "claim_financial_trend",
            "category": "financial_trend",
            "statement": "财务趋势有已验证证据支持。",
            "evidence_ids": ["ev_financial"],
            "calculation_ids": ["calc_financial"],
            "confidence": 0.8,
        },
        {
            "claim_id": "claim_risk",
            "category": "risk",
            "statement": "风险章节有已验证证据支持。",
            "evidence_ids": ["ev_risk"],
            "calculation_ids": [],
            "confidence": 0.8,
        },
    ]
    for field in ("valuation", "historical_valuation", "reverse_dcf"):
        setattr(
            flow.state,
            field,
            {
                "status": "not_applicable",
                "reason_code": "foreign_currency_fx_not_implemented",
            },
        )
    flow._validation_result = SimpleNamespace(status="valid")
    flow._pipeline_state = {
        "company_name": "TSM",
        "ticker": "TSM",
        "facts": {},
        "calculations": [],
    }
    flow._risk_input = {"filings": [], "validated_filing_ids": []}

    result = flow.generate_report()

    assert result["status"] == "ok"
    assert result["report"]
    assert result["verdict"]["status"] == "insufficient_data"
    assert result["verdict"]["policy_defined"] is False
    report_crew.kickoff.assert_called_once()


def test_foreign_metadata_reaches_json_context_and_renderer_without_numeric_fabrication() -> None:
    market_price = MarketPriceRecord(
        evidence_id="ev_adr_price",
        ticker="FPI",
        price="100",
        currency="USD",
        price_timestamp=AS_OF,
        source_reference="market:test/FPI",
        adjustment_basis="raw",
        validation_status=ValidationStatus.VALID,
    )
    context = build_profile_policy_context(
        profile=_profile(),
        source_metadata={
            "filing_forms": ["20-F", "6-K"],
            "filing_envelopes": [
                {
                    "form": "20-F",
                    "accession_number": "acc-20f",
                    "source_reference": "sec:test/20f",
                    "filed_at": "2026-02-20",
                }
            ],
            "taxonomy": ["ifrs-full"],
            "reporting_currency": "EUR",
        },
        facts={},
        calculations=[],
        market_price_records=(market_price,),
    )
    report_context = build_report_context(
        company={"name": "Foreign Example", "ticker": "FPI"},
        deterministic_verdict={"status": "blocked"},
        source_metadata={},
        policy_context=context,
    )

    metadata = report_context["profile_metrics"]["foreign_metadata"]
    assert metadata["filing_forms"] == ["20-F", "6-K"]
    assert metadata["ifrs_taxonomy"] == ["ifrs-full"]
    assert metadata["reporting_currency"] == "EUR"
    assert metadata["market_currency"] == "USD"
    assert metadata["adr_ratio_status"] == "unavailable"
    report = render_validated_report(report_context, build_deterministic_report_draft())
    assert "SEC foreign filings：20-F, 6-K" in report
    assert "ADR ratio 状态：unavailable" in report
    assert "ADR 等价市值：unavailable" in report
    assert "ADR 等价市值：0" not in report


def _foreign_profile_input(*, metric_inputs: dict[str, str] | None = None) -> dict[str, object]:
    return {
        "profile_version": "foreign-issuer-profile:v1",
        "policy_version": "metric-policy:foreign-issuer:v1",
        "issuer_profile": "standard_operating",
        "security_profile": "adr",
        "reporting_profile": "foreign_private_issuer_ifrs",
        "coverage_level": "full",
        "as_of": AS_OF.isoformat(),
        "metric_inputs": metric_inputs or {},
    }


def _foreign_market_price_data() -> dict[str, object]:
    return {
        "status": "ok",
        "ticker": "FPI",
        "market_price": "100",
        "currency": "USD",
        "price_timestamp": AS_OF.isoformat(),
        "source_reference": "market:test/FPI",
        "historical_prices": [],
    }


def _foreign_market_price() -> MarketPriceRecord:
    return MarketPriceRecord(
        evidence_id="ev_base_market_price",
        ticker="FPI",
        price="100",
        currency="USD",
        price_timestamp=AS_OF,
        source_reference="market:test/FPI",
        adjustment_basis="raw",
        validation_status=ValidationStatus.VALID,
    )


def test_foreign_prepare_valuation_skips_usd_tools_and_keeps_typed_market_price() -> None:
    profile = _foreign_profile_input()
    policy_context = {"profile": profile, "policy_version": profile["policy_version"]}
    valuation_tool = Mock()
    historical_valuation_tool = Mock()
    reverse_dcf_tool = Mock()
    valuation_tool.run.return_value = {
        "status": "unavailable",
        "readiness": "not_ready",
        "calculations": [],
    }
    historical_valuation_tool.run.return_value = {"status": "unavailable"}
    reverse_dcf_tool.run.return_value = {"status": "unavailable"}
    flow = ResearchFlow(
        profile_input=profile,
        market_price_data=_foreign_market_price_data(),
        valuation_tool=valuation_tool,
        historical_valuation_tool=historical_valuation_tool,
        reverse_dcf_tool=reverse_dcf_tool,
    )
    flow.state.profile = profile
    flow.state.policy_context = policy_context
    flow._pipeline_state = {
        "company_name": "Foreign Example",
        "ticker": "FPI",
        "profile": profile,
        "policy_context": policy_context,
        "facts": {},
        "calculations": [],
    }

    valuation = flow.prepare_valuation(flow._pipeline_state)

    for tool in (valuation_tool, historical_valuation_tool, reverse_dcf_tool):
        tool.run.assert_not_called()
    assert valuation["status"] == "not_applicable"
    assert valuation["reason_code"] == "foreign_currency_fx_not_implemented"
    assert valuation["calculations"] == []
    assert flow.state.historical_valuation["status"] == "not_applicable"
    assert flow.state.reverse_dcf["status"] == "not_applicable"
    assert flow.state.market_price_data["market_price"] == "100"
    assert "market_capitalization" not in valuation
    assert "pe_ratio" not in valuation
    assert "fcf_yield" not in valuation


@pytest.mark.parametrize("include_ratio", [False, True])
def test_foreign_report_context_keeps_valid_base_calculation_and_adr_status(
    include_ratio: bool,
) -> None:
    revenue = _evidence("ev_base_revenue", "1000")
    prior_revenue = _evidence("ev_base_revenue_prior", "900")
    ratio = _evidence(
        "ev_base_ratio", "2", unit="ratio", currency="ratio"
    )
    ordinary = _evidence(
        "ev_base_ordinary_shares", "1000", unit="shares", currency="shares"
    )
    evidence = [revenue, prior_revenue, ordinary, *([ratio] if include_ratio else [])]
    metric_inputs = {"ordinary_shares_outstanding": ordinary.evidence_id}
    if include_ratio:
        metric_inputs["ordinary_shares_per_adr"] = ratio.evidence_id
    base_calculation = CalculationRecord(
        calculation_id="calc_base_revenue_growth",
        formula_id="revenue_growth:v1",
        input_evidence_ids=[revenue.evidence_id, prior_revenue.evidence_id],
        source_reference="derived:test/revenue_growth",
        as_of=AS_OF,
        result="0.111111111111111111",
        unit="ratio",
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        validation_status=ValidationStatus.VALID,
    )
    market_price = _foreign_market_price()
    policy_context = build_profile_policy_context(
        profile=_foreign_profile_input(metric_inputs=metric_inputs),
        source_metadata={"filing_forms": ["20-F"], "taxonomy": ["ifrs-full"]},
        facts={},
        calculations=(base_calculation,),
        evidence_records=tuple(evidence),
        market_price_records=(market_price,),
    )

    report_context = build_report_context(
        company={"name": "Foreign Example", "ticker": "FPI"},
        deterministic_verdict={"status": "ready"},
        source_metadata={},
        policy_context=policy_context,
    )

    metrics = {metric["metric_id"]: metric for metric in report_context["metrics"]}
    assert metrics["revenue_growth"]["calculation_id"] == "calc_base_revenue_growth"
    assert metrics["revenue_growth"]["evidence_ids"] == [
        "ev_base_revenue",
        "ev_base_revenue_prior",
    ]
    assert metrics["revenue_growth"]["source_reference"] == "derived:test/revenue_growth"
    assert metrics["revenue_growth"]["validation_status"] == "valid"
    adr_decision = next(
        item
        for item in policy_context["policy_decisions"]
        if item["metric_id"] == "adr_market_cap"
    )
    if include_ratio:
        assert adr_decision["status"] == "available"
        assert metrics["adr_market_cap"]["calculation_id"]
    else:
        assert adr_decision["status"] == "unavailable"
        assert "adr_market_cap" not in metrics
    assert "revenue_growth" in report_context["profile_metrics"]["calculation_records"][0]["formula_id"]
