from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import socket
from typing import Any

import pytest

from stockcrewai.models.evidence import CalculationRecord, EvidenceRecord, MarketPriceRecord
from stockcrewai.models.policy import PolicyDecision


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "profiles" / "utility"
UTILITY_METRIC_IDS = (
    "utility_operating_margin",
    "rate_base",
    "capex_intensity",
    "interest_coverage",
    "utility_roe",
    "price_to_book",
    "pe_ratio",
    "fcf_yield",
)


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
    return (
        tuple(EvidenceRecord.model_validate(item) for item in evidence_items),
        tuple(MarketPriceRecord.model_validate(item) for item in market_items),
    )


def _utility_api() -> tuple[str, str, tuple[str, ...], Any]:
    try:
        from stockcrewai.profiles.utility import (
            POLICY_VERSION,
            PROFILE_VERSION,
            UTILITY_METRIC_IDS,
            evaluate_utility_profile,
        )
    except (ImportError, AttributeError) as exc:
        pytest.fail(f"Utility profile API is not implemented: {exc}", pytrace=False)
    return PROFILE_VERSION, POLICY_VERSION, tuple(UTILITY_METRIC_IDS), evaluate_utility_profile


def _evaluate(
    fixture: dict[str, Any],
    *,
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
    _, _, _, evaluate_utility_profile = _utility_api()
    return evaluate_utility_profile(
        fixture["profile_input"] if profile_input is None else profile_input,
        evidence_records,
        market_price_records,
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

    assert tuple(values) == UTILITY_METRIC_IDS
    assert tuple(decision.metric_id for decision in decisions) == UTILITY_METRIC_IDS
    assert len(decisions) == len(UTILITY_METRIC_IDS)
    assert all(isinstance(decision, PolicyDecision) for decision in decisions)

    calculation_ids = [calculation.calculation_id for calculation in calculations]
    assert len(calculation_ids) == len(set(calculation_ids))
    assert all(isinstance(calculation, CalculationRecord) for calculation in calculations)
    calculation_id_allowlist = set(calculation_ids)
    for calculation in calculations:
        assert calculation.input_evidence_ids
        assert len(calculation.input_evidence_ids) == len(set(calculation.input_evidence_ids))
        assert set(calculation.input_evidence_ids) <= source_ids
        assert calculation.source_reference.startswith("derived:")
        assert calculation.result is not None
        assert calculation.result.is_finite()
        assert calculation.as_of.tzinfo is not None
        assert calculation.period_start <= calculation.period_end

    decision_by_metric = {decision.metric_id: decision for decision in decisions}
    for decision in decisions:
        assert decision.reason_code
        assert len(decision.evidence_ids) == len(set(decision.evidence_ids))
        assert len(decision.calculation_ids) == len(set(decision.calculation_ids))
        assert set(decision.evidence_ids) <= source_ids
        assert set(decision.calculation_ids) <= calculation_id_allowlist
        if decision.status != "available":
            assert decision.evidence_ids == []
            assert decision.calculation_ids == []

    for value in values.values():
        if value is not None:
            assert isinstance(value, Decimal)
            assert value.is_finite()

    return decision_by_metric


def test_complete_utility_profile_computes_values_and_traceable_provenance() -> None:
    fixture = _load_fixture("complete")
    evidence_records, market_price_records = _validated_records(fixture)
    profile_version, policy_version, metric_ids, evaluate_utility_profile = _utility_api()
    result = evaluate_utility_profile(
        fixture["profile_input"], evidence_records, market_price_records
    )
    values, decisions, calculations = result
    decision_by_metric = _assert_result_shape(fixture, result)
    expected = fixture["expected"]

    assert profile_version == expected["profile_version"] == "utility-profile:v1"
    assert policy_version == expected["policy_version"] == "metric-policy:utility:v1"
    assert metric_ids == tuple(expected["metric_ids"]) == UTILITY_METRIC_IDS
    for metric_id, expected_value in expected["values"].items():
        assert values[metric_id] == Decimal(expected_value)
    for metric_id, expected_decision in expected["decisions"].items():
        decision = decision_by_metric[metric_id]
        assert decision.status == expected_decision["status"]
        assert decision.blocking is expected_decision["blocking"]
        assert decision.reason_code == expected_decision["reason_code"]
        assert decision.evidence_ids == expected_decision["evidence_ids"]
        assert decision.calculation_ids == expected_decision["calculation_ids"]

    calculations_by_formula = {calculation.formula_id: calculation for calculation in calculations}
    assert set(calculations_by_formula) == set(expected["calculation_formula_ids"])
    assert calculations_by_formula["utility-price-to-book-v1"].unit == "multiple"
    assert calculations_by_formula["utility-pe-ratio-v1"].unit == "multiple"
    assert all(
        calculation.unit == "ratio"
        for calculation in calculations
        if calculation.formula_id not in {"utility-price-to-book-v1", "utility-pe-ratio-v1"}
    )


def test_rate_base_is_direct_evidence_without_calculation_or_balance_sheet_recalculation() -> None:
    fixture = _load_fixture("regulated_capital")
    values, decisions, calculations = _evaluate(fixture)
    decision_by_metric = _assert_result_shape(fixture, (values, decisions, calculations))

    assert values["rate_base"] == Decimal("5000")
    assert decision_by_metric["rate_base"].status == "available"
    assert decision_by_metric["rate_base"].evidence_ids == ["ev_util_rate_base_regulated"]
    assert decision_by_metric["rate_base"].calculation_ids == []
    assert all(calculation.formula_id != "utility-rate-base-direct-v1" for calculation in calculations)
    assert all(
        "ev_util_total_assets" not in calculation.input_evidence_ids
        and "ev_util_property_plant" not in calculation.input_evidence_ids
        for calculation in calculations
    )


def test_missing_rate_base_stays_unavailable_without_blocking_operating_margin() -> None:
    fixture = _load_fixture("missing_rate_base")
    values, decisions, calculations = _evaluate(fixture)
    decision_by_metric = _assert_result_shape(fixture, (values, decisions, calculations))

    assert values["utility_operating_margin"] == Decimal("0.2")
    assert decision_by_metric["utility_operating_margin"].blocking is True
    assert values["rate_base"] is None
    assert decision_by_metric["rate_base"].status == "unavailable"
    assert decision_by_metric["rate_base"].reason_code == "rate_base_not_disclosed"
    assert decision_by_metric["rate_base"].blocking is False
    assert decision_by_metric["rate_base"].evidence_ids == []
    assert decision_by_metric["rate_base"].calculation_ids == []
    assert all(calculation.formula_id != "utility-rate-base-direct-v1" for calculation in calculations)


def test_zero_interest_expense_fails_closed() -> None:
    fixture = _load_fixture("complete")
    evidence_payload = deepcopy(fixture["evidence_records"])
    next(
        record
        for record in evidence_payload
        if record["evidence_id"] == "ev_util_interest_expense"
    )["value"] = "0"

    values, decisions, calculations = _evaluate(fixture, evidence_payload=evidence_payload)
    decision_by_metric = _assert_result_shape(fixture, (values, decisions, calculations))

    assert values["interest_coverage"] is None
    assert decision_by_metric["interest_coverage"].reason_code == "zero_denominator"
    assert decision_by_metric["interest_coverage"].blocking is False
    assert all(
        calculation.formula_id != "utility-interest-coverage-v1" for calculation in calculations
    )


def test_negative_capex_and_net_income_preserve_signs() -> None:
    fixture = _load_fixture("complete")
    evidence_payload = deepcopy(fixture["evidence_records"])
    for evidence_id, value in (
        ("ev_util_capex", "-100"),
        ("ev_util_net_income", "-120"),
    ):
        next(
            record for record in evidence_payload if record["evidence_id"] == evidence_id
        )["value"] = value

    values, decisions, calculations = _evaluate(fixture, evidence_payload=evidence_payload)
    _assert_result_shape(fixture, (values, decisions, calculations))

    assert values["capex_intensity"] == Decimal("-0.1")
    assert values["utility_roe"] == Decimal("-0.12")
    calculations_by_formula = {calculation.formula_id: calculation for calculation in calculations}
    assert calculations_by_formula["utility-capex-intensity-v1"].result == Decimal("-0.1")
    assert calculations_by_formula["utility-roe-v1"].result == Decimal("-0.12")


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("future_filed_at", "filed_after_as_of"),
        ("future_as_of", "filed_after_as_of"),
        ("unvalidated", "unvalidated_evidence_id"),
        ("duplicate", "duplicate_evidence_id"),
    ],
)
def test_invalid_evidence_fails_closed(mutation: str, expected_reason: str) -> None:
    fixture = _load_fixture("complete")
    evidence_payload = deepcopy(fixture["evidence_records"])
    operating_income = next(
        record
        for record in evidence_payload
        if record["evidence_id"] == "ev_util_operating_income"
    )
    if mutation == "future_filed_at":
        operating_income["filed_at"] = "2026-03-03"
    elif mutation == "future_as_of":
        operating_income["as_of"] = "2026-03-03T00:00:00Z"
    elif mutation == "unvalidated":
        operating_income["validation_status"] = "invalid"
    else:
        evidence_payload.append(deepcopy(operating_income))

    values, decisions, calculations = _evaluate(fixture, evidence_payload=evidence_payload)
    decision_by_metric = {
        decision.metric_id: decision for decision in decisions
    }
    assert values["utility_operating_margin"] is None
    assert decision_by_metric["utility_operating_margin"].status == "unavailable"
    assert decision_by_metric["utility_operating_margin"].reason_code == expected_reason
    assert decision_by_metric["utility_operating_margin"].blocking is True
    assert decision_by_metric["utility_operating_margin"].evidence_ids == []
    assert decision_by_metric["utility_operating_margin"].calculation_ids == []
    assert all(
        "ev_util_operating_income" not in calculation.input_evidence_ids
        for calculation in calculations
    )


@pytest.mark.parametrize("market_case", ["invalid", "missing", "multiple"])
def test_market_price_must_be_valid_unique_and_present(market_case: str) -> None:
    fixture = _load_fixture("complete")
    market_payload = deepcopy(fixture["market_price_records"])
    if market_case == "invalid":
        market_payload[0]["validation_status"] = "invalid"
    elif market_case == "missing":
        market_payload = []
    else:
        market_payload.append(deepcopy(market_payload[0]) | {"evidence_id": "ev_util_market_price_2"})

    values, decisions, calculations = _evaluate(fixture, market_payload=market_payload)
    decision_by_metric = {
        decision.metric_id: decision for decision in decisions
    }
    expected_reason = (
        "unvalidated_evidence_id" if market_case == "invalid" else "market_price_missing"
    )
    for metric_id in ("price_to_book", "pe_ratio"):
        assert values[metric_id] is None
        assert decision_by_metric[metric_id].status == "unavailable"
        assert decision_by_metric[metric_id].reason_code == expected_reason
        assert decision_by_metric[metric_id].evidence_ids == []
        assert decision_by_metric[metric_id].calculation_ids == []
    assert all(
        calculation.formula_id
        not in {"utility-price-to-book-v1", "utility-pe-ratio-v1"}
        for calculation in calculations
    )


def test_fcf_yield_requires_market_cap_evidence_without_price_times_shares_fallback() -> None:
    fixture = _load_fixture("complete")
    profile_input = deepcopy(fixture["profile_input"])
    del profile_input["metric_inputs"]["market_cap"]

    values, decisions, calculations = _evaluate(fixture, profile_input=profile_input)
    decision_by_metric = {decision.metric_id: decision for decision in decisions}

    assert values["fcf_yield"] is None
    assert decision_by_metric["fcf_yield"].status == "unavailable"
    assert decision_by_metric["fcf_yield"].reason_code == "fcf_yield_missing"
    assert decision_by_metric["fcf_yield"].evidence_ids == []
    assert decision_by_metric["fcf_yield"].calculation_ids == []
    assert all(calculation.formula_id != "utility-fcf-yield-v1" for calculation in calculations)


def test_utility_profile_does_not_open_network(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _load_fixture("complete")

    def fail_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("utility profile must not access the network")

    monkeypatch.setattr(socket, "socket", fail_network)
    values, decisions, calculations = _evaluate(fixture)
    _assert_result_shape(fixture, (values, decisions, calculations))
