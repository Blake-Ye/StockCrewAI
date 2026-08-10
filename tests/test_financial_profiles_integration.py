from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stockcrewai.flow import ResearchFlow
from stockcrewai.models.evidence import EvidenceRecord, MarketPriceRecord
from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ProfileResult,
    ReportingProfile,
    SecurityProfile,
)
from stockcrewai.pipelines.evidence_pipeline import build_profile_policy_context
from stockcrewai.pipelines.metric_registry import resolve_metric_policies
from stockcrewai.reporting.context import build_report_context
from stockcrewai.reporting.renderer import (
    build_deterministic_report_draft,
    render_validated_report,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "profiles"


def _fixture(profile: str, name: str) -> dict[str, Any]:
    return json.loads(
        (FIXTURE_ROOT / profile / f"{name}.json").read_text(encoding="utf-8")
    )


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


def _policy_context(profile: str, name: str) -> dict[str, Any]:
    fixture = _fixture(profile, name)
    evidence_records, market_price_records = _typed_records(fixture)
    return build_profile_policy_context(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )


def _source_metadata(
    evidence_records: tuple[EvidenceRecord, ...],
    market_price_records: tuple[MarketPriceRecord, ...],
) -> dict[str, Any]:
    return {
        "facts": {
            record.evidence_id: record.model_dump(mode="json")
            for record in evidence_records
        },
        "market_price": (
            market_price_records[0].model_dump(mode="json")
            if market_price_records
            else {}
        ),
    }


def _bank_profile_result() -> ProfileResult:
    return ProfileResult(
        issuer_profile=IssuerProfile.BANK,
        security_profile=SecurityProfile.COMMON_STOCK,
        reporting_profile=ReportingProfile.DOMESTIC_US_GAAP,
        coverage_level=CoverageLevel.FULL,
        registry_version="profile-registry:test-input",
    )


def _bank_fact(
    evidence_id: str,
    value: str,
    period_start: str,
    period_end: str,
    *,
    unit: str = "USD millions",
    currency: str = "USD",
) -> dict[str, str]:
    return {
        "evidence_id": evidence_id,
        "source_reference": f"fixture:bank/{evidence_id}",
        "as_of": "2026-03-02T21:00:00Z",
        "filed_at": "2026-02-20",
        "period_start": period_start,
        "period_end": period_end,
        "unit": unit,
        "currency": currency,
        "value": value,
        "validation_status": "valid",
    }


def _automatic_bank_facts() -> dict[str, dict[str, str]]:
    duration = {"period_start": "2025-01-01", "period_end": "2025-12-31"}
    return {
        "net_income": _bank_fact("ev_auto_net_income", "120", **duration),
        "net_interest_income": _bank_fact("ev_auto_nii", "360", **duration),
        "noninterest_income": _bank_fact("ev_auto_noninterest_income", "140", **duration),
        "noninterest_expense": _bank_fact(
            "ev_auto_noninterest_expense", "200", **duration
        ),
        "total_assets_beginning": _bank_fact(
            "ev_auto_assets_beginning", "10000", "2024-12-31", "2024-12-31"
        ),
        "total_assets_ending": _bank_fact(
            "ev_auto_assets_ending", "12000", "2025-12-31", "2025-12-31"
        ),
        "stockholders_equity_beginning": _bank_fact(
            "ev_auto_equity_beginning", "1000", "2024-12-31", "2024-12-31"
        ),
        "stockholders_equity_ending": _bank_fact(
            "ev_auto_equity_ending", "1400", "2025-12-31", "2025-12-31"
        ),
        "interest_earning_assets_beginning": _bank_fact(
            "ev_auto_earning_assets_beginning", "8000", "2024-12-31", "2024-12-31"
        ),
        "interest_earning_assets_ending": _bank_fact(
            "ev_auto_earning_assets_ending", "10000", "2025-12-31", "2025-12-31"
        ),
    }


def _automatic_bank_context(facts: dict[str, dict[str, str]]) -> dict[str, Any]:
    return build_profile_policy_context(
        profile=_bank_profile_result(),
        facts=facts,
        evidence_records=(),
        market_price_records=(),
    )


def test_registry_contains_frozen_bank_and_insurance_policy_matrices() -> None:
    expected = {
        "bank": (
            "bank_roa",
            "bank_roe",
            "net_interest_margin",
            "efficiency_ratio",
            "cet1_ratio",
            "loan_to_deposit",
            "nonperforming_loan_ratio",
            "provision_coverage",
            "price_to_book",
            "pe_ratio",
            "fcf_yield",
        ),
        "insurance": (
            "loss_ratio",
            "expense_ratio",
            "combined_ratio",
            "insurance_roe",
            "book_value_per_share",
            "investment_income",
            "solvency_ratio",
            "price_to_book",
            "pe_ratio",
            "fcf_yield",
        ),
    }
    for issuer, metric_ids in expected.items():
        profile = ProfileResult(
            issuer_profile=IssuerProfile(issuer),
            security_profile=SecurityProfile.COMMON_STOCK,
            reporting_profile=ReportingProfile.DOMESTIC_US_GAAP,
            coverage_level=CoverageLevel.FULL,
            registry_version="profile-registry:test-input",
        )
        policies = resolve_metric_policies(profile)
        assert tuple(policy.metric_id for policy in policies) == metric_ids
        assert all(
            policy.policy_version == f"metric-policy:{issuer}:v1"
            for policy in policies
        )


def test_complete_bank_context_is_ready_without_ordinary_enterprise_inputs() -> None:
    context = _policy_context("bank", "complete")
    decisions = {item["metric_id"]: item for item in context["policy_decisions"]}

    assert context["policy_version"] == "metric-policy:bank:v1"
    assert context["gate"]["status"] == "ready"
    assert decisions["bank_roa"]["status"] == "available"
    assert decisions["bank_roe"]["status"] == "available"
    assert decisions["cet1_ratio"]["status"] == "available"
    assert decisions["cet1_ratio"]["calculation_ids"] == []
    assert context["values"]["fcf_yield"] is None
    assert decisions["fcf_yield"] == {
        "metric_id": "fcf_yield",
        "status": "not_applicable",
        "evidence_ids": [],
        "calculation_ids": [],
        "reason_code": "bank_fcf_not_applicable",
        "blocking": False,
    }
    required_evidence = {
        evidence
        for policy in context["policies"]
        for evidence in policy["required_evidence"]
    }
    assert not required_evidence & {
        "operating_income",
        "capex",
        "current_assets",
        "current_liabilities",
    }


def test_automatic_bank_core_uses_two_point_evidence_and_optional_missing_is_ready() -> None:
    context = _automatic_bank_context(_automatic_bank_facts())
    decisions = {item["metric_id"]: item for item in context["policy_decisions"]}
    calculations = {
        item["calculation_id"]: item for item in context["calculation_records"]
    }

    assert context["profile_envelope"] == {
        "status": "valid",
        "reason_code": "typed_profile_envelope_valid",
    }
    assert context["gate"]["status"] == "ready"
    assert context["values"]["bank_roa"] == "0.01090909090909090909090909091"
    assert context["values"]["bank_roe"] == "0.1"
    assert context["values"]["net_interest_margin"] == "0.04"
    assert context["values"]["efficiency_ratio"] == "0.4"

    expected_inputs = {
        "bank_roa": {
            "ev_auto_net_income",
            "ev_auto_assets_beginning",
            "ev_auto_assets_ending",
        },
        "bank_roe": {
            "ev_auto_net_income",
            "ev_auto_equity_beginning",
            "ev_auto_equity_ending",
        },
        "net_interest_margin": {
            "ev_auto_nii",
            "ev_auto_earning_assets_beginning",
            "ev_auto_earning_assets_ending",
        },
        "efficiency_ratio": {
            "ev_auto_noninterest_expense",
            "ev_auto_nii",
            "ev_auto_noninterest_income",
        },
    }
    for metric_id, evidence_ids in expected_inputs.items():
        decision = decisions[metric_id]
        assert decision["status"] == "available"
        assert decision["blocking"] is False
        assert len(decision["calculation_ids"]) == 1
        calculation = calculations[decision["calculation_ids"][0]]
        assert set(calculation["input_evidence_ids"]) == evidence_ids

    for metric_id in (
        "cet1_ratio",
        "loan_to_deposit",
        "nonperforming_loan_ratio",
        "provision_coverage",
        "price_to_book",
        "pe_ratio",
    ):
        assert decisions[metric_id]["status"] == "unavailable"
        assert decisions[metric_id]["blocking"] is False
    assert decisions["fcf_yield"]["status"] == "not_applicable"


def test_automatic_bank_average_requires_both_matching_point_evidence() -> None:
    cases = (
        (
            "missing ending assets",
            "total_assets_ending",
            lambda fact: None,
            {"bank_roa"},
            {"bank_roe", "net_interest_margin", "efficiency_ratio"},
        ),
        (
            "unit mismatch",
            "total_assets_ending",
            lambda fact: fact.update(unit="USD thousands"),
            {"bank_roa"},
            {"bank_roe", "net_interest_margin", "efficiency_ratio"},
        ),
        (
            "currency mismatch",
            "stockholders_equity_ending",
            lambda fact: fact.update(currency="EUR"),
            {"bank_roe"},
            {"bank_roa", "net_interest_margin", "efficiency_ratio"},
        ),
        (
            "period mismatch",
            "interest_earning_assets_ending",
            lambda fact: fact.update(period_start="2025-09-30", period_end="2025-09-30"),
            {"net_interest_margin"},
            {"bank_roa", "bank_roe", "efficiency_ratio"},
        ),
    )
    for label, key, mutate, affected, unaffected in cases:
        facts = _automatic_bank_facts()
        if mutate.__name__ == "<lambda>":
            if label == "missing ending assets":
                facts.pop(key)
            else:
                mutate(facts[key])
        context = _automatic_bank_context(facts)
        decisions = {item["metric_id"]: item for item in context["policy_decisions"]}
        calculations = context["calculation_records"]
        for metric_id in affected:
            decision = decisions[metric_id]
            assert decision["status"] == "unavailable", label
            assert decision["blocking"] is True, label
            assert decision["evidence_ids"] == [], label
            assert decision["calculation_ids"] == [], label
            assert all(
                not item["calculation_id"].endswith(f"-{metric_id}-v1")
                for item in calculations
            ), label
        for metric_id in unaffected:
            assert decisions[metric_id]["status"] == "available", label


def test_automatic_bank_profile_does_not_infer_from_ordinary_enterprise_fields() -> None:
    from stockcrewai.pipelines.evidence_pipeline import (
        _automatic_profile_input,
        _typed_profile_facts,
    )

    ordinary_facts = {
        "net_income": _bank_fact("ev_ordinary_net_income", "120", "2025-01-01", "2025-12-31"),
        "revenue": _bank_fact("ev_ordinary_revenue", "1000", "2025-01-01", "2025-12-31"),
        "operating_income": _bank_fact(
            "ev_ordinary_operating_income", "200", "2025-01-01", "2025-12-31"
        ),
        "operating_expenses": _bank_fact(
            "ev_ordinary_operating_expenses", "800", "2025-01-01", "2025-12-31"
        ),
        "stockholders_equity": _bank_fact(
            "ev_ordinary_equity", "1400", "2025-12-31", "2025-12-31"
        ),
    }
    records_by_metric, _ = _typed_profile_facts(ordinary_facts)
    automatic_profile = _automatic_profile_input(
        _bank_profile_result(), records_by_metric, ()
    )
    context = _automatic_bank_context(ordinary_facts)
    decisions = {item["metric_id"]: item for item in context["policy_decisions"]}

    assert automatic_profile is not None
    assert set(automatic_profile["metric_inputs"]) == {"net_income"}
    assert context["gate"]["status"] == "blocked"
    assert context["calculation_records"] == []
    for metric_id in ("bank_roa", "bank_roe", "net_interest_margin", "efficiency_ratio"):
        assert decisions[metric_id]["status"] == "unavailable"
        assert decisions[metric_id]["blocking"] is True


def test_missing_bank_cet1_remains_ready_and_typed_not_applicable_fcf() -> None:
    context = _policy_context("bank", "missing_cet1")
    decisions = {item["metric_id"]: item for item in context["policy_decisions"]}

    assert context["gate"]["status"] == "ready"
    assert decisions["cet1_ratio"]["status"] == "unavailable"
    assert decisions["cet1_ratio"]["blocking"] is False
    assert decisions["cet1_ratio"]["reason_code"] == "cet1_ratio_not_disclosed"
    assert decisions["fcf_yield"]["status"] == "not_applicable"


def test_financial_profile_without_typed_envelope_fails_closed() -> None:
    fixture = _fixture("bank", "complete")
    context = build_profile_policy_context(
        profile=fixture["profile_input"],
        facts={"net_income": object()},
        calculations=[],
        evidence_records=[],
        market_price_records=[],
    )

    assert context["profile_envelope"] == {
        "status": "unavailable",
        "reason_code": "typed_profile_envelope_required",
    }
    assert context["calculation_records"] == []
    assert context["gate"]["status"] == "blocked"
    assert all(
        decision["status"] == "not_applicable"
        if decision["metric_id"] == "fcf_yield"
        else decision["status"] == "unavailable"
        for decision in context["policy_decisions"]
    )


def test_complete_insurance_context_is_ready_with_direct_evidence_metrics() -> None:
    fixture = _fixture("insurance", "complete")
    evidence_records, market_price_records = _typed_records(fixture)
    context = _policy_context("insurance", "complete")
    decisions = {item["metric_id"]: item for item in context["policy_decisions"]}

    assert context["policy_version"] == "metric-policy:insurance:v1"
    assert context["gate"]["status"] == "ready"
    assert decisions["combined_ratio"]["status"] == "available"
    assert decisions["investment_income"]["evidence_ids"] == [
        "ev_ins_investment_income"
    ]
    assert decisions["investment_income"]["calculation_ids"] == []
    assert decisions["solvency_ratio"]["calculation_ids"] == []
    assert decisions["fcf_yield"]["status"] == "not_applicable"

    report_context = build_report_context(
        company={"name": "Example Insurance", "ticker": "EXI"},
        deterministic_verdict={"status": "ready"},
        source_metadata=_source_metadata(evidence_records, market_price_records),
        policy_context=context,
    )
    report_metrics = {
        metric["metric_id"]: metric for metric in report_context["metrics"]
    }
    for metric_id in ("investment_income", "solvency_ratio"):
        assert report_metrics[metric_id]["provenance_type"] == "direct_evidence"
        assert report_metrics[metric_id]["calculation_id"] is None


def test_missing_insurance_combined_ratio_is_explicitly_blocked() -> None:
    context = _policy_context("insurance", "missing_combined")
    decisions = {item["metric_id"]: item for item in context["policy_decisions"]}

    assert context["gate"]["status"] == "blocked"
    assert decisions["combined_ratio"]["status"] == "unavailable"
    assert decisions["combined_ratio"]["blocking"] is True
    assert decisions["combined_ratio"]["reason_code"] == (
        "combined_ratio_components_missing"
    )
    assert all(
        item["formula_id"] != "insurance-combined-ratio-v1"
        for item in context["calculation_records"]
    )


def test_financial_report_renders_profile_labels_and_never_fabricates_fcf() -> None:
    fixture = _fixture("bank", "complete")
    evidence_records, market_price_records = _typed_records(fixture)
    policy_context = _policy_context("bank", "complete")
    report_context = build_report_context(
        company={"name": "Example Bank", "ticker": "EXB"},
        deterministic_verdict={"status": "ready"},
        source_metadata=_source_metadata(evidence_records, market_price_records),
        policy_context=policy_context,
    )

    assert report_context["profile_metrics"]["values"]["fcf_yield"] is None
    assert all(
        metric["metric_id"] != "fcf_yield" for metric in report_context["metrics"]
    )
    report = render_validated_report(
        report_context,
        build_deterministic_report_draft(),
    )
    for label in ("ROA", "ROE", "NIM", "效率比率", "CET1", "贷存比", "P/B", "P/E"):
        assert label in report
    assert "FCF Yield：not_applicable" in report
    assert "FCF Yield：0" not in report
    assert "ROA（资产回报率）" in report


def test_flow_accepts_generic_typed_profile_dependencies_and_keeps_reit_keys() -> None:
    flow = ResearchFlow(
        profile_input={"issuer_profile": "bank", "profile_version": "bank-profile:v1"},
        profile_evidence_records=[],
        profile_market_price_records=[],
        reit_profile_input={"issuer_profile": "reit"},
    )

    assert flow._profile_input["issuer_profile"] == "bank"
    assert flow._reit_profile_input["issuer_profile"] == "reit"
    assert flow._profile_evidence_records == ()
    assert flow._profile_market_price_records == ()
