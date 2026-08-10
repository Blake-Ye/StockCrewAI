from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from stockcrewai.models.profile import IssuerProfile
from stockcrewai.models.quant import FactorObservation, PointInTimeSnapshot


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "quant" / "factors" / "snapshots.json"
FORMULA_VERSION = "factor-formulas-v1"
TOLERANCE = Decimal("1e-12")

EXPECTED_FACTOR_IDS = (
    "value.earnings_yield",
    "value.fcf_yield",
    "value.price_to_book",
    "value.ev_to_ebitda",
    "quality.roe",
    "quality.roic",
    "quality.operating_margin",
    "quality.fcf_margin",
    "quality.cash_conversion",
    "quality.debt_to_equity",
    "growth.revenue_cagr_3y",
    "growth.eps_growth_3y",
    "growth.fcf_growth_3y",
    "market.momentum_12_1",
    "risk.volatility_12m",
    "risk.beta_12m",
    "risk.max_drawdown_12m",
)

EXPECTED_DIRECTIONS = {
    "value.earnings_yield": "high",
    "value.fcf_yield": "high",
    "value.price_to_book": "low",
    "value.ev_to_ebitda": "low",
    "quality.roe": "high",
    "quality.roic": "high",
    "quality.operating_margin": "high",
    "quality.fcf_margin": "high",
    "quality.cash_conversion": "high",
    "quality.debt_to_equity": "low",
    "growth.revenue_cagr_3y": "high",
    "growth.eps_growth_3y": "high",
    "growth.fcf_growth_3y": "high",
    "market.momentum_12_1": "high",
    "risk.volatility_12m": "low",
    "risk.beta_12m": "low",
    "risk.max_drawdown_12m": "low",
}


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _case(name: str) -> dict[str, Any]:
    return next(item for item in _fixture()["cases"] if item["name"] == name)


def _snapshot(name: str) -> PointInTimeSnapshot:
    return PointInTimeSnapshot.model_validate(_case(name)["snapshot"])


def _factor_api() -> tuple[dict[str, str], Any]:
    try:
        from stockcrewai.quant.factors import FACTOR_DIRECTIONS, compute_factor_observations
    except ModuleNotFoundError as exc:
        pytest.fail(f"factor engine is not implemented: {exc}", pytrace=False)
    return FACTOR_DIRECTIONS, compute_factor_observations


def _observations(name: str) -> dict[str, FactorObservation]:
    _, compute_factor_observations = _factor_api()
    return {
        observation.factor_id: observation
        for observation in compute_factor_observations([_snapshot(name)], FORMULA_VERSION)
    }


def test_factor_registry_has_the_frozen_ids_and_directions() -> None:
    factor_directions, _ = _factor_api()
    assert tuple(factor_directions) == EXPECTED_FACTOR_IDS
    assert dict(factor_directions) == EXPECTED_DIRECTIONS


def test_standard_snapshot_computes_all_factors_from_independent_decimal_fixture() -> None:
    _, compute_factor_observations = _factor_api()
    observations = compute_factor_observations([_snapshot("standard_operating")], FORMULA_VERSION)
    expected_values = _case("standard_operating")["expected_values"]

    assert len(observations) == 17
    assert tuple(item.factor_id for item in observations) == EXPECTED_FACTOR_IDS
    for observation in observations:
        expected = Decimal(expected_values[observation.factor_id])
        assert observation.status == "available"
        assert observation.reason_code == "validated_inputs"
        assert observation.raw_value is not None
        assert isinstance(observation.raw_value, Decimal)
        assert observation.raw_value.is_finite()
        assert abs(observation.raw_value - expected) <= TOLERANCE
        assert observation.normalized_value is None
        assert observation.peer_count == 0
        assert observation.formula_version == FORMULA_VERSION
        assert observation.peer_group == "standard_operating:technology"
        assert observation.evidence_ids == ["ev_standard_a", "ev_standard_z"]
        assert observation.calculation_ids == ["calc_standard_a", "calc_standard_z"]


def test_negative_eps_and_fcf_remain_computable_and_same_sign_cagr_is_valid() -> None:
    observations = _observations("negative_allowed")
    expected_values = _case("negative_allowed")["expected_values"]

    for factor_id, expected_value in expected_values.items():
        observation = observations[factor_id]
        assert observation.status == "available"
        assert observation.raw_value is not None
        assert abs(observation.raw_value - Decimal(expected_value)) <= TOLERANCE


def test_boundary_inputs_are_typed_unavailable_with_stable_reasons() -> None:
    observations = _observations("boundary_unavailable")
    expected_statuses = _case("boundary_unavailable")["expected_statuses"]

    assert len(observations) == len(EXPECTED_FACTOR_IDS)
    for factor_id, (status, reason_code) in expected_statuses.items():
        observation = observations[factor_id]
        assert (observation.status, observation.reason_code) == (status, reason_code)
        if status == "available":
            assert observation.raw_value is not None
        else:
            assert observation.raw_value is None
            assert observation.evidence_ids == []
            assert observation.calculation_ids == []


def test_empty_provenance_allowlists_never_produce_available_observations() -> None:
    _, compute_factor_observations = _factor_api()
    snapshot = _snapshot("standard_operating")
    snapshot.available_evidence_ids = []
    snapshot.available_calculation_ids = []

    observations = compute_factor_observations([snapshot], FORMULA_VERSION)

    assert all(item.status == "invalid" for item in observations)
    assert all(item.reason_code == "invalid_input" for item in observations)
    assert all(item.evidence_ids == [] and item.calculation_ids == [] for item in observations)


def test_bank_and_insurance_profiles_mark_only_applicable_factors_available() -> None:
    applicable = {
        "value.price_to_book",
        "quality.roe",
        "market.momentum_12_1",
        "risk.volatility_12m",
        "risk.beta_12m",
        "risk.max_drawdown_12m",
    }

    for name in ("bank_profile", "insurance_profile"):
        observations = _observations(name)
        for factor_id, observation in observations.items():
            if factor_id in applicable:
                assert observation.status == "available"
                assert observation.reason_code == "validated_inputs"
            else:
                assert observation.status == "not_applicable"
                assert observation.reason_code == "profile_not_applicable"
                assert observation.raw_value is None
                assert observation.evidence_ids == []
                assert observation.calculation_ids == []


def test_holding_company_profile_only_applies_market_and_risk_factors() -> None:
    _, compute_factor_observations = _factor_api()
    snapshot = _snapshot("standard_operating").model_copy(
        update={"issuer_profile": IssuerProfile.HOLDING_COMPANY}
    )
    applicable = {
        "market.momentum_12_1",
        "risk.volatility_12m",
        "risk.beta_12m",
        "risk.max_drawdown_12m",
    }

    observations = compute_factor_observations([snapshot], FORMULA_VERSION)

    for observation in observations:
        if observation.factor_id in applicable:
            assert observation.status == "available"
            assert observation.reason_code == "validated_inputs"
        else:
            assert observation.status == "not_applicable"
            assert observation.reason_code == "profile_not_applicable"
            assert observation.raw_value is None
            assert observation.evidence_ids == []
            assert observation.calculation_ids == []


def test_snapshot_input_order_does_not_change_observation_json_or_hash() -> None:
    _, compute_factor_observations = _factor_api()
    snapshots = [
        _snapshot("standard_operating"),
        _snapshot("negative_allowed"),
        _snapshot("boundary_unavailable"),
        _snapshot("bank_profile"),
        _snapshot("insurance_profile"),
    ]
    first = compute_factor_observations(snapshots, FORMULA_VERSION)
    second = compute_factor_observations(list(reversed(snapshots)), FORMULA_VERSION)

    first_json = json.dumps(
        [item.model_dump(mode="json") for item in first],
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    second_json = json.dumps(
        [item.model_dump(mode="json") for item in second],
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert first_json == second_json
    assert hashlib.sha256(first_json.encode()).hexdigest() == hashlib.sha256(
        second_json.encode()
    ).hexdigest()
    assert [item.snapshot_id for item in first[::17]] == [
        "snapshot_bank",
        "snapshot_boundary",
        "snapshot_insurance",
        "snapshot_negative",
        "snapshot_standard",
    ]


def test_missing_industry_uses_unknown_peer_group() -> None:
    _, compute_factor_observations = _factor_api()
    snapshot = _snapshot("standard_operating")
    snapshot.data_quality.pop("industry")

    observations = compute_factor_observations([snapshot], FORMULA_VERSION)

    assert {item.peer_group for item in observations} == {"standard_operating:unknown"}


def test_nonfinite_feature_is_invalid_not_unavailable() -> None:
    _, compute_factor_observations = _factor_api()
    snapshot = _snapshot("standard_operating")
    snapshot.market_features["volatility_12m"] = Decimal("NaN")

    observations = {
        observation.factor_id: observation
        for observation in compute_factor_observations([snapshot], FORMULA_VERSION)
    }

    assert observations["risk.volatility_12m"].status == "invalid"
    assert observations["risk.volatility_12m"].reason_code == "invalid_input"
    assert observations["risk.volatility_12m"].raw_value is None
