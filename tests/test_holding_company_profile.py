from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, Overflow, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP, localcontext
import json
from pathlib import Path
from typing import Any

import pytest

from stockcrewai.models.evidence import CalculationRecord, EvidenceRecord, MarketPriceRecord
from stockcrewai.models.policy import PolicyDecision
from stockcrewai.profiles.holding_company import (
    HOLDING_COMPANY_METRIC_IDS,
    POLICY_VERSION,
    PROFILE_VERSION,
    evaluate_holding_company_profile,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "profiles" / "holding_company"
FIXTURE = FIXTURE_DIR / "complete.json"


def _load_fixture(name: str) -> dict[str, Any]:
    fixture_path = FIXTURE_DIR / f"{name}.json"
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def _evaluate(
    fixture: dict[str, Any],
) -> tuple[
    dict[str, Decimal | None],
    tuple[PolicyDecision, ...],
    tuple[CalculationRecord, ...],
]:
    evidence = tuple(
        EvidenceRecord.model_validate(item) for item in fixture["evidence_records"]
    )
    prices = tuple(
        MarketPriceRecord.model_validate(item)
        for item in fixture["market_price_records"]
    )
    return evaluate_holding_company_profile(fixture["profile_input"], evidence, prices)


def _decisions_by_metric(
    decisions: tuple[PolicyDecision, ...],
) -> dict[str, PolicyDecision]:
    return {decision.metric_id: decision for decision in decisions}


def _assert_unavailable_without_provenance(
    decision: PolicyDecision,
    reason_code: str,
    *,
    status: str = "unavailable",
    blocking: bool = True,
) -> None:
    assert decision.status == status
    assert decision.reason_code == reason_code
    assert decision.blocking is blocking
    assert decision.evidence_ids == []
    assert decision.calculation_ids == []


def test_complete_holding_company_hand_calculation_and_provenance() -> None:
    fixture = _load_fixture("complete")
    values, decisions, calculations = _evaluate(fixture)

    assert PROFILE_VERSION == "holding-company-profile:v1"
    assert POLICY_VERSION == "metric-policy:holding-company:v1"
    assert tuple(values) == HOLDING_COMPANY_METRIC_IDS == (
        "attributable_holdings_value",
        "holding_company_nav",
        "holding_company_market_cap",
        "holding_company_nav_discount",
        "pe_ratio",
        "fcf_yield",
        "historical_valuation",
        "reverse_dcf",
    )
    assert values["attributable_holdings_value"] == Decimal("800")
    assert values["holding_company_nav"] == Decimal("680")
    assert values["holding_company_market_cap"] == Decimal("500")
    assert values["holding_company_nav_discount"] == Decimal("180") / Decimal("680")
    assert all(values[metric_id] is None for metric_id in HOLDING_COMPANY_METRIC_IDS[4:])

    decisions_by_metric = {decision.metric_id: decision for decision in decisions}
    assert all(isinstance(decision, PolicyDecision) for decision in decisions)
    assert decisions_by_metric["attributable_holdings_value"].status == "available"
    assert decisions_by_metric["attributable_holdings_value"].blocking is True
    assert decisions_by_metric["attributable_holdings_value"].evidence_ids == [
        "ev_holding_a_fair_value",
        "ev_holding_a_ownership",
        "ev_holding_b_fair_value",
        "ev_holding_b_ownership",
    ]
    assert decisions_by_metric["attributable_holdings_value"].calculation_ids == [
        "calc_holding_attributable_holdings_value_v1"
    ]
    assert decisions_by_metric["holding_company_nav"].evidence_ids == [
        "ev_holding_a_fair_value",
        "ev_holding_a_ownership",
        "ev_holding_b_fair_value",
        "ev_holding_b_ownership",
        "ev_parent_net_debt",
        "ev_other_adjustments",
    ]
    assert decisions_by_metric["holding_company_nav"].calculation_ids == [
        "calc_holding_company_nav_v1"
    ]
    assert decisions_by_metric["holding_company_market_cap"].evidence_ids == [
        "ev_parent_market_price",
        "ev_parent_shares",
    ]
    assert decisions_by_metric["holding_company_nav_discount"].evidence_ids == [
        "ev_holding_a_fair_value",
        "ev_holding_a_ownership",
        "ev_holding_b_fair_value",
        "ev_holding_b_ownership",
        "ev_parent_net_debt",
        "ev_other_adjustments",
        "ev_parent_market_price",
        "ev_parent_shares",
    ]
    for metric_id, reason_code in {
        "pe_ratio": "holding_company_pe_not_applicable",
        "fcf_yield": "holding_company_fcf_not_applicable",
        "historical_valuation": "holding_company_historical_valuation_not_applicable",
        "reverse_dcf": "holding_company_reverse_dcf_not_applicable",
    }.items():
        decision = decisions_by_metric[metric_id]
        assert decision.status == "not_applicable"
        assert decision.blocking is False
        assert decision.reason_code == reason_code
        assert decision.evidence_ids == []
        assert decision.calculation_ids == []

    assert all(isinstance(calculation, CalculationRecord) for calculation in calculations)
    assert {calculation.formula_id for calculation in calculations} == {
        "holding-attributable-component-value-v1",
        "holding-attributable-holdings-value-v1",
        "holding-company-nav-v1",
        "holding-company-market-cap-v1",
        "holding-company-nav-discount-v1",
    }


@pytest.mark.parametrize(
    ("fixture_name", "reason_code", "status"),
    [
        ("missing_ownership_ratio", "holding_ownership_ratio_missing", "unavailable"),
        ("duplicate_holding_value", "holding_double_count_detected", "invalid"),
        (
            "parent_consolidated_assets",
            "holding_parent_consolidated_value_disallowed",
            "invalid",
        ),
        ("currency_mismatch", "currency_mismatch", "invalid"),
        ("point_in_time_mismatch", "point_in_time_mismatch", "unavailable"),
    ],
)
def test_holding_company_core_inputs_fail_closed(
    fixture_name: str, reason_code: str, status: str
) -> None:
    fixture = _load_fixture(fixture_name)
    values, decisions, calculations = _evaluate(fixture)
    decisions_by_metric = _decisions_by_metric(decisions)

    for metric_id in ("attributable_holdings_value", "holding_company_nav"):
        assert values[metric_id] is None
        _assert_unavailable_without_provenance(
            decisions_by_metric[metric_id],
            reason_code,
            status=status,
        )
    assert all(
        calculation.formula_id
        not in {
            "holding-attributable-component-value-v1",
            "holding-attributable-holdings-value-v1",
            "holding-company-nav-v1",
        }
        for calculation in calculations
    )


def test_holding_company_missing_or_malformed_input_never_raises() -> None:
    for profile_input in (
        {},
        {"metric_inputs": {}},
        {"metric_inputs": {"holdings": []}},
        {"metric_inputs": {"holdings": [None]}},
    ):
        values, decisions, calculations = evaluate_holding_company_profile(
            profile_input, (), ()
        )
        decisions_by_metric = _decisions_by_metric(decisions)
        assert values["attributable_holdings_value"] is None
        assert values["holding_company_nav"] is None
        _assert_unavailable_without_provenance(
            decisions_by_metric["attributable_holdings_value"],
            "holding_components_missing",
        )
        _assert_unavailable_without_provenance(
            decisions_by_metric["holding_company_nav"],
            "holding_components_missing",
        )
        assert calculations == ()


def test_holding_company_supports_arbitrary_number_of_holdings() -> None:
    fixture = _load_fixture("complete")
    fixture["profile_input"]["metric_inputs"]["holdings"].append(
        {
            "holding_id": "holding_c",
            "fair_value_evidence_id": "ev_holding_c_fair_value",
            "ownership_ratio_evidence_id": "ev_holding_c_ownership",
            "value_scope": "standalone_equity_or_asset_value",
        }
    )
    fixture["evidence_records"].extend(
        [
            deepcopy(fixture["evidence_records"][0])
            | {
                "evidence_id": "ev_holding_c_fair_value",
                "source_reference": "fixture:holding-company/holding-c/fair-value",
                "value": "200",
            },
            deepcopy(fixture["evidence_records"][1])
            | {
                "evidence_id": "ev_holding_c_ownership",
                "source_reference": "fixture:holding-company/holding-c/ownership",
                "value": "0.5",
            },
        ]
    )

    values, decisions, calculations = _evaluate(fixture)
    decisions_by_metric = _decisions_by_metric(decisions)
    expected_holding_evidence = [
        "ev_holding_a_fair_value",
        "ev_holding_a_ownership",
        "ev_holding_b_fair_value",
        "ev_holding_b_ownership",
        "ev_holding_c_fair_value",
        "ev_holding_c_ownership",
    ]
    assert values["attributable_holdings_value"] == Decimal("900")
    assert values["holding_company_nav"] == Decimal("780")
    assert (
        decisions_by_metric["attributable_holdings_value"].evidence_ids
        == expected_holding_evidence
    )
    assert decisions_by_metric["holding_company_nav"].evidence_ids == [
        *expected_holding_evidence,
        "ev_parent_net_debt",
        "ev_other_adjustments",
    ]
    calculations_by_formula = {
        calculation.formula_id: calculation for calculation in calculations
    }
    assert (
        calculations_by_formula["holding-attributable-holdings-value-v1"].input_evidence_ids
        == expected_holding_evidence
    )
    assert calculations_by_formula["holding-company-nav-v1"].input_evidence_ids == [
        *expected_holding_evidence,
        "ev_parent_net_debt",
        "ev_other_adjustments",
    ]


def test_missing_parent_adjustments_do_not_default_to_zero() -> None:
    for missing_key, expected_reason in (
        ("parent_net_debt", "parent_net_debt_missing"),
        ("other_adjustments", "other_adjustments_missing"),
    ):
        fixture = _load_fixture("complete")
        del fixture["profile_input"]["metric_inputs"][missing_key]
        values, decisions, calculations = _evaluate(fixture)
        decisions_by_metric = _decisions_by_metric(decisions)

        assert values["attributable_holdings_value"] == Decimal("800")
        assert values["holding_company_nav"] is None
        assert (
            decisions_by_metric["attributable_holdings_value"].status == "available"
        )
        _assert_unavailable_without_provenance(
            decisions_by_metric["holding_company_nav"],
            expected_reason,
        )
        assert all(
            calculation.formula_id != "holding-company-nav-v1"
            for calculation in calculations
        )


def test_parent_shares_are_optional_and_market_price_is_not_a_nav_input() -> None:
    fixture = _load_fixture("complete")
    del fixture["profile_input"]["metric_inputs"]["parent_shares_outstanding"]
    values, decisions, calculations = _evaluate(fixture)
    decisions_by_metric = _decisions_by_metric(decisions)

    assert values["holding_company_nav"] == Decimal("680")
    assert values["holding_company_market_cap"] is None
    assert values["holding_company_nav_discount"] is None
    _assert_unavailable_without_provenance(
        decisions_by_metric["holding_company_market_cap"],
        "parent_shares_missing",
        blocking=False,
    )
    _assert_unavailable_without_provenance(
        decisions_by_metric["holding_company_nav_discount"],
        "parent_shares_missing",
        blocking=False,
    )
    assert all(
        calculation.formula_id != "holding-company-market-cap-v1"
        for calculation in calculations
    )


def test_missing_market_price_keeps_nav_available() -> None:
    fixture = _load_fixture("missing_market_price")
    values, decisions, calculations = _evaluate(fixture)
    decisions_by_metric = _decisions_by_metric(decisions)

    assert values["holding_company_nav"] == Decimal("680")
    assert values["holding_company_market_cap"] is None
    assert values["holding_company_nav_discount"] is None
    assert decisions_by_metric["holding_company_nav"].status == "available"
    _assert_unavailable_without_provenance(
        decisions_by_metric["holding_company_market_cap"],
        "market_price_missing",
        blocking=False,
    )
    _assert_unavailable_without_provenance(
        decisions_by_metric["holding_company_nav_discount"],
        "market_price_missing",
        blocking=False,
    )
    assert all(
        calculation.formula_id != "holding-company-market-cap-v1"
        for calculation in calculations
    )
    assert all(
        calculation.formula_id != "holding-company-nav-discount-v1"
        for calculation in calculations
    )


def test_nav_non_positive_does_not_produce_discount() -> None:
    fixture = _load_fixture("complete")
    net_debt = next(
        item
        for item in fixture["evidence_records"]
        if item["evidence_id"] == "ev_parent_net_debt"
    )
    net_debt["value"] = "900"
    values, decisions, calculations = _evaluate(fixture)
    decisions_by_metric = _decisions_by_metric(decisions)

    assert values["holding_company_nav"] == Decimal("-120")
    assert values["holding_company_nav_discount"] is None
    assert decisions_by_metric["holding_company_nav"].status == "available"
    _assert_unavailable_without_provenance(
        decisions_by_metric["holding_company_nav_discount"],
        "holding_company_nav_non_positive",
        blocking=False,
    )
    assert all(
        calculation.formula_id != "holding-company-nav-discount-v1"
        for calculation in calculations
    )


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        ("invalid", "unvalidated_evidence_id"),
        ("unvalidated", "unvalidated_evidence_id"),
        ("future_as_of", "filed_after_as_of"),
        ("future_filed_at", "filed_after_as_of"),
        ("unit", "unit_mismatch"),
        ("ratio", "holding_ownership_ratio_invalid"),
    ],
)
def test_invalid_evidence_and_values_fail_closed(
    mutation: str, reason_code: str
) -> None:
    fixture = _load_fixture("complete")
    target = fixture["evidence_records"][1]
    if mutation in {"invalid", "unvalidated"}:
        target["validation_status"] = mutation
    elif mutation == "future_as_of":
        target["as_of"] = "2026-03-03T00:00:00Z"
    elif mutation == "future_filed_at":
        target["filed_at"] = "2026-03-03"
    elif mutation == "unit":
        target["unit"] = "percent"
    else:
        target["value"] = "1.2"

    values, decisions, calculations = _evaluate(fixture)
    decisions_by_metric = _decisions_by_metric(decisions)
    status = "invalid" if mutation in {"unit", "ratio"} else "unavailable"
    for metric_id in ("attributable_holdings_value", "holding_company_nav"):
        assert values[metric_id] is None
        _assert_unavailable_without_provenance(
            decisions_by_metric[metric_id], reason_code, status=status
        )
    assert all(
        "ev_holding_a_ownership" not in calculation.input_evidence_ids
        for calculation in calculations
    )


def test_parent_adjustment_evidence_ids_must_be_unique_across_roles() -> None:
    fixture = _load_fixture("complete")
    metric_inputs = fixture["profile_input"]["metric_inputs"]
    metric_inputs["other_adjustments"]["evidence_id"] = metric_inputs[
        "parent_net_debt"
    ]["evidence_id"]

    values, decisions, calculations = _evaluate(fixture)
    decisions_by_metric = _decisions_by_metric(decisions)

    assert values["holding_company_nav"] is None
    _assert_unavailable_without_provenance(
        decisions_by_metric["holding_company_nav"],
        "holding_double_count_detected",
        status="invalid",
    )
    assert values["holding_company_nav_discount"] is None
    _assert_unavailable_without_provenance(
        decisions_by_metric["holding_company_nav_discount"],
        "holding_double_count_detected",
        status="invalid",
        blocking=False,
    )
    assert all(
        calculation.formula_id
        not in {"holding-company-nav-v1", "holding-company-nav-discount-v1"}
        for calculation in calculations
    )


def test_holding_and_parent_adjustment_evidence_ids_must_be_unique_across_roles() -> None:
    fixture = _load_fixture("complete")
    metric_inputs = fixture["profile_input"]["metric_inputs"]
    metric_inputs["parent_net_debt"]["evidence_id"] = metric_inputs["holdings"][0][
        "fair_value_evidence_id"
    ]

    values, decisions, calculations = _evaluate(fixture)
    decisions_by_metric = _decisions_by_metric(decisions)

    assert values["holding_company_nav"] is None
    _assert_unavailable_without_provenance(
        decisions_by_metric["holding_company_nav"],
        "holding_double_count_detected",
        status="invalid",
    )
    assert values["holding_company_nav_discount"] is None
    _assert_unavailable_without_provenance(
        decisions_by_metric["holding_company_nav_discount"],
        "holding_double_count_detected",
        status="invalid",
        blocking=False,
    )
    assert all(
        calculation.formula_id
        not in {"holding-company-nav-v1", "holding-company-nav-discount-v1"}
        for calculation in calculations
    )


def test_holding_company_calculations_ignore_global_decimal_context() -> None:
    fixture = _load_fixture("complete")

    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        expected_values, _, _ = _evaluate(fixture)

    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        actual_values, _, _ = _evaluate(fixture)

    for metric_id in (
        "holding_company_nav",
        "holding_company_market_cap",
        "holding_company_nav_discount",
    ):
        assert actual_values[metric_id] == expected_values[metric_id]


def test_finite_extreme_nav_inputs_fail_closed_without_raising() -> None:
    fixture = _load_fixture("complete")
    extreme_value = "9.999999999999999999999999999e999999"
    holding_value = next(
        item
        for item in fixture["evidence_records"]
        if item["evidence_id"] == "ev_holding_a_fair_value"
    )
    other_adjustments = next(
        item
        for item in fixture["evidence_records"]
        if item["evidence_id"] == "ev_other_adjustments"
    )
    holding_value["value"] = extreme_value
    other_adjustments["value"] = extreme_value

    values, decisions, calculations = _evaluate(fixture)
    decisions_by_metric = _decisions_by_metric(decisions)

    assert values["holding_company_nav"] is None
    _assert_unavailable_without_provenance(
        decisions_by_metric["holding_company_nav"],
        "holding_decimal_arithmetic_failed",
        status="unavailable",
    )
    assert values["holding_company_nav_discount"] is None
    _assert_unavailable_without_provenance(
        decisions_by_metric["holding_company_nav_discount"],
        "holding_decimal_arithmetic_failed",
        status="unavailable",
        blocking=False,
    )
    assert all(
        calculation.formula_id
        not in {"holding-company-nav-v1", "holding-company-nav-discount-v1"}
        for calculation in calculations
    )


def test_nonzero_tiny_holding_component_fails_closed_without_provenance() -> None:
    fixture = _load_fixture("complete")
    tiny_evidence_ids = {"ev_holding_a_fair_value", "ev_holding_a_ownership"}
    for item in fixture["evidence_records"]:
        if item["evidence_id"] in tiny_evidence_ids:
            item["value"] = "1e-999999"

    values, decisions, calculations = _evaluate(fixture)
    decisions_by_metric = _decisions_by_metric(decisions)

    for metric_id in ("attributable_holdings_value", "holding_company_nav"):
        assert values[metric_id] is None
        _assert_unavailable_without_provenance(
            decisions_by_metric[metric_id],
            "holding_decimal_arithmetic_failed",
        )
    assert all(
        calculation.formula_id
        not in {
            "holding-attributable-component-value-v1",
            "holding-attributable-holdings-value-v1",
            "holding-company-nav-v1",
        }
        for calculation in calculations
    )


def test_finite_extreme_market_cap_inputs_fail_closed_without_raising() -> None:
    fixture = _load_fixture("complete")
    extreme_value = "1e999999"
    parent_shares = next(
        item
        for item in fixture["evidence_records"]
        if item["evidence_id"] == "ev_parent_shares"
    )
    market_price = fixture["market_price_records"][0]
    parent_shares["value"] = extreme_value
    market_price["price"] = extreme_value

    values, decisions, calculations = _evaluate(fixture)
    decisions_by_metric = _decisions_by_metric(decisions)

    assert values["holding_company_nav"] == Decimal("680")
    assert values["holding_company_market_cap"] is None
    _assert_unavailable_without_provenance(
        decisions_by_metric["holding_company_market_cap"],
        "holding_decimal_arithmetic_failed",
        status="unavailable",
        blocking=False,
    )
    assert values["holding_company_nav_discount"] is None
    _assert_unavailable_without_provenance(
        decisions_by_metric["holding_company_nav_discount"],
        "holding_decimal_arithmetic_failed",
        status="unavailable",
        blocking=False,
    )
    assert all(
        calculation.formula_id
        not in {"holding-company-market-cap-v1", "holding-company-nav-discount-v1"}
        for calculation in calculations
    )


@pytest.mark.parametrize(
    "holding_evidence_key",
    ["fair_value_evidence_id", "ownership_ratio_evidence_id"],
)
def test_parent_shares_evidence_conflict_blocks_market_cap(
    holding_evidence_key: str,
) -> None:
    fixture = _load_fixture("complete")
    metric_inputs = fixture["profile_input"]["metric_inputs"]
    metric_inputs["parent_shares_outstanding"]["evidence_id"] = metric_inputs[
        "holdings"
    ][0][holding_evidence_key]

    values, decisions, calculations = _evaluate(fixture)
    decisions_by_metric = _decisions_by_metric(decisions)

    assert values["holding_company_nav"] is None
    _assert_unavailable_without_provenance(
        decisions_by_metric["holding_company_nav"],
        "holding_double_count_detected",
        status="invalid",
    )
    assert values["holding_company_market_cap"] is None
    _assert_unavailable_without_provenance(
        decisions_by_metric["holding_company_market_cap"],
        "holding_double_count_detected",
        status="invalid",
        blocking=False,
    )
    assert all(
        calculation.formula_id != "holding-company-market-cap-v1"
        for calculation in calculations
    )


def test_holding_arithmetic_ignores_caller_decimal_bounds_and_traps() -> None:
    fixture = _load_fixture("complete")

    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_UP
        context.Emax = 1
        context.Emin = -1
        context.traps[Overflow] = False
        values, decisions, calculations = _evaluate(fixture)

    decisions_by_metric = _decisions_by_metric(decisions)
    assert values["attributable_holdings_value"] == Decimal("800")
    assert values["holding_company_nav"] == Decimal("680")
    assert values["holding_company_market_cap"] == Decimal("500")
    assert values["holding_company_nav_discount"] == Decimal("180") / Decimal("680")
    assert decisions_by_metric["attributable_holdings_value"].status == "available"
    component_calculation = next(
        calculation
        for calculation in calculations
        if calculation.formula_id == "holding-attributable-component-value-v1"
    )
    assert component_calculation.result == Decimal("600")
