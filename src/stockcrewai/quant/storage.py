"""本地 DuckDB/Parquet 量化快照存储。

最小接口是 ``QuantSnapshotStorage.write_snapshots``、
``read_snapshots``、``read_manifest`` 和 ``get_metadata``。写入会在调用方
提供的 root 下生成 ``snapshots.parquet``、``manifest.json`` 和
``metadata.json``；读取按 ``as_of``（包含截止时点）和 ``ticker`` 过滤。
模型先用 ``model_dump(mode="json")`` 序列化，Decimal 只以 JSON 字符串
进入 Parquet，读取时再由 Pydantic 恢复为 Decimal。DuckDB 只使用内置的
本地 Parquet reader/writer，不安装或加载网络 extension，也不保留全局连接。
每个 artifact 的 schema/version、snapshot/as_of、证据和计算 ID、hash 及
序列化说明都记录在 metadata/manifest artifact 中。
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

import duckdb

from stockcrewai.models.quant import PointInTimeSnapshot, UniverseManifest


STORAGE_SCHEMA_VERSION = "quant-storage-v1"
SNAPSHOT_ARTIFACT_SCHEMA_VERSION = "quant-snapshot-parquet-v1"
MANIFEST_ARTIFACT_SCHEMA_VERSION = "quant-manifest-json-v1"

_SNAPSHOTS_FILENAME = "snapshots.parquet"
_MANIFEST_FILENAME = "manifest.json"
_METADATA_FILENAME = "metadata.json"


class StorageError(Exception):
    """所有本地量化存储错误的基类。"""

    code = "storage_error"


class StorageSchemaError(StorageError):
    code = "storage_schema_error"


class StorageArtifactMissingError(StorageError):
    code = "storage_artifact_missing"


class StorageWriteError(StorageError):
    code = "storage_write_error"


class StorageReadError(StorageError):
    code = "storage_read_error"


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _reject_float(value: object) -> None:
    if isinstance(value, float):
        raise StorageSchemaError("金融快照序列化结果不能包含 Python float")
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_float(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_float(nested)


def _model_payload(model: object, model_type: type[object]) -> tuple[dict[str, Any], bytes]:
    if not isinstance(model, model_type):
        raise StorageSchemaError(f"存储输入必须是 {model_type.__name__}")
    try:
        payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
        _reject_float(payload)
        encoded = _json_bytes(payload)
    except StorageError:
        raise
    except (TypeError, ValueError) as exc:
        raise StorageSchemaError("模型无法生成 JSON-safe artifact") from exc
    return payload, encoded


def _iso_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_cutoff(value: datetime | None) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise StorageSchemaError("as_of 过滤时间戳必须带时区")


def _hash_records(records: Sequence[bytes]) -> str:
    digest = sha256()
    for record in records:
        digest.update(len(record).to_bytes(8, "big"))
        digest.update(record)
    return digest.hexdigest()


def _sql_string(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _unique_sorted(values: Sequence[str]) -> list[str]:
    return sorted(set(values))


def _same_instant(left: datetime, right: datetime) -> bool:
    return left.astimezone(timezone.utc) == right.astimezone(timezone.utc)


class QuantSnapshotStorage:
    """在一个调用方指定的目录中保存和查询 point-in-time snapshots。"""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def write_snapshots(
        self,
        snapshots: Sequence[PointInTimeSnapshot],
        manifest: UniverseManifest,
    ) -> dict[str, Any]:
        """写入完整 snapshot 集合及其 manifest，返回稳定 metadata。"""

        ordered: list[PointInTimeSnapshot] = []
        seen_ids: set[str] = set()
        snapshot_payloads: list[tuple[PointInTimeSnapshot, dict[str, Any], bytes]] = []
        for item in snapshots:
            payload, encoded = _model_payload(item, PointInTimeSnapshot)
            if item.snapshot_id in seen_ids:
                raise StorageSchemaError(f"snapshot_id 重复: {item.snapshot_id}")
            seen_ids.add(item.snapshot_id)
            snapshot_payloads.append((item, payload, encoded))
        snapshot_payloads.sort(
            key=lambda item: (_iso_datetime(item[0].as_of), item[0].ticker, item[0].snapshot_id)
        )
        ordered = [item[0] for item in snapshot_payloads]

        manifest_payload, manifest_encoded = _model_payload(manifest, UniverseManifest)
        snapshot_hash = _hash_records([item[2] for item in snapshot_payloads])
        manifest_hash = sha256(manifest_encoded).hexdigest()
        snapshot_ids = [item.snapshot_id for item in ordered]
        as_of_values = [_iso_datetime(item.as_of) for item in ordered]
        evidence_ids = _unique_sorted(
            [evidence_id for item in ordered for evidence_id in item.available_evidence_ids]
        )
        calculation_ids = _unique_sorted(
            [calculation_id for item in ordered for calculation_id in item.available_calculation_ids]
        )
        artifact_hash = sha256(
            _json_bytes(
                {
                    "schema_version": STORAGE_SCHEMA_VERSION,
                    "snapshot_hash": snapshot_hash,
                    "manifest_hash": manifest_hash,
                    "row_count": len(ordered),
                }
            )
        ).hexdigest()

        snapshot_artifact = {
            "path": _SNAPSHOTS_FILENAME,
            "schema_version": SNAPSHOT_ARTIFACT_SCHEMA_VERSION,
            "row_count": len(ordered),
            "snapshot_ids": snapshot_ids,
            "as_of": as_of_values,
            "evidence_ids": evidence_ids,
            "calculation_ids": calculation_ids,
            "hash": snapshot_hash,
            "manifest_hash": manifest_hash,
        }
        manifest_artifact = {
            "path": _MANIFEST_FILENAME,
            "schema_version": MANIFEST_ARTIFACT_SCHEMA_VERSION,
            "row_count": 1,
            "snapshot_ids": snapshot_ids,
            "as_of": as_of_values,
            "evidence_ids": evidence_ids,
            "calculation_ids": calculation_ids,
            "hash": manifest_hash,
            "snapshot_hash": snapshot_hash,
            "manifest_hash": manifest_hash,
        }
        metadata = {
            "schema_version": STORAGE_SCHEMA_VERSION,
            "serialization": {
                "decimal": "model_dump(mode=json) Decimal strings",
                "parquet": "snapshot_json and provenance IDs are UTF-8 JSON strings",
                "read": "Pydantic model validation restores Decimal from strings",
            },
            "snapshot_hash": snapshot_hash,
            "manifest_hash": manifest_hash,
            "artifact_hash": artifact_hash,
            "artifacts": {
                "snapshots": snapshot_artifact,
                "manifest": manifest_artifact,
            },
        }
        manifest_artifact_payload = {
            "schema_version": MANIFEST_ARTIFACT_SCHEMA_VERSION,
            "manifest": manifest_payload,
            "manifest_hash": manifest_hash,
            "snapshot_hash": snapshot_hash,
            "artifact_hash": artifact_hash,
            "snapshot_ids": snapshot_ids,
            "as_of": as_of_values,
            "evidence_ids": evidence_ids,
            "calculation_ids": calculation_ids,
        }

        temp_paths: list[Path] = []
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            snapshots_tmp = self._temporary_path(".parquet")
            manifest_tmp = self._temporary_path(".json")
            metadata_tmp = self._temporary_path(".json")
            temp_paths.extend((snapshots_tmp, manifest_tmp, metadata_tmp))

            self._write_parquet(snapshots_tmp, snapshot_payloads, manifest_hash)
            self._write_json(manifest_tmp, manifest_artifact_payload)
            self._write_json(metadata_tmp, metadata)

            snapshots_tmp.replace(self.root / _SNAPSHOTS_FILENAME)
            manifest_tmp.replace(self.root / _MANIFEST_FILENAME)
            metadata_tmp.replace(self.root / _METADATA_FILENAME)
        except StorageError:
            self._cleanup(temp_paths)
            raise
        except Exception as exc:
            self._cleanup(temp_paths)
            raise StorageWriteError("写入量化快照 artifact 失败") from exc

        return metadata

    def write(self, snapshots: Sequence[PointInTimeSnapshot], manifest: UniverseManifest) -> dict[str, Any]:
        """``write_snapshots`` 的简短别名。"""

        return self.write_snapshots(snapshots, manifest)

    def read_snapshots(
        self,
        *,
        as_of: datetime | None = None,
        ticker: str | None = None,
    ) -> tuple[PointInTimeSnapshot, ...]:
        """读取 ``as_of`` 截止时间和 ticker 条件下的 snapshots。"""

        metadata = self.get_metadata()
        _validate_cutoff(as_of)
        if ticker is not None and not ticker.strip():
            raise StorageSchemaError("ticker 过滤条件不能为空")
        parquet_path = self._artifact_path(_SNAPSHOTS_FILENAME)

        clauses: list[str] = []
        parameters: list[object] = []
        if as_of is not None:
            clauses.append("as_of <= ?")
            parameters.append(as_of)
        if ticker is not None:
            clauses.append("ticker = ?")
            parameters.append(ticker)
        query = (
            "SELECT schema_version, snapshot_id, as_of, ticker, evidence_ids_json, "
            "calculation_ids_json, snapshot_hash, manifest_hash, snapshot_json "
            f"FROM read_parquet({_sql_string(parquet_path)})"
        )
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY as_of, ticker, snapshot_id"

        connection: duckdb.DuckDBPyConnection | None = None
        try:
            connection = self._connect()
            rows = connection.execute(query, parameters).fetchall()
        except StorageError:
            raise
        except Exception as exc:
            raise StorageReadError("读取量化快照 Parquet 失败") from exc
        finally:
            if connection is not None:
                connection.close()

        result: list[PointInTimeSnapshot] = []
        for row in rows:
            (
                schema_version,
                snapshot_id,
                stored_as_of,
                stored_ticker,
                evidence_ids_json,
                calculation_ids_json,
                row_hash,
                manifest_hash,
                snapshot_json,
            ) = row
            if schema_version != STORAGE_SCHEMA_VERSION:
                raise StorageSchemaError("Parquet schema/version 不匹配")
            try:
                payload = json.loads(snapshot_json)
                parsed = PointInTimeSnapshot.model_validate(payload)
                stored_evidence_ids = json.loads(evidence_ids_json)
                stored_calculation_ids = json.loads(calculation_ids_json)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise StorageSchemaError("Parquet snapshot_json 或 provenance ID 无效") from exc
            if (
                parsed.snapshot_id != snapshot_id
                or parsed.ticker != stored_ticker
                or not _same_instant(parsed.as_of, stored_as_of)
                or stored_evidence_ids != parsed.available_evidence_ids
                or stored_calculation_ids != parsed.available_calculation_ids
            ):
                raise StorageSchemaError("Parquet 行字段与 snapshot_json 不一致")
            if row_hash != sha256(_json_bytes(payload)).hexdigest():
                raise StorageSchemaError("snapshot 行 hash 不匹配")
            if manifest_hash != metadata["manifest_hash"]:
                raise StorageSchemaError("snapshot 行 manifest hash 不匹配")
            result.append(parsed)
        return tuple(result)

    def read_manifest(self) -> UniverseManifest:
        """读取并校验 UniverseManifest artifact。"""

        metadata = self.get_metadata()
        manifest_path = self._artifact_path(_MANIFEST_FILENAME)
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("manifest artifact 不是对象")
            if payload.get("schema_version") != MANIFEST_ARTIFACT_SCHEMA_VERSION:
                raise ValueError("manifest schema/version 不匹配")
            manifest = UniverseManifest.model_validate(payload["manifest"])
            _, encoded = _model_payload(manifest, UniverseManifest)
            manifest_hash = sha256(encoded).hexdigest()
        except StorageError:
            raise
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StorageSchemaError("manifest artifact 无效") from exc
        if manifest_hash != payload.get("manifest_hash") or manifest_hash != metadata["manifest_hash"]:
            raise StorageSchemaError("manifest hash 不匹配")
        return manifest

    def get_metadata(self) -> dict[str, Any]:
        """读取稳定 metadata，不从其他来源补齐缺失字段。"""

        path = self._artifact_path(_METADATA_FILENAME)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise StorageArtifactMissingError("metadata.json 不存在") from exc
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StorageSchemaError("metadata.json 不是有效 JSON") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != STORAGE_SCHEMA_VERSION:
            raise StorageSchemaError("metadata schema/version 不匹配")
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, dict):
            raise StorageSchemaError("metadata 缺少 artifacts")
        for name, expected_path in (
            ("snapshots", _SNAPSHOTS_FILENAME),
            ("manifest", _MANIFEST_FILENAME),
        ):
            artifact = artifacts.get(name)
            if not isinstance(artifact, dict) or artifact.get("path") != expected_path:
                raise StorageSchemaError(f"metadata artifact 定义无效: {name}")
        return payload

    def get_hashes(self) -> dict[str, str]:
        metadata = self.get_metadata()
        return {
            "snapshot_hash": metadata["snapshot_hash"],
            "manifest_hash": metadata["manifest_hash"],
            "artifact_hash": metadata["artifact_hash"],
        }

    def _artifact_path(self, filename: str) -> Path:
        path = self.root / filename
        if filename != _METADATA_FILENAME and not path.is_file():
            raise StorageArtifactMissingError(f"artifact 不存在: {filename}")
        if filename == _METADATA_FILENAME and not path.is_file():
            raise StorageArtifactMissingError("metadata.json 不存在")
        return path

    def _temporary_path(self, suffix: str) -> Path:
        with tempfile.NamedTemporaryFile(
            dir=self.root,
            prefix=".quant-storage-",
            suffix=suffix,
            delete=False,
        ) as handle:
            path = Path(handle.name)
        path.unlink()
        return path

    @staticmethod
    def _cleanup(paths: Sequence[Path]) -> None:
        for path in paths:
            path.unlink(missing_ok=True)

    @staticmethod
    def _connect() -> duckdb.DuckDBPyConnection:
        return duckdb.connect(
            database=":memory:",
            config={
                # 本地 Parquet 需要文件系统访问；禁止自动安装/加载网络扩展。
                "enable_external_access": True,
                "autoload_known_extensions": False,
                "autoinstall_known_extensions": False,
                "allow_community_extensions": False,
            },
        )

    @classmethod
    def _write_parquet(
        cls,
        path: Path,
        snapshot_payloads: Sequence[tuple[PointInTimeSnapshot, dict[str, Any], bytes]],
        manifest_hash: str,
    ) -> None:
        connection: duckdb.DuckDBPyConnection | None = None
        try:
            connection = cls._connect()
            connection.execute(
                """
                CREATE TABLE snapshots (
                    schema_version VARCHAR NOT NULL,
                    snapshot_id VARCHAR NOT NULL,
                    as_of TIMESTAMPTZ NOT NULL,
                    ticker VARCHAR NOT NULL,
                    evidence_ids_json VARCHAR NOT NULL,
                    calculation_ids_json VARCHAR NOT NULL,
                    snapshot_hash VARCHAR NOT NULL,
                    manifest_hash VARCHAR NOT NULL,
                    snapshot_json VARCHAR NOT NULL
                )
                """
            )
            rows = [
                (
                    STORAGE_SCHEMA_VERSION,
                    snapshot.snapshot_id,
                    snapshot.as_of,
                    snapshot.ticker,
                    _json_bytes(payload["available_evidence_ids"]).decode("utf-8"),
                    _json_bytes(payload["available_calculation_ids"]).decode("utf-8"),
                    sha256(encoded).hexdigest(),
                    manifest_hash,
                    encoded.decode("utf-8"),
                )
                for snapshot, payload, encoded in snapshot_payloads
            ]
            if rows:
                connection.executemany("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            connection.execute(
                f"COPY snapshots TO {_sql_string(path)} (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.write_text(_json_bytes(payload).decode("utf-8") + "\n", encoding="utf-8")


__all__ = [
    "MANIFEST_ARTIFACT_SCHEMA_VERSION",
    "QuantSnapshotStorage",
    "SNAPSHOT_ARTIFACT_SCHEMA_VERSION",
    "STORAGE_SCHEMA_VERSION",
    "StorageArtifactMissingError",
    "StorageError",
    "StorageReadError",
    "StorageSchemaError",
    "StorageWriteError",
]
