from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest

from stockcrewai.models.evidence import CalculationRecord, EvidenceRecord, MarketPriceRecord
from stockcrewai.models.policy import PolicyDecision


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "profiles" / "spac"
_UNSET = object()
SPAC_METRIC_IDS = (
    "spac_trust_cash",
    "spac_warrant_dilution_ratio",
    "spac_pro_forma_shares",
    "spac_cash_per_pro_forma_share",
)


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
    }
    assert fixture["synthetic"] is True
    assert fixture["offline"] is True
    assert fixture["no_network"] is True
    assert "offline" in fixture["source_note"].lower()
    assert "network" in fixture["source_note"].lower()
    return fixture


def _validated_records(
    fixture: dict[str, Any],
    *,
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


def _spac_api() -> tuple[str, str, tuple[str, ...], Any]:
    try:
        from stockcrewai.profiles.spac import (
            POLICY_VERSION,
            PROFILE_VERSION,
            SPAC_METRIC_IDS,
            evaluate_spac_profile,
        )
    except (ImportError, AttributeError) as exc:
        pytest.fail(f"SPAC profile API is not implemented: {exc}", pytrace=False)
    return PROFILE_VERSION, POLICY_VERSION, tuple(SPAC_METRIC_IDS), evaluate_spac_profile


def _evaluate(
    fixture: dict[str, Any],
    *,
    profile_input: Any = _UNSET,
    evidence_payload: list[dict[str, Any]] | None = None,
    market_payload: list[dict[str, Any]] | None = None,
) -> tuple[
    dict[str, Decimal | None],
    tuple[PolicyDecision, ...],
    tuple[CalculationRecord, ...],
]:
    evidence_records, market_price_records = _validated_records(
        fixture,
        evidence_payload=evidence_payload,
        market_payload=market_payload,
    )
    evaluate = _spac_api()[3]
    return evaluate(
        fixture["profile_input"] if profile_input is _UNSET else profile_input,
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
    evidence_records, _ = _validated_records(fixture)
    evidence_ids = {record.evidence_id for record in evidence_records}

    assert tuple(values) == SPAC_METRIC_IDS
    assert tuple(decision.metric_id for decision in decisions) == SPAC_METRIC_IDS
    assert all(isinstance(decision, PolicyDecision) for decision in decisions)
    assert all(decision.blocking is False for decision in decisions)
    assert all(isinstance(calculation, CalculationRecord) for calculation in calculations)

    calculation_ids = {calculation.calculation_id for calculation in calculations}
    assert len(calculation_ids) == len(calculations)
    for calculation in calculations:
        assert calculation.input_evidence_ids
        assert len(calculation.input_evidence_ids) == len(set(calculation.input_evidence_ids))
        assert set(calculation.input_evidence_ids) <= evidence_ids
        assert calculation.source_reference == f"derived:{calculation.formula_id}"
        assert calculation.result is not None and calculation.result.is_finite()
        assert calculation.as_of.tzinfo is not None
        assert calculation.period_start <= calculation.period_end
        assert calculation.validation_status.value == "valid"

    decisions_by_metric = {decision.metric_id: decision for decision in decisions}
    for decision in decisions:
        assert decision.reason_code
        assert len(decision.evidence_ids) == len(set(decision.evidence_ids))
        assert len(decision.calculation_ids) == len(set(decision.calculation_ids))
        assert set(decision.evidence_ids) <= evidence_ids
        assert set(decision.calculation_ids) <= calculation_ids
        if decision.status != "available":
            assert decision.evidence_ids == []
            assert decision.calculation_ids == []

    for value in values.values():
        if value is not None:
            assert isinstance(value, Decimal)
            assert value.is_finite()
    return decisions_by_metric


def test_complete_pre_merger_calculates_spac_metrics_with_provenance() -> None:
    fixture = _load_fixture("complete_pre_merger")
    values, decisions, calculations = _evaluate(fixture)
    decisions_by_metric = _assert_result_shape(fixture, (values, decisions, calculations))

    profile_version, policy_version, metric_ids, _ = _spac_api()
    assert profile_version == "spac-profile:v1"
    assert policy_version == "metric-policy:spac:v1"
    assert metric_ids == SPAC_METRIC_IDS
    assert values == {
        "spac_trust_cash": Decimal("1200"),
        "spac_warrant_dilution_ratio": Decimal("0.25"),
        "spac_pro_forma_shares": Decimal("125"),
        "spac_cash_per_pro_forma_share": Decimal("9.6"),
    }

    assert decisions_by_metric["spac_trust_cash"].status == "available"
    assert decisions_by_metric["spac_trust_cash"].reason_code == "validated_evidence"
    assert decisions_by_metric["spac_trust_cash"].evidence_ids == ["ev_spac_trust_cash"]
    assert decisions_by_metric["spac_trust_cash"].calculation_ids == []
    assert decisions_by_metric["spac_warrant_dilution_ratio"].evidence_ids == [
        "ev_spac_warrants",
        "ev_spac_basic_shares",
    ]
    assert decisions_by_metric["spac_pro_forma_shares"].evidence_ids == [
        "ev_spac_basic_shares",
        "ev_spac_warrants",
    ]
    assert decisions_by_metric["spac_cash_per_pro_forma_share"].evidence_ids == [
        "ev_spac_trust_cash",
        "ev_spac_basic_shares",
        "ev_spac_warrants",
    ]

    calculations_by_metric = {
        calculation.formula_id: calculation for calculation in calculations
    }
    assert set(calculations_by_metric) == {
        "spac-warrant-dilution-ratio-v1",
        "spac-pro-forma-shares-v1",
        "spac-cash-per-pro-forma-share-v1",
    }
    assert calculations_by_metric["spac-warrant-dilution-ratio-v1"].unit == "ratio"
    assert calculations_by_metric["spac-pro-forma-shares-v1"].unit == "shares"
    assert calculations_by_metric["spac-cash-per-pro-forma-share-v1"].unit == "USD/share"
    assert all(calculation.source_reference.startswith("derived:") for calculation in calculations)
    assert not {"revenue", "pe_ratio", "fcf_yield", "operating_margin"}.intersection(values)


def test_post_merger_uses_the_same_spac_only_metric_boundary() -> None:
    pre = _evaluate(_load_fixture("complete_pre_merger"))
    post = _evaluate(_load_fixture("complete_post_merger"))

    assert pre[0] == post[0]
    assert pre[1] == post[1]
    assert pre[2] == post[2]
    assert tuple(post[0]) == SPAC_METRIC_IDS


def test_missing_trust_cash_keeps_share_structure_metrics_and_blocks_cash_per_share() -> None:
    fixture = _load_fixture("missing_trust_cash")
    values, decisions, calculations = _evaluate(fixture)
    decisions_by_metric = _assert_result_shape(fixture, (values, decisions, calculations))

    assert values["spac_trust_cash"] is None
    assert values["spac_warrant_dilution_ratio"] == Decimal("0.25")
    assert values["spac_pro_forma_shares"] == Decimal("125")
    assert values["spac_cash_per_pro_forma_share"] is None
    assert decisions_by_metric["spac_trust_cash"].reason_code == "missing_input"
    assert decisions_by_metric["spac_cash_per_pro_forma_share"].reason_code == "missing_input"
    assert all(
        calculation.formula_id != "spac-cash-per-pro-forma-share-v1"
        for calculation in calculations
    )


def test_invalid_warrants_fail_closed_without_partial_calculations() -> None:
    fixture = _load_fixture("invalid_warrants")
    values, decisions, calculations = _evaluate(fixture)
    decisions_by_metric = _assert_result_shape(fixture, (values, decisions, calculations))

    assert values["spac_trust_cash"] == Decimal("1200")
    assert all(value is None for value in values.values() if value != values["spac_trust_cash"])
    assert calculations == ()
    for metric_id in SPAC_METRIC_IDS[1:]:
        assert decisions_by_metric[metric_id].reason_code == "zero_denominator"
        assert decisions_by_metric[metric_id].evidence_ids == []
        assert decisions_by_metric[metric_id].calculation_ids == []


def test_duplicate_and_future_evidence_fail_closed_with_stable_reasons() -> None:
    fixture = _load_fixture("future_or_duplicate")
    values, decisions, calculations = _evaluate(fixture)
    decisions_by_metric = _assert_result_shape(fixture, (values, decisions, calculations))

    assert all(value is None for value in values.values())
    assert calculations == ()
    assert decisions_by_metric["spac_trust_cash"].reason_code == "duplicate_evidence_id"
    assert decisions_by_metric["spac_warrant_dilution_ratio"].reason_code == "filed_after_as_of"
    assert decisions_by_metric["spac_pro_forma_shares"].reason_code == "filed_after_as_of"
    assert decisions_by_metric["spac_cash_per_pro_forma_share"].reason_code == "duplicate_evidence_id"


@pytest.mark.parametrize(
    ("metric_key", "field", "value", "reason_code"),
    [
        ("trust_cash", "unit", "shares", "unit_mismatch"),
        ("trust_cash", "value", "0", "spac_trust_cash_invalid"),
        ("basic_shares", "currency", "USD", "unit_mismatch"),
        ("basic_shares", "validation_status", "unvalidated", "unvalidated_evidence_id"),
    ],
)
def test_invalid_units_status_and_trust_cash_fail_closed(
    metric_key: str, field: str, value: str, reason_code: str
) -> None:
    fixture = _load_fixture("complete_pre_merger")
    evidence_payload = deepcopy(fixture["evidence_records"])
    evidence_id = fixture["profile_input"]["metric_inputs"][metric_key]["evidence_id"]
    target = next(item for item in evidence_payload if item["evidence_id"] == evidence_id)
    target[field] = value
    if field == "validation_status":
        target["value"] = None

    values, decisions, calculations = _evaluate(fixture, evidence_payload=evidence_payload)
    decisions_by_metric = _assert_result_shape(fixture, (values, decisions, calculations))

    assert values["spac_trust_cash"] is None if metric_key == "trust_cash" else True
    affected_metrics = {
        "trust_cash": ("spac_trust_cash", "spac_cash_per_pro_forma_share"),
        "basic_shares": (
            "spac_warrant_dilution_ratio",
            "spac_pro_forma_shares",
            "spac_cash_per_pro_forma_share",
        ),
    }[metric_key]
    for affected_metric in affected_metrics:
        assert values[affected_metric] is None
        assert decisions_by_metric[affected_metric].reason_code == reason_code
        assert decisions_by_metric[affected_metric].evidence_ids == []
        assert decisions_by_metric[affected_metric].calculation_ids == []


def test_raw_evidence_id_envelope_and_invalid_transaction_status_fail_closed() -> None:
    fixture = _load_fixture("complete_pre_merger")
    profile_input = deepcopy(fixture["profile_input"])
    profile_input["metric_inputs"]["trust_cash"] = "ev_spac_trust_cash"
    values, decisions, calculations = _evaluate(fixture, profile_input=profile_input)
    decisions_by_metric = _assert_result_shape(fixture, (values, decisions, calculations))
    assert values["spac_trust_cash"] is None
    assert values["spac_warrant_dilution_ratio"] == Decimal("0.25")
    assert decisions_by_metric["spac_trust_cash"].reason_code == "missing_input"
    assert decisions_by_metric["spac_cash_per_pro_forma_share"].reason_code == "missing_input"

    profile_input = deepcopy(fixture["profile_input"])
    profile_input["transaction_status"] = "merged"
    values, decisions, calculations = _evaluate(fixture, profile_input=profile_input)
    decisions_by_metric = _assert_result_shape(fixture, (values, decisions, calculations))
    assert all(value is None for value in values.values())
    assert calculations == ()
    assert all(
        decision.reason_code == "spac_transaction_status_invalid"
        for decision in decisions_by_metric.values()
    )


def test_market_price_records_are_not_read_or_selected() -> None:
    fixture = _load_fixture("complete_pre_merger")
    baseline = _evaluate(fixture)
    market_payload = deepcopy(fixture["market_price_records"])
    market_payload[0]["price"] = "1"
    market_payload[1]["price"] = "1000000"
    changed = _evaluate(fixture, market_payload=market_payload)

    assert changed == baseline
    assert all(metric_id.startswith("spac_") for metric_id in changed[0])


@pytest.mark.parametrize(
    ("profile_input", "reason_code"),
    [
        (None, "spac_security_profile_invalid"),
        ([], "spac_security_profile_invalid"),
        ({}, "spac_security_profile_invalid"),
        (
            {"security_profile": "spac", "transaction_status": "pre_merger"},
            "missing_input",
        ),
        (
            {
                "security_profile": "spac",
                "transaction_status": "pre_merger",
                "as_of": "not-a-datetime",
                "metric_inputs": {},
            },
            "missing_input",
        ),
    ],
)
def test_malformed_profile_input_never_raises_and_returns_typed_unavailable(
    profile_input: Any,
    reason_code: str,
) -> None:
    fixture = _load_fixture("complete_pre_merger")
    values, decisions, calculations = _evaluate(
        fixture,
        profile_input=profile_input,
        evidence_payload=[],
        market_payload=[],
    )

    assert tuple(values) == SPAC_METRIC_IDS
    assert all(value is None for value in values.values())
    assert tuple(decision.metric_id for decision in decisions) == SPAC_METRIC_IDS
    assert all(decision.status == "unavailable" for decision in decisions)
    assert all(decision.reason_code == reason_code for decision in decisions)
    assert all(decision.blocking is False for decision in decisions)
    assert calculations == ()
