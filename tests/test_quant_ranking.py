from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from stockcrewai.models.quant import FactorObservation


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "quant" / "ranking" / "ranking.json"
TOLERANCE = Decimal("1e-12")
OUT_OF_RANGE_FIXTURE = (("BELOW_ZERO", "-0.01"), ("ABOVE_ONE", "1.01"))


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _case(name: str) -> dict[str, Any]:
    return _fixture()["cases"][name]


def _observations(name: str) -> list[FactorObservation]:
    return [FactorObservation.model_validate(item) for item in _case(name)["observations"]]


def _ranking_api() -> Any:
    try:
        from stockcrewai.quant.ranking import compute_composite_scores
    except ModuleNotFoundError as exc:
        pytest.fail(f"ranking engine is not implemented: {exc}", pytrace=False)
    return compute_composite_scores


def test_composite_score_uses_decimal_equal_weight_and_stable_partitioned_ranks() -> None:
    compute = _ranking_api()
    fixture = _fixture()

    result = compute(_observations("mixed_partitions"), fixture["composite_version"])

    assert isinstance(result, tuple)
    assert [item.to_dict() for item in result] == _case("mixed_partitions")["expected"]

    manual_scores = {
        ("standard_operating:technology", "AAA"): Decimal("0.7"),
        ("standard_operating:technology", "AAB"): Decimal("0.6"),
        ("standard_operating:technology", "ALPHA"): Decimal("0.4"),
        ("standard_operating:technology", "BETA"): Decimal("0.4"),
        ("bank:technology", "AAA"): Decimal("0.9"),
        ("standard_operating:technology", "AAA", "2026-02-02T00:00:00+00:00"): Decimal("0.1"),
        ("standard_operating:technology", "AAB", "2026-02-02T00:00:00+00:00"): Decimal("0.2"),
    }
    for item in result:
        if item.status != "available":
            continue
        key = (item.peer_group, item.ticker)
        if item.as_of.isoformat() != "2026-01-02T00:00:00+00:00":
            key = (*key, item.as_of.isoformat())
        assert item.score is not None
        assert isinstance(item.score, Decimal)
        assert item.score.is_finite()
        assert abs(item.score - manual_scores[key]) <= TOLERANCE


def test_unavailable_and_not_applicable_factors_do_not_become_zero_or_take_a_rank() -> None:
    compute = _ranking_api()
    result = compute(_observations("mixed_partitions"), _fixture()["composite_version"])

    empty = next(item for item in result if item.ticker == "EMPTY")
    assert empty.score is None
    assert empty.available_factor_count == 0
    assert empty.factor_ids == ()
    assert empty.rank is None
    assert empty.status == "unavailable"
    assert empty.reason_code == "no_available_factors"

    standard = [
        item
        for item in result
        if item.peer_group == "standard_operating:technology"
        and item.as_of.isoformat() == "2026-01-02T00:00:00+00:00"
    ]
    assert [item.ticker for item in standard if item.rank is not None] == [
        "AAA",
        "AAB",
        "ALPHA",
        "BETA",
    ]
    assert [item.rank for item in standard if item.rank is not None] == [1, 2, 3, 4]


def test_equal_scores_use_ticker_ascii_order() -> None:
    compute = _ranking_api()
    result = compute(_observations("mixed_partitions"), _fixture()["composite_version"])

    tied = [
        item
        for item in result
        if item.peer_group == "standard_operating:technology"
        and item.as_of.isoformat() == "2026-01-02T00:00:00+00:00"
        and item.score == Decimal("0.4")
    ]
    assert [(item.ticker, item.rank) for item in tied] == [("ALPHA", 3), ("BETA", 4)]


def test_shuffled_observations_have_identical_output_json_and_hash() -> None:
    compute = _ranking_api()
    observations = _observations("mixed_partitions")
    version = _fixture()["composite_version"]

    first = compute(observations, version)
    second = compute(list(reversed(observations)), version)

    assert first == second
    assert [item.to_dict() for item in first] == [item.to_dict() for item in second]
    assert [item.to_json() for item in first] == [item.to_json() for item in second]
    assert [item.stable_hash for item in first] == [item.stable_hash for item in second]
    first_json = json.dumps(
        [item.to_dict() for item in first],
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    second_json = json.dumps(
        [item.to_dict() for item in second],
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert first_json == second_json
    assert hashlib.sha256(first_json.encode("utf-8")).hexdigest() == hashlib.sha256(
        second_json.encode("utf-8")
    ).hexdigest()


def test_composite_score_serialization_keeps_decimal_as_string_and_has_hash_entry() -> None:
    compute = _ranking_api()
    result = compute(_observations("mixed_partitions"), _fixture()["composite_version"])

    for item in result:
        payload = item.to_dict()
        assert isinstance(payload["score"], (str, type(None)))
        assert json.loads(item.to_json()) == payload
        assert item.to_json() == item.json
        assert item.stable_hash == hashlib.sha256(item.to_json().encode("utf-8")).hexdigest()
        assert item.hash == item.stable_hash
        json.dumps(payload, allow_nan=False)
        assert "NaN" not in item.to_json()
        assert "Infinity" not in item.to_json()


@pytest.mark.parametrize("composite_version", ["", "  "])
def test_empty_composite_version_raises_value_error(composite_version: str) -> None:
    compute = _ranking_api()

    with pytest.raises(ValueError, match="composite_version"):
        compute(_observations("mixed_partitions"), composite_version)


def test_only_factor_observation_and_known_factor_ids_are_accepted() -> None:
    compute = _ranking_api()
    observations = _observations("mixed_partitions")

    with pytest.raises(ValueError, match="FactorObservation"):
        compute([object()], _fixture()["composite_version"])

    unknown = observations[0].model_copy(update={"factor_id": "unknown.factor"})
    with pytest.raises(ValueError, match="unknown factor id"):
        compute([unknown], _fixture()["composite_version"])


def test_duplicate_ticker_factor_partition_is_rejected() -> None:
    compute = _ranking_api()
    observation = _observations("mixed_partitions")[0]
    duplicate = observation.model_copy(update={"snapshot_id": "duplicate_snapshot"})

    with pytest.raises(ValueError, match="duplicate"):
        compute([observation, duplicate], _fixture()["composite_version"])


def test_available_observation_requires_finite_normalized_value() -> None:
    compute = _ranking_api()
    observation = FactorObservation.model_validate(
        _case("available_without_normalized")["observations"][0]
    )

    with pytest.raises(ValueError, match="normalized_value"):
        compute([observation], _fixture()["composite_version"])

    non_finite = _observations("mixed_partitions")[0].model_copy(
        update={"normalized_value": Decimal("NaN")}
    )
    with pytest.raises(ValueError, match="normalized_value"):
        compute([non_finite], _fixture()["composite_version"])


@pytest.mark.parametrize(("ticker", "normalized_value"), OUT_OF_RANGE_FIXTURE)
def test_normalized_value_must_be_between_zero_and_one(
    ticker: str, normalized_value: str
) -> None:
    compute = _ranking_api()
    payload = dict(_case("available_without_normalized")["observations"][0])
    payload.update(
        {
            "snapshot_id": f"{ticker.lower()}_normalized",
            "ticker": ticker,
            "normalized_value": normalized_value,
        }
    )
    observation = FactorObservation.model_validate(payload)

    assert isinstance(observation.normalized_value, Decimal)
    with pytest.raises(ValueError, match="normalized_value"):
        compute([observation], _fixture()["composite_version"])


def test_factor_observation_rejects_naive_datetime() -> None:
    payload = dict(_case("mixed_partitions")["observations"][0])
    payload["as_of"] = "2026-01-02T00:00:00"

    with pytest.raises(ValidationError):
        FactorObservation.model_validate(payload)
