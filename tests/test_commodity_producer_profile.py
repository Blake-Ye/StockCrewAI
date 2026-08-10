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


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "profiles" / "commodity_producer"
COMMODITY_PRODUCER_METRIC_IDS = (
    "realized_price",
    "production",
    "realized_price_change",
    "production_change",
    "proved_reserves",
    "reserve_life_years",
    "impairment_charge",
    "impairment_to_commodity_revenue",
    "pe_ratio",
)
REQUIRED_METRIC_IDS = {"realized_price", "production"}


def _load_fixture(name: str) -> dict[str, Any]:
    fixture = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert set(fixture) == {
        "fixture_version",
        "synthetic",
        "offline",
        "no_network",
        "source_note",
        "profile_input",
        "evidence_records",
        "market_price_records",
        "expected",
    }
    assert fixture["synthetic"] is True
    assert fixture["offline"] is True
    assert fixture["no_network"] is True
    assert "offline" in fixture["source_note"].lower()
    assert "network" in fixture["source_note"].lower()

    profile_input = fixture["profile_input"]
    assert profile_input["profile_version"] == "commodity-profile:v1"
    assert profile_input["policy_version"] == "metric-policy:commodity:v1"
    assert profile_input["primary_commodity"] == "copper"
    assert profile_input["as_of"] == "2026-03-02T21:00:00Z"
    assert isinstance(profile_input["metric_inputs"], dict)
    for input_envelope in profile_input["metric_inputs"].values():
        assert set(input_envelope) == {"evidence_id", "commodity"}
        assert input_envelope["commodity"] == "copper"

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
        fixture["market_price_records"]
        if market_payload is None
        else market_payload
    )
    return (
        tuple(EvidenceRecord.model_validate(item) for item in evidence_items),
        tuple(MarketPriceRecord.model_validate(item) for item in market_items),
    )


def _commodity_api() -> tuple[str, str, tuple[str, ...], Any]:
    try:
        from stockcrewai.profiles.commodity_producer import (
            COMMODITY_PRODUCER_METRIC_IDS,
            POLICY_VERSION,
            PROFILE_VERSION,
            evaluate_commodity_producer_profile,
        )
    except (ImportError, AttributeError) as exc:
        pytest.fail(f"Commodity producer profile API is not implemented: {exc}", pytrace=False)
    return (
        PROFILE_VERSION,
        POLICY_VERSION,
        tuple(COMMODITY_PRODUCER_METRIC_IDS),
        evaluate_commodity_producer_profile,
    )


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
    _, _, _, evaluate_commodity_producer_profile = _commodity_api()
    return evaluate_commodity_producer_profile(
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

    assert tuple(values) == COMMODITY_PRODUCER_METRIC_IDS
    assert tuple(decision.metric_id for decision in decisions) == COMMODITY_PRODUCER_METRIC_IDS
    assert len(decisions) == len(COMMODITY_PRODUCER_METRIC_IDS)
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
        assert calculation.validation_status.value == "valid"

    decision_by_metric = {decision.metric_id: decision for decision in decisions}
    for decision in decisions:
        assert decision.reason_code
        assert len(decision.evidence_ids) == len(set(decision.evidence_ids))
        assert len(decision.calculation_ids) == len(set(decision.calculation_ids))
        assert set(decision.evidence_ids) <= source_ids
        assert set(decision.calculation_ids) <= calculation_id_allowlist
        assert decision.blocking is (decision.metric_id in REQUIRED_METRIC_IDS)
        if decision.status != "available":
            assert decision.evidence_ids == []
            assert decision.calculation_ids == []

    for value in values.values():
        if value is not None:
            assert isinstance(value, Decimal)
            assert value.is_finite()

    return decision_by_metric


def test_commodity_profile_constants_and_metric_order() -> None:
    profile_version, policy_version, metric_ids, _ = _commodity_api()

    assert profile_version == "commodity-profile:v1"
    assert policy_version == "metric-policy:commodity:v1"
    assert metric_ids == COMMODITY_PRODUCER_METRIC_IDS
    assert metric_ids[:2] == ("realized_price", "production")
    assert set(metric_ids[2:]) == set(COMMODITY_PRODUCER_METRIC_IDS[2:])


def test_complete_commodity_profile_values_decisions_and_calculation_records() -> None:
    fixture = _load_fixture("complete")
    evidence_records, market_price_records = _validated_records(fixture)
    profile_version, policy_version, metric_ids, evaluate = _commodity_api()

    result = evaluate(fixture["profile_input"], evidence_records, market_price_records)
    values, decisions, calculations = result
    decision_by_metric = _assert_result_shape(fixture, result)
    expected = fixture["expected"]

    assert profile_version == expected["profile_version"] == "commodity-profile:v1"
    assert policy_version == expected["policy_version"] == "metric-policy:commodity:v1"
    assert metric_ids == tuple(expected["metric_ids"]) == COMMODITY_PRODUCER_METRIC_IDS
    for metric_id, expected_value in expected["values"].items():
        assert values[metric_id] == Decimal(expected_value)

    for metric_id, expected_decision in expected["decisions"].items():
        decision = decision_by_metric[metric_id]
        assert decision.status == expected_decision["status"]
        assert decision.blocking is expected_decision["blocking"]
        assert decision.reason_code == expected_decision["reason_code"]
        assert decision.evidence_ids == expected_decision["evidence_ids"]
        assert decision.calculation_ids == expected_decision["calculation_ids"]

    calculations_by_id = {calculation.calculation_id: calculation for calculation in calculations}
    assert set(calculations_by_id) == set(expected["calculations"])
    for calculation_id, expected_calculation in expected["calculations"].items():
        calculation = calculations_by_id[calculation_id]
        assert calculation.formula_id == expected_calculation["formula_id"]
        assert calculation.input_evidence_ids == expected_calculation["input_evidence_ids"]
        assert calculation.result == Decimal(expected_calculation["result"])
        assert calculation.unit == expected_calculation["unit"]
        assert calculation.source_reference == f"derived:{calculation.formula_id}"


def test_price_and_production_change_use_independent_pairs() -> None:
    fixture = _load_fixture("price_cycle")
    values, decisions, calculations = _evaluate(fixture)
    _assert_result_shape(fixture, (values, decisions, calculations))

    assert values["realized_price"] == Decimal("100")
    assert values["production"] == Decimal("120")
    assert values["realized_price_change"] == Decimal("0.25")
    assert values["production_change"] == Decimal(
        "-0.0769230769230769230769230769"
    )

    calculations_by_metric = {
        calculation.formula_id: calculation for calculation in calculations
    }
    assert calculations_by_metric["commodity-realized-price-change-v1"].input_evidence_ids == [
        "ev_com_realized_price_current",
        "ev_com_realized_price_prior",
    ]
    assert calculations_by_metric["commodity-production-change-v1"].input_evidence_ids == [
        "ev_com_production_current",
        "ev_com_production_prior",
    ]
    assert set(
        calculations_by_metric["commodity-realized-price-change-v1"].input_evidence_ids
    ).isdisjoint(
        calculations_by_metric["commodity-production-change-v1"].input_evidence_ids
    )
    assert decisions[2].metric_id == "realized_price_change"
    assert decisions[3].metric_id == "production_change"


def test_reserves_use_only_proved_reserves_and_do_not_infer_substitutes() -> None:
    fixture = _load_fixture("reserves")
    values, decisions, calculations = _evaluate(fixture)
    decision_by_metric = _assert_result_shape(fixture, (values, decisions, calculations))

    assert values["proved_reserves"] == Decimal("1200")
    assert values["reserve_life_years"] == Decimal("10")
    assert decision_by_metric["proved_reserves"].evidence_ids == ["ev_com_proved_reserves"]
    assert decision_by_metric["reserve_life_years"].evidence_ids == [
        "ev_com_proved_reserves",
        "ev_com_annual_production",
    ]
    mapped_keys = set(fixture["profile_input"]["metric_inputs"])
    assert not mapped_keys & {"resources", "probable_reserves", "total_reserves"}
    assert not any(
        forbidden_id in source_id
        for forbidden_id in ("resources", "probable", "total")
        for source_id in (
            decision_by_metric["proved_reserves"].evidence_ids
            + decision_by_metric["reserve_life_years"].evidence_ids
        )
    )
    assert all(
        not any(
            forbidden_id in source_id
            for forbidden_id in ("resources", "probable", "total")
            for source_id in calculation.input_evidence_ids
        )
        for calculation in calculations
    )


def test_impairment_ratio_uses_impairment_and_commodity_revenue_only() -> None:
    fixture = _load_fixture("impairment")
    values, decisions, calculations = _evaluate(fixture)
    decision_by_metric = _assert_result_shape(fixture, (values, decisions, calculations))

    assert values["impairment_charge"] == Decimal("50")
    assert values["impairment_to_commodity_revenue"] == Decimal("0.05")
    assert decision_by_metric["impairment_charge"].evidence_ids == [
        "ev_com_impairment_charge"
    ]
    assert decision_by_metric["impairment_to_commodity_revenue"].evidence_ids == [
        "ev_com_impairment_charge",
        "ev_com_commodity_revenue",
    ]
    assert not set(fixture["profile_input"]["metric_inputs"]) & {
        "operating_loss",
        "restructuring_charge",
    }
    assert all(
        not any(
            forbidden_id in source_id
            for forbidden_id in ("operating_loss", "restructuring")
            for source_id in calculation.input_evidence_ids
        )
        for calculation in calculations
    )


@pytest.mark.parametrize("missing_metric", ["realized_price", "production"])
def test_missing_required_metric_blocks_independently(missing_metric: str) -> None:
    fixture = _load_fixture("missing_required")
    profile_input = deepcopy(fixture["profile_input"])
    profile_input["metric_inputs"].pop(missing_metric)

    values, decisions, calculations = _evaluate(fixture, profile_input=profile_input)
    decision_by_metric = _assert_result_shape(fixture, (values, decisions, calculations))
    decision = decision_by_metric[missing_metric]

    assert values[missing_metric] is None
    assert decision.status == "unavailable"
    assert decision.blocking is True
    assert decision.evidence_ids == []
    assert decision.calculation_ids == []
    assert all(
        other_decision.blocking is (other_decision.metric_id in REQUIRED_METRIC_IDS)
        for other_decision in decisions
    )
    assert all(
        missing_metric not in calculation.formula_id for calculation in calculations
    )


def test_commodity_scope_fails_closed() -> None:
    fixture = _load_fixture("complete")

    mismatch_input = deepcopy(fixture["profile_input"])
    mismatch_input["metric_inputs"]["realized_price"]["commodity"] = "gold"
    values, decisions, _ = _evaluate(fixture, profile_input=mismatch_input)
    decision = next(item for item in decisions if item.metric_id == "realized_price")

    assert values["realized_price"] is None
    assert decision.status == "unavailable"
    assert decision.reason_code == "commodity_mismatch"
    assert decision.blocking is True
    assert decision.evidence_ids == []
    assert decision.calculation_ids == []

    missing_scope_input = deepcopy(fixture["profile_input"])
    missing_scope_input["metric_inputs"]["realized_price"].pop("commodity")
    values, decisions, _ = _evaluate(fixture, profile_input=missing_scope_input)
    decision = next(item for item in decisions if item.metric_id == "realized_price")

    assert values["realized_price"] is None
    assert decision.status == "unavailable"
    assert decision.reason_code == "commodity_scope_missing"
    assert decision.blocking is True
    assert decision.evidence_ids == []
    assert decision.calculation_ids == []


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("invalid", "unvalidated_evidence_id"),
        ("unvalidated", "unvalidated_evidence_id"),
        ("duplicate", "duplicate_evidence_id"),
        ("future_as_of", "filed_after_as_of"),
        ("future_filed_at", "filed_after_as_of"),
    ],
)
def test_invalid_duplicate_unvalidated_or_future_evidence_fails_closed(
    mutation: str, expected_reason: str
) -> None:
    fixture = _load_fixture("complete")
    evidence_payload = deepcopy(fixture["evidence_records"])
    current_price = next(
        record
        for record in evidence_payload
        if record["evidence_id"] == "ev_com_realized_price_current"
    )
    if mutation == "invalid":
        current_price["validation_status"] = "invalid"
    elif mutation == "unvalidated":
        current_price["validation_status"] = "unvalidated"
    elif mutation == "duplicate":
        evidence_payload.append(deepcopy(current_price))
    elif mutation == "future_as_of":
        current_price["as_of"] = "2026-03-03T00:00:00Z"
    else:
        current_price["filed_at"] = "2026-03-03"

    values, decisions, calculations = _evaluate(
        fixture, evidence_payload=evidence_payload
    )
    decision_by_metric = {
        decision.metric_id: decision for decision in decisions
    }
    decision = decision_by_metric["realized_price"]

    assert values["realized_price"] is None
    assert decision.status == "unavailable"
    assert decision.reason_code == expected_reason
    assert decision.blocking is True
    assert decision.evidence_ids == []
    assert decision.calculation_ids == []
    assert all(
        "ev_com_realized_price_current" not in calculation.input_evidence_ids
        for calculation in calculations
    )


def test_market_price_is_used_only_for_pe() -> None:
    fixture = _load_fixture("complete")
    market_payload = deepcopy(fixture["market_price_records"])
    market_payload[0]["price"] = "200"

    values, decisions, calculations = _evaluate(
        fixture, market_payload=market_payload
    )
    decision_by_metric = _assert_result_shape(fixture, (values, decisions, calculations))

    assert values["realized_price"] == Decimal("100")
    assert values["pe_ratio"] == Decimal("50")
    for metric_id, decision in decision_by_metric.items():
        if metric_id != "pe_ratio":
            assert "ev_com_market_price" not in decision.evidence_ids
    assert decision_by_metric["pe_ratio"].evidence_ids == [
        "ev_com_market_price",
        "ev_com_diluted_eps",
    ]
    assert all(
        calculation.input_evidence_ids == [
            "ev_com_market_price",
            "ev_com_diluted_eps",
        ]
        or "ev_com_market_price" not in calculation.input_evidence_ids
        for calculation in calculations
    )


@pytest.mark.parametrize("eps", ["0", "-4"])
def test_non_positive_eps_makes_pe_not_applicable(eps: str) -> None:
    fixture = _load_fixture("complete")
    evidence_payload = deepcopy(fixture["evidence_records"])
    diluted_eps = next(
        record
        for record in evidence_payload
        if record["evidence_id"] == "ev_com_diluted_eps"
    )
    diluted_eps["value"] = eps

    values, decisions, calculations = _evaluate(
        fixture, evidence_payload=evidence_payload
    )
    decision_by_metric = _assert_result_shape(fixture, (values, decisions, calculations))
    decision = decision_by_metric["pe_ratio"]

    assert values["pe_ratio"] is None
    assert decision.status == "not_applicable"
    assert decision.reason_code == "non-positive-eps"
    assert decision.blocking is False
    assert decision.evidence_ids == []
    assert decision.calculation_ids == []
    assert all(calculation.formula_id != "commodity-pe-ratio-v1" for calculation in calculations)


def test_commodity_profile_does_not_open_network(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _load_fixture("complete")

    def fail_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("commodity profile must not access the network")

    monkeypatch.setattr(socket, "socket", fail_network)
    values, decisions, calculations = _evaluate(fixture)
    _assert_result_shape(fixture, (values, decisions, calculations))
