from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest

from stockcrewai.models.evidence import CalculationRecord, EvidenceRecord, MarketPriceRecord
from stockcrewai.models.policy import PolicyDecision


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "profiles" / "bank"
EXPECTED_BANK_METRIC_IDS = (
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


def _bank_api() -> tuple[str, str, tuple[str, ...], Any]:
    try:
        from stockcrewai.profiles.bank import (
            BANK_METRIC_IDS,
            POLICY_VERSION,
            PROFILE_VERSION,
            evaluate_bank_profile,
        )
    except ImportError as exc:
        error = f"BANK profile API is not implemented: {exc}"
    else:
        return PROFILE_VERSION, POLICY_VERSION, tuple(BANK_METRIC_IDS), evaluate_bank_profile
    pytest.fail(error, pytrace=False)


def _evaluate_fixture(
    fixture: dict[str, Any],
) -> tuple[
    dict[str, Decimal | None],
    tuple[PolicyDecision, ...],
    tuple[CalculationRecord, ...],
]:
    evidence_records, market_price_records = _validated_records(fixture)
    _, _, _, evaluate_bank_profile = _bank_api()
    return evaluate_bank_profile(
        fixture["profile_input"], evidence_records, market_price_records
    )


def _assert_result_shape(
    fixture: dict[str, Any],
    result: tuple[
        dict[str, Decimal | None],
        tuple[PolicyDecision, ...],
        tuple[CalculationRecord, ...],
    ],
) -> dict[str, PolicyDecision]:
    values, decisions, calculations = result
    evidence_records, market_price_records = _validated_records(fixture)
    source_ids = {
        record.evidence_id for record in evidence_records
    } | {record.evidence_id for record in market_price_records}

    assert set(values) == set(EXPECTED_BANK_METRIC_IDS)
    assert tuple(values) == EXPECTED_BANK_METRIC_IDS
    assert len(decisions) == len(EXPECTED_BANK_METRIC_IDS)
    assert all(isinstance(decision, PolicyDecision) for decision in decisions)
    decision_ids = [decision.metric_id for decision in decisions]
    assert len(decision_ids) == len(set(decision_ids))
    decision_by_metric = {decision.metric_id: decision for decision in decisions}
    assert tuple(decision_by_metric) == EXPECTED_BANK_METRIC_IDS

    calculation_ids = [calculation.calculation_id for calculation in calculations]
    assert len(calculation_ids) == len(set(calculation_ids))
    assert all(isinstance(calculation, CalculationRecord) for calculation in calculations)
    calculation_id_allowlist = set(calculation_ids)
    for calculation in calculations:
        assert len(calculation.input_evidence_ids) == len(
            set(calculation.input_evidence_ids)
        )
        assert set(calculation.input_evidence_ids) <= source_ids
        assert calculation.result is not None
        assert isinstance(calculation.result, Decimal)
        assert calculation.result.is_finite()

    for decision in decisions:
        assert decision.reason_code
        assert len(decision.evidence_ids) == len(set(decision.evidence_ids))
        assert len(decision.calculation_ids) == len(set(decision.calculation_ids))
        assert set(decision.evidence_ids) <= source_ids
        assert set(decision.calculation_ids) <= calculation_id_allowlist

    for value in values.values():
        if value is not None:
            assert isinstance(value, Decimal)
            assert value.is_finite()

    return decision_by_metric


def test_complete_bank_profile_computes_metrics_with_traceable_records() -> None:
    fixture = _load_fixture("complete")
    evidence_records, market_price_records = _validated_records(fixture)
    profile_version, policy_version, metric_ids, evaluate_bank_profile = _bank_api()
    values, decisions, calculations = evaluate_bank_profile(
        fixture["profile_input"], evidence_records, market_price_records
    )
    decision_by_metric = _assert_result_shape(
        fixture, (values, decisions, calculations)
    )

    expected = fixture["expected"]
    assert profile_version == expected["profile_version"] == "bank-profile:v1"
    assert policy_version == expected["policy_version"] == "metric-policy:bank:v1"
    assert metric_ids == tuple(expected["metric_ids"]) == EXPECTED_BANK_METRIC_IDS

    for metric_id, expected_value in expected["values"].items():
        actual = values[metric_id]
        assert actual == (None if expected_value is None else Decimal(expected_value))

    for metric_id, expected_decision in expected["decisions"].items():
        decision = decision_by_metric[metric_id]
        assert decision.status == expected_decision["status"]
        assert decision.blocking is expected_decision["blocking"]
        if "reason_code" in expected_decision:
            assert decision.reason_code == expected_decision["reason_code"]

    assert {calculation.formula_id for calculation in calculations} == set(
        expected["calculation_formula_ids"]
    )
    assert decision_by_metric["cet1_ratio"].evidence_ids == ["ev_bank_cet1"]
    assert decision_by_metric["cet1_ratio"].calculation_ids == []
    assert all(
        calculation.formula_id not in expected["direct_formula_ids"]
        for calculation in calculations
    )


def test_missing_cet1_is_unavailable_and_nonblocking() -> None:
    fixture = _load_fixture("missing_cet1")
    values, decisions, calculations = _evaluate_fixture(fixture)
    decision_by_metric = _assert_result_shape(
        fixture, (values, decisions, calculations)
    )

    for metric_id in fixture["expected"]["core_metric_ids"]:
        assert values[metric_id] is not None
        assert decision_by_metric[metric_id].status == "available"

    cet1_decision = decision_by_metric["cet1_ratio"]
    expected = fixture["expected"]["cet1_decision"]
    assert values["cet1_ratio"] is None
    assert cet1_decision.status == expected["status"]
    assert cet1_decision.reason_code == expected["reason_code"]
    assert cet1_decision.blocking is expected["blocking"]
    assert cet1_decision.evidence_ids == expected["evidence_ids"]
    assert cet1_decision.calculation_ids == expected["calculation_ids"]
    assert all(
        calculation.formula_id != "bank-cet1-ratio-v1" for calculation in calculations
    )


def test_negative_provision_preserves_sign_without_blocking() -> None:
    fixture = _load_fixture("negative_provision")
    values, decisions, calculations = _evaluate_fixture(fixture)
    decision_by_metric = _assert_result_shape(
        fixture, (values, decisions, calculations)
    )

    expected = fixture["expected"]
    actual = values["provision_coverage"]
    assert actual == Decimal(expected["values"]["provision_coverage"])
    assert actual is not None and actual < 0

    provision_decision = decision_by_metric["provision_coverage"]
    assert provision_decision.status == expected["provision_coverage_decision"]["status"]
    assert (
        provision_decision.blocking
        is expected["provision_coverage_decision"]["blocking"]
    )
    provision_calculation = next(
        calculation
        for calculation in calculations
        if calculation.formula_id == expected["formula_id"]
    )
    assert provision_calculation.result == actual
    assert set(provision_calculation.input_evidence_ids) == {
        "ev_bank_allowance",
        "ev_bank_npl",
    }


def test_bank_does_not_require_ordinary_enterprise_fcf_inputs() -> None:
    fixture = _load_fixture("complete")
    values, decisions, calculations = _evaluate_fixture(fixture)
    decision_by_metric = _assert_result_shape(
        fixture, (values, decisions, calculations)
    )

    fcf_decision = decision_by_metric["fcf_yield"]
    expected = fixture["expected"]
    assert values["fcf_yield"] is None
    assert fcf_decision.status == expected["decisions"]["fcf_yield"]["status"]
    assert fcf_decision.reason_code == expected["decisions"]["fcf_yield"]["reason_code"]
    assert fcf_decision.blocking is False
    assert fcf_decision.evidence_ids == []
    assert fcf_decision.calculation_ids == []
    assert all(
        calculation.formula_id != expected["fcf_formula_id"]
        for calculation in calculations
    )


def test_invalid_or_duplicate_source_fails_closed() -> None:
    fixture = _load_fixture("complete")
    bad_evidence_id = "ev_bank_net_income"

    duplicate_fixture = deepcopy(fixture)
    duplicate_fixture["evidence_records"].append(
        deepcopy(
            next(
                record
                for record in duplicate_fixture["evidence_records"]
                if record["evidence_id"] == bad_evidence_id
            )
        )
    )

    invalid_fixture = deepcopy(fixture)
    next(
        record
        for record in invalid_fixture["evidence_records"]
        if record["evidence_id"] == bad_evidence_id
    )["validation_status"] = "invalid"

    validated_cases = []
    for label, mutated_fixture in (
        ("duplicate", duplicate_fixture),
        ("invalid", invalid_fixture),
    ):
        validated_cases.append((label, mutated_fixture, _validated_records(mutated_fixture)))

    _, _, _, evaluate_bank_profile = _bank_api()
    affected_metrics = [
        metric_id
        for metric_id, input_names in {
            "bank_roa": ("net_income", "average_assets"),
            "bank_roe": ("net_income", "average_equity"),
        }.items()
        if bad_evidence_id
        in {
            fixture["profile_input"]["metric_inputs"][input_name]
            for input_name in input_names
        }
    ]
    expected_affected_metrics = {"bank_roa", "bank_roe"}
    assert set(affected_metrics) == expected_affected_metrics

    for label, mutated_fixture, (evidence_records, market_price_records) in validated_cases:
        values, decisions, calculations = evaluate_bank_profile(
            mutated_fixture["profile_input"], evidence_records, market_price_records
        )
        decision_by_metric = {decision.metric_id: decision for decision in decisions}
        for metric_id in affected_metrics:
            assert values[metric_id] is None, label
            assert decision_by_metric[metric_id].status != "available", label

        assert all(
            bad_evidence_id not in calculation.input_evidence_ids
            for calculation in calculations
        ), label
        assert all(
            not (
                decision.status == "available"
                and bad_evidence_id in decision.evidence_ids
            )
            for decision in decisions
        ), label
