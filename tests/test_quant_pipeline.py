from __future__ import annotations

from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from stockcrewai.models.quant import PointInTimeSnapshot


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "quant" / "integration" / "snapshots.json"
FORMULA_VERSION = "factor-formulas-v1"
NORMALIZATION_VERSION = "cross-section-normalization-v1"
COMPOSITE_VERSION = "equal-weight-composite-v1"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _snapshots() -> tuple[PointInTimeSnapshot, ...]:
    return tuple(PointInTimeSnapshot.model_validate(item) for item in _fixture()["snapshots"])


def _pipeline_api() -> tuple[Any, Any]:
    try:
        from stockcrewai.quant.pipeline import (
            build_quant_factor_artifact,
            write_quant_factor_artifact,
        )
    except ModuleNotFoundError as exc:
        pytest.fail(f"quant pipeline is not implemented: {exc}", pytrace=False)
    return build_quant_factor_artifact, write_quant_factor_artifact


def _build(snapshots: tuple[PointInTimeSnapshot, ...] | list[PointInTimeSnapshot] | None = None) -> dict[str, Any]:
    build_quant_factor_artifact, _ = _pipeline_api()
    return build_quant_factor_artifact(
        _snapshots() if snapshots is None else snapshots,
        formula_version=FORMULA_VERSION,
        winsor_lower=Decimal("0.10"),
        winsor_upper=Decimal("0.90"),
        normalization_version=NORMALIZATION_VERSION,
        composite_version=COMPOSITE_VERSION,
    )


def test_integration_fixture_contains_ten_synthetic_us_stock_snapshots() -> None:
    fixture = _fixture()

    assert fixture["fixture_version"] == "quant-integration-fixture:v1"
    assert fixture["synthetic"] is True
    assert len(fixture["snapshots"]) == 10
    assert len({item["ticker"] for item in fixture["snapshots"]}) == 10


def test_pipeline_builds_complete_ten_stock_artifact() -> None:
    artifact = _build()

    assert artifact["artifact_schema_version"] == "quant-factor-artifact-v1"
    assert artifact["formula_version"] == FORMULA_VERSION
    assert artifact["normalization_version"] == NORMALIZATION_VERSION
    assert artifact["composite_version"] == COMPOSITE_VERSION
    assert artifact["winsor_lower"] == "0.10"
    assert artifact["winsor_upper"] == "0.90"
    assert artifact["snapshot_ids"] == sorted(item.snapshot_id for item in _snapshots())
    assert artifact["row_counts"] == {
        "snapshots": 10,
        "observations_raw": 170,
        "observations_normalized": 170,
        "rankings": 10,
    }
    assert len(artifact["observations_raw"]) == 170
    assert len(artifact["observations_normalized"]) == 170
    assert len(artifact["rankings"]) == 10
    assert Counter(item["snapshot_id"] for item in artifact["observations_raw"]) == Counter(
        {snapshot.snapshot_id: 17 for snapshot in _snapshots()}
    )
    assert {item["ticker"] for item in artifact["rankings"]} == {
        snapshot.ticker for snapshot in _snapshots()
    }
    assert all(
        isinstance(item["raw_value"], (str, type(None)))
        for item in artifact["observations_raw"]
    )
    assert all(
        isinstance(item["normalized_value"], (str, type(None)))
        for item in artifact["observations_normalized"]
    )


def test_shuffled_snapshot_input_has_identical_artifact_json_and_hash() -> None:
    snapshots = _snapshots()
    first = _build(snapshots)
    second = _build(list(reversed(snapshots)))

    first_json = json.dumps(first, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    second_json = json.dumps(second, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert first_json == second_json
    assert first["artifact_hash"] == second["artifact_hash"]
    assert first["artifact_hash"] == hashlib.sha256(
        json.dumps(
            {key: value for key, value in first.items() if key != "artifact_hash"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def test_repeated_build_and_write_has_identical_bytes_and_only_requested_file(tmp_path: Path) -> None:
    _, write_quant_factor_artifact = _pipeline_api()
    artifact = _build()
    output_path = tmp_path / "nested" / "quant-factor.json"

    first_path = write_quant_factor_artifact(artifact, output_path)
    first_bytes = output_path.read_bytes()
    second_path = write_quant_factor_artifact(_build(), output_path)
    second_bytes = output_path.read_bytes()

    assert first_path == second_path == output_path
    assert first_bytes == second_bytes
    assert json.loads(first_bytes) == artifact
    assert [path for path in output_path.parent.iterdir() if path.is_file()] == [output_path]


def test_provenance_is_a_stable_union_of_snapshot_allowlists_only() -> None:
    snapshots = _snapshots()
    artifact = _build(snapshots)
    expected_evidence_ids = sorted(
        {identifier for snapshot in snapshots for identifier in snapshot.available_evidence_ids}
    )
    expected_calculation_ids = sorted(
        {
            identifier
            for snapshot in snapshots
            for identifier in snapshot.available_calculation_ids
        }
    )

    assert artifact["provenance"] == {
        "evidence_ids": expected_evidence_ids,
        "calculation_ids": expected_calculation_ids,
    }
    for collection in (artifact["observations_raw"], artifact["observations_normalized"]):
        for observation in collection:
            assert set(observation["evidence_ids"]).issubset(expected_evidence_ids)
            assert set(observation["calculation_ids"]).issubset(expected_calculation_ids)


def test_not_applicable_or_unavailable_factor_does_not_block_other_rows() -> None:
    artifact = _build()
    raw_by_ticker = {
        ticker: [item for item in artifact["observations_raw"] if item["ticker"] == ticker]
        for ticker in {item["ticker"] for item in artifact["observations_raw"]}
    }
    normalized_by_ticker = {
        ticker: [
            item for item in artifact["observations_normalized"] if item["ticker"] == ticker
        ]
        for ticker in {item["ticker"] for item in artifact["observations_normalized"]}
    }
    rankings = {item["ticker"]: item for item in artifact["rankings"]}

    assert {item["status"] for item in raw_by_ticker["IPRX"]} == {"not_applicable"}
    jqtx_beta = next(item for item in raw_by_ticker["JQTX"] if item["factor_id"] == "risk.beta_12m")
    assert (jqtx_beta["status"], jqtx_beta["reason_code"]) == ("unavailable", "missing_input")
    assert all(item["status"] == "available" for item in raw_by_ticker["AURX"])
    assert next(
        item for item in normalized_by_ticker["JQTX"] if item["factor_id"] == "risk.beta_12m"
    )["normalized_value"] is None
    assert rankings["IPRX"]["status"] == "unavailable"
    assert rankings["IPRX"]["rank"] is None
    assert rankings["AURX"]["status"] == "available"
    assert rankings["AURX"]["rank"] is not None


@pytest.mark.parametrize("version_field", ["formula_version", "normalization_version", "composite_version"])
def test_empty_version_is_rejected(version_field: str) -> None:
    build_quant_factor_artifact, _ = _pipeline_api()
    versions = {
        "formula_version": FORMULA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "composite_version": COMPOSITE_VERSION,
    }
    versions[version_field] = "  "

    with pytest.raises(ValueError):
        build_quant_factor_artifact(
            _snapshots(),
            formula_version=versions["formula_version"],
            winsor_lower=Decimal("0.10"),
            winsor_upper=Decimal("0.90"),
            normalization_version=versions["normalization_version"],
            composite_version=versions["composite_version"],
        )


@pytest.mark.parametrize(
    ("lower", "upper"),
    [
        (Decimal("-0.01"), Decimal("0.90")),
        (Decimal("0.90"), Decimal("0.10")),
        (Decimal("0.10"), Decimal("0.10")),
        (Decimal("NaN"), Decimal("0.90")),
    ],
)
def test_invalid_winsor_parameters_are_rejected(lower: Decimal, upper: Decimal) -> None:
    build_quant_factor_artifact, _ = _pipeline_api()

    with pytest.raises(ValueError):
        build_quant_factor_artifact(
            _snapshots(),
            formula_version=FORMULA_VERSION,
            winsor_lower=lower,
            winsor_upper=upper,
            normalization_version=NORMALIZATION_VERSION,
            composite_version=COMPOSITE_VERSION,
        )


def test_empty_and_duplicate_snapshot_inputs_are_rejected() -> None:
    build_quant_factor_artifact, _ = _pipeline_api()

    with pytest.raises(ValueError):
        build_quant_factor_artifact(
            [],
            formula_version=FORMULA_VERSION,
            winsor_lower=Decimal("0.10"),
            winsor_upper=Decimal("0.90"),
            normalization_version=NORMALIZATION_VERSION,
            composite_version=COMPOSITE_VERSION,
        )

    duplicate = [_snapshots()[0], _snapshots()[0].model_copy(deep=True)]
    with pytest.raises(ValueError):
        build_quant_factor_artifact(
            duplicate,
            formula_version=FORMULA_VERSION,
            winsor_lower=Decimal("0.10"),
            winsor_upper=Decimal("0.90"),
            normalization_version=NORMALIZATION_VERSION,
            composite_version=COMPOSITE_VERSION,
        )
