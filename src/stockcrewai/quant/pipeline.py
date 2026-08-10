"""Deterministic offline factor pipeline artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any

from stockcrewai.models.quant import PointInTimeSnapshot
from stockcrewai.quant.factors import compute_factor_observations
from stockcrewai.quant.normalization import normalize_cross_section
from stockcrewai.quant.ranking import compute_composite_scores


ARTIFACT_SCHEMA_VERSION = "quant-factor-artifact-v1"


def _reject_float(value: object) -> None:
    if isinstance(value, float):
        raise ValueError("quant artifact 不能包含 Python float")
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_float(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_float(nested)


def _canonical_bytes(value: object) -> bytes:
    _reject_float(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("quant artifact 必须是 JSON-safe mapping") from exc


def _version(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 不能为空")
    return value.strip()


def _validated_snapshots(
    snapshots: Sequence[PointInTimeSnapshot],
) -> tuple[PointInTimeSnapshot, ...]:
    try:
        items = tuple(snapshots)
    except TypeError as exc:
        raise ValueError("snapshots 必须是 PointInTimeSnapshot 序列") from exc
    if not items:
        raise ValueError("snapshots 不能为空")
    if not all(isinstance(item, PointInTimeSnapshot) for item in items):
        raise ValueError("snapshots 必须是 PointInTimeSnapshot 序列")
    snapshot_ids = [item.snapshot_id for item in items]
    if len(snapshot_ids) != len(set(snapshot_ids)):
        raise ValueError("snapshot_id 不能重复")
    return tuple(sorted(items, key=lambda item: item.snapshot_id))


def build_quant_factor_artifact(
    snapshots: Sequence[PointInTimeSnapshot],
    *,
    formula_version: str,
    winsor_lower: Decimal,
    winsor_upper: Decimal,
    normalization_version: str,
    composite_version: str,
) -> dict[str, Any]:
    """Build a stable JSON-safe artifact from local point-in-time snapshots."""

    ordered_snapshots = _validated_snapshots(snapshots)
    formula = _version(formula_version, "formula_version")
    normalization = _version(normalization_version, "normalization_version")
    composite = _version(composite_version, "composite_version")

    observations_raw = compute_factor_observations(ordered_snapshots, formula)
    observations_normalized = normalize_cross_section(
        observations_raw,
        winsor_lower,
        winsor_upper,
        normalization,
    )
    rankings = compute_composite_scores(observations_normalized, composite)

    raw_payload = [observation.model_dump(mode="json") for observation in observations_raw]
    normalized_payload = [
        observation.model_dump(mode="json") for observation in observations_normalized
    ]
    ranking_payload = [ranking.to_dict() for ranking in rankings]
    artifact: dict[str, Any] = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "formula_version": formula,
        "normalization_version": normalization,
        "composite_version": composite,
        "winsor_lower": str(winsor_lower),
        "winsor_upper": str(winsor_upper),
        "snapshot_ids": [snapshot.snapshot_id for snapshot in ordered_snapshots],
        "observations_raw": raw_payload,
        "observations_normalized": normalized_payload,
        "rankings": ranking_payload,
        "row_counts": {
            "snapshots": len(ordered_snapshots),
            "observations_raw": len(raw_payload),
            "observations_normalized": len(normalized_payload),
            "rankings": len(ranking_payload),
        },
        "provenance": {
            "evidence_ids": sorted(
                {
                    evidence_id
                    for snapshot in ordered_snapshots
                    for evidence_id in snapshot.available_evidence_ids
                }
            ),
            "calculation_ids": sorted(
                {
                    calculation_id
                    for snapshot in ordered_snapshots
                    for calculation_id in snapshot.available_calculation_ids
                }
            ),
        },
    }
    artifact["artifact_hash"] = hashlib.sha256(
        _canonical_bytes({key: value for key, value in artifact.items() if key != "artifact_hash"})
    ).hexdigest()
    return artifact


def write_quant_factor_artifact(
    artifact: Mapping[str, Any], output_path: str | Path
) -> Path:
    """Write one stable UTF-8 JSON artifact to the caller-provided path."""

    if not isinstance(artifact, Mapping):
        raise ValueError("artifact 必须是 mapping")
    if isinstance(output_path, str) and not output_path.strip():
        raise ValueError("output_path 不能为空")
    try:
        path = Path(output_path)
    except TypeError as exc:
        raise ValueError("output_path 必须是 str 或 Path") from exc
    if path == Path("."):
        raise ValueError("output_path 必须是文件路径")

    encoded = _canonical_bytes(dict(artifact))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded + b"\n")
    return path


__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "build_quant_factor_artifact",
    "write_quant_factor_artifact",
]
