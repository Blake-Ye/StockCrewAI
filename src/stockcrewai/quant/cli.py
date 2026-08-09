"""Offline CLI for normalized point-in-time dataset inputs and artifacts."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any

from pydantic import ValidationError

from stockcrewai.models.evidence import CalculationRecord, EvidenceRecord
from stockcrewai.models.quant import UniverseManifest
from stockcrewai.quant.dataset import build_point_in_time_dataset
from stockcrewai.quant.storage import QuantSnapshotStorage, StorageError
from stockcrewai.services.market_data import (
    MarketDataCollectionError,
    MarketDataError,
    MarketDataValidationError,
    normalize_market_price_record,
)


class QuantCLIError(Exception):
    """Base error rendered by the CLI as a small JSON object."""

    reason_code = "quant_cli_error"

    def __init__(self, message: str, *, reason_code: str | None = None):
        super().__init__(message)
        self.message = message
        if reason_code is not None:
            self.reason_code = reason_code


class CLIArgumentError(QuantCLIError):
    reason_code = "argument_invalid"


class CLIInputError(QuantCLIError):
    reason_code = "input_invalid"


class CLIOutputError(QuantCLIError):
    reason_code = "output_write_failed"


class SECDataCollectionError(QuantCLIError):
    reason_code = "sec_collection_failed"


class BuildInputError(QuantCLIError):
    reason_code = "build_input_invalid"


class BuildError(QuantCLIError):
    reason_code = "build_failed"


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CLIArgumentError(message)


_SEC_METADATA_FIELDS = frozenset(
    {
        "metric_id",
        "form",
        "filing_form",
        "amendment",
        "is_amendment",
        "revision",
        "revision_id",
        "accession_number",
        "cik",
        "entity_cik",
        "ticker",
        "symbol",
    }
)
_SEC_COLLECTION_FIELDS = frozenset(
    {"records", "evidence", "evidence_records", "calculations", "calculation_records"}
)
_UNIVERSE_EXTRA_FIELDS = frozenset({"cik_by_ticker", "profiles_by_cik"})


def build_parser() -> argparse.ArgumentParser:
    """Build the parser without reading project defaults or external state."""

    parser = _ArgumentParser(
        prog="python -m stockcrewai.quant.cli",
        description="构建离线 point-in-time 量化数据集 artifact",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=_ArgumentParser,
        title="subcommands",
    )

    collect_sec = subparsers.add_parser(
        "collect-sec", help="验证显式 SEC 规范化 JSON 并写出本地 JSON"
    )
    collect_sec.add_argument("--input", required=True, type=Path)
    collect_sec.add_argument("--output", required=True, type=Path)

    collect_market = subparsers.add_parser(
        "collect-market", help="验证显式行情规范化 JSON 并写出本地 JSON"
    )
    collect_market.add_argument("--input", required=True, type=Path)
    collect_market.add_argument("--output", required=True, type=Path)

    build = subparsers.add_parser("build", help="只用本地规范化 JSON 写入 snapshot artifact")
    build.add_argument("--universe", required=True, type=Path)
    build.add_argument("--evidence", required=True, type=Path)
    build.add_argument("--calculations", required=True, type=Path)
    build.add_argument("--prices", required=True, type=Path)
    build.add_argument("--artifact-root", required=True, type=Path)
    build.add_argument(
        "--as-of",
        "--rebalance-date",
        dest="as_of",
        action="append",
        help="带时区的 ISO 8601 rebalance/as_of 时间，可重复指定",
    )
    build.add_argument("--builder-version", default="point-in-time-cli:v1")
    return parser


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(nested) for nested in value]
    raise ValueError("输入包含不可序列化字段")


def _read_json(path: Path, label: str) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CLIInputError(f"{label} 文件不存在", reason_code="input_missing") from exc
    except OSError as exc:
        raise CLIInputError(f"{label} 文件无法读取", reason_code="input_read_failed") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CLIInputError(f"{label} 不是有效 JSON", reason_code="input_json_invalid") from exc
    if not isinstance(payload, (Mapping, list)):
        raise CLIInputError(f"{label} 必须是 JSON 对象或数组", reason_code="input_shape_invalid")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError) as exc:
        raise CLIOutputError("规范化 JSON 写入失败") from exc


def _normalize_record(
    value: object,
    model_type: type[EvidenceRecord] | type[CalculationRecord],
) -> dict[str, Any]:
    if isinstance(value, model_type):
        return value.model_dump(mode="json")
    if not isinstance(value, Mapping):
        raise SECDataCollectionError("SEC 记录必须是 JSON 对象", reason_code="sec_record_invalid")
    payload = dict(value)
    model_fields = set(model_type.model_fields)
    metadata: dict[str, object] = {}
    for key in tuple(payload):
        if key not in model_fields:
            if key not in _SEC_METADATA_FIELDS:
                raise SECDataCollectionError(
                    "SEC 记录包含未声明字段", reason_code="sec_record_invalid"
                )
            metadata[key] = payload.pop(key)
    try:
        model = model_type.model_validate(payload)
        normalized = model.model_dump(mode="json")
        normalized.update({key: _json_safe(value) for key, value in metadata.items()})
    except (ValidationError, TypeError, ValueError) as exc:
        raise SECDataCollectionError(
            "SEC 记录不满足 EvidenceRecord/CalculationRecord 契约",
            reason_code="sec_record_invalid",
        ) from exc
    return normalized


def _classify_sec_records(items: Sequence[object]) -> tuple[list[object], list[object]]:
    evidence: list[object] = []
    calculations: list[object] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise SECDataCollectionError(
                "SEC records 必须是 JSON 对象数组", reason_code="sec_record_invalid"
            )
        has_evidence_id = "evidence_id" in item
        has_calculation_id = "calculation_id" in item
        if has_evidence_id == has_calculation_id:
            raise SECDataCollectionError(
                "SEC 记录必须明确是 EvidenceRecord 或 CalculationRecord",
                reason_code="sec_record_invalid",
            )
        (evidence if has_evidence_id else calculations).append(item)
    return evidence, calculations


def _sec_collections(payload: object) -> tuple[dict[str, object], list[object], list[object]]:
    if isinstance(payload, list):
        evidence, calculations = _classify_sec_records(payload)
        return {}, evidence, calculations
    if not isinstance(payload, Mapping):
        raise SECDataCollectionError("SEC 输入必须是 JSON 对象或数组", reason_code="sec_record_invalid")

    top = dict(payload)
    evidence: list[object] = []
    calculations: list[object] = []
    found_collection = False
    for key in ("evidence", "evidence_records"):
        if key in payload:
            found_collection = True
            items = payload[key]
            if not isinstance(items, list):
                raise SECDataCollectionError(
                    f"{key} 必须是 JSON 数组", reason_code="sec_record_invalid"
                )
            evidence.extend(items)
    for key in ("calculations", "calculation_records"):
        if key in payload:
            found_collection = True
            items = payload[key]
            if not isinstance(items, list):
                raise SECDataCollectionError(
                    f"{key} 必须是 JSON 数组", reason_code="sec_record_invalid"
                )
            calculations.extend(items)
    if "records" in payload:
        found_collection = True
        records = payload["records"]
        if not isinstance(records, list):
            raise SECDataCollectionError(
                "records 必须是 JSON 数组", reason_code="sec_record_invalid"
            )
        classified_evidence, classified_calculations = _classify_sec_records(records)
        evidence.extend(classified_evidence)
        calculations.extend(classified_calculations)
    if not found_collection:
        if "evidence_id" in payload or "calculation_id" in payload:
            classified_evidence, classified_calculations = _classify_sec_records([payload])
            return top, classified_evidence, classified_calculations
        raise SECDataCollectionError(
            "SEC 输入缺少 records/evidence/calculations", reason_code="sec_records_missing"
        )
    return top, evidence, calculations


def _normalize_sec_payload(payload: object) -> dict[str, Any]:
    top, evidence_items, calculation_items = _sec_collections(payload)
    evidence = [_normalize_record(item, EvidenceRecord) for item in evidence_items]
    calculations = [_normalize_record(item, CalculationRecord) for item in calculation_items]
    normalized: dict[str, Any] = {
        "command": "collect-sec",
        "status": "ok",
        "records": evidence + calculations,
        "evidence": evidence,
        "calculations": calculations,
    }
    for key in ("cik", "entity_cik", "ticker", "fixture_version"):
        if key in top:
            normalized[key] = _json_safe(top[key])
    return normalized


def _market_items(payload: object) -> tuple[dict[str, object], list[object]]:
    if isinstance(payload, list):
        return {}, list(payload)
    if hasattr(payload, "model_dump"):
        return {}, [payload]
    if not isinstance(payload, Mapping):
        raise MarketDataValidationError("行情输入必须是 JSON 对象或数组")
    top = dict(payload)
    for key in ("records", "prices", "market_prices"):
        if key in payload:
            items = payload[key]
            if not isinstance(items, list):
                raise MarketDataValidationError(f"{key} 必须是 JSON 数组")
            return top, list(items)
    if any(key in payload for key in ("evidence_id", "ticker", "price", "market_price")):
        return top, [payload]
    raise MarketDataValidationError("行情输入缺少 records 或价格记录字段")


def _normalize_market_payload(payload: object) -> dict[str, Any]:
    top, items = _market_items(payload)
    records = [normalize_market_price_record(item).model_dump(mode="json") for item in items]
    normalized: dict[str, Any] = {
        "command": "collect-market",
        "status": "ok",
        "records": records,
    }
    if "ticker" in top:
        normalized["ticker"] = _json_safe(top["ticker"])
    elif records:
        tickers = sorted({record["ticker"] for record in records})
        if len(tickers) == 1:
            normalized["ticker"] = tickers[0]
    return normalized


def _collector_result(
    collector: Callable[[object], object] | None,
    payload: object,
    *,
    label: str,
) -> object:
    if collector is None:
        return payload
    if not callable(collector):
        if label == "sec":
            raise SECDataCollectionError("显式 SEC collector 必须是可调用对象")
        raise MarketDataCollectionError("显式行情 collector 必须是可调用对象")
    try:
        return collector(payload)
    except (SECDataCollectionError, MarketDataError):
        raise
    except Exception as exc:
        if label == "sec":
            raise SECDataCollectionError("显式 SEC collector 失败") from exc
        raise MarketDataCollectionError("显式行情 collector 失败") from exc


def _execute_collect_sec(
    args: argparse.Namespace,
    collector: Callable[[object], object] | None,
) -> dict[str, Any]:
    payload = _read_json(args.input, "SEC input")
    collected = _collector_result(collector, payload, label="sec")
    try:
        normalized = _normalize_sec_payload(collected)
    except SECDataCollectionError as exc:
        if collector is not None and exc.reason_code != "sec_collection_failed":
            raise SECDataCollectionError("显式 SEC collector 返回无效记录") from exc
        raise
    _write_json(args.output, normalized)
    return {
        "command": "collect-sec",
        "status": "ok",
        "output": str(args.output),
        "evidence_count": len(normalized["evidence"]),
        "calculation_count": len(normalized["calculations"]),
    }


def _execute_collect_market(
    args: argparse.Namespace,
    collector: Callable[[object], object] | None,
) -> dict[str, Any]:
    payload = _read_json(args.input, "market input")
    collected = _collector_result(collector, payload, label="market")
    try:
        normalized = _normalize_market_payload(collected)
    except MarketDataError as exc:
        if collector is not None and not isinstance(exc, MarketDataCollectionError):
            raise MarketDataCollectionError("显式行情 collector 返回无效记录") from exc
        raise
    _write_json(args.output, normalized)
    return {
        "command": "collect-market",
        "status": "ok",
        "output": str(args.output),
        "record_count": len(normalized["records"]),
    }


def _manifest(payload: object) -> tuple[UniverseManifest, dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise BuildInputError("universe 必须是 JSON 对象", reason_code="universe_invalid")
    fields = set(UniverseManifest.model_fields)
    allowed = fields | _UNIVERSE_EXTRA_FIELDS
    if any(key not in allowed for key in payload):
        raise BuildInputError("universe 包含未声明字段", reason_code="universe_invalid")
    model_payload = {key: payload[key] for key in fields if key in payload}
    try:
        manifest = UniverseManifest.model_validate(model_payload)
    except (ValidationError, TypeError, ValueError) as exc:
        raise BuildInputError("universe 不满足 UniverseManifest 契约", reason_code="universe_invalid") from exc
    return manifest, dict(payload)


def _build_sec_records(
    path: Path,
    expected: str,
) -> tuple[dict[str, object], list[dict[str, Any]]]:
    try:
        top, evidence_items, calculation_items = _sec_collections(
            _read_json(path, f"{expected} input")
        )
    except SECDataCollectionError as exc:
        raise BuildInputError(
            f"{expected} records 输入无效", reason_code=f"{expected}_records_invalid"
        ) from exc
    if expected == "evidence":
        if not evidence_items:
            raise BuildInputError("evidence records 缺失", reason_code="evidence_records_missing")
        try:
            records = [_normalize_record(item, EvidenceRecord) for item in evidence_items]
        except SECDataCollectionError as exc:
            raise BuildInputError("evidence 包含非法记录", reason_code="evidence_record_invalid") from exc
    else:
        try:
            records = [_normalize_record(item, CalculationRecord) for item in calculation_items]
        except SECDataCollectionError as exc:
            raise BuildInputError(
                "calculations 包含非法记录", reason_code="calculation_record_invalid"
            ) from exc
    return top, records


def _build_market_records(path: Path) -> tuple[dict[str, object], list[dict[str, Any]]]:
    try:
        top, items = _market_items(_read_json(path, "prices input"))
    except MarketDataError as exc:
        raise BuildInputError("prices 输入无效", reason_code="price_records_invalid") from exc
    if not items:
        raise BuildInputError("prices records 缺失", reason_code="price_records_missing")
    try:
        records = [normalize_market_price_record(item).model_dump(mode="json") for item in items]
    except MarketDataError as exc:
        raise BuildInputError("prices 包含非法 MarketPriceRecord", reason_code="price_record_invalid") from exc
    return top, records


def _group_by_cik(
    top: Mapping[str, object],
    records: Sequence[Mapping[str, Any]],
    *,
    label: str,
) -> dict[str, list[Mapping[str, Any]]]:
    top_cik = top.get("cik", top.get("entity_cik"))
    if top_cik is not None and (not isinstance(top_cik, str) or not top_cik.strip()):
        raise BuildInputError(f"{label} cik 无效", reason_code="cik_invalid")
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        record_cik = record.get("cik", record.get("entity_cik"))
        if record_cik is not None and (not isinstance(record_cik, str) or not record_cik.strip()):
            raise BuildInputError(f"{label} record cik 无效", reason_code="cik_invalid")
        cik = str(top_cik or record_cik or "").strip()
        if not cik:
            raise BuildInputError(f"{label} 缺少 cik", reason_code="cik_missing")
        if record_cik and top_cik and record_cik.strip() != top_cik.strip():
            raise BuildInputError(f"{label} record cik 与文件 cik 不一致", reason_code="cik_mismatch")
        groups.setdefault(cik, []).append(record)
    return groups


def _group_by_ticker(
    top: Mapping[str, object], records: Sequence[Mapping[str, Any]]
) -> dict[str, list[Mapping[str, Any]]]:
    top_ticker = top.get("ticker")
    if top_ticker is not None and (not isinstance(top_ticker, str) or not top_ticker.strip()):
        raise BuildInputError("prices ticker 无效", reason_code="ticker_invalid")
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        record_ticker = record.get("ticker")
        if not isinstance(record_ticker, str) or not record_ticker.strip():
            raise BuildInputError("prices record 缺少 ticker", reason_code="ticker_missing")
        ticker = (str(top_ticker or record_ticker)).strip().upper()
        if top_ticker and record_ticker.strip().upper() != ticker:
            raise BuildInputError("prices record ticker 与文件 ticker 不一致", reason_code="ticker_mismatch")
        record_payload = dict(record)
        record_payload["ticker"] = ticker
        groups.setdefault(ticker, []).append(record_payload)
    return groups


def _parse_as_of(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise BuildInputError("as_of 必须是非空 ISO 8601 字符串", reason_code="as_of_invalid")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise BuildInputError("as_of 不是有效 ISO 8601 时间", reason_code="as_of_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BuildInputError("as_of 必须带时区", reason_code="as_of_required")
    return parsed


def _execute_build(args: argparse.Namespace) -> dict[str, Any]:
    if not args.as_of:
        raise BuildInputError("build 必须显式提供 as_of", reason_code="as_of_required")
    as_of = tuple(_parse_as_of(value) for value in args.as_of)
    manifest, universe_payload = _manifest(_read_json(args.universe, "universe input"))
    evidence_top, evidence = _build_sec_records(args.evidence, "evidence")
    calculations_top, calculations = _build_sec_records(args.calculations, "calculations")
    prices_top, prices = _build_market_records(args.prices)
    evidence_by_cik = _group_by_cik(evidence_top, evidence, label="evidence")
    calculation_by_cik = _group_by_cik(calculations_top, calculations, label="calculations")
    prices_by_ticker = _group_by_ticker(prices_top, prices)
    try:
        snapshots = build_point_in_time_dataset(
            universe=universe_payload,
            rebalance_dates=as_of,
            evidence_by_cik=evidence_by_cik,
            calculations_by_cik=calculation_by_cik,
            prices_by_ticker=prices_by_ticker,
            builder_version=args.builder_version,
        )
        metadata = QuantSnapshotStorage(args.artifact_root).write_snapshots(snapshots, manifest)
    except BuildInputError:
        raise
    except StorageError as exc:
        raise BuildError("本地 storage artifact 写入失败", reason_code="storage_write_failed") from exc
    except Exception as exc:
        raise BuildError("本地量化数据集构建或写入失败") from exc
    return {
        "command": "build",
        "status": "ok",
        "artifact_root": str(args.artifact_root),
        "write_path": str(args.artifact_root),
        "snapshot_count": len(snapshots),
        "metadata": metadata,
    }


def run(
    argv: Sequence[str] | None = None,
    *,
    sec_collector: Callable[[object], object] | None = None,
    market_collector: Callable[[object], object] | None = None,
) -> dict[str, Any]:
    """Parse and execute one command; typed errors are left to the caller."""

    args = build_parser().parse_args(argv)
    if args.command == "collect-sec":
        return _execute_collect_sec(args, sec_collector)
    if args.command == "collect-market":
        return _execute_collect_market(args, market_collector)
    if args.command == "build":
        return _execute_build(args)
    raise CLIArgumentError("未知子命令")


def _error_payload(exc: Exception) -> dict[str, str]:
    if isinstance(exc, (QuantCLIError, MarketDataError)):
        return {
            "error_type": type(exc).__name__,
            "reason_code": exc.reason_code,
            "message": exc.message if isinstance(exc, QuantCLIError) else str(exc),
        }
    return {
        "error_type": type(exc).__name__,
        "reason_code": "unexpected_error",
        "message": "命令执行失败",
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    sec_collector: Callable[[object], object] | None = None,
    market_collector: Callable[[object], object] | None = None,
) -> int:
    """CLI entry point returning zero on success and non-zero on typed failure."""

    try:
        result = run(
            argv,
            sec_collector=sec_collector,
            market_collector=market_collector,
        )
    except (QuantCLIError, MarketDataError) as exc:
        print(json.dumps(_error_payload(exc), ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BuildError",
    "BuildInputError",
    "CLIArgumentError",
    "CLIInputError",
    "CLIOutputError",
    "QuantCLIError",
    "SECDataCollectionError",
    "build_parser",
    "main",
    "run",
]
