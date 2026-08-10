from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from stockcrewai.models.evidence import EvidenceRecord, MarketPriceRecord
from stockcrewai.pipelines.evidence_pipeline import build_profile_policy_context
from stockcrewai.reporting.context import build_report_context
from stockcrewai.reporting.renderer import (
    build_deterministic_report_draft,
    render_validated_report,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "profiles" / "reit"
EXPECTED_CALCULATION_METRICS = {
    "ffo_total",
    "ffo_per_share",
    "affo",
    "net_debt_to_ebitda",
    "dividend_coverage",
    "price_to_ffo",
}


def _load_fixture(name: str) -> dict[str, Any]:
    fixture = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert fixture["synthetic"] is True
    return fixture


def _report_inputs(
    fixture: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
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
    }
    if market_price_records:
        source_metadata["market_price"] = market_price_records[0].model_dump(
            mode="json"
        )
    context = build_report_context(
        company={"name": "Synthetic REIT", "ticker": "SREIT"},
        validated_claims=[],
        deterministic_verdict={"status": policy_context["gate"]["status"]},
        source_metadata=source_metadata,
        policy_context=policy_context,
    )
    return fixture, policy_context, context


def _render(context: dict[str, Any]) -> str:
    return render_validated_report(
        report_context=context,
        report_draft=build_deterministic_report_draft(),
    )


def test_complete_reit_report_keeps_verified_metrics_and_provenance() -> None:
    _, policy_context, context = _report_inputs(_load_fixture("complete"))

    assert context["policy_version"] == "metric-policy:v2"
    reit_metrics = context["reit_metrics"]
    assert reit_metrics["profile_version"] == "reit-profile:v1"
    assert reit_metrics["policy_version"] == "metric-policy:v2"
    assert reit_metrics["values"]["ffo_total"] == "150"
    assert reit_metrics["values"]["affo"] == "120"
    assert reit_metrics["policy_decisions"] == policy_context["policy_decisions"]
    assert reit_metrics["calculation_records"]
    assert all(
        {"status", "reason_code", "blocking", "evidence_ids", "calculation_ids"}
        <= set(decision)
        for decision in reit_metrics["policy_decisions"]
    )

    metrics = {metric["metric_id"]: metric for metric in context["metrics"]}
    assert EXPECTED_CALCULATION_METRICS <= metrics.keys()
    for metric_id in EXPECTED_CALCULATION_METRICS:
        metric = metrics[metric_id]
        assert metric["calculation_id"]
        assert metric["evidence_ids"]
        assert metric["source_reference"].startswith("fixture:")
        assert metric["as_of"]
        assert metric["period_end"] == "2025-12-31"

    report = _render(context)
    for fragment in (
        "FFO 总额",
        "FFO/股",
        "AFFO",
        "P/FFO",
        "P/E",
        "FCF Yield",
        "reit_primary_valuation_not_pe",
        "reit_primary_cash_metric_not_fcf",
        "fixture:sec-like/reit/complete/",
    ):
        assert fragment in report
    assert "pe_ratio" not in {metric["metric_id"] for metric in context["metrics"]}
    assert "fcf_yield" not in {metric["metric_id"] for metric in context["metrics"]}


def test_missing_affo_keeps_gate_ready_and_renders_typed_unavailable() -> None:
    _, policy_context, context = _report_inputs(_load_fixture("missing_affo"))

    assert policy_context["gate"]["status"] == "ready"
    affo_decision = next(
        decision
        for decision in policy_context["policy_decisions"]
        if decision["metric_id"] == "affo"
    )
    assert affo_decision == {
        "metric_id": "affo",
        "status": "unavailable",
        "evidence_ids": [],
        "calculation_ids": [],
        "reason_code": "affo_reconciliation_not_disclosed",
        "blocking": False,
    }
    assert all(metric["metric_id"] != "affo" for metric in context["metrics"])
    assert all(
        calculation["formula_id"]
        != "company-disclosed-affo-reconciliation-v1"
        for calculation in context["reit_metrics"]["calculation_records"]
    )

    report = _render(context)
    assert "AFFO：unavailable（affo_reconciliation_not_disclosed）" in report
    assert "AFFO=0" not in report
    assert "AFFO：0" not in report
    assert "FFO 总额" in report
    assert "P/FFO" in report


def test_reit_report_drops_metric_when_evidence_or_calculation_provenance_changes() -> None:
    fixture = _load_fixture("complete")
    _, policy_context, context = _report_inputs(fixture)

    missing_source_metadata = deepcopy(context["source_metadata"])
    ffo_input_id = next(
        calculation
        for calculation in policy_context["calculation_records"]
        if calculation["formula_id"] == "reit-ffo-reconciliation-v1"
    )["input_evidence_ids"][0]
    missing_source_metadata["facts"].pop(ffo_input_id)
    missing_source_context = build_report_context(
        company=context["company"],
        validated_claims=[],
        deterministic_verdict={"status": "ready"},
        source_metadata=missing_source_metadata,
        policy_context=policy_context,
    )
    assert all(
        metric["metric_id"] != "ffo_total"
        for metric in missing_source_context["metrics"]
    )

    unvalidated_policy = deepcopy(policy_context)
    affo_calculation = next(
        calculation
        for calculation in unvalidated_policy["calculation_records"]
        if calculation["formula_id"] == "company-disclosed-affo-reconciliation-v1"
    )
    affo_calculation["validation_status"] = "unvalidated"
    unvalidated_context = build_report_context(
        company=context["company"],
        validated_claims=[],
        deterministic_verdict={"status": "ready"},
        source_metadata=context["source_metadata"],
        policy_context=unvalidated_policy,
    )
    assert all(
        metric["metric_id"] != "affo" for metric in unvalidated_context["metrics"]
    )
