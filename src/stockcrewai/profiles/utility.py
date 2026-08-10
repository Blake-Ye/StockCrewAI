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


PROFILE_VERSION = "utility-profile:v1"
POLICY_VERSION = "metric-policy:utility:v1"
UTILITY_METRIC_IDS = (
    "utility_operating_margin",
    "rate_base",
    "capex_intensity",
    "interest_coverage",
    "utility_roe",
    "price_to_book",
    "pe_ratio",
    "fcf_yield",
)

_REQUIRED_METRICS = frozenset({"utility_operating_margin"})
_FORMULAS = {
    "utility_operating_margin": "utility-operating-margin-v1",
    "rate_base": "utility-rate-base-direct-v1",
    "capex_intensity": "utility-capex-intensity-v1",
    "interest_coverage": "utility-interest-coverage-v1",
    "utility_roe": "utility-roe-v1",
    "price_to_book": "utility-price-to-book-v1",
    "pe_ratio": "utility-pe-ratio-v1",
    "fcf_yield": "utility-fcf-yield-v1",
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
) -> tuple[dict[str, EvidenceRecord], dict[str, str], frozenset[str], dict[str, str]]:
    records = tuple(evidence_records) + tuple(market_price_records)
    counts = Counter(
        record.evidence_id
        for record in records
        if isinstance(record, (EvidenceRecord, MarketPriceRecord))
    )
    duplicate_ids = frozenset(
        evidence_id for evidence_id, count in counts.items() if count != 1
    )
    evidence_by_id = {
        record.evidence_id: record
        for record in evidence_records
        if isinstance(record, EvidenceRecord)
        and record.evidence_id not in duplicate_ids
    }
    evidence_status: dict[str, str] = {}
    for record in evidence_records:
        if not isinstance(record, EvidenceRecord):
            continue
        if record.evidence_id in duplicate_ids:
            evidence_status[record.evidence_id] = "duplicate_evidence_id"
        elif record.validation_status is not ValidationStatus.VALID:
            evidence_status[record.evidence_id] = "unvalidated_evidence_id"
        elif record.value is None or not record.value.is_finite():
            evidence_status[record.evidence_id] = "unvalidated_evidence_id"
        elif profile_as_of is None:
            evidence_status[record.evidence_id] = "missing_input"
        elif record.filed_at > profile_as_of.date() or record.as_of > profile_as_of:
            evidence_status[record.evidence_id] = "filed_after_as_of"
        else:
            evidence_status[record.evidence_id] = "available"

    market_status: dict[str, str] = {}
    for record in market_price_records:
        if not isinstance(record, MarketPriceRecord):
            continue
        if record.evidence_id in duplicate_ids:
            market_status[record.evidence_id] = "duplicate_evidence_id"
        elif record.validation_status is not ValidationStatus.VALID:
            market_status[record.evidence_id] = "unvalidated_evidence_id"
        elif profile_as_of is None or record.price_timestamp > profile_as_of:
            market_status[record.evidence_id] = "market_price_missing"
        else:
            market_status[record.evidence_id] = "available"

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


def _resolve_market_price(
    market_price_records: Sequence[MarketPriceRecord],
    market_status: Mapping[str, str],
    duplicate_ids: frozenset[str],
) -> tuple[MarketPriceRecord | None, str]:
    records = tuple(market_price_records)
    if not records:
        return None, "market_price_missing"
    if len(records) != 1:
        if any(
            isinstance(record, MarketPriceRecord) and record.evidence_id in duplicate_ids
            for record in records
        ):
            return None, "duplicate_evidence_id"
        return None, "market_price_missing"
    record = records[0]
    if not isinstance(record, MarketPriceRecord):
        return None, "unvalidated_evidence_id"
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
    metric_id: str,
    formula_id: str,
    records: Sequence[EvidenceRecord | MarketPriceRecord],
    result: Decimal,
    unit: str,
) -> CalculationRecord:
    timestamps = [
        record.as_of if isinstance(record, EvidenceRecord) else record.price_timestamp
        for record in records
    ]
    evidence_records = [
        record for record in records if isinstance(record, EvidenceRecord)
    ]
    as_of = max(timestamps)
    period_start = min(
        (record.period_start for record in evidence_records),
        default=as_of.date(),
    )
    period_end = max(
        (record.period_end for record in evidence_records),
        default=as_of.date(),
    )
    return CalculationRecord(
        calculation_id=f"calc_{formula_id.replace('-', '_')}",
        formula_id=formula_id,
        input_evidence_ids=[record.evidence_id for record in records],
        source_reference=f"derived:{formula_id}",
        as_of=as_of,
        result=result,
        unit=unit,
        period_start=period_start,
        period_end=period_end,
        validation_status=ValidationStatus.VALID,
    )


def evaluate_utility_profile(
    profile_input: Mapping[str, object],
    evidence_records: Sequence[EvidenceRecord],
    market_price_records: Sequence[MarketPriceRecord] = (),
) -> tuple[
    dict[str, Decimal | None],
    tuple[PolicyDecision, ...],
    tuple[CalculationRecord, ...],
]:
    profile_as_of = _profile_as_of(profile_input)
    metric_inputs = _metric_inputs(profile_input)
    evidence_by_id, evidence_status, duplicate_ids, market_status = _prepare_sources(
        evidence_records,
        market_price_records,
        profile_as_of,
    )
    market_price, market_price_reason = _resolve_market_price(
        market_price_records,
        market_status,
        duplicate_ids,
    )

    values: dict[str, Decimal | None] = {metric_id: None for metric_id in UTILITY_METRIC_IDS}
    decisions: dict[str, PolicyDecision] = {}
    calculations: list[CalculationRecord] = []

    def unavailable(metric_id: str, reason: str) -> None:
        decisions[metric_id] = _decision(metric_id, "unavailable", reason)

    def derived_division(
        metric_id: str,
        input_keys: tuple[str, str],
        formula_id: str,
        missing_reason: str,
        numerator_index: int = 0,
    ) -> None:
        records: list[EvidenceRecord] = []
        for key in input_keys:
            record, reason = _resolve_evidence(
                metric_inputs,
                key,
                evidence_by_id,
                evidence_status,
                duplicate_ids,
                missing_reason,
            )
            if record is None:
                unavailable(metric_id, reason or missing_reason)
                return
            records.append(record)
        numerator = records[numerator_index].value
        denominator = records[1 - numerator_index].value
        if numerator is None or denominator is None:
            unavailable(metric_id, "unvalidated_evidence_id")
            return
        if denominator == 0:
            unavailable(metric_id, "zero_denominator")
            return
        result = _divide(numerator, denominator)
        calculation = _calculation(metric_id, formula_id, records, result, "ratio")
        calculations.append(calculation)
        values[metric_id] = result
        decisions[metric_id] = _decision(
            metric_id,
            "available",
            "calculated",
            evidence_ids=[record.evidence_id for record in records],
            calculation_ids=[calculation.calculation_id],
        )

    derived_division(
        "utility_operating_margin",
        ("operating_income", "revenue"),
        _FORMULAS["utility_operating_margin"],
        "utility_operating_margin_missing",
    )

    rate_base, rate_base_reason = _resolve_evidence(
        metric_inputs,
        "rate_base",
        evidence_by_id,
        evidence_status,
        duplicate_ids,
        "rate_base_not_disclosed",
    )
    if rate_base is None:
        unavailable("rate_base", rate_base_reason or "rate_base_not_disclosed")
    else:
        values["rate_base"] = rate_base.value
        decisions["rate_base"] = _decision(
            "rate_base",
            "available",
            "validated_evidence",
            evidence_ids=[rate_base.evidence_id],
        )

    derived_division(
        "capex_intensity",
        ("capex", "revenue"),
        _FORMULAS["capex_intensity"],
        "capex_intensity_missing",
    )
    derived_division(
        "interest_coverage",
        ("operating_income", "interest_expense"),
        _FORMULAS["interest_coverage"],
        "interest_coverage_missing",
    )
    derived_division(
        "utility_roe",
        ("net_income", "average_equity"),
        _FORMULAS["utility_roe"],
        "utility_roe_missing",
    )

    if market_price is None:
        unavailable("price_to_book", market_price_reason or "market_price_missing")
    else:
        book_value, book_value_reason = _resolve_evidence(
            metric_inputs,
            "book_value_per_share",
            evidence_by_id,
            evidence_status,
            duplicate_ids,
            "price_to_book_missing",
        )
        if book_value is None:
            unavailable("price_to_book", book_value_reason or "price_to_book_missing")
        elif book_value.value is None:
            unavailable("price_to_book", "unvalidated_evidence_id")
        elif book_value.value == 0:
            unavailable("price_to_book", "zero_denominator")
        else:
            records = [market_price, book_value]
            result = _divide(market_price.price, book_value.value)
            calculation = _calculation(
                "price_to_book",
                _FORMULAS["price_to_book"],
                records,
                result,
                "multiple",
            )
            calculations.append(calculation)
            values["price_to_book"] = result
            decisions["price_to_book"] = _decision(
                "price_to_book",
                "available",
                "calculated",
                evidence_ids=[record.evidence_id for record in records],
                calculation_ids=[calculation.calculation_id],
            )

    if market_price is None:
        unavailable("pe_ratio", market_price_reason or "market_price_missing")
    else:
        diluted_eps, diluted_eps_reason = _resolve_evidence(
            metric_inputs,
            "diluted_eps",
            evidence_by_id,
            evidence_status,
            duplicate_ids,
            "pe_ratio_missing",
        )
        if diluted_eps is None:
            unavailable("pe_ratio", diluted_eps_reason or "pe_ratio_missing")
        elif diluted_eps.value is None:
            unavailable("pe_ratio", "unvalidated_evidence_id")
        elif diluted_eps.value <= 0:
            unavailable("pe_ratio", "non-positive-eps")
        else:
            records = [market_price, diluted_eps]
            result = _divide(market_price.price, diluted_eps.value)
            calculation = _calculation(
                "pe_ratio",
                _FORMULAS["pe_ratio"],
                records,
                result,
                "multiple",
            )
            calculations.append(calculation)
            values["pe_ratio"] = result
            decisions["pe_ratio"] = _decision(
                "pe_ratio",
                "available",
                "calculated",
                evidence_ids=[record.evidence_id for record in records],
                calculation_ids=[calculation.calculation_id],
            )

    free_cash_flow, fcf_reason = _resolve_evidence(
        metric_inputs,
        "free_cash_flow",
        evidence_by_id,
        evidence_status,
        duplicate_ids,
        "fcf_yield_missing",
    )
    market_cap, market_cap_reason = _resolve_evidence(
        metric_inputs,
        "market_cap",
        evidence_by_id,
        evidence_status,
        duplicate_ids,
        "fcf_yield_missing",
    )
    if free_cash_flow is None or market_cap is None:
        unavailable("fcf_yield", fcf_reason or market_cap_reason or "fcf_yield_missing")
    elif free_cash_flow.value is None or market_cap.value is None:
        unavailable("fcf_yield", "unvalidated_evidence_id")
    elif market_cap.value == 0:
        unavailable("fcf_yield", "zero_denominator")
    else:
        records = [free_cash_flow, market_cap]
        result = _divide(free_cash_flow.value, market_cap.value)
        calculation = _calculation(
            "fcf_yield",
            _FORMULAS["fcf_yield"],
            records,
            result,
            "ratio",
        )
        calculations.append(calculation)
        values["fcf_yield"] = result
        decisions["fcf_yield"] = _decision(
            "fcf_yield",
            "available",
            "calculated",
            evidence_ids=[record.evidence_id for record in records],
            calculation_ids=[calculation.calculation_id],
        )

    return values, tuple(decisions[metric_id] for metric_id in UTILITY_METRIC_IDS), tuple(calculations)


__all__ = [
    "POLICY_VERSION",
    "PROFILE_VERSION",
    "UTILITY_METRIC_IDS",
    "evaluate_utility_profile",
]
