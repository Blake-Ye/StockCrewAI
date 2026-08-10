from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Any, Mapping

from stockcrewai.models.evidence import (
    CalculationRecord,
    EvidenceRecord,
    MarketPriceRecord,
    ValidationStatus,
)
from stockcrewai.models.policy import PolicyDecision


PROFILE_VERSION = "bank-profile:v1"
POLICY_VERSION = "metric-policy:bank:v1"
BANK_METRIC_IDS = (
    "bank_roa",
    "bank_roe",
    "net_interest_margin",
    "efficiency_ratio",
    "cet1_ratio",
    "loan_to_deposit",
    "nonperforming_loan_ratio",
    "provision_coverage",
    "price_to_book",
    "pe_ratio",
    "fcf_yield",
)

_CORE_METRICS = frozenset(
    {"bank_roa", "bank_roe", "net_interest_margin", "efficiency_ratio"}
)
_INPUTS = {
    "bank_roa": ("net_income", "average_assets"),
    "bank_roe": ("net_income", "average_equity"),
    "net_interest_margin": ("net_interest_income", "average_earning_assets"),
    "efficiency_ratio": (
        "noninterest_expense",
        "net_interest_income",
        "noninterest_income",
    ),
    "loan_to_deposit": ("total_loans", "total_deposits"),
    "nonperforming_loan_ratio": ("nonperforming_loans", "total_loans"),
    "provision_coverage": ("allowance_for_credit_losses", "nonperforming_loans"),
    "price_to_book": ("book_value_per_share",),
    "pe_ratio": ("diluted_eps",),
}
_FORMULAS = {
    "bank_roa": "bank-roa-v1",
    "bank_roe": "bank-roe-v1",
    "net_interest_margin": "bank-net-interest-margin-v1",
    "efficiency_ratio": "bank-efficiency-ratio-v1",
    "loan_to_deposit": "bank-loan-to-deposit-v1",
    "nonperforming_loan_ratio": "bank-nonperforming-loan-ratio-v1",
    "provision_coverage": "bank-provision-coverage-v1",
    "price_to_book": "bank-price-to-book-v1",
    "pe_ratio": "bank-pe-ratio-v1",
}


def _profile_as_of(profile_input: Mapping[str, Any]) -> datetime:
    value = profile_input["as_of"]
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _usable_evidence(
    name: str,
    metric_inputs: Mapping[str, Any],
    records: Mapping[str, EvidenceRecord],
    counts: Counter[str],
    as_of: datetime,
) -> tuple[EvidenceRecord | None, str]:
    evidence_id = metric_inputs.get(name)
    if not isinstance(evidence_id, str) or not evidence_id:
        return None, "missing_required_evidence"
    if counts[evidence_id] != 1:
        return None, "invalid_evidence"
    record = records[evidence_id]
    if record.validation_status is not ValidationStatus.VALID or record.value is None:
        return None, "invalid_evidence"
    if record.filed_at > as_of.date():
        return None, "filed_after_as_of"
    if record.as_of > as_of:
        return None, "future_evidence"
    return record, ""


def _usable_market_price(
    records: tuple[MarketPriceRecord, ...], as_of: datetime
) -> tuple[MarketPriceRecord | None, str]:
    counts = Counter(record.evidence_id for record in records)
    eligible = [
        record
        for record in records
        if counts[record.evidence_id] == 1
        and record.validation_status is ValidationStatus.VALID
        and record.price_timestamp <= as_of
    ]
    if eligible:
        return max(eligible, key=lambda record: (record.price_timestamp, record.evidence_id)), ""
    if any(
        counts[record.evidence_id] != 1
        or record.validation_status is not ValidationStatus.VALID
        for record in records
    ):
        return None, "invalid_evidence"
    if any(record.price_timestamp > as_of for record in records):
        return None, "future_evidence"
    return None, "missing_market_price"


def _calculate(metric_id: str, values: list[Decimal]) -> Decimal | None:
    with localcontext() as context:
        context.prec = 28
        if metric_id == "bank_roa":
            numerator, denominator = values
        elif metric_id == "bank_roe":
            numerator, denominator = values
        elif metric_id == "net_interest_margin":
            numerator, denominator = values
        elif metric_id == "efficiency_ratio":
            numerator = values[0]
            denominator = values[1] + values[2]
        elif metric_id == "loan_to_deposit":
            numerator, denominator = values
        elif metric_id == "nonperforming_loan_ratio":
            numerator, denominator = values
        elif metric_id == "provision_coverage":
            numerator, denominator = values
        elif metric_id == "price_to_book":
            numerator, denominator = values[0], values[1]
        else:
            numerator, denominator = values[0], values[1]
        if denominator == 0:
            return None
        return numerator / denominator


def _calculation(
    metric_id: str,
    formula_id: str,
    records: list[EvidenceRecord | MarketPriceRecord],
    result: Decimal,
    as_of: datetime,
) -> CalculationRecord:
    period_records = [record for record in records if isinstance(record, EvidenceRecord)]
    return CalculationRecord(
        calculation_id=f"calc-{metric_id}-v1",
        formula_id=formula_id,
        input_evidence_ids=[record.evidence_id for record in records],
        source_reference=f"derived:{formula_id}",
        as_of=as_of,
        result=result,
        unit="multiple" if metric_id in {"price_to_book", "pe_ratio"} else "ratio",
        period_start=min(
            (record.period_start for record in period_records),
            default=as_of.date(),
        ),
        period_end=max(
            (record.period_end for record in period_records),
            default=as_of.date(),
        ),
        validation_status=ValidationStatus.VALID,
    )


def _decision(
    metric_id: str,
    status: str,
    reason_code: str,
    evidence_ids: list[str] | None = None,
    calculation_ids: list[str] | None = None,
    blocking: bool = False,
) -> PolicyDecision:
    return PolicyDecision(
        metric_id=metric_id,
        status=status,
        evidence_ids=evidence_ids or [],
        calculation_ids=calculation_ids or [],
        reason_code=reason_code,
        blocking=blocking,
    )


def evaluate_bank_profile(
    profile_input: Mapping[str, Any],
    evidence_records: tuple[EvidenceRecord, ...],
    market_price_records: tuple[MarketPriceRecord, ...] = (),
) -> tuple[
    dict[str, Decimal | None],
    tuple[PolicyDecision, ...],
    tuple[CalculationRecord, ...],
]:
    as_of = _profile_as_of(profile_input)
    metric_inputs = profile_input["metric_inputs"]
    counts = Counter(record.evidence_id for record in evidence_records)
    evidence_by_id = {
        record.evidence_id: record
        for record in evidence_records
        if counts[record.evidence_id] == 1
    }
    market_price, market_reason = _usable_market_price(market_price_records, as_of)

    values: dict[str, Decimal | None] = {}
    decisions: list[PolicyDecision] = []
    calculations: list[CalculationRecord] = []

    for metric_id in BANK_METRIC_IDS:
        if metric_id == "fcf_yield":
            values[metric_id] = None
            decisions.append(
                _decision(
                    metric_id,
                    "not_applicable",
                    "bank_fcf_not_applicable",
                )
            )
            continue

        if metric_id == "cet1_ratio":
            record, reason = _usable_evidence(
                "cet1_ratio", metric_inputs, evidence_by_id, counts, as_of
            )
            if record is None:
                reason = (
                    "cet1_ratio_not_disclosed"
                    if reason == "missing_required_evidence"
                    else reason
                )
                values[metric_id] = None
                decisions.append(_decision(metric_id, "unavailable", reason))
            else:
                values[metric_id] = record.value
                decisions.append(
                    _decision(metric_id, "available", "metric_available", [record.evidence_id])
                )
            continue

        input_names = _INPUTS[metric_id]
        source_records: list[EvidenceRecord | MarketPriceRecord] = []
        reason = ""
        for name in input_names:
            record, reason = _usable_evidence(
                name, metric_inputs, evidence_by_id, counts, as_of
            )
            if record is None:
                break
            source_records.append(record)

        if reason == "" and metric_id in {"price_to_book", "pe_ratio"}:
            if market_price is None:
                reason = market_reason
            else:
                source_records.insert(0, market_price)

        if reason:
            status = "invalid" if reason == "invalid_evidence" else "unavailable"
            values[metric_id] = None
            decisions.append(
                _decision(
                    metric_id,
                    status,
                    reason,
                    blocking=metric_id in _CORE_METRICS,
                )
            )
            continue

        result = _calculate(
            metric_id,
            [
                record.price if isinstance(record, MarketPriceRecord) else record.value
                for record in source_records
            ],
        )
        if result is None:
            values[metric_id] = None
            decisions.append(
                _decision(
                    metric_id,
                    "unavailable",
                    "invalid_denominator",
                    blocking=metric_id in _CORE_METRICS,
                )
            )
            continue

        formula_id = _FORMULAS[metric_id]
        calculation = _calculation(
            metric_id, formula_id, source_records, result, as_of
        )
        calculations.append(calculation)
        values[metric_id] = result
        decisions.append(
            _decision(
                metric_id,
                "available",
                "metric_available",
                [record.evidence_id for record in source_records],
                [calculation.calculation_id],
            )
        )

    return values, tuple(decisions), tuple(calculations)


__all__ = [
    "BANK_METRIC_IDS",
    "POLICY_VERSION",
    "PROFILE_VERSION",
    "evaluate_bank_profile",
]
