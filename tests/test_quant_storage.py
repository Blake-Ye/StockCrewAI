from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json

import duckdb
import pytest

from stockcrewai.models.profile import (
    IssuerProfile,
    ReportingProfile,
    SecurityProfile,
)
from stockcrewai.models.quant import PointInTimeSnapshot, UniverseManifest
from stockcrewai.quant.storage import (
    QuantSnapshotStorage,
    StorageArtifactMissingError,
    StorageSchemaError,
)


OLD_AS_OF = datetime(2026, 1, 31, 21, 0, tzinfo=timezone.utc)
NEW_AS_OF = datetime(2026, 2, 28, 21, 0, tzinfo=timezone.utc)


def snapshot(
    *,
    snapshot_id: str,
    as_of: datetime,
    ticker: str = "AAPL",
    roe: str = "0.123456789012345678901234567890",
) -> PointInTimeSnapshot:
    return PointInTimeSnapshot.model_validate(
        {
            "snapshot_id": snapshot_id,
            "as_of": as_of,
            "cik": "0000320193",
            "ticker": ticker,
            "issuer_profile": IssuerProfile.STANDARD_OPERATING,
            "security_profile": SecurityProfile.COMMON_STOCK,
            "reporting_profile": ReportingProfile.DOMESTIC_US_GAAP,
            "filing_cutoff": as_of,
            "price_cutoff": as_of,
            "available_evidence_ids": [f"ev_{snapshot_id}", "ev_shared"],
            "available_calculation_ids": [f"calc_{snapshot_id}"],
            "financial_features": {
                "roe": Decimal(roe),
                "missing_value": None,
            },
            "market_features": {"momentum_12m": Decimal("0.98765432109876543210")},
            "data_quality": {"source": "fixture", "complete": True},
            "builder_version": "snapshot-builder-v1",
        }
    )


def manifest() -> UniverseManifest:
    return UniverseManifest.model_validate(
        {
            "universe_id": "fixture-universe-v1",
            "tickers": ["AAPL", "MSFT"],
            "selection_as_of": OLD_AS_OF,
            "membership_source": "fixture:universe",
            "membership_basis": "fixed-test-membership",
            "known_biases": ["survivorship_bias_known"],
            "manifest_version": "universe-manifest-v1",
        }
    )


def test_storage_writes_schema_parquet_rows_and_manifest_with_exact_values(tmp_path) -> None:
    root = tmp_path / "quant-artifacts"
    store = QuantSnapshotStorage(root)
    old = snapshot(snapshot_id="snapshot_old", as_of=OLD_AS_OF)
    new = snapshot(snapshot_id="snapshot_new", as_of=NEW_AS_OF, ticker="MSFT")

    metadata = store.write_snapshots([old, new], manifest())

    assert metadata["schema_version"] == "quant-storage-v1"
    assert metadata["artifacts"]["snapshots"]["row_count"] == 2
    assert metadata["artifacts"]["snapshots"]["schema_version"] == "quant-snapshot-parquet-v1"
    assert metadata["artifacts"]["manifest"]["schema_version"] == "quant-manifest-json-v1"

    rows = store.read_snapshots()
    assert rows == (old, new)
    assert rows[0].financial_features["roe"] == Decimal("0.123456789012345678901234567890")
    assert rows[0].financial_features["missing_value"] is None
    assert store.read_manifest() == manifest()

    parquet_schema = duckdb.connect(":memory:")
    try:
        columns = parquet_schema.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)",
            [str(root / "snapshots.parquet")],
        ).fetchall()
    finally:
        parquet_schema.close()
    assert {column[0] for column in columns} == {
        "schema_version",
        "snapshot_id",
        "as_of",
        "ticker",
        "evidence_ids_json",
        "calculation_ids_json",
        "snapshot_hash",
        "manifest_hash",
        "snapshot_json",
    }


def test_storage_filters_by_as_of_cutoff_and_ticker_without_lookahead(tmp_path) -> None:
    store = QuantSnapshotStorage(tmp_path / "artifacts")
    old = snapshot(snapshot_id="snapshot_old", as_of=OLD_AS_OF)
    new = snapshot(snapshot_id="snapshot_new", as_of=NEW_AS_OF, ticker="MSFT")
    store.write_snapshots([old, new], manifest())

    assert store.read_snapshots(as_of=OLD_AS_OF) == (old,)
    assert store.read_snapshots(ticker="MSFT") == (new,)
    assert store.read_snapshots(as_of=OLD_AS_OF, ticker="MSFT") == ()


def test_manifest_and_snapshot_hashes_are_stable_for_reordered_input(tmp_path) -> None:
    old = snapshot(snapshot_id="snapshot_old", as_of=OLD_AS_OF)
    new = snapshot(snapshot_id="snapshot_new", as_of=NEW_AS_OF, ticker="MSFT")
    first = QuantSnapshotStorage(tmp_path / "first").write_snapshots([old, new], manifest())
    second = QuantSnapshotStorage(tmp_path / "second").write_snapshots([new, old], manifest())

    assert first["snapshot_hash"] == second["snapshot_hash"]
    assert first["manifest_hash"] == second["manifest_hash"]
    assert first["artifact_hash"] == second["artifact_hash"]
    assert first["artifacts"]["snapshots"]["snapshot_ids"] == [
        "snapshot_old",
        "snapshot_new",
    ]


def test_repeated_identical_write_has_the_same_hash_and_metadata(tmp_path) -> None:
    old = snapshot(snapshot_id="snapshot_old", as_of=OLD_AS_OF)
    root = tmp_path / "artifacts"
    store = QuantSnapshotStorage(root)

    first = store.write_snapshots([old], manifest())
    second = store.write_snapshots([old], manifest())

    assert first == second
    assert store.get_metadata() == second
    assert store.get_hashes() == {
        "snapshot_hash": second["snapshot_hash"],
        "manifest_hash": second["manifest_hash"],
        "artifact_hash": second["artifact_hash"],
    }


def test_storage_only_writes_under_the_callers_root(tmp_path) -> None:
    root = tmp_path / "nested" / "artifacts"
    store = QuantSnapshotStorage(root)
    store.write_snapshots([snapshot(snapshot_id="snapshot_old", as_of=OLD_AS_OF)], manifest())

    assert (root / "snapshots.parquet").is_file()
    assert (root / "manifest.json").is_file()
    assert (root / "metadata.json").is_file()
    assert not (tmp_path / "snapshots.parquet").exists()
    assert not (tmp_path / "manifest.json").exists()
    assert all(path.is_relative_to(root) for path in tmp_path.rglob("*") if path.is_file())


def test_storage_does_not_install_or_load_duckdb_network_extensions(tmp_path, monkeypatch) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("network extension loading is forbidden")

    monkeypatch.setattr(duckdb, "install_extension", fail_if_called, raising=False)
    monkeypatch.setattr(duckdb, "load_extension", fail_if_called, raising=False)

    store = QuantSnapshotStorage(tmp_path / "artifacts")
    expected = snapshot(snapshot_id="snapshot_old", as_of=OLD_AS_OF)
    store.write_snapshots([expected], manifest())

    assert store.read_snapshots() == (expected,)


def test_missing_or_invalid_artifact_raises_a_stable_typed_error(tmp_path) -> None:
    store = QuantSnapshotStorage(tmp_path / "artifacts")

    with pytest.raises(StorageArtifactMissingError) as missing:
        store.read_snapshots()
    assert missing.value.code == "storage_artifact_missing"

    store.write_snapshots(
        [snapshot(snapshot_id="snapshot_old", as_of=OLD_AS_OF)],
        manifest(),
    )
    (tmp_path / "artifacts" / "metadata.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(StorageSchemaError) as invalid:
        store.get_metadata()
    assert invalid.value.code == "storage_schema_error"


def test_metadata_artifacts_keep_provenance_and_serialization_contract(tmp_path) -> None:
    store = QuantSnapshotStorage(tmp_path / "artifacts")
    expected = snapshot(snapshot_id="snapshot_old", as_of=OLD_AS_OF)
    metadata = store.write_snapshots([expected], manifest())

    assert metadata["serialization"]["decimal"] == "model_dump(mode=json) Decimal strings"
    for artifact in metadata["artifacts"].values():
        assert artifact["schema_version"]
        assert artifact["snapshot_ids"] == ["snapshot_old"]
        assert artifact["as_of"] == [OLD_AS_OF.isoformat().replace("+00:00", "Z")]
        assert artifact["evidence_ids"] == ["ev_shared", "ev_snapshot_old"]
        assert artifact["calculation_ids"] == ["calc_snapshot_old"]
        assert artifact["hash"]

    raw_manifest = json.loads((tmp_path / "artifacts" / "manifest.json").read_text())
    assert raw_manifest["manifest"]["universe_id"] == "fixture-universe-v1"
    assert raw_manifest["manifest_hash"] == metadata["manifest_hash"]
