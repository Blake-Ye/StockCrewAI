from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest

from stockcrewai.models.evidence import CalculationRecord, EvidenceRecord, MarketPriceRecord
from stockcrewai.models.policy import PolicyDecision


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "profiles" / "insurance"
INSURANCE_METRIC_IDS = (
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
)
DERIVED_METRIC_IDS = {
    "loss_ratio",
    "expense_ratio",
    "combined_ratio",
    "insurance_roe",
    "book_value_per_share",
    "price_to_book",
    "pe_ratio",
}


def _load_fixture(name: str) -> dict[str, Any]:
    fixture = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert set(fixture) == {
        "fixture_version",
        "synthetic",
        "source_note",
        "profile_input",
        "evidence_records",
        "market_price_records",
        "expected",
    }
    assert fixture["synthetic"] is True
    assert isinstance(fixture["profile_input"], dict)
    assert isinstance(fixture["evidence_records"], list)
    assert isinstance(fixture["market_price_records"], list)
    assert isinstance(fixture["expected"], dict)
    return fixture


def _validated_records(
    fixture: dict[str, Any],
    evidence_payload: list[dict[str, Any]] | None = None,
    market_payload: list[dict[str, Any]] | None = None,
) -> tuple[tuple[EvidenceRecord, ...], tuple[MarketPriceRecord, ...]]:
    evidence_items = fixture["evidence_records"] if evidence_payload is None else evidence_payload
    market_items = (
        fixture["market_price_records"] if market_payload is None else market_payload
    )
    evidence_records = tuple(EvidenceRecord.model_validate(item) for item in evidence_items)
    market_price_records = tuple(
        MarketPriceRecord.model_validate(item) for item in market_items
    )
    return evidence_records, market_price_records


def _insurance_api() -> tuple[str, str, tuple[str, ...], Any]:
    try:
        from stockcrewai.profiles.insurance import (
            INSURANCE_METRIC_IDS,
            POLICY_VERSION,
            PROFILE_VERSION,
            evaluate_insurance_profile,
        )
    except (ImportError, AttributeError) as exc:
        pytest.fail(f"Insurance profile API is not implemented: {exc}", pytrace=False)
    return PROFILE_VERSION, POLICY_VERSION, tuple(INSURANCE_METRIC_IDS), evaluate_insurance_profile


def _evaluate_fixture(
    fixture: dict[str, Any],
    profile_input: dict[str, Any] | None = None,
    evidence_payload: list[dict[str, Any]] | None = None,
    market_payload: list[dict[str, Any]] | None = None,
) -> tuple[
    dict[str, Decimal | None],
    tuple[PolicyDecision, ...],
    tuple[CalculationRecord, ...],
]:
    evidence_records, market_price_records = _validated_records(
        fixture, evidence_payload, market_payload
    )
    _, _, _, evaluate_insurance_profile = _insurance_api()
    input_value = fixture["profile_input"] if profile_input is None else profile_input
    return evaluate_insurance_profile(input_value, evidence_records, market_price_records)


def _assert_result_shape(
    fixture: dict[str, Any],
    result: tuple[
        dict[str, Decimal | None],
        tuple[PolicyDecision, ...],
        tuple[CalculationRecord, ...],
    ],
) -> tuple[dict[str, PolicyDecision], tuple[CalculationRecord, ...]]:
    values, decisions, calculations = result
    evidence_records, market_price_records = _validated_records(fixture)
    evidence_ids = [record.evidence_id for record in evidence_records]
    market_price_ids = [record.evidence_id for record in market_price_records]
    assert len(evidence_ids) == len(set(evidence_ids))
    assert len(market_price_ids) == len(set(market_price_ids))
    evidence_allowlist = set(evidence_ids) | set(market_price_ids)

    assert tuple(values) == INSURANCE_METRIC_IDS
    assert len(decisions) == len(INSURANCE_METRIC_IDS)
    assert all(isinstance(decision, PolicyDecision) for decision in decisions)
    decision_by_metric = {decision.metric_id: decision for decision in decisions}
    assert tuple(decision_by_metric) == INSURANCE_METRIC_IDS

    calculation_ids = [calculation.calculation_id for calculation in calculations]
    assert len(calculation_ids) == len(set(calculation_ids))
    assert all(isinstance(calculation, CalculationRecord) for calculation in calculations)
    calculation_id_allowlist = set(calculation_ids)
    for calculation in calculations:
        assert len(calculation.input_evidence_ids) == len(
            set(calculation.input_evidence_ids)
        )
        assert set(calculation.input_evidence_ids) <= evidence_allowlist
        assert isinstance(calculation.result, Decimal)
        assert calculation.result.is_finite()

    for metric_id, value in values.items():
        if value is not None:
            assert isinstance(value, Decimal), metric_id
            assert value.is_finite()

    for decision in decisions:
        assert decision.reason_code
        assert len(decision.evidence_ids) == len(set(decision.evidence_ids))
        assert len(decision.calculation_ids) == len(set(decision.calculation_ids))
        assert set(decision.evidence_ids) <= evidence_allowlist
        assert set(decision.calculation_ids) <= calculation_id_allowlist
        if decision.status != "available":
            assert decision.evidence_ids == []
            assert decision.calculation_ids == []
        elif decision.metric_id in DERIVED_METRIC_IDS:
            assert decision.evidence_ids
            assert decision.calculation_ids

    return decision_by_metric, calculations


def test_complete_insurance_profile_computes_metrics_with_traceable_records() -> None:
    fixture = _load_fixture("complete")
    evidence_records, market_price_records = _validated_records(fixture)
    profile_version, policy_version, metric_ids, evaluate_insurance_profile = _insurance_api()
    values, decisions, calculations = evaluate_insurance_profile(
        fixture["profile_input"], evidence_records, market_price_records
    )
    decision_by_metric, calculations = _assert_result_shape(
        fixture, (values, decisions, calculations)
    )

    expected = fixture["expected"]
    assert profile_version == expected["profile_version"] == "insurance-profile:v1"
    assert policy_version == expected["policy_version"] == "metric-policy:insurance:v1"
    assert metric_ids == tuple(expected["metric_ids"]) == INSURANCE_METRIC_IDS

    for metric_id, expected_value in expected["values"].items():
        actual = values[metric_id]
        if expected_value is None:
            assert actual is None
        else:
            assert actual == Decimal(expected_value)

    for metric_id, expected_decision in expected["decisions"].items():
        decision = decision_by_metric[metric_id]
        assert decision.status == expected_decision["status"]
        assert decision.blocking is expected_decision["blocking"]
        if "reason_code" in expected_decision:
            assert decision.reason_code == expected_decision["reason_code"]
        if "evidence_ids" in expected_decision:
            assert set(decision.evidence_ids) == set(expected_decision["evidence_ids"])

    calculations_by_formula = {calculation.formula_id: calculation for calculation in calculations}
    expected_formula_ids = set(expected["calculation_formula_ids"])
    assert len(calculations) == len(expected_formula_ids)
    assert set(calculations_by_formula) == expected_formula_ids
    for formula_id, expected_input_ids in expected["calculation_inputs"].items():
        assert set(calculations_by_formula[formula_id].input_evidence_ids) == set(
            expected_input_ids
        )
    assert all(
        calculations_by_formula[formula_id].unit == "multiple"
        for formula_id in (
            "insurance-price-to-book-v1",
            "insurance-pe-ratio-v1",
        )
    )
    assert (
        calculations_by_formula["insurance-book-value-per-share-v1"].unit
        == "currency/share"
    )
    assert all(
        calculation.unit == "ratio"
        for calculation in calculations
        if calculation.formula_id
        not in {
            "insurance-price-to-book-v1",
            "insurance-pe-ratio-v1",
            "insurance-book-value-per-share-v1",
        }
    )


def test_missing_combined_is_blocking_without_fabricated_components() -> None:
    fixture = _load_fixture("missing_combined")
    assert "underwriting_expenses" not in fixture["profile_input"]["metric_inputs"]
    fixture_evidence_ids = {item["evidence_id"] for item in fixture["evidence_records"]}
    assert "ev_ins_expenses" not in fixture_evidence_ids

    values, decisions, calculations = _evaluate_fixture(fixture)
    decision_by_metric, calculations = _assert_result_shape(
        fixture, (values, decisions, calculations)
    )
    expected = fixture["expected"]

    assert values["loss_ratio"] == Decimal(expected["values"]["loss_ratio"])
    assert values["expense_ratio"] is None
    assert values["combined_ratio"] is None
    assert decision_by_metric["loss_ratio"].status == "available"

    for metric_id in ("expense_ratio", "combined_ratio"):
        decision = decision_by_metric[metric_id]
        expected_decision = expected["decisions"][metric_id]
        assert decision.status == expected_decision["status"]
        assert decision.reason_code == expected_decision["reason_code"]
        assert decision.blocking is expected_decision["blocking"]
        assert decision.evidence_ids == []
        assert decision.calculation_ids == []

    assert all(
        calculation.formula_id != "insurance-combined-ratio-v1"
        for calculation in calculations
    )
    unrelated_ids = {"ev_ins_operating_expenses", "ev_ins_reinsurance_change"}
    assert all(
        unrelated_ids.isdisjoint(calculation.input_evidence_ids)
        for calculation in calculations
    )


def test_high_loss_ratio_preserves_economic_value_without_clipping() -> None:
    fixture = _load_fixture("high_loss_ratio")
    values, decisions, calculations = _evaluate_fixture(fixture)
    decision_by_metric, calculations = _assert_result_shape(
        fixture, (values, decisions, calculations)
    )
    expected = fixture["expected"]

    assert values["loss_ratio"] == Decimal(expected["values"]["loss_ratio"]) == Decimal("1.1")
    assert values["combined_ratio"] == Decimal(expected["values"]["combined_ratio"]) == Decimal(
        "1.4"
    )
    assert values["loss_ratio"] not in (Decimal("0"), Decimal("1"))
    assert values["combined_ratio"] not in (Decimal("0"), Decimal("1"))
    for metric_id in ("loss_ratio", "expense_ratio", "combined_ratio"):
        assert decision_by_metric[metric_id].status == "available"
        assert decision_by_metric[metric_id].blocking is False
    assert {calculation.formula_id for calculation in calculations} == set(
        expected["calculation_formula_ids"]
    )


def test_insurance_does_not_require_ordinary_enterprise_fcf_inputs() -> None:
    fixture = _load_fixture("complete")
    forbidden_keys = {
        "capex",
        "free_cash_flow",
        "current_assets",
        "current_liabilities",
    }
    assert forbidden_keys.isdisjoint(fixture["profile_input"]["metric_inputs"])
    assert forbidden_keys.isdisjoint(
        {
            key
            for item in fixture["evidence_records"]
            for key in item
            if key in forbidden_keys
        }
    )

    values, decisions, calculations = _evaluate_fixture(fixture)
    decision_by_metric, calculations = _assert_result_shape(
        fixture, (values, decisions, calculations)
    )
    fcf_decision = decision_by_metric["fcf_yield"]
    assert values["fcf_yield"] is None
    assert fcf_decision.status == "not_applicable"
    assert fcf_decision.reason_code == "insurance_fcf_not_applicable"
    assert fcf_decision.blocking is False
    assert fcf_decision.evidence_ids == []
    assert fcf_decision.calculation_ids == []
    assert all("fcf-yield" not in calculation.formula_id for calculation in calculations)


def test_invalid_or_duplicate_source_fails_closed() -> None:
    fixture = _load_fixture("complete")
    invalid_payload = deepcopy(fixture["evidence_records"])
    invalid_source_id = "ev_ins_losses"
    next(item for item in invalid_payload if item["evidence_id"] == invalid_source_id)[
        "validation_status"
    ] = "invalid"
    invalid_records, market_price_records = _validated_records(fixture, invalid_payload)

    duplicate_payload = deepcopy(fixture["evidence_records"])
    duplicate_source_id = "ev_ins_losses"
    duplicate_payload.append(
        next(item for item in duplicate_payload if item["evidence_id"] == duplicate_source_id)
    )
    duplicate_records, duplicate_market_price_records = _validated_records(
        fixture, duplicate_payload
    )
    _, _, _, evaluate_insurance_profile = _insurance_api()

    invalid_values, invalid_decisions, invalid_calculations = evaluate_insurance_profile(
        fixture["profile_input"], invalid_records, market_price_records
    )
    invalid_by_metric = {decision.metric_id: decision for decision in invalid_decisions}
    for metric_id in ("loss_ratio", "combined_ratio"):
        assert invalid_values[metric_id] is None
        assert invalid_by_metric[metric_id].status == "unavailable"
        assert invalid_by_metric[metric_id].reason_code == "unvalidated_evidence_id"
        assert invalid_by_metric[metric_id].calculation_ids == []
    assert all(
        invalid_source_id not in calculation.input_evidence_ids
        for calculation in invalid_calculations
    )

    duplicate_values, duplicate_decisions, duplicate_calculations = evaluate_insurance_profile(
        fixture["profile_input"], duplicate_records, duplicate_market_price_records
    )
    duplicate_by_metric = {decision.metric_id: decision for decision in duplicate_decisions}
    for metric_id in ("loss_ratio", "combined_ratio"):
        assert duplicate_values[metric_id] is None
        assert duplicate_by_metric[metric_id].status == "unavailable"
        assert duplicate_by_metric[metric_id].reason_code == "duplicate_evidence_id"
        assert duplicate_by_metric[metric_id].calculation_ids == []
    assert all(
        duplicate_source_id not in calculation.input_evidence_ids
        for calculation in duplicate_calculations
    )
