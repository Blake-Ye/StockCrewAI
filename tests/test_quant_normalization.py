from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest

from stockcrewai.models.quant import FactorObservation


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "quant" / "normalization" / "normalization.json"
TOLERANCE = Decimal("1e-12")


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _case(name: str) -> dict[str, Any]:
    return _fixture()["cases"][name]


def _observations(name: str) -> list[FactorObservation]:
    return [FactorObservation.model_validate(item) for item in _case(name)["observations"]]


def _normalization_api() -> Any:
    try:
        from stockcrewai.quant.normalization import normalize_cross_section
    except ModuleNotFoundError as exc:
        pytest.fail(f"normalization engine is not implemented: {exc}", pytrace=False)
    return normalize_cross_section


def _normalize(name: str) -> tuple[FactorObservation, ...]:
    fixture = _fixture()
    return _normalization_api()(
        _observations(name),
        Decimal(fixture["winsor_lower"]),
        Decimal(fixture["winsor_upper"]),
        fixture["normalization_version"],
    )


def _assert_expected(name: str, result: tuple[FactorObservation, ...]) -> None:
    case = _case(name)
    assert isinstance(result, tuple)
    assert [item.snapshot_id for item in result] == case["expected_order"]
    by_id = {item.snapshot_id: item for item in result}

    for snapshot_id, expected in case["expected"].items():
        observation = by_id[snapshot_id]
        expected_value = expected["normalized"]
        if expected_value is None:
            assert observation.normalized_value is None
        else:
            assert observation.normalized_value is not None
            assert abs(observation.normalized_value - Decimal(expected_value)) <= TOLERANCE
            assert Decimal("0") <= observation.normalized_value <= Decimal("1")
            assert observation.normalized_value.is_finite()
        assert observation.peer_count == expected["peer_count"]
        assert observation.status == expected["status"]
        assert observation.reason_code == expected["reason_code"]


def test_winsorization_keeps_decimal_raw_values_and_uses_independent_midrank_fixture() -> None:
    inputs = _observations("winsor_directions")
    raw_values = {item.snapshot_id: item.raw_value for item in inputs}

    result = _normalize("winsor_directions")
    _assert_expected("winsor_directions", result)

    for observation in result:
        assert observation.raw_value == raw_values[observation.snapshot_id]
        assert isinstance(observation.raw_value, Decimal)
        assert observation.raw_value.is_finite()


def test_low_direction_reverses_percentile_and_midrank_ties_remain_equal() -> None:
    result = _normalize("winsor_directions")
    by_id = {item.snapshot_id: item for item in result}

    assert by_id["low_a"].normalized_value == Decimal("1")
    assert by_id["low_b"].normalized_value == Decimal("0.5")
    assert by_id["low_c"].normalized_value == Decimal("0.5")
    assert by_id["low_d"].normalized_value == Decimal("0")


def test_constant_series_has_midrank_half_and_small_sample_is_typed_unavailable() -> None:
    inputs = {item.snapshot_id: item for item in _observations("constant_and_small_sample")}
    result = _normalize("constant_and_small_sample")
    by_id = {item.snapshot_id: item for item in result}

    for snapshot_id in ("constant_a", "constant_b", "constant_c"):
        assert by_id[snapshot_id].normalized_value == Decimal("0.5")
        assert by_id[snapshot_id].peer_count == 3

    small = by_id["small_a"]
    assert small.status == "unavailable"
    assert small.reason_code == "insufficient_peer_sample"
    assert small.normalized_value is None
    assert small.peer_count == 1
    assert small.raw_value == inputs["small_a"].raw_value == Decimal("0.2")


def test_factor_date_and_peer_group_partitions_never_share_peers() -> None:
    result = _normalize("partitioned_groups")
    _assert_expected("partitioned_groups", result)


def test_shuffling_input_does_not_change_canonical_output_order_or_values() -> None:
    inputs = _observations("partitioned_groups")
    normalize = _normalization_api()
    fixture = _fixture()
    kwargs = {
        "winsor_lower": Decimal(fixture["winsor_lower"]),
        "winsor_upper": Decimal(fixture["winsor_upper"]),
        "normalization_version": fixture["normalization_version"],
    }

    first = normalize(inputs, **kwargs)
    shuffled = inputs[3:1:-1] + inputs[:2] + inputs[6:] + inputs[4:6]
    second = normalize(shuffled, **kwargs)

    assert [item.model_dump(mode="json") for item in first] == [
        item.model_dump(mode="json") for item in second
    ]


def test_typed_states_are_preserved_and_available_provenance_and_formula_version_survive() -> None:
    inputs = {item.snapshot_id: item for item in _observations("typed_states_and_provenance")}
    result = _normalize("typed_states_and_provenance")
    _assert_expected("typed_states_and_provenance", result)
    by_id = {item.snapshot_id: item for item in result}

    for snapshot_id in ("typed_unavailable", "typed_not_applicable", "typed_invalid"):
        assert by_id[snapshot_id].model_dump(mode="json") == inputs[snapshot_id].model_dump(mode="json")

    for snapshot_id in ("typed_available_a", "typed_available_b"):
        original = inputs[snapshot_id]
        normalized = by_id[snapshot_id]
        assert normalized.formula_version == original.formula_version
        assert normalized.raw_value == original.raw_value
        assert normalized.evidence_ids == original.evidence_ids
        assert normalized.calculation_ids == original.calculation_ids


@pytest.mark.parametrize(
    ("winsor_lower", "winsor_upper", "normalization_version"),
    [
        (Decimal("-0.01"), Decimal("0.5"), "v1"),
        (Decimal("0"), Decimal("1.01"), "v1"),
        (Decimal("0.5"), Decimal("0.5"), "v1"),
        (Decimal("0.8"), Decimal("0.2"), "v1"),
        (Decimal("NaN"), Decimal("1"), "v1"),
        (Decimal("0"), Decimal("Infinity"), "v1"),
        (Decimal("0"), Decimal("1"), ""),
        (Decimal("0"), Decimal("1"), "  "),
    ],
)
def test_invalid_winsor_bounds_or_empty_version_raise_value_error(
    winsor_lower: Decimal, winsor_upper: Decimal, normalization_version: str
) -> None:
    normalize = _normalization_api()
    with pytest.raises(ValueError):
        normalize(
            _observations("winsor_directions"),
            winsor_lower,
            winsor_upper,
            normalization_version,
        )
