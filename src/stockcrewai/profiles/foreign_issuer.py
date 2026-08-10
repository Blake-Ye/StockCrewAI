"""Deterministic foreign-private-issuer and ADR metrics."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, localcontext

from stockcrewai.models.evidence import (
    CalculationRecord,
    EvidenceRecord,
    MarketPriceRecord,
    ValidationStatus,
)
from stockcrewai.models.policy import PolicyDecision


PROFILE_VERSION = "foreign-issuer-profile:v1"
POLICY_VERSION = "metric-policy:foreign-issuer:v1"
FOREIGN_ISSUER_METRIC_IDS = (
    "adr_ratio",
    "adr_equivalent_shares",
    "adr_market_cap",
)
FOREIGN_METRIC_IDS = FOREIGN_ISSUER_METRIC_IDS

_FORMULAS = {
    "adr_ratio": "foreign-adr-ratio-direct-v1",
    "adr_equivalent_shares": "foreign-adr-equivalent-shares-v1",
    "adr_market_cap": "foreign-adr-market-cap-v1",
}
_NOT_APPLICABLE_REASON = "foreign_adr_not_applicable"


def _profile_as_of(profile_input: Mapping[str, object]) -> datetime | None:
    value = profile_input.get("as_of")
    if isinstance(value, datetime):
        return value if value.tzinfo is not None and value.utcoffset() is not None else None
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo is not None and result.utcoffset() is not None else None


def _metric_inputs(profile_input: Mapping[str, object]) -> Mapping[str, object]:
    value = profile_input.get("metric_inputs")
    return value if isinstance(value, Mapping) else {}


def _input_id(metric_inputs: Mapping[str, object], key: str) -> str | None:
    value = metric_inputs.get(key)
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, Mapping):
        evidence_id = value.get("evidence_id")
        if isinstance(evidence_id, str) and evidence_id.strip():
            return evidence_id
    return None


def _prepare_sources(
    evidence_records: Sequence[EvidenceRecord],
    market_price_records: Sequence[MarketPriceRecord],
    profile_as_of: datetime | None,
) -> tuple[
    dict[str, EvidenceRecord],
    dict[str, str],
    frozenset[str],
    dict[str, str],
]:
    evidence = tuple(record for record in evidence_records if isinstance(record, EvidenceRecord))
    market = tuple(
        record for record in market_price_records if isinstance(record, MarketPriceRecord)
    )
    records = evidence + market
    counts = Counter(record.evidence_id for record in records)
    duplicate_ids = frozenset(
        evidence_id for evidence_id, count in counts.items() if count != 1
    )
    evidence_by_id = {
        record.evidence_id: record
        for record in evidence
        if record.evidence_id not in duplicate_ids
    }
    evidence_status: dict[str, str] = {}
    for record in evidence:
        if record.evidence_id in duplicate_ids:
            reason = "duplicate_evidence_id"
        elif record.validation_status is not ValidationStatus.VALID:
            reason = "unvalidated_evidence_id"
        elif record.value is None or not record.value.is_finite():
            reason = "unvalidated_evidence_id"
        elif profile_as_of is None:
            reason = "missing_input"
        elif record.filed_at > profile_as_of.date() or record.as_of > profile_as_of:
            reason = "filed_after_as_of"
        else:
            reason = "available"
        evidence_status[record.evidence_id] = reason

    market_status: dict[str, str] = {}
    for record in market:
        if record.evidence_id in duplicate_ids:
            reason = "duplicate_evidence_id"
        elif record.validation_status is not ValidationStatus.VALID:
            reason = "unvalidated_evidence_id"
        elif profile_as_of is None or record.price_timestamp > profile_as_of:
            reason = "filed_after_as_of"
        else:
            reason = "available"
        market_status[record.evidence_id] = reason
    return evidence_by_id, evidence_status, duplicate_ids, market_status


def _resolve_evidence(
    metric_inputs: Mapping[str, object],
    key: str,
    evidence_by_id: Mapping[str, EvidenceRecord],
    evidence_status: Mapping[str, str],
    duplicate_ids: frozenset[str],
    missing_reason: str,
) -> tuple[EvidenceRecord | None, str | None]:
    if key not in metric_inputs:
        return None, missing_reason
    evidence_id = _input_id(metric_inputs, key)
    if evidence_id is None:
        return None, "missing_input"
    if evidence_id in duplicate_ids:
        return None, "duplicate_evidence_id"
    record = evidence_by_id.get(evidence_id)
    if record is None:
        return None, "unvalidated_evidence_id"
    reason = evidence_status.get(evidence_id)
    if reason != "available" or record.value is None:
        return None, reason or "unvalidated_evidence_id"
    return record, None


def _domain_valid(record: EvidenceRecord, domain: str) -> bool:
    unit = record.unit.strip().casefold()
    currency = record.currency.strip().casefold()
    if domain == "ratio":
        return unit in {"ratio", "x"} and currency in {"ratio", "x", "n/a", "na"}
    if domain == "shares":
        return unit in {"share", "shares"} and currency in {
            "share",
            "shares",
            "n/a",
            "na",
        }
    return False


def _resolve_domain_evidence(
    metric_inputs: Mapping[str, object],
    key: str,
    domain: str,
    evidence_by_id: Mapping[str, EvidenceRecord],
    evidence_status: Mapping[str, str],
    duplicate_ids: frozenset[str],
    missing_reason: str,
) -> tuple[EvidenceRecord | None, str | None]:
    record, reason = _resolve_evidence(
        metric_inputs,
        key,
        evidence_by_id,
        evidence_status,
        duplicate_ids,
        missing_reason,
    )
    if record is None:
        return None, reason
    if not _domain_valid(record, domain):
        return None, f"{key}_unit_currency_mismatch"
    return record, None


def _resolve_market_price(
    market_price_records: Sequence[MarketPriceRecord],
    market_status: Mapping[str, str],
    duplicate_ids: frozenset[str],
) -> tuple[MarketPriceRecord | None, str]:
    records = tuple(market_price_records)
    if not records:
        return None, "market_price_missing"
    if len(records) != 1:
        if any(record.evidence_id in duplicate_ids for record in records):
            return None, "duplicate_evidence_id"
        return None, "market_price_missing"
    record = records[0]
    if record.evidence_id in duplicate_ids:
        return None, "duplicate_evidence_id"
    if market_status.get(record.evidence_id) != "available":
        return None, market_status.get(record.evidence_id, "unvalidated_evidence_id")
    if record.currency.strip().upper() != "USD":
        return None, "market_currency_required"
    return record, ""


def _divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        return numerator / denominator


def _calculation(
    formula_id: str,
    records: Sequence[EvidenceRecord | MarketPriceRecord],
    result: Decimal,
    unit: str,
) -> CalculationRecord:
    timestamps = [
        record.as_of if isinstance(record, EvidenceRecord) else record.price_timestamp
        for record in records
    ]
    evidence = [record for record in records if isinstance(record, EvidenceRecord)]
    as_of = max(timestamps)
    return CalculationRecord(
        calculation_id=f"calc_{formula_id.replace('-', '_')}",
        formula_id=formula_id,
        input_evidence_ids=[record.evidence_id for record in records],
        source_reference=f"derived:{formula_id}",
        as_of=as_of,
        result=result,
        unit=unit,
        period_start=min((record.period_start for record in evidence), default=as_of.date()),
        period_end=max((record.period_end for record in evidence), default=as_of.date()),
        validation_status=ValidationStatus.VALID,
    )


def _decision(
    metric_id: str,
    status: str,
    reason_code: str,
    *,
    evidence_ids: Sequence[str] = (),
    calculation_ids: Sequence[str] = (),
) -> PolicyDecision:
    return PolicyDecision(
        metric_id=metric_id,
        status=status,  # type: ignore[arg-type]
        evidence_ids=list(evidence_ids),
        calculation_ids=list(calculation_ids),
        reason_code=reason_code,
        blocking=False,
    )


def evaluate_foreign_issuer_profile(
    profile_input: Mapping[str, object],
    evidence_records: Sequence[EvidenceRecord],
    market_price_records: Sequence[MarketPriceRecord] = (),
) -> tuple[
    dict[str, Decimal | None],
    tuple[PolicyDecision, ...],
    tuple[CalculationRecord, ...],
]:
    """Evaluate only the fixed ADR metrics from typed, validated records."""
    values: dict[str, Decimal | None] = {
        metric_id: None for metric_id in FOREIGN_ISSUER_METRIC_IDS
    }
    profile_is_adr = (
        str(profile_input.get("security_profile", "")).strip().casefold() == "adr"
    )
    if not profile_is_adr:
        decisions = tuple(
            _decision(metric_id, "not_applicable", _NOT_APPLICABLE_REASON)
            for metric_id in FOREIGN_ISSUER_METRIC_IDS
        )
        return values, decisions, ()

    profile_as_of = _profile_as_of(profile_input)
    metric_inputs = _metric_inputs(profile_input)
    evidence_by_id, evidence_status, duplicate_ids, market_status = _prepare_sources(
        evidence_records,
        market_price_records,
        profile_as_of,
    )
    decisions: dict[str, PolicyDecision] = {}
    calculations: list[CalculationRecord] = []

    ratio, ratio_reason = _resolve_domain_evidence(
        metric_inputs,
        "ordinary_shares_per_adr",
        "ratio",
        evidence_by_id,
        evidence_status,
        duplicate_ids,
        "adr_ratio_missing",
    )
    if ratio is None:
        decisions["adr_ratio"] = _decision(
            "adr_ratio", "unavailable", ratio_reason or "adr_ratio_missing"
        )
    elif ratio.value is None or ratio.value <= 0:
        decisions["adr_ratio"] = _decision(
            "adr_ratio", "unavailable", "adr_ratio_invalid"
        )
        ratio = None
    else:
        calculation = _calculation(
            _FORMULAS["adr_ratio"],
            [ratio],
            ratio.value,
            "ratio",
        )
        calculations.append(calculation)
        values["adr_ratio"] = ratio.value
        decisions["adr_ratio"] = _decision(
            "adr_ratio",
            "available",
            "calculated",
            evidence_ids=[ratio.evidence_id],
            calculation_ids=[calculation.calculation_id],
        )

    ordinary, ordinary_reason = _resolve_domain_evidence(
        metric_inputs,
        "ordinary_shares_outstanding",
        "shares",
        evidence_by_id,
        evidence_status,
        duplicate_ids,
        "ordinary_shares_outstanding_missing",
    )
    if ordinary is None:
        ordinary_reason = ordinary_reason or "ordinary_shares_outstanding_missing"
    elif ordinary.value is None or ordinary.value <= 0:
        ordinary = None
        ordinary_reason = "ordinary_shares_outstanding_invalid"

    if ratio is None:
        decisions["adr_equivalent_shares"] = _decision(
            "adr_equivalent_shares", "unavailable", "adr_ratio_missing_or_invalid"
        )
    elif ordinary is None:
        decisions["adr_equivalent_shares"] = _decision(
            "adr_equivalent_shares",
            "unavailable",
            ordinary_reason or "ordinary_shares_outstanding_missing",
        )
    else:
        assert ratio.value is not None and ordinary.value is not None
        result = _divide(ordinary.value, ratio.value)
        calculation = _calculation(
            _FORMULAS["adr_equivalent_shares"],
            [ordinary, ratio],
            result,
            "shares",
        )
        calculations.append(calculation)
        values["adr_equivalent_shares"] = result
        decisions["adr_equivalent_shares"] = _decision(
            "adr_equivalent_shares",
            "available",
            "calculated",
            evidence_ids=[ordinary.evidence_id, ratio.evidence_id],
            calculation_ids=[calculation.calculation_id],
        )

    market_price, market_reason = _resolve_market_price(
        market_price_records,
        market_status,
        duplicate_ids,
    )
    if ratio is None:
        decisions["adr_market_cap"] = _decision(
            "adr_market_cap", "unavailable", "adr_ratio_missing_or_invalid"
        )
    elif ordinary is None:
        decisions["adr_market_cap"] = _decision(
            "adr_market_cap",
            "unavailable",
            ordinary_reason or "ordinary_shares_outstanding_missing",
        )
    elif market_price is None:
        decisions["adr_market_cap"] = _decision(
            "adr_market_cap", "unavailable", market_reason or "market_price_missing"
        )
    else:
        assert ratio.value is not None and ordinary.value is not None
        equivalent_shares = _divide(ordinary.value, ratio.value)
        result = market_price.price * equivalent_shares
        calculation = _calculation(
            _FORMULAS["adr_market_cap"],
            [market_price, ordinary, ratio],
            result,
            "USD",
        )
        calculations.append(calculation)
        values["adr_market_cap"] = result
        decisions["adr_market_cap"] = _decision(
            "adr_market_cap",
            "available",
            "calculated",
            evidence_ids=[market_price.evidence_id, ordinary.evidence_id, ratio.evidence_id],
            calculation_ids=[calculation.calculation_id],
        )

    return values, tuple(decisions[metric_id] for metric_id in FOREIGN_ISSUER_METRIC_IDS), tuple(calculations)


__all__ = [
    "FOREIGN_ISSUER_METRIC_IDS",
    "FOREIGN_METRIC_IDS",
    "POLICY_VERSION",
    "PROFILE_VERSION",
    "evaluate_foreign_issuer_profile",
]
