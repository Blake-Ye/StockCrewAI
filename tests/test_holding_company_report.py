from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stockcrewai.models.evidence import EvidenceRecord, MarketPriceRecord
from stockcrewai.pipelines.evidence_pipeline import build_profile_policy_context
from stockcrewai.reporting.context import ReportContext, build_report_context
from stockcrewai.reporting.renderer import (
    build_deterministic_report_draft,
    render_validated_report,
)


FIXTURE = Path(__file__).parent / "fixtures" / "profiles" / "holding_company" / "complete.json"
HOLDING_METRIC_IDS = (
    "attributable_holdings_value",
    "holding_company_nav",
    "holding_company_market_cap",
    "holding_company_nav_discount",
)
ORDINARY_VALUATION_IDS = {
    "pe_ratio",
    "fcf_yield",
    "historical_valuation",
    "reverse_dcf",
}


def _report_context() -> tuple[dict[str, Any], ReportContext]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["synthetic"] is True
    assert fixture["offline"] is True
    assert fixture["no_network"] is True

    evidence_records = tuple(
        EvidenceRecord.model_validate(item) for item in fixture["evidence_records"]
    )
    market_price_records = tuple(
        MarketPriceRecord.model_validate(item)
        for item in fixture["market_price_records"]
    )
    policy_context = build_profile_policy_context(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )
    source_metadata = {
        "facts": {
            record.evidence_id: record.model_dump(mode="json")
            for record in evidence_records
        },
        "market_price": market_price_records[0].model_dump(mode="json"),
    }
    report_context = ReportContext.model_validate(
        build_report_context(
            company={"name": "Synthetic Holding Company", "ticker": "HOLD"},
            deterministic_verdict={"status": policy_context["gate"]["status"]},
            source_metadata=source_metadata,
            policy_context=policy_context,
        )
    )
    return policy_context, report_context


def test_complete_holding_report_context_exposes_metrics_and_provenance() -> None:
    policy_context, report_context = _report_context()

    assert policy_context["profile"]["issuer_profile"] == "holding_company"
    assert policy_context["profile_envelope"] == {
        "status": "valid",
        "reason_code": "typed_profile_envelope_valid",
    }
    assert policy_context["gate"]["status"] == "ready"
    assert report_context.profile_metrics is not None
    assert report_context.profile_metrics["profile_version"] == "holding-company-profile:v1"
    assert set(HOLDING_METRIC_IDS) <= set(report_context.profile_metrics["metric_ids"])

    expected_provenance = {
        "attributable_holdings_value": (
            [
                "ev_holding_a_fair_value",
                "ev_holding_a_ownership",
                "ev_holding_b_fair_value",
                "ev_holding_b_ownership",
            ],
            "calc_holding_attributable_holdings_value_v1",
            "derived:holding-attributable-holdings-value-v1",
        ),
        "holding_company_nav": (
            [
                "ev_holding_a_fair_value",
                "ev_holding_a_ownership",
                "ev_holding_b_fair_value",
                "ev_holding_b_ownership",
                "ev_parent_net_debt",
                "ev_other_adjustments",
            ],
            "calc_holding_company_nav_v1",
            "derived:holding-company-nav-v1",
        ),
        "holding_company_market_cap": (
            ["ev_parent_market_price", "ev_parent_shares"],
            "calc_holding_company_market_cap_v1",
            "derived:holding-company-market-cap-v1",
        ),
        "holding_company_nav_discount": (
            [
                "ev_holding_a_fair_value",
                "ev_holding_a_ownership",
                "ev_holding_b_fair_value",
                "ev_holding_b_ownership",
                "ev_parent_net_debt",
                "ev_other_adjustments",
                "ev_parent_market_price",
                "ev_parent_shares",
            ],
            "calc_holding_company_nav_discount_v1",
            "derived:holding-company-nav-discount-v1",
        ),
    }
    report_metrics = {
        metric.metric_id: metric for metric in report_context.metrics
    }
    assert set(HOLDING_METRIC_IDS) <= report_metrics.keys()
    for metric_id, (evidence_ids, calculation_id, source_reference) in expected_provenance.items():
        metric = report_metrics[metric_id]
        assert metric.status == "available"
        assert metric.validation_status == "valid"
        assert metric.evidence_ids == evidence_ids
        assert metric.calculation_id == calculation_id
        assert metric.source_reference == source_reference
        assert metric.as_of


def test_holding_report_renders_primary_nav_and_not_applicable_boundary() -> None:
    policy_context, report_context = _report_context()

    report = render_validated_report(
        report_context=report_context,
        report_draft=build_deterministic_report_draft(),
    )

    for label in (
        "归属持仓价值",
        "控股公司 NAV（净资产价值）",
        "控股公司市值",
        "NAV 折价/溢价",
    ):
        assert label in report
    assert "### 控股公司专用指标" in report
    holding_section = report.split("### 控股公司专用指标", 1)[1].split("\n## ", 1)[0]

    report_metric_ids = {metric.metric_id for metric in report_context.metrics}
    assert report_metric_ids.isdisjoint(ORDINARY_VALUATION_IDS)
    assert report_context.historical_valuation == {}
    assert report_context.reverse_dcf == {}

    decisions = {
        decision["metric_id"]: decision
        for decision in policy_context["policy_decisions"]
    }
    expected_not_applicable = {
        "pe_ratio": "holding_company_pe_not_applicable",
        "fcf_yield": "holding_company_fcf_not_applicable",
        "historical_valuation": "holding_company_historical_valuation_not_applicable",
        "reverse_dcf": "holding_company_reverse_dcf_not_applicable",
    }
    for metric_id, reason_code in expected_not_applicable.items():
        assert decisions[metric_id]["status"] == "not_applicable"
        assert decisions[metric_id]["reason_code"] == reason_code
    for label, reason_code in {
        "P/E": "holding_company_pe_not_applicable",
        "FCF Yield": "holding_company_fcf_not_applicable",
        "历史估值": "holding_company_historical_valuation_not_applicable",
        "反向 DCF": "holding_company_reverse_dcf_not_applicable",
    }.items():
        lines = [
            line
            for line in holding_section.splitlines()
            if line.startswith(f"- {label}：")
        ]
        assert len(lines) == 1
        assert "not_applicable" in lines[0]
        assert reason_code in lines[0]


def test_holding_report_renders_nonzero_nav_amounts_with_usd_units() -> None:
    _, report_context = _report_context()

    report = render_validated_report(
        report_context=report_context,
        report_draft=build_deterministic_report_draft(),
    )
    holding_section = report.split("### 控股公司专用指标", 1)[1].split("\n## ", 1)[0]

    for label, value in {
        "归属持仓价值": "800.00 USD",
        "控股公司 NAV（净资产价值）": "680.00 USD",
        "控股公司市值": "500.00 USD",
    }.items():
        assert f"- {label}：{value}（" in holding_section
    assert "- NAV 折价/溢价：26.47%（" in holding_section


def test_holding_report_omits_or_marks_ordinary_valuation_sections() -> None:
    _, report_context = _report_context()

    report = render_validated_report(
        report_context=report_context,
        report_draft=build_deterministic_report_draft(),
    )

    for heading, reason_code in (
        (
            "历史估值",
            "holding_company_historical_valuation_not_applicable",
        ),
        ("反向 DCF", "holding_company_reverse_dcf_not_applicable"),
    ):
        heading_marker = f"## {heading}"
        if heading_marker not in report:
            continue
        section = report.split(heading_marker, 1)[1].split("\n## ", 1)[0]
        assert "控股公司" in section
        assert reason_code in section
        assert "缺少已验证" not in section
