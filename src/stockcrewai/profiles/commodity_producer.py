from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal, localcontext

from stockcrewai.models.evidence import (
    CalculationRecord,
    EvidenceRecord,
    MarketPriceRecord,
    ValidationStatus,
)
from stockcrewai.models.policy import PolicyDecision


PROFILE_VERSION = "commodity-profile:v1"
POLICY_VERSION = "metric-policy:commodity:v1"
COMMODITY_PRODUCER_METRIC_IDS = (
    "realized_price",
    "production",
    "realized_price_change",
    "production_change",
    "proved_reserves",
    "reserve_life_years",
    "impairment_charge",
    "impairment_to_commodity_revenue",
    "pe_ratio",
)
COMMODITY_METRIC_IDS = COMMODITY_PRODUCER_METRIC_IDS

_REQUIRED_METRICS = frozenset({"realized_price", "production"})
_FORMULAS = {
    "realized_price_change": "commodity-realized-price-change-v1",
    "production_change": "commodity-production-change-v1",
    "reserve_life_years": "commodity-reserve-life-years-v1",
    "impairment_to_commodity_revenue": "commodity-impairment-to-commodity-revenue-v1",
    "pe_ratio": "commodity-pe-ratio-v1",
}


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


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _profile_contract(
    profile_input: object,
) -> tuple[str | None, Mapping[str, object] | None, str | None]:
    if not isinstance(profile_input, Mapping):
        return None, None, "invalid_profile_input"
    primary_commodity = _text(profile_input.get("primary_commodity"))
    if primary_commodity is None:
        return None, None, "missing_primary_commodity"
    metric_inputs = profile_input.get("metric_inputs")
    if not isinstance(metric_inputs, Mapping):
        return primary_commodity, None, "missing_metric_inputs"
    return primary_commodity, metric_inputs, None


def _input_id(
    metric_inputs: Mapping[str, object],
    key: str,
    primary_commodity: str,
) -> tuple[str | None, str | None]:
    if key not in metric_inputs:
        return None, "missing_input"
    value = metric_inputs[key]
    if isinstance(value, str):
        return (_text(value), None) if _text(value) is not None else (None, "missing_input")
    if not isinstance(value, Mapping):
        return None, "missing_input"
    evidence_id = _text(value.get("evidence_id"))
    if evidence_id is None:
        return None, "missing_input"
    if "commodity" not in value:
        return None, "commodity_scope_missing"
    commodity = _text(value.get("commodity"))
    if commodity is None:
        return None, "commodity_scope_missing"
    if commodity != primary_commodity:
        return None, "commodity_mismatch"
    return evidence_id, None


def _prepare_sources(
    evidence_records: Sequence[EvidenceRecord],
    market_price_records: Sequence[MarketPriceRecord],
    profile_as_of: datetime | None,
) -> tuple[
    dict[str, EvidenceRecord],
    dict[str, str],
    frozenset[str],
    dict[str, str],
    tuple[EvidenceRecord, ...],
    tuple[MarketPriceRecord, ...],
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
        elif profile_as_of is None:
            reason = "market_price_missing"
        elif record.price_timestamp > profile_as_of:
            reason = "filed_after_as_of"
        else:
            reason = "available"
        market_status[record.evidence_id] = reason

    return evidence_by_id, evidence_status, duplicate_ids, market_status, evidence, market


def _resolve_evidence(
    metric_inputs: Mapping[str, object],
    key: str,
    primary_commodity: str,
    evidence_by_id: Mapping[str, EvidenceRecord],
    evidence_status: Mapping[str, str],
    duplicate_ids: frozenset[str],
    missing_reason: str,
) -> tuple[EvidenceRecord | None, str | None]:
    if key not in metric_inputs:
        return None, missing_reason
    evidence_id, input_reason = _input_id(metric_inputs, key, primary_commodity)
    if input_reason is not None or evidence_id is None:
        return None, input_reason or missing_reason
    if evidence_id in duplicate_ids:
        return None, "duplicate_evidence_id"
    record = evidence_by_id.get(evidence_id)
    if record is None:
        return None, "unvalidated_evidence_id"
    reason = evidence_status.get(evidence_id)
    if reason != "available" or record.value is None:
        return None, reason or "unvalidated_evidence_id"
    return record, None


def _resolve_market_price(
    market_price_records: Sequence[MarketPriceRecord],
    market_status: Mapping[str, str],
    duplicate_ids: frozenset[str],
    typed_records: Sequence[MarketPriceRecord],
) -> tuple[MarketPriceRecord | None, str]:
    records = tuple(market_price_records)
    if not records:
        return None, "market_price_missing"
    if len(records) != len(typed_records):
        return None, "unvalidated_evidence_id"
    if len(typed_records) != 1:
        if any(record.evidence_id in duplicate_ids for record in typed_records):
            return None, "duplicate_evidence_id"
        return None, "market_price_missing"
    record = typed_records[0]
    reason = market_status.get(record.evidence_id)
    if reason != "available":
        return None, reason or "unvalidated_evidence_id"
    return record, ""


def _divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        return numerator / denominator


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
        blocking=metric_id in _REQUIRED_METRICS,
    )


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


def _same_unit_currency(records: Sequence[EvidenceRecord]) -> bool:
    first = records[0]
    return all(
        record.unit == first.unit and record.currency == first.currency for record in records[1:]
    )


def _same_period(records: Sequence[EvidenceRecord]) -> bool:
    first = records[0]
    return all(
        record.period_start == first.period_start and record.period_end == first.period_end
        for record in records[1:]
    )


def _comparable_periods(current: EvidenceRecord, prior: EvidenceRecord) -> bool:
    current_span = current.period_end - current.period_start
    prior_span = prior.period_end - prior.period_start
    return abs(current_span - prior_span) <= timedelta(days=1)


def evaluate_commodity_producer_profile(
    profile_input: Mapping[str, object],
    evidence_records: Sequence[EvidenceRecord],
    market_price_records: Sequence[MarketPriceRecord] = (),
) -> tuple[
    dict[str, Decimal | None],
    tuple[PolicyDecision, ...],
    tuple[CalculationRecord, ...],
]:
    values: dict[str, Decimal | None] = {
        metric_id: None for metric_id in COMMODITY_PRODUCER_METRIC_IDS
    }
    decisions: dict[str, PolicyDecision] = {}
    calculations: list[CalculationRecord] = []
    primary_commodity, metric_inputs, contract_reason = _profile_contract(profile_input)

    def unavailable(metric_id: str, reason: str) -> None:
        decisions[metric_id] = _decision(metric_id, "unavailable", reason)

    if contract_reason is not None or metric_inputs is None or primary_commodity is None:
        reason = contract_reason or "invalid_profile_input"
        for metric_id in COMMODITY_PRODUCER_METRIC_IDS:
            unavailable(metric_id, reason)
        return values, tuple(decisions[metric_id] for metric_id in COMMODITY_PRODUCER_METRIC_IDS), ()

    (
        evidence_by_id,
        evidence_status,
        duplicate_ids,
        market_status,
        _typed_evidence,
        typed_market,
    ) = _prepare_sources(evidence_records, market_price_records, _profile_as_of(profile_input))

    def direct(metric_id: str, key: str, missing_reason: str) -> None:
        record, reason = _resolve_evidence(
            metric_inputs,
            key,
            primary_commodity,
            evidence_by_id,
            evidence_status,
            duplicate_ids,
            missing_reason,
        )
        if record is None or record.value is None:
            unavailable(metric_id, reason or "unvalidated_evidence_id")
            return
        values[metric_id] = record.value
        decisions[metric_id] = _decision(
            metric_id,
            "available",
            "validated_evidence",
            evidence_ids=[record.evidence_id],
        )

    def change(
        metric_id: str,
        current_key: str,
        prior_key: str,
        missing_reason: str,
    ) -> None:
        current, current_reason = _resolve_evidence(
            metric_inputs,
            current_key,
            primary_commodity,
            evidence_by_id,
            evidence_status,
            duplicate_ids,
            missing_reason,
        )
        prior, prior_reason = _resolve_evidence(
            metric_inputs,
            prior_key,
            primary_commodity,
            evidence_by_id,
            evidence_status,
            duplicate_ids,
            missing_reason,
        )
        if current is None or prior is None:
            unavailable(metric_id, current_reason or prior_reason or missing_reason)
            return
        records = [current, prior]
        if len({record.evidence_id for record in records}) != len(records):
            unavailable(metric_id, "duplicate_evidence_id")
        elif not _same_unit_currency(records):
            unavailable(metric_id, "unit_mismatch")
        elif not _comparable_periods(current, prior):
            unavailable(metric_id, "period_mismatch")
        elif prior.value is None or prior.value <= 0:
            unavailable(metric_id, "non-positive-prior")
        else:
            result = _divide(current.value, prior.value) - Decimal(1)
            calculation = _calculation(_FORMULAS[metric_id], records, result, "ratio")
            calculations.append(calculation)
            values[metric_id] = result
            decisions[metric_id] = _decision(
                metric_id,
                "available",
                "calculated",
                evidence_ids=[record.evidence_id for record in records],
                calculation_ids=[calculation.calculation_id],
            )

    def ratio(
        metric_id: str,
        numerator_key: str,
        denominator_key: str,
        missing_reason: str,
        unit: str = "ratio",
    ) -> None:
        numerator, numerator_reason = _resolve_evidence(
            metric_inputs,
            numerator_key,
            primary_commodity,
            evidence_by_id,
            evidence_status,
            duplicate_ids,
            missing_reason,
        )
        denominator, denominator_reason = _resolve_evidence(
            metric_inputs,
            denominator_key,
            primary_commodity,
            evidence_by_id,
            evidence_status,
            duplicate_ids,
            missing_reason,
        )
        if numerator is None or denominator is None:
            unavailable(metric_id, numerator_reason or denominator_reason or missing_reason)
            return
        records = [numerator, denominator]
        if len({record.evidence_id for record in records}) != len(records):
            unavailable(metric_id, "duplicate_evidence_id")
        elif not _same_unit_currency(records):
            unavailable(metric_id, "unit_mismatch")
        elif not _same_period(records):
            unavailable(metric_id, "period_mismatch")
        elif denominator.value is None or denominator.value <= 0:
            unavailable(metric_id, "non-positive-denominator")
        else:
            result = _divide(numerator.value, denominator.value)
            calculation = _calculation(_FORMULAS[metric_id], records, result, unit)
            calculations.append(calculation)
            values[metric_id] = result
            decisions[metric_id] = _decision(
                metric_id,
                "available",
                "calculated",
                evidence_ids=[record.evidence_id for record in records],
                calculation_ids=[calculation.calculation_id],
            )

    direct("realized_price", "realized_price", "realized_price_missing")
    direct("production", "production", "production_missing")
    change(
        "realized_price_change",
        "realized_price",
        "realized_price_prior",
        "realized_price_change_missing",
    )
    change("production_change", "production", "production_prior", "production_change_missing")
    direct("proved_reserves", "proved_reserves", "proved_reserves_missing")

    ratio(
        "reserve_life_years",
        "proved_reserves",
        "annual_production",
        "reserve_life_years_missing",
        "years",
    )
    direct("impairment_charge", "impairment_charge", "impairment_charge_missing")
    ratio(
        "impairment_to_commodity_revenue",
        "impairment_charge",
        "commodity_revenue",
        "impairment_to_commodity_revenue_missing",
    )

    market_price, market_reason = _resolve_market_price(
        market_price_records,
        market_status,
        duplicate_ids,
        typed_market,
    )
    diluted_eps, eps_reason = _resolve_evidence(
        metric_inputs,
        "diluted_eps",
        primary_commodity,
        evidence_by_id,
        evidence_status,
        duplicate_ids,
        "pe_ratio_missing",
    )
    if market_price is None or diluted_eps is None:
        unavailable("pe_ratio", market_reason or eps_reason or "pe_ratio_missing")
    elif diluted_eps.value is None:
        unavailable("pe_ratio", "unvalidated_evidence_id")
    elif diluted_eps.value <= 0:
        decisions["pe_ratio"] = _decision("pe_ratio", "not_applicable", "non-positive-eps")
    elif market_price.currency != diluted_eps.currency:
        unavailable("pe_ratio", "currency_mismatch")
    else:
        records = [market_price, diluted_eps]
        calculation = _calculation(
            _FORMULAS["pe_ratio"],
            records,
            _divide(market_price.price, diluted_eps.value),
            "multiple",
        )
        calculations.append(calculation)
        values["pe_ratio"] = calculation.result
        decisions["pe_ratio"] = _decision(
            "pe_ratio",
            "available",
            "calculated",
            evidence_ids=[record.evidence_id for record in records],
            calculation_ids=[calculation.calculation_id],
        )

    return (
        values,
        tuple(decisions[metric_id] for metric_id in COMMODITY_PRODUCER_METRIC_IDS),
        tuple(calculations),
    )


__all__ = [
    "COMMODITY_METRIC_IDS",
    "COMMODITY_PRODUCER_METRIC_IDS",
    "POLICY_VERSION",
    "PROFILE_VERSION",
    "evaluate_commodity_producer_profile",
]
