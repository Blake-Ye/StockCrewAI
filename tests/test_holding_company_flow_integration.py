from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

from stockcrewai.flow import ResearchFlow, _allow_empty_foreign_valuation_claims
from stockcrewai.models.evidence import EvidenceRecord, MarketPriceRecord
from stockcrewai.tools.calculator_tool import CalculationBatch, CalculationResult
from stockcrewai.tools.edgar_tool import (
    EdgarFact,
    EdgarFilingEvidence,
    EdgarResult,
    EdgarRiskEligibility,
    EdgarRiskSection,
)
from stockcrewai.tools.validation_tool import ValidationResult


FIXTURE = Path(__file__).parent / "fixtures" / "profiles" / "holding_company" / "complete.json"
ORDINARY_VALUATION_TOOL_NAMES = (
    "valuation_tool",
    "historical_valuation_tool",
    "reverse_dcf_tool",
)


def _fixture() -> dict[str, Any]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["synthetic"] is True
    assert payload["offline"] is True
    assert payload["no_network"] is True
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


def _offline_edgar_result() -> EdgarResult:
    fact = EdgarFact(
        metric_id="net_income",
        evidence_id="ev_flow_financial",
        value="1",
        unit="USD",
        period_end="2025-12-31",
        filed_at="2026-02-20",
        form="10-K",
        source_reference="fixture:holding-company/flow/financial",
    )
    filing = EdgarFilingEvidence(
        evidence_id="ev_flow_filing",
        cik="0000000001",
        form="10-K",
        filed_at="2026-02-20",
        period_end="2025-12-31",
        accession_number="fixture-flow-10k",
        source_reference="fixture:holding-company/flow/10-k",
        text="Synthetic risk section.",
        text_source_reference="fixture:holding-company/flow/10-k/text",
        risk_sections=[
            EdgarRiskSection(
                section_type="10k_item_1a",
                section_title="Item 1A. Risk Factors",
                text="Synthetic supply-chain risk.",
                complete=True,
            )
        ],
        risk_eligibility=EdgarRiskEligibility(
            evidence_id="ev_flow_filing",
            eligibility="eligible",
            evidence_kind="item_1a",
            reason_code="eligible_item_1a",
            section_title="Item 1A. Risk Factors",
            filed_at="2026-02-20",
            source_reference="fixture:holding-company/flow/10-k",
        ),
        text_retrieval_status="available",
    )
    return EdgarResult(
        status="ok",
        company_name="Synthetic Holding Company",
        ticker="HOLD",
        sec_registrant_profile="holding_company",
        sec_security_profile="common_stock",
        sec_reporting_profile="domestic_us_gaap",
        facts={"net_income": fact},
        filings=[filing],
    )


def _offline_calculation_result() -> CalculationBatch:
    return CalculationBatch(
        status="ok",
        company_name="Synthetic Holding Company",
        ticker="HOLD",
        calculations=[
            CalculationResult(
                calculation_id="calc_flow_net_margin",
                formula_id="net_margin",
                input_evidence_ids=["ev_flow_financial"],
                raw_inputs={"net_income": "1", "revenue_current": "2"},
                raw_result="0.5",
                normalized_result="5.00000E-1",
                display_result="50.00%",
                unit="ratio",
                status="available",
            )
        ],
    )


def _holding_flow() -> tuple[ResearchFlow, dict[str, Mock]]:
    fixture = _fixture()
    evidence_records, market_price_records = _typed_records(fixture)
    edgar_tool = Mock(name="edgar_tool")
    edgar_tool.run.return_value = _offline_edgar_result()
    calculator_tool = Mock(name="calculator_tool")
    calculator_tool.run.return_value = _offline_calculation_result()
    validation_tool = Mock(name="validation_tool")
    validation_tool.run.return_value = ValidationResult(
        status="valid",
        validated=True,
        company_name="Synthetic Holding Company",
        ticker="HOLD",
        validated_evidence_ids=["ev_flow_financial"],
        validated_calculation_ids=["calc_flow_net_margin"],
        validated_filing_ids=["ev_flow_filing"],
    )
    ttm_builder_tool = Mock(name="ttm_builder_tool")
    ttm_builder_tool.run.return_value = {
        "status": "unavailable",
        "company_name": "Synthetic Holding Company",
        "ticker": "HOLD",
        "metrics": [],
        "warnings": [],
        "reason_code": "fixture_ttm_unavailable",
    }
    market_price_data = {
        **market_price_records[0].model_dump(mode="json"),
        "status": "ok",
        "market_price": market_price_records[0].price,
        "historical_prices": [],
    }
    ordinary_tools = {
        name: Mock(name=name) for name in ORDINARY_VALUATION_TOOL_NAMES
    }
    with patch.dict(os.environ, {"STOCKCREWAI_RUNTIME_METRICS": "0"}):
        flow = ResearchFlow(
            edgar_tool=edgar_tool,
            calculator_tool=calculator_tool,
            validation_tool=validation_tool,
            ttm_builder_tool=ttm_builder_tool,
            market_price_data=market_price_data,
            valuation_tool=ordinary_tools["valuation_tool"],
            historical_valuation_tool=ordinary_tools["historical_valuation_tool"],
            reverse_dcf_tool=ordinary_tools["reverse_dcf_tool"],
            profile_input=fixture["profile_input"],
            profile_evidence_records=evidence_records,
            profile_market_price_records=market_price_records,
        )
    return flow, {
        "edgar_tool": edgar_tool,
        "calculator_tool": calculator_tool,
        "validation_tool": validation_tool,
        "ttm_builder_tool": ttm_builder_tool,
        **ordinary_tools,
    }


def _prepare_holding_flow() -> tuple[ResearchFlow, dict[str, Mock], dict[str, Any]]:
    flow, tools = _holding_flow()
    evidence_state = flow.prepare_evidence(
        {"company_name_guess": "Synthetic Holding Company", "ticker_guess": "HOLD"}
    )
    valuation_state = flow.prepare_valuation(evidence_state)
    return flow, tools, valuation_state


def _holding_analysis_result(flow: ResearchFlow) -> SimpleNamespace:
    flow._analysis_inputs = {
        "financial_analysis_input": {
            "validated_evidence_ids": list(
                flow._pipeline_state["validated_evidence_ids"]
            ),
            "validated_calculation_ids": list(
                flow._pipeline_state["validated_calculation_ids"]
            ),
        }
    }
    flow._valuation_analysis_input = {
        "validated_evidence_ids": [],
        "validated_calculation_ids": [],
    }
    return SimpleNamespace(
        tasks_output=[
            SimpleNamespace(
                raw=json.dumps(
                    {
                        "claims": [
                            {
                                "claim_id": "claim_holding_financial_quality",
                                "category": "financial_quality",
                                "statement": "Holding NAV input is traceable.",
                                "evidence_ids": ["ev_flow_financial"],
                                "calculation_ids": ["calc_flow_net_margin"],
                                "confidence": 0.9,
                            },
                            {
                                "claim_id": "claim_holding_financial_trend",
                                "category": "financial_trend",
                                "statement": "Holding evidence remains bounded.",
                                "evidence_ids": ["ev_flow_financial"],
                                "calculation_ids": ["calc_flow_net_margin"],
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
                                "claim_id": "claim_holding_risk",
                                "category": "risk",
                                "statement": "Synthetic filing contains a risk section.",
                                "evidence_ids": ["ev_flow_filing"],
                                "calculation_ids": [],
                                "confidence": 0.8,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ),
            SimpleNamespace(raw=json.dumps({"claims": []}, ensure_ascii=False)),
        ]
    )


def _ready_holding_flow() -> tuple[ResearchFlow, dict[str, Mock]]:
    flow, tools, valuation_state = _prepare_holding_flow()
    assert flow.route_analysis(valuation_state) == "analysis_ready"
    return flow, tools


def test_holding_prepare_valuation_skips_ordinary_tools_and_persists_nav_reason() -> None:
    flow, tools, valuation_state = _prepare_holding_flow()

    for tool_name in ORDINARY_VALUATION_TOOL_NAMES:
        tools[tool_name].run.assert_not_called()
    payload = flow.state.model_dump(mode="json")
    assert valuation_state["reason_code"] == "holding_company_nav_primary_valuation"
    assert payload["valuation"]["reason_code"] == "holding_company_nav_primary_valuation"
    assert payload["historical_valuation"]["reason_code"] == (
        "holding_company_nav_primary_valuation"
    )
    assert payload["reverse_dcf"]["reason_code"] == (
        "holding_company_nav_primary_valuation"
    )
    assert Decimal(flow.state.policy_context["values"]["holding_company_nav"]) == Decimal(
        "680"
    )


def test_holding_route_analysis_uses_ready_nav_policy() -> None:
    flow, _ = _ready_holding_flow()

    assert flow.state.policy_context["gate"]["status"] == "ready"
    nav_decision = next(
        item
        for item in flow.state.policy_context["policy_decisions"]
        if item["metric_id"] == "holding_company_nav"
    )
    assert nav_decision["status"] == "available"
    assert flow.state.required_data == []
    assert flow.state.status == "running"


def test_holding_claim_gate_accepts_empty_ordinary_valuation_claims() -> None:
    flow, _ = _ready_holding_flow()

    route = flow.route_claims(_holding_analysis_result(flow))

    assert route == "claims_ready"
    assert flow.state.required_data == []
    assert {claim["category"] for claim in flow.state.analysis} == {
        "financial_quality",
        "financial_trend",
        "risk",
    }
    assert flow.state.analysis_diagnostics == {}


def test_holding_generate_report_uses_nav_only_verdict_and_report_context() -> None:
    flow, _ = _ready_holding_flow()
    assert flow.route_claims(_holding_analysis_result(flow)) == "claims_ready"
    flow_module = __import__("stockcrewai.flow", fromlist=["build_report_context"])
    captured: dict[str, Any] = {}
    original_build_report_context = flow_module.build_report_context

    def capture_report_context(**kwargs: Any) -> dict[str, Any]:
        context = original_build_report_context(**kwargs)
        captured["value"] = context
        return context

    report_crew = Mock(name="report_crew")
    report_crew.kickoff.return_value = SimpleNamespace(raw="ignored by patched parser")
    report_factory = Mock(name="ReportCrew")
    report_factory.return_value.crew.return_value = report_crew

    with (
        patch.object(flow_module, "ReportCrew", report_factory),
        patch.object(
            flow_module, "build_report_context", side_effect=capture_report_context
        ),
        patch.object(flow_module, "parse_report_draft", return_value=object()),
        patch.object(flow_module, "render_validated_report", return_value="# report"),
        patch.object(flow_module, "validate_rendered_report", return_value=(True, "")),
    ):
        result = flow.generate_report()

    assert result["verdict"] == {
        "status": "insufficient_data",
        "policy_defined": False,
        "is_investment_rating": False,
        "business_quality": "insufficient_data",
        "financial_trend": "insufficient_data",
        "valuation": "insufficient_data",
        "risk_level": "insufficient_data",
        "overall_rating": "insufficient_data",
        "summary_code": "HOLDING_COMPANY_NAV_ONLY",
        "triggered_rules": ["holding_company_nav_only"],
        "reasons": ["holding_company_nav_primary_valuation"],
        "rules_version": "v1",
    }
    context = captured["value"]
    assert Decimal(context["profile_metrics"]["values"]["holding_company_nav"]) == Decimal(
        "680"
    )
    assert any(
        metric["metric_id"] == "holding_company_nav"
        and metric["status"] == "available"
        for metric in context["metrics"]
    )
    ordinary_ids = {
        "pe_ratio",
        "fcf_yield",
        "historical_valuation",
        "reverse_dcf",
    }
    assert not any(
        metric["metric_id"] in ordinary_ids and metric["status"] == "available"
        for metric in context["metrics"]
    )
    assert context["historical_valuation"] == {}
    assert context["reverse_dcf"] == {}
    report_factory.assert_called_once_with()
    report_crew.kickoff.assert_called_once()


def test_ready_holding_nav_allows_empty_valuation_claims() -> None:
    valuation = {
        "status": "not_applicable",
        "readiness": "not_applicable",
        "validation_status": "unvalidated",
        "reason_code": "holding_company_nav_primary_valuation",
    }
    policy_context = {
        "profile": {
            "issuer_profile": "holding_company",
            "reporting_profile": "domestic_us_gaap",
        },
        "policy_decisions": [
            {"metric_id": "holding_company_nav", "status": "available"}
        ],
        "gate": {"status": "ready"},
    }

    assert _allow_empty_foreign_valuation_claims(
        policy_context,
        valuation,
        valuation,
        valuation,
    )
