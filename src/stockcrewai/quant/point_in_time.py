"""Deterministic, offline point-in-time snapshot construction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import hashlib
import json
from typing import Any

from stockcrewai.models.evidence import (
    CalculationRecord,
    EvidenceRecord,
    MarketPriceRecord,
    ValidationStatus,
)
from stockcrewai.models.profile import ProfileResult
from stockcrewai.models.quant import PointInTimeSnapshot


_RECORD_METADATA = frozenset(
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
        "profile",
    }
)
_PROFILE_IDENTITY = frozenset({"cik", "entity_cik", "ticker", "symbol"})


@dataclass(frozen=True)
class _Record:
    model: EvidenceRecord | CalculationRecord | MarketPriceRecord
    metadata: dict[str, Any]


@dataclass(frozen=True)
class _Candidate:
    key: str
    value: Decimal
    record: _Record
    sort_key: tuple[Any, ...]


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} 必须带时区")
    return value


def _record(value: Any, model_type: type[Any]) -> _Record:
    if isinstance(value, model_type):
        return _Record(value, {})
    if not isinstance(value, Mapping):
        raise TypeError(f"记录必须是 {model_type.__name__} 或 JSON-safe Mapping")
    payload = dict(value)
    model_fields = set(model_type.model_fields)
    metadata = {
        key: payload.pop(key)
        for key in tuple(payload)
        if key in _RECORD_METADATA and key not in model_fields
    }
    return _Record(model_type.model_validate(payload), metadata)


def _profile(value: Any) -> tuple[ProfileResult, dict[str, Any]]:
    if isinstance(value, ProfileResult):
        return value, {}
    if not isinstance(value, Mapping):
        return ProfileResult.model_validate(value), {}
    payload = dict(value)
    identity = {key: payload.pop(key) for key in tuple(payload) if key in _PROFILE_IDENTITY}
    nested = payload.pop("profile", None)
    if isinstance(nested, ProfileResult):
        profile = nested
    elif isinstance(nested, Mapping):
        profile = ProfileResult.model_validate(nested)
    else:
        profile = ProfileResult.model_validate(payload)
    return profile, identity


def _valid(record: _Record) -> bool:
    status = getattr(record.model, "validation_status")
    return status is ValidationStatus.VALID or status == ValidationStatus.VALID.value


def _metric_id(record: _Record) -> str:
    metric_id = record.metadata.get("metric_id")
    if isinstance(metric_id, str) and metric_id.strip():
        return metric_id.strip()
    model = record.model
    if isinstance(model, EvidenceRecord):
        return model.evidence_id
    if isinstance(model, CalculationRecord):
        return model.formula_id
    return "price"


def _evidence_id(record: _Record) -> str:
    model = record.model
    if isinstance(model, (EvidenceRecord, MarketPriceRecord)):
        return model.evidence_id
    raise TypeError("record 不是 evidence 或 market price")


def _amendment_rank(record: _Record) -> int:
    if record.metadata.get("amendment") is True or record.metadata.get("is_amendment") is True:
        return 1
    form = record.metadata.get("form", record.metadata.get("filing_form", ""))
    return int(isinstance(form, str) and (form.upper().endswith("/A") or "AMEND" in form.upper()))


def _evidence_candidate(record: _Record, cutoff: datetime) -> _Candidate | None:
    model = record.model
    assert isinstance(model, EvidenceRecord)
    if model.filed_at > cutoff.date() or not _valid(record) or model.value is None:
        return None
    return _Candidate(
        _metric_id(record),
        model.value,
        record,
        (
            model.period_end,
            model.period_start,
            model.filed_at,
            _amendment_rank(record),
            model.evidence_id,
        ),
    )


def _calculation_candidate(
    record: _Record,
    cutoff: datetime,
    available_source_ids: set[str],
) -> _Candidate | None:
    model = record.model
    assert isinstance(model, CalculationRecord)
    if (
        model.as_of > cutoff
        or not _valid(record)
        or model.result is None
        or not set(model.input_evidence_ids).issubset(available_source_ids)
    ):
        return None
    return _Candidate(
        _metric_id(record),
        model.result,
        record,
        (
            model.period_end,
            model.period_start,
            model.as_of,
            _amendment_rank(record),
            model.calculation_id,
        ),
    )


def _latest(candidates: Sequence[_Candidate]) -> dict[str, _Candidate]:
    selected: dict[str, _Candidate] = {}
    for candidate in candidates:
        current = selected.get(candidate.key)
        if current is None or candidate.sort_key > current.sort_key:
            selected[candidate.key] = candidate
    return selected


def _canonical(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date, Decimal)):
        return value.isoformat() if not isinstance(value, Decimal) else str(value)
    if hasattr(value, "model_dump"):
        return _canonical(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_canonical(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return value


def _record_for_hash(record: _Record) -> dict[str, Any]:
    return {"record": _canonical(record.model), "metadata": _canonical(record.metadata)}


def _snapshot_hash(
    *,
    as_of: datetime,
    profile: ProfileResult,
    identity: Mapping[str, Any],
    evidence: Sequence[_Record],
    calculations: Sequence[_Record],
    prices: Sequence[_Record],
    builder_version: str,
) -> str:
    payload = {
        "as_of": _canonical(as_of),
        "profile": _canonical(profile),
        "identity": _canonical(identity),
        "evidence": sorted((_record_for_hash(item) for item in evidence), key=str),
        "calculations": sorted((_record_for_hash(item) for item in calculations), key=str),
        "prices": sorted((_record_for_hash(item) for item in prices), key=str),
        "builder_version": builder_version,
    }
    encoded = json.dumps(_canonical(payload), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _identity(
    profile_identity: Mapping[str, Any],
    evidence: Sequence[_Record],
    prices: Sequence[_Record],
) -> tuple[str, str]:
    cik = profile_identity.get("cik", profile_identity.get("entity_cik"))
    ticker = profile_identity.get("ticker", profile_identity.get("symbol"))
    evidence_ciks = {
        item.metadata.get("cik", item.metadata.get("entity_cik"))
        for item in evidence
        if item.metadata.get("cik", item.metadata.get("entity_cik"))
    }
    if not cik and len(evidence_ciks) == 1:
        cik = next(iter(evidence_ciks))
    price_tickers = {
        item.model.ticker for item in prices if isinstance(item.model, MarketPriceRecord)
    }
    if not ticker and len(price_tickers) == 1:
        ticker = next(iter(price_tickers))
    if not isinstance(cik, str) or not cik.strip():
        raise ValueError("snapshot 构建需要可追溯的 cik")
    if not isinstance(ticker, str) or not ticker.strip():
        raise ValueError("snapshot 构建需要可追溯的 ticker")
    return cik.strip(), ticker.strip()


def build_point_in_time_snapshot(
    *,
    as_of: datetime,
    profile: ProfileResult | Mapping[str, Any],
    evidence: Sequence[EvidenceRecord | Mapping[str, Any]],
    calculations: Sequence[CalculationRecord | Mapping[str, Any]],
    prices: Sequence[MarketPriceRecord | Mapping[str, Any]],
    builder_version: str,
) -> PointInTimeSnapshot:
    """Build one snapshot without reading any external source."""
    cutoff = _aware(as_of, "as_of")
    if not isinstance(builder_version, str) or not builder_version.strip():
        raise ValueError("builder_version 不能为空")
    profile_model, profile_identity = _profile(profile)
    evidence_records = tuple(_record(item, EvidenceRecord) for item in evidence)
    calculation_records = tuple(_record(item, CalculationRecord) for item in calculations)
    price_records = tuple(_record(item, MarketPriceRecord) for item in prices)
    cik, ticker = _identity(profile_identity, evidence_records, price_records)

    eligible_evidence = tuple(
        item
        for item in evidence_records
        if _evidence_candidate(item, cutoff) is not None
    )
    eligible_evidence_ids = {_evidence_id(item) for item in eligible_evidence}
    eligible_prices = tuple(
        item
        for item in price_records
        if isinstance(item.model, MarketPriceRecord)
        and item.model.ticker == ticker
        and item.model.price_timestamp <= cutoff
        and _valid(item)
    )
    available_source_ids = eligible_evidence_ids | {_evidence_id(item) for item in eligible_prices}
    eligible_calculations = tuple(
        item
        for item in calculation_records
        if _calculation_candidate(item, cutoff, available_source_ids) is not None
    )

    known_keys = {_metric_id(item) for item in evidence_records + calculation_records}
    evidence_candidates = tuple(
        candidate
        for item in evidence_records
        if (candidate := _evidence_candidate(item, cutoff)) is not None
    )
    calculation_candidates = tuple(
        candidate
        for item in calculation_records
        if (candidate := _calculation_candidate(item, cutoff, available_source_ids)) is not None
    )
    selected_evidence = _latest(evidence_candidates)
    selected_calculations = _latest(calculation_candidates)

    financial_features: dict[str, Decimal | None] = {}
    selected_evidence_ids: set[str] = set()
    selected_calculation_ids: set[str] = set()
    for key in sorted(known_keys | set(selected_evidence) | set(selected_calculations)):
        calculation = selected_calculations.get(key)
        evidence_item = selected_evidence.get(key)
        if calculation is not None:
            financial_features[key] = calculation.value
            selected_calculation_ids.add(calculation.record.model.calculation_id)  # type: ignore[union-attr]
            selected_evidence_ids.update(calculation.record.model.input_evidence_ids)  # type: ignore[union-attr]
        elif evidence_item is not None:
            financial_features[key] = evidence_item.value
            selected_evidence_ids.add(evidence_item.record.model.evidence_id)  # type: ignore[union-attr]
        else:
            financial_features[key] = None

    selected_price: _Record | None = None
    if eligible_prices:
        bases = {item.model.adjustment_basis for item in eligible_prices}  # type: ignore[union-attr]
        if len(bases) != 1:
            raise ValueError("eligible prices must use one adjustment_basis")
        selected_price = max(
            eligible_prices,
            key=lambda item: (item.model.price_timestamp, item.model.evidence_id),  # type: ignore[union-attr]
        )
        market_features: dict[str, Decimal | None] = {"price": selected_price.model.price}  # type: ignore[union-attr]
        selected_evidence_ids.add(selected_price.model.evidence_id)  # type: ignore[union-attr]
        adjustment_basis: str | None = selected_price.model.adjustment_basis  # type: ignore[union-attr]
    else:
        market_features = {"price": None}
        adjustment_basis = None

    unavailable_financial = sorted(key for key, value in financial_features.items() if value is None)
    financial_values = [value for value in financial_features.values() if value is not None]
    if not financial_values:
        financial_status = "unavailable"
    elif unavailable_financial:
        financial_status = "partial"
    else:
        financial_status = "available"
    data_quality: dict[str, str | bool | Decimal | None] = {
        "financial_status": financial_status,
        "market_status": "available" if selected_price is not None else "unavailable",
        "adjustment_basis": adjustment_basis,
        "financial_unavailable_keys": ",".join(unavailable_financial) or None,
        "market_unavailable_keys": None if selected_price is not None else "price",
    }
    snapshot_identity = {"cik": cik, "ticker": ticker}
    snapshot_id = _snapshot_hash(
        as_of=cutoff,
        profile=profile_model,
        identity=snapshot_identity,
        evidence=eligible_evidence,
        calculations=eligible_calculations,
        prices=eligible_prices,
        builder_version=builder_version.strip(),
    )
    return PointInTimeSnapshot(
        snapshot_id=snapshot_id,
        as_of=cutoff,
        cik=cik,
        ticker=ticker,
        issuer_profile=profile_model.issuer_profile,
        security_profile=profile_model.security_profile,
        reporting_profile=profile_model.reporting_profile,
        filing_cutoff=cutoff,
        price_cutoff=cutoff,
        available_evidence_ids=sorted(selected_evidence_ids),
        available_calculation_ids=sorted(selected_calculation_ids),
        financial_features=financial_features,
        market_features=market_features,
        data_quality=data_quality,
        builder_version=builder_version.strip(),
    )


__all__ = ["build_point_in_time_snapshot"]
