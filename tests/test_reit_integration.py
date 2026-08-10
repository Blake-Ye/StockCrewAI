from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from stockcrewai.models.evidence import EvidenceRecord, MarketPriceRecord
from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ProfileResult,
    ReportingProfile,
    SecurityProfile,
)
from stockcrewai.pipelines import evidence_pipeline
from stockcrewai.pipelines.evidence_pipeline import build_profile_policy_context


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "profiles" / "reit"
EXPECTED_METRICS = (
    "ffo_total",
    "ffo_per_share",
    "affo",
    "same_store_noi",
    "occupancy",
    "net_debt_to_ebitda",
    "dividend_coverage",
    "price_to_ffo",
    "pe",
    "fcf_yield",
)


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _typed_records(
    fixture: dict[str, Any],
) -> tuple[tuple[EvidenceRecord, ...], tuple[MarketPriceRecord, ...]]:
    evidence_records = tuple(
        EvidenceRecord.model_validate(item) for item in fixture["evidence_records"]
    )
    market_price_records = tuple(
        MarketPriceRecord.model_validate(item)
        for item in fixture["market_price_records"]
    )
    return evidence_records, market_price_records


def _decision_by_metric(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["metric_id"]: item for item in context["policy_decisions"]}


def test_complete_reit_is_classified_and_uses_adapter_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _load_fixture("complete")
    evidence_records, market_price_records = _typed_records(fixture)
    original = evidence_pipeline.evaluate_reit_profile
    calls: list[tuple[Any, ...]] = []

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(evidence_pipeline, "evaluate_reit_profile", spy)

    context = build_profile_policy_context(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )

    assert context["profile"]["issuer_profile"] == "reit"
    assert context["coverage_level"] == "full"
    assert len(calls) == 1
    assert calls[0][1] == evidence_records
    assert calls[0][2] == market_price_records


def test_complete_reit_context_has_v2_policy_adapter_values_and_ready_gate() -> None:
    fixture = _load_fixture("complete")
    evidence_records, market_price_records = _typed_records(fixture)

    context = build_profile_policy_context(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )
    decisions = _decision_by_metric(context)

    assert tuple(item["metric_id"] for item in context["policies"]) == EXPECTED_METRICS
    assert all(item["policy_version"] == "metric-policy:v2" for item in context["policies"])
    assert tuple(item["metric_id"] for item in context["policy_decisions"]) == EXPECTED_METRICS
    assert decisions["ffo_total"]["status"] == "available"
    assert decisions["ffo_per_share"]["status"] == "available"
    assert decisions["pe"] == {
        "metric_id": "pe",
        "status": "not_applicable",
        "evidence_ids": [],
        "calculation_ids": [],
        "reason_code": "reit_primary_valuation_not_pe",
        "blocking": False,
    }
    assert context["policy_version"] == "metric-policy:v2"
    assert context["gate"]["status"] == "ready"
    assert context["gate"]["policy_version"] == "metric-policy:v2"
    assert context["values"]["ffo_total"] == "150"
    assert context["values"]["ffo_per_share"] == "3"
    assert context["values"]["price_to_ffo"] == "10"
    assert {
        item["formula_id"] for item in context["calculation_records"]
    } == {
        "reit-ffo-reconciliation-v1",
        "reit-ffo-per-share-v1",
        "company-disclosed-affo-reconciliation-v1",
        "reit-net-debt-to-ebitda-v1",
        "reit-dividend-coverage-v1",
        "reit-price-to-ffo-v1",
    }


def test_missing_affo_is_non_blocking_and_has_no_affo_calculation() -> None:
    fixture = _load_fixture("missing_affo")
    evidence_records, market_price_records = _typed_records(fixture)

    context = build_profile_policy_context(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )
    affo = _decision_by_metric(context)["affo"]

    assert affo["status"] == "unavailable"
    assert affo["blocking"] is False
    assert affo["evidence_ids"] == []
    assert affo["calculation_ids"] == []
    assert all(
        item["formula_id"] != "company-disclosed-affo-reconciliation-v1"
        for item in context["calculation_records"]
    )
    assert context["gate"]["status"] == "ready"


def test_missing_diluted_shares_blocks_ffo_per_share_without_fabricated_calculation() -> None:
    fixture = _load_fixture("complete")
    profile_input = deepcopy(fixture["profile_input"])
    profile_input["metric_inputs"].pop("diluted_weighted_average_shares")
    evidence_records, market_price_records = _typed_records(fixture)

    context = build_profile_policy_context(
        profile=profile_input,
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )
    decisions = _decision_by_metric(context)

    assert decisions["ffo_total"]["status"] == "available"
    assert decisions["ffo_per_share"]["status"] == "unavailable"
    assert decisions["ffo_per_share"]["blocking"] is True
    assert decisions["ffo_per_share"]["calculation_ids"] == []
    assert context["values"]["ffo_total"] == "150"
    assert context["values"]["ffo_per_share"] is None
    assert context["gate"]["status"] == "blocked"
    assert all(
        item["formula_id"] != "reit-ffo-per-share-v1"
        for item in context["calculation_records"]
    )


def test_ordinary_profile_keeps_v1_metrics_and_gate_without_reit_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = ProfileResult(
        issuer_profile=IssuerProfile.STANDARD_OPERATING,
        security_profile=SecurityProfile.COMMON_STOCK,
        reporting_profile=ReportingProfile.DOMESTIC_US_GAAP,
        coverage_level=CoverageLevel.FULL,
        registry_version="profile-registry:test-input",
    )

    def fail_if_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("ordinary profile must not call REIT adapter")

    monkeypatch.setattr(
        evidence_pipeline, "evaluate_reit_profile", fail_if_called, raising=False
    )
    context = build_profile_policy_context(profile=profile, facts={}, calculations=[])

    assert context["policy_version"] == "metric-policy:v1"
    assert tuple(item["metric_id"] for item in context["policies"]) == (
        "revenue_growth",
        "operating_margin",
        "pe_ratio",
        "fcf_yield",
    )
    assert context["gate"]["status"] == "blocked"
    assert context["gate"]["policy_version"] == "metric-policy:v1"
    assert "values" not in context
    assert "calculation_records" not in context


def test_reit_provenance_is_allowlisted_and_cross_store_duplicate_ids_fail_closed() -> None:
    fixture = _load_fixture("complete")
    evidence_records, market_price_records = _typed_records(fixture)
    context = build_profile_policy_context(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )
    evidence_ids = {record.evidence_id for record in evidence_records}
    market_ids = {record.evidence_id for record in market_price_records}
    calculation_ids = {
        item["calculation_id"] for item in context["calculation_records"]
    }
    allowlist = evidence_ids | market_ids

    assert len(evidence_ids) == len(evidence_records)
    assert len(market_ids) == len(market_price_records)
    for decision in context["policy_decisions"]:
        assert len(decision["evidence_ids"]) == len(set(decision["evidence_ids"]))
        assert len(decision["calculation_ids"]) == len(set(decision["calculation_ids"]))
        assert set(decision["evidence_ids"]) <= allowlist
        assert set(decision["calculation_ids"]) <= calculation_ids
        if decision["status"] in {"unavailable", "not_applicable"}:
            assert decision["evidence_ids"] == []
            assert decision["calculation_ids"] == []
    for calculation in context["calculation_records"]:
        assert len(calculation["input_evidence_ids"]) == len(
            set(calculation["input_evidence_ids"])
        )
        assert set(calculation["input_evidence_ids"]) <= allowlist

    duplicate_market_payload = market_price_records[0].model_dump(mode="json")
    duplicate_market_payload["evidence_id"] = evidence_records[4].evidence_id
    duplicate_market = MarketPriceRecord.model_validate(duplicate_market_payload)
    duplicate_context = build_profile_policy_context(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=(duplicate_market,),
    )
    duplicate_decisions = _decision_by_metric(duplicate_context)

    assert duplicate_decisions["ffo_total"]["status"] == "unavailable"
    assert duplicate_decisions["ffo_total"]["evidence_ids"] == []
    assert duplicate_decisions["ffo_total"]["calculation_ids"] == []
    assert duplicate_decisions["price_to_ffo"]["status"] == "unavailable"
    assert all(
        item["formula_id"] != "reit-ffo-reconciliation-v1"
        for item in duplicate_context["calculation_records"]
    )
    assert all(
        item["formula_id"] != "reit-price-to-ffo-v1"
        for item in duplicate_context["calculation_records"]
    )
    assert duplicate_context["values"]["ffo_total"] is None
    assert duplicate_context["values"]["price_to_ffo"] is None
