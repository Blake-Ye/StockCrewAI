from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest

from stockcrewai.models.evidence import CalculationRecord, EvidenceRecord, MarketPriceRecord
from stockcrewai.models.policy import PolicyDecision


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "profiles" / "reit"
EXPECTED_REIT_METRIC_IDS = (
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
    fixture = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert fixture["synthetic"] is True
    assert isinstance(fixture["profile_input"], dict)
    assert isinstance(fixture["evidence_records"], list)
    assert isinstance(fixture["market_price_records"], list)
    assert isinstance(fixture["expected"], dict)
    return fixture


def _validated_records(
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


def _reit_api() -> tuple[str, str, tuple[str, ...], Any]:
    try:
        from stockcrewai.profiles.reit import (
            POLICY_VERSION,
            PROFILE_VERSION,
            REIT_METRIC_IDS,
            evaluate_reit_profile,
        )
    except ImportError as exc:
        error = f"REIT profile API is not implemented: {exc}"
    else:
        return PROFILE_VERSION, POLICY_VERSION, tuple(REIT_METRIC_IDS), evaluate_reit_profile
    pytest.fail(error, pytrace=False)


def _evaluate_fixture(
    fixture: dict[str, Any],
    profile_input: dict[str, Any] | None = None,
) -> tuple[
    dict[str, Decimal | None],
    tuple[PolicyDecision, ...],
    tuple[CalculationRecord, ...],
]:
    evidence_records, market_price_records = _validated_records(fixture)
    _, _, _, evaluate_reit_profile = _reit_api()
    input_value = fixture["profile_input"] if profile_input is None else profile_input
    return evaluate_reit_profile(input_value, evidence_records, market_price_records)


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

    assert set(values) == set(EXPECTED_REIT_METRIC_IDS)
    assert len(decisions) == len(EXPECTED_REIT_METRIC_IDS)
    assert all(isinstance(decision, PolicyDecision) for decision in decisions)
    decision_by_metric = {decision.metric_id: decision for decision in decisions}
    assert tuple(decision_by_metric) == EXPECTED_REIT_METRIC_IDS

    calculation_ids = [calculation.calculation_id for calculation in calculations]
    assert len(calculation_ids) == len(set(calculation_ids))
    assert all(isinstance(calculation, CalculationRecord) for calculation in calculations)
    calculation_id_allowlist = set(calculation_ids)
    for calculation in calculations:
        assert len(calculation.input_evidence_ids) == len(
            set(calculation.input_evidence_ids)
        )
        assert set(calculation.input_evidence_ids) <= evidence_allowlist
        if calculation.result is not None:
            assert isinstance(calculation.result, Decimal)
            assert calculation.result.is_finite()

    for decision in decisions:
        assert decision.reason_code
        assert len(decision.evidence_ids) == len(set(decision.evidence_ids))
        assert len(decision.calculation_ids) == len(set(decision.calculation_ids))
        assert set(decision.evidence_ids) <= evidence_allowlist
        assert set(decision.calculation_ids) <= calculation_id_allowlist

    for value in values.values():
        if value is not None:
            assert isinstance(value, Decimal)
            assert value.is_finite()

    return decision_by_metric, calculations


def test_complete_reit_reconciliation_computes_expected_metrics_with_traceable_records() -> None:
    fixture = _load_fixture("complete")
    evidence_records, market_price_records = _validated_records(fixture)
    profile_version, policy_version, metric_ids, evaluate_reit_profile = _reit_api()
    values, decisions, calculations = evaluate_reit_profile(
        fixture["profile_input"], evidence_records, market_price_records
    )
    decision_by_metric, calculations = _assert_result_shape(
        fixture, (values, decisions, calculations)
    )

    expected = fixture["expected"]
    assert profile_version == expected["profile_version"] == "reit-profile:v1"
    assert policy_version == expected["policy_version"] == "metric-policy:v2"
    assert metric_ids == tuple(expected["metric_ids"]) == EXPECTED_REIT_METRIC_IDS
    assert tuple(values) == EXPECTED_REIT_METRIC_IDS
    assert tuple(decision_by_metric) == EXPECTED_REIT_METRIC_IDS

    for metric_id, expected_value in expected["values"].items():
        actual = values[metric_id]
        assert actual == (
            None if expected_value is None else Decimal(expected_value)
        )

    for metric_id, expected_decision in expected["decisions"].items():
        decision = decision_by_metric[metric_id]
        assert decision.status == expected_decision["status"]
        assert decision.blocking is expected_decision["blocking"]
        assert decision.reason_code == expected_decision["reason_code"]

    formula_ids = {calculation.formula_id for calculation in calculations}
    assert set(expected["required_formula_ids"]) <= formula_ids


def test_missing_affo_is_unavailable_nonblocking_and_creates_no_affo_calculation() -> None:
    fixture = _load_fixture("missing_affo")
    values, decisions, calculations = _evaluate_fixture(fixture)
    decision_by_metric, calculations = _assert_result_shape(
        fixture, (values, decisions, calculations)
    )

    expected = fixture["expected"]
    assert values["ffo_total"] == Decimal(expected["values"]["ffo_total"])
    assert values["ffo_per_share"] == Decimal(expected["values"]["ffo_per_share"])
    assert values["affo"] is None

    affo_decision = decision_by_metric["affo"]
    expected_affo = expected["affo_decision"]
    assert affo_decision.status == expected_affo["status"]
    assert affo_decision.reason_code == expected_affo["reason_code"]
    assert affo_decision.blocking is expected_affo["blocking"]
    assert affo_decision.evidence_ids == expected_affo["evidence_ids"]
    assert affo_decision.calculation_ids == expected_affo["calculation_ids"]
    assert all(
        calculation.formula_id != expected["absent_formula_id"]
        for calculation in calculations
    )


def test_non_positive_ffo_keeps_core_values_and_disables_price_to_ffo() -> None:
    fixture = _load_fixture("negative_ffo")
    expected = fixture["expected"]
    assert [case["name"] for case in fixture["cases"]] == expected["case_names"]

    for case in fixture["cases"]:
        profile_input = deepcopy(fixture["profile_input"])
        profile_input.update(
            {
                "ffo_reconciliation": case["ffo_reconciliation"],
                "metric_inputs": case["metric_inputs"],
            }
        )
        values, decisions, calculations = _evaluate_fixture(fixture, profile_input)
        decision_by_metric, calculations = _assert_result_shape(
            fixture, (values, decisions, calculations)
        )

        case_expected = expected["cases"][case["name"]]
        for metric_id, expected_value in case_expected["values"].items():
            actual = values[metric_id]
            assert actual == (
                None if expected_value is None else Decimal(expected_value)
            )

        price_to_ffo = decision_by_metric["price_to_ffo"]
        expected_price_to_ffo = case_expected["price_to_ffo_decision"]
        assert price_to_ffo.status == expected_price_to_ffo["status"]
        assert price_to_ffo.reason_code == expected_price_to_ffo["reason_code"]
        assert price_to_ffo.blocking is expected_price_to_ffo["blocking"]
        assert all(
            calculation.formula_id != expected["price_to_ffo_formula_id"]
            for calculation in calculations
        )


def test_property_type_metadata_does_not_change_reit_core_policy() -> None:
    fixture = _load_fixture("property_types")
    expected = fixture["expected"]
    assert [case["property_type"] for case in fixture["cases"]] == expected[
        "property_types"
    ]
    evidence_records, _ = _validated_records(fixture)
    evidence_ids = {record.evidence_id for record in evidence_records}

    baseline: tuple[dict[str, Decimal | None], dict[str, tuple[str, bool]], frozenset[str]] | None = None
    for case in fixture["cases"]:
        classification_metadata = case["classification_metadata"]
        assert classification_metadata["property_type"] == case["property_type"]
        assert all(
            classification_metadata[field]
            for field in ("property_type", "evidence_id", "source_reference")
        )
        assert classification_metadata["evidence_id"] in evidence_ids

        profile_input = deepcopy(fixture["profile_input"])
        profile_input["property_type"] = case["property_type"]
        profile_input["classification_metadata"] = deepcopy(classification_metadata)
        values, decisions, calculations = _evaluate_fixture(fixture, profile_input)
        decision_by_metric, calculations = _assert_result_shape(
            fixture, (values, decisions, calculations)
        )

        core_values = {
            metric_id: values[metric_id]
            for metric_id in ("ffo_total", "ffo_per_share")
        }
        status_and_blocking = {
            metric_id: (decision.status, decision.blocking)
            for metric_id, decision in decision_by_metric.items()
        }
        formula_ids = frozenset(calculation.formula_id for calculation in calculations)
        current = (core_values, status_and_blocking, formula_ids)
        if baseline is None:
            baseline = current
        else:
            assert current == baseline

        assert core_values == {
            metric_id: Decimal(expected_value)
            for metric_id, expected_value in expected["core_values"].items()
        }
        assert set(expected["formula_ids"]) <= formula_ids


def test_missing_diluted_shares_does_not_fabricate_ffo_per_share_calculation() -> None:
    fixture = _load_fixture("missing_affo")
    mutated_fixture = deepcopy(fixture)
    shares_input = mutated_fixture["profile_input"]["metric_inputs"].pop(
        "diluted_weighted_average_shares"
    )
    shares_evidence_id = shares_input["evidence_id"]
    mutated_fixture["evidence_records"] = [
        record
        for record in mutated_fixture["evidence_records"]
        if record["evidence_id"] != shares_evidence_id
    ]

    values, decisions, calculations = _evaluate_fixture(mutated_fixture)
    decision_by_metric, calculations = _assert_result_shape(
        mutated_fixture, (values, decisions, calculations)
    )

    assert values["ffo_total"] == Decimal("150")
    assert decision_by_metric["ffo_total"].status == "available"
    assert values["ffo_per_share"] is None
    ffo_per_share = decision_by_metric["ffo_per_share"]
    assert ffo_per_share.status == "unavailable"
    assert ffo_per_share.reason_code == "diluted_weighted_average_shares_missing"
    assert ffo_per_share.blocking is True
    assert ffo_per_share.evidence_ids == []
    assert ffo_per_share.calculation_ids == []
    assert all(
        calculation.formula_id != "reit-ffo-per-share-v1"
        for calculation in calculations
    )
