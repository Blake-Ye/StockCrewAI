"""只读的、本次运行范围内的已验证记录索引。"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any


_CATEGORIES = ("evidence", "calculations", "filings")
_SINGULAR_CATEGORIES = {
    "evidence": "evidence",
    "calculations": "calculation",
    "filings": "filing",
}
_CATEGORY_KEYS = {
    "evidence": ("evidence", "evidences", "validated_evidence"),
    "calculations": ("calculations", "calculation", "validated_calculations"),
    "filings": ("filings", "filing_sections", "sections", "validated_filings"),
}
_ID_KEYS = {
    "evidence": ("evidence_id", "id"),
    "calculations": ("calculation_id", "id"),
    "filings": ("section_id", "filing_id", "evidence_id", "id"),
}
_ALLOWLIST_KEYS = {
    "evidence": ("evidence_ids", "validated_evidence_ids", "evidence"),
    "calculations": (
        "calculation_ids",
        "validated_calculation_ids",
        "validated_calculations",
        "calculations",
    ),
    "filings": (
        "filing_section_ids",
        "validated_filing_sections",
        "filing_ids",
        "validated_filing_ids",
        "filings",
    ),
}
_STATUS_VALUES = frozenset({"unvalidated", "valid", "invalid"})
_TIME_KEYS = (
    "as_of",
    "filed_at",
    "period",
    "period_start",
    "period_end",
    "price_timestamp",
    "date",
)
_TEXT_KEYS = ("text", "content", "body", "section_title", "title")


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _json_safe(value: Any) -> Any:
    """复制并转换常见记录值，拒绝无法安全序列化的对象。"""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, datetime):
        result = value.isoformat()
        if value.utcoffset() == timedelta(0):
            result = result.replace("+00:00", "Z")
        return result
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("record contains a non-finite Decimal")
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("record contains a non-finite float")
        return value
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump(mode="json"))
        except TypeError:
            return _json_safe(value.model_dump())
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json_safe(item) for item in sorted(value, key=repr)]
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"record value is not JSON-safe: {type(value).__name__}") from exc
    return copy.deepcopy(value)


def _normalise_values(values: Sequence[Any] | str | None, field: str) -> set[str] | None:
    if values is None:
        return None
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, Sequence) or isinstance(values, (bytes, bytearray)):
        raise ValueError(f"{field} must be a sequence of strings")
    normalised = {_text(value) for value in values}
    if None in normalised:
        raise ValueError(f"{field} must contain non-empty strings")
    if not normalised:
        return None
    return {value.casefold() for value in normalised if value is not None}


def _normalise_ids(values: Sequence[Any] | str | None, field: str) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, Sequence) or isinstance(values, (bytes, bytearray)):
        raise ValueError(f"{field} must be a sequence of strings")
    normalised = {_text(value) for value in values}
    if None in normalised:
        raise ValueError(f"{field} must contain non-empty strings")
    return sorted(value for value in normalised if value is not None)


class EvidenceStore:
    """在构造时复制记录，并只暴露当前 run allowlist 中的 valid 记录。"""

    MAX_LIMIT = 100

    def __init__(
        self,
        records: Mapping[str, Any],
        run_id: str,
        *,
        allowlist: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(records, Mapping):
            raise TypeError("records must be a Mapping")
        self._run_id = self._require_text(run_id, "run_id")
        snapshot = _json_safe(records)
        if not isinstance(snapshot, Mapping):
            raise TypeError("records must be a Mapping")

        record_payload = snapshot.get("records", snapshot)
        if not isinstance(record_payload, Mapping):
            raise TypeError("records must contain a Mapping payload")
        context_run_id = _text(snapshot.get("run_id"))
        allowlist_payload = allowlist
        if allowlist_payload is None:
            embedded = snapshot.get("allowlist", snapshot.get("allowlists"))
            if embedded is not None:
                allowlist_payload = embedded
            else:
                allowlist_payload = {
                    key: snapshot[key]
                    for category in _CATEGORIES
                    for key in _ALLOWLIST_KEYS[category]
                    if key in snapshot
                }
        if allowlist_payload is not None and not isinstance(allowlist_payload, Mapping):
            raise TypeError("allowlist must be a Mapping")
        explicit_allowlist = self._normalise_allowlist(allowlist_payload)

        self._index: dict[str, dict[str, dict[str, Any]]] = {
            category: {} for category in _CATEGORIES
        }
        self._known_ids: dict[str, set[str]] = {category: set() for category in _CATEGORIES}
        self._foreign_ids: dict[str, set[str]] = {category: set() for category in _CATEGORIES}
        self._foreign_record_values: dict[str, list[dict[str, Any]]] = {
            category: [] for category in _CATEGORIES
        }

        for category in _CATEGORIES:
            raw_value = self._category_value(record_payload, category)
            for record in self._entries(raw_value, category):
                prepared = self._prepare_record(record, category)
                record_id = self._record_id(prepared, category)
                self._known_ids[category].add(record_id)
                record_run_id = _text(prepared.get("run_id")) or context_run_id
                if record_run_id is not None and record_run_id != self._run_id:
                    self._foreign_ids[category].add(record_id)
                    self._foreign_record_values[category].append(prepared)
                    continue
                if record_id in self._index[category]:
                    raise ValueError(f"duplicate {category} record ID: {record_id}")
                self._index[category][record_id] = prepared

        self._allowlist = {
            category: frozenset(
                explicit_allowlist[category]
                if category in explicit_allowlist
                else self._index[category]
            )
            for category in _CATEGORIES
        }

    @property
    def run_id(self) -> str:
        return self._run_id

    def query_validated_evidence(
        self,
        metric_ids: Sequence[str] | str | None = None,
        periods: Sequence[str] | str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        error = self._limit_error(limit)
        if error is not None:
            return error
        try:
            metrics = _normalise_values(metric_ids, "metric_ids")
            period_values = _normalise_values(periods, "periods")
        except ValueError as exc:
            return self._error("filter_invalid", str(exc))

        records = list(self._index["evidence"].values())
        if metrics is not None:
            metric_reason = self._evidence_metric_reason(metrics)
            if metric_reason is not None:
                return self._error(metric_reason)
            records = [
                record
                for record in records
                if (metric_id := _text(record.get("metric_id"))) is not None
                and metric_id.casefold() in metrics
            ]
        if period_values is not None:
            records = [
                record for record in records if self._matches_period(record, period_values)
            ]
            if not records:
                return self._result([], "no_match")

        scoped = [
            record
            for record in records
            if self._record_id(record, "evidence") in self._allowlist["evidence"]
        ]
        if not scoped:
            return self._error("evidence_not_allowlisted")
        validated = [record for record in scoped if self._is_valid(record)]
        if not validated:
            return self._error("evidence_not_validated")
        return self._result(self._sorted_records(validated, "evidence")[:limit])

    def get_validated_calculations(
        self, calculation_ids: Sequence[str] | str | None
    ) -> dict[str, Any]:
        return self._get_validated_by_ids(
            "calculations",
            calculation_ids,
            "calculation_ids",
        )

    def search_validated_filing_sections(
        self,
        query: str,
        forms: Sequence[str] | str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        error = self._limit_error(limit)
        if error is not None:
            return error
        if not isinstance(query, str):
            return self._error("query_invalid")
        try:
            form_values = _normalise_values(forms, "forms")
        except ValueError as exc:
            return self._error("filter_invalid", str(exc))

        query_value = query.strip().casefold()
        records = [
            record
            for record in self._index["filings"].values()
            if self._record_id(record, "filings") in self._allowlist["filings"]
            and (form_values is None or bool(self._record_forms(record) & form_values))
            and (not query_value or query_value in self._record_text(record).casefold())
        ]
        if not records:
            return self._result([], "no_match")
        validated = [record for record in records if self._is_valid(record)]
        if not validated:
            return self._error("filing_not_validated")
        return self._result(self._sorted_records(validated, "filings")[:limit])

    def _get_validated_by_ids(
        self,
        category: str,
        requested_ids: Sequence[str] | str | None,
        input_name: str,
    ) -> dict[str, Any]:
        try:
            ids = _normalise_ids(requested_ids, input_name)
        except ValueError as exc:
            return self._error("ids_invalid", str(exc))
        if not ids:
            return self._result([])
        for record_id in ids:
            reason = self._access_reason(category, record_id)
            if reason is not None:
                return self._error(
                    f"{_SINGULAR_CATEGORIES[category]}_id_{reason}"
                )
            record = self._index[category][record_id]
            if not self._is_valid(record):
                return self._error(
                    f"{_SINGULAR_CATEGORIES[category]}_not_validated"
                )
        records = [self._index[category][record_id] for record_id in ids]
        return self._result(self._sorted_records(records, category))

    def _evidence_metric_reason(self, metrics: set[str]) -> str | None:
        for metric in sorted(metrics):
            current = [
                record
                for record in self._index["evidence"].values()
                if (metric_id := _text(record.get("metric_id"))) is not None
                and metric_id.casefold() == metric
            ]
            if current:
                if any(
                    self._record_id(record, "evidence")
                    in self._allowlist["evidence"]
                    for record in current
                ):
                    continue
                return "evidence_metric_not_allowlisted"
            if any(
                metric == metric_id.casefold()
                for record in self._foreign_records("evidence")
                if (metric_id := _text(record.get("metric_id"))) is not None
            ):
                return "evidence_metric_run_mismatch"
            return "evidence_metric_unknown"
        return None

    def _foreign_records(self, category: str) -> list[dict[str, Any]]:
        # 只保留为 reason code 所需的内部副本；这些记录永远不会进入结果。
        return [
            record
            for record in getattr(self, "_foreign_record_values", {}).get(category, ())
        ]

    def _access_reason(self, category: str, record_id: str) -> str | None:
        if record_id not in self._known_ids[category]:
            return "unknown"
        if record_id in self._foreign_ids[category]:
            return "run_mismatch"
        if record_id not in self._allowlist[category]:
            return "not_allowlisted"
        return None

    def _category_value(self, payload: Mapping[str, Any], category: str) -> Any:
        for key in _CATEGORY_KEYS[category]:
            if key in payload:
                return payload[key]
        return None

    @staticmethod
    def _entries(value: Any, category: str) -> list[dict[str, Any]]:
        if value is None:
            return []
        if isinstance(value, Mapping):
            if any(_text(value.get(key)) for key in _ID_KEYS[category]):
                return [dict(value)]
            entries: list[dict[str, Any]] = []
            for key, item in value.items():
                if not isinstance(item, Mapping):
                    raise TypeError(f"{category} mapping values must be records")
                record = dict(item)
                if not any(_text(record.get(id_key)) for id_key in _ID_KEYS[category]):
                    record[_ID_KEYS[category][0]] = str(key)
                entries.append(record)
            return entries
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            if not all(isinstance(item, Mapping) for item in value):
                raise TypeError(f"{category} entries must be mappings")
            return [dict(item) for item in value]
        raise TypeError(f"{category} must be a mapping or sequence of mappings")

    @staticmethod
    def _record_id(record: Mapping[str, Any], category: str) -> str:
        for key in _ID_KEYS[category]:
            value = _text(record.get(key))
            if value is not None:
                return value
        raise ValueError(f"{category} record missing stable ID")

    @classmethod
    def _prepare_record(cls, record: Mapping[str, Any], category: str) -> dict[str, Any]:
        prepared = copy.deepcopy(dict(record))
        record_id = cls._record_id(prepared, category)
        canonical_id = _ID_KEYS[category][0]
        prepared.setdefault(canonical_id, record_id)

        source = _text(prepared.get("source_reference")) or _text(prepared.get("source"))
        if source is None:
            raise ValueError(f"{category} record {record_id} missing source_reference")
        prepared.setdefault("source_reference", source)

        status = _text(prepared.get("validation_status"))
        if status is None or status.casefold() not in _STATUS_VALUES:
            raise ValueError(f"{category} record {record_id} missing validation_status")
        prepared["validation_status"] = status.casefold()

        if not any(_text(prepared.get(key)) is not None for key in _TIME_KEYS):
            raise ValueError(f"{category} record {record_id} missing as_of/filed_at/period")
        return prepared

    @staticmethod
    def _is_valid(record: Mapping[str, Any]) -> bool:
        return record.get("validation_status") == "valid"

    @staticmethod
    def _matches_period(record: Mapping[str, Any], periods: set[str]) -> bool:
        for key in ("period", "fiscal_period", "period_basis", "period_start", "period_end"):
            value = record.get(key)
            if isinstance(value, (list, tuple, set, frozenset)):
                values = value
            else:
                values = (value,)
            if any(
                (period := _text(item)) is not None and period.casefold() in periods
                for item in values
            ):
                return True
        return False

    @staticmethod
    def _record_forms(record: Mapping[str, Any]) -> set[str]:
        value = record.get("forms", record.get("form"))
        if isinstance(value, str):
            return {value.strip().casefold()} if value.strip() else set()
        if isinstance(value, (list, tuple, set, frozenset)):
            return {item.casefold() for item in (_text(item) for item in value) if item}
        return set()

    @classmethod
    def _record_text(cls, record: Mapping[str, Any]) -> str:
        values: list[str] = []

        def visit(value: Any, key: str | None = None) -> None:
            if key in _TEXT_KEYS and isinstance(value, str):
                values.append(value)
            elif isinstance(value, Mapping):
                for child_key, child_value in value.items():
                    visit(child_value, str(child_key))
            elif isinstance(value, (list, tuple)):
                for child in value:
                    visit(child, key)

        visit(record)
        return " ".join(values)

    @staticmethod
    def _sorted_records(
        records: Sequence[Mapping[str, Any]], category: str
    ) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(dict(record))
            for record in sorted(records, key=lambda item: EvidenceStore._record_id(item, category))
        ]

    @classmethod
    def _normalise_allowlist(
        cls, payload: Mapping[str, Any] | None
    ) -> dict[str, set[str]]:
        if payload is None:
            return {}
        result: dict[str, set[str]] = {}
        for category in _CATEGORIES:
            for key in _ALLOWLIST_KEYS[category]:
                if key in payload:
                    value = payload[key]
                    if isinstance(value, str):
                        value = [value]
                    if isinstance(value, Mapping):
                        value = value.keys()
                    if not isinstance(value, Sequence) and not isinstance(value, (set, frozenset)):
                        raise TypeError(f"allowlist {key} must contain IDs")
                    ids = {_text(item) for item in value}
                    if None in ids:
                        raise ValueError(f"allowlist {key} must contain non-empty IDs")
                    result[category] = {item for item in ids if item is not None}
                    break
        return result

    @classmethod
    def _limit_error(cls, limit: Any) -> dict[str, Any] | None:
        if isinstance(limit, bool) or not isinstance(limit, int):
            return cls._error("limit_invalid")
        if limit < 0:
            return cls._error("limit_negative")
        if limit > cls.MAX_LIMIT:
            return cls._error("limit_too_large")
        return None

    @staticmethod
    def _require_text(value: Any, field: str) -> str:
        result = _text(value)
        if result is None:
            raise ValueError(f"{field} must be a non-empty string")
        return result

    @staticmethod
    def _result(records: Sequence[Mapping[str, Any]], reason_code: str = "ok") -> dict[str, Any]:
        return {
            "status": "ok",
            "reason_code": reason_code,
            "records": [copy.deepcopy(dict(record)) for record in records],
        }

    @staticmethod
    def _error(reason_code: str, message: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": "error",
            "reason_code": reason_code,
            "records": [],
        }
        if message is not None:
            result["message"] = message
        return result


__all__ = ["EvidenceStore"]
