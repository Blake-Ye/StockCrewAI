from __future__ import annotations

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


PROFILE_VERSION = "insurance-profile:v1"
POLICY_VERSION = "metric-policy:insurance:v1"
INSURANCE_METRIC_IDS = (
    "loss_ratio",
    "expense_ratio",
    "combined_ratio",
    "insurance_roe",
    "book_value_per_share",
    "investment_income",
    "solvency_ratio",
    "price_to_book",
    "pe_ratio",
    "fcf_yield",
)

_REQUIRED_METRICS = frozenset(
    {"loss_ratio", "expense_ratio", "combined_ratio", "insurance_roe"}
)


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


def _is_future(
    record: EvidenceRecord | MarketPriceRecord,
    profile_as_of: datetime | None,
) -> bool:
    if profile_as_of is None:
        return False
    timestamp = record.as_of if isinstance(record, EvidenceRecord) else record.price_timestamp
    if timestamp > profile_as_of:
        return True
    return isinstance(record, EvidenceRecord) and record.filed_at > profile_as_of.date()


def _prepare_sources(
    evidence_records: Sequence[EvidenceRecord],
    market_price_records: Sequence[MarketPriceRecord],
    profile_as_of: datetime | None,
) -> tuple[dict[str, EvidenceRecord | MarketPriceRecord], dict[str, str]]:
    records = tuple(evidence_records) + tuple(market_price_records)
    by_id: dict[str, EvidenceRecord | MarketPriceRecord] = {}
    duplicate_ids: set[str] = set()
    for record in records:
        if not isinstance(record, (EvidenceRecord, MarketPriceRecord)):
            continue
        if record.evidence_id in by_id:
            duplicate_ids.add(record.evidence_id)
        else:
            by_id[record.evidence_id] = record

    status: dict[str, str] = {}
    for evidence_id, record in by_id.items():
        if evidence_id in duplicate_ids:
            status[evidence_id] = "duplicate_evidence_id"
        elif record.validation_status is not ValidationStatus.VALID:
            status[evidence_id] = "unvalidated_evidence_id"
        elif _is_future(record, profile_as_of):
            status[evidence_id] = "future_evidence_id"
        else:
            status[evidence_id] = "available"
    return by_id, status


def _input_source_id(profile_input: Mapping[str, object], key: str) -> str | None:
    metric_inputs = profile_input.get("metric_inputs")
    if not isinstance(metric_inputs, Mapping):
        return None
    source_id = metric_inputs.get(key)
    return source_id if isinstance(source_id, str) and source_id else None


def _resolve_evidence(
    profile_input: Mapping[str, object],
    key: str,
    source_by_id: Mapping[str, EvidenceRecord | MarketPriceRecord],
    source_status: Mapping[str, str],
    missing_reason: str,
) -> tuple[EvidenceRecord | None, str | None]:
    source_id = _input_source_id(profile_input, key)
    if source_id is None:
        return None, missing_reason
    record = source_by_id.get(source_id)
    if record is None:
        return None, "missing_evidence_id"
    if not isinstance(record, EvidenceRecord):
        return None, "invalid_evidence_type"
    reason = source_status.get(source_id)
    if reason != "available" or record.value is None:
        return None, reason or "unvalidated_evidence_id"
    return record, None


def _resolve_market_price(
    market_price_records: Sequence[MarketPriceRecord],
    source_status: Mapping[str, str],
) -> tuple[MarketPriceRecord | None, str | None]:
    records = tuple(market_price_records)
    typed_records = tuple(record for record in records if isinstance(record, MarketPriceRecord))
    if not typed_records:
        return None, "market_price_missing"
    for record in typed_records:
        reason = source_status.get(record.evidence_id)
        if reason != "available":
            return None, reason or "unvalidated_evidence_id"
    if len(typed_records) != 1 or len(typed_records) != len(records):
        return None, "ambiguous_market_price"
    return typed_records[0], None


def _divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        return numerator / denominator


def _calculation(
    calculation_id: str,
    formula_id: str,
    input_evidence_ids: Sequence[str],
    source_by_id: Mapping[str, EvidenceRecord | MarketPriceRecord],
    result: Decimal,
    unit: str,
) -> CalculationRecord:
    source_records = [source_by_id[source_id] for source_id in input_evidence_ids]
    timestamps = [
        record.as_of if isinstance(record, EvidenceRecord) else record.price_timestamp
        for record in source_records
    ]
    evidence_records = [
        record for record in source_records if isinstance(record, EvidenceRecord)
    ]
    if evidence_records:
        period_start = min(record.period_start for record in evidence_records)
        period_end = max(record.period_end for record in evidence_records)
    else:
        period_start = max(timestamps).date()
        period_end = period_start
    return CalculationRecord(
        calculation_id=calculation_id,
        formula_id=formula_id,
        input_evidence_ids=list(input_evidence_ids),
        source_reference=f"derived:{formula_id}",
        as_of=max(timestamps),
        result=result,
        unit=unit,
        period_start=period_start,
        period_end=period_end,
        validation_status=ValidationStatus.VALID,
    )


def evaluate_insurance_profile(
    profile_input: Mapping[str, object],
    evidence_records: Sequence[EvidenceRecord],
    market_price_records: Sequence[MarketPriceRecord] = (),
) -> tuple[
    dict[str, Decimal | None],
    tuple[PolicyDecision, ...],
    tuple[CalculationRecord, ...],
]:
    source_by_id, source_status = _prepare_sources(
        evidence_records,
        market_price_records,
        _profile_as_of(profile_input),
    )
    values: dict[str, Decimal | None] = {metric_id: None for metric_id in INSURANCE_METRIC_IDS}
    decisions: dict[str, PolicyDecision] = {}
    calculations: list[CalculationRecord] = []

    def unavailable(metric_id: str, reason: str) -> None:
        decisions[metric_id] = PolicyDecision(
            metric_id=metric_id,
            status="unavailable",
            reason_code=reason,
            blocking=metric_id in _REQUIRED_METRICS,
        )

    def available(
        metric_id: str,
        value: Decimal,
        evidence_ids: Sequence[str],
        calculation: CalculationRecord | None = None,
    ) -> None:
        values[metric_id] = value
        calculations_ids = [] if calculation is None else [calculation.calculation_id]
        decisions[metric_id] = PolicyDecision(
            metric_id=metric_id,
            status="available",
            evidence_ids=list(evidence_ids),
            calculation_ids=calculations_ids,
            reason_code="calculated" if calculation is not None else "validated_evidence",
            blocking=False,
        )
        if calculation is not None:
            calculations.append(calculation)

    def metric_evidence(
        metric_id: str,
        key: str,
        missing_reason: str,
    ) -> tuple[EvidenceRecord | None, str | None]:
        return _resolve_evidence(
            profile_input,
            key,
            source_by_id,
            source_status,
            missing_reason,
        )

    losses, loss_reason = metric_evidence("loss_ratio", "incurred_losses", "loss_ratio_missing")
    premiums, premium_reason = metric_evidence(
        "loss_ratio", "earned_premiums", "loss_ratio_missing"
    )
    if losses is None or premiums is None:
        unavailable("loss_ratio", loss_reason or premium_reason or "loss_ratio_missing")
    elif premiums.value == 0:
        unavailable("loss_ratio", "zero_earned_premiums")
    else:
        input_ids = [losses.evidence_id, premiums.evidence_id]
        available(
            "loss_ratio",
            _divide(losses.value, premiums.value),
            input_ids,
            _calculation(
                "calc_insurance_loss_ratio_v1",
                "insurance-loss-ratio-v1",
                input_ids,
                source_by_id,
                _divide(losses.value, premiums.value),
                "ratio",
            ),
        )

    expenses, expense_reason = metric_evidence(
        "expense_ratio", "underwriting_expenses", "expense_ratio_missing"
    )
    premiums, premium_reason = metric_evidence(
        "expense_ratio", "earned_premiums", "expense_ratio_missing"
    )
    if expenses is None or premiums is None:
        unavailable("expense_ratio", expense_reason or premium_reason or "expense_ratio_missing")
    elif premiums.value == 0:
        unavailable("expense_ratio", "zero_earned_premiums")
    else:
        input_ids = [expenses.evidence_id, premiums.evidence_id]
        result = _divide(expenses.value, premiums.value)
        available(
            "expense_ratio",
            result,
            input_ids,
            _calculation(
                "calc_insurance_expense_ratio_v1",
                "insurance-expense-ratio-v1",
                input_ids,
                source_by_id,
                result,
                "ratio",
            ),
        )

    losses, loss_reason = metric_evidence(
        "combined_ratio", "incurred_losses", "combined_ratio_components_missing"
    )
    expenses, expense_reason = metric_evidence(
        "combined_ratio", "underwriting_expenses", "combined_ratio_components_missing"
    )
    premiums, premium_reason = metric_evidence(
        "combined_ratio", "earned_premiums", "combined_ratio_components_missing"
    )
    if losses is None or expenses is None or premiums is None:
        unavailable(
            "combined_ratio",
            loss_reason or expense_reason or premium_reason or "combined_ratio_components_missing",
        )
    elif premiums.value == 0:
        unavailable("combined_ratio", "zero_earned_premiums")
    else:
        input_ids = [losses.evidence_id, expenses.evidence_id, premiums.evidence_id]
        result = _divide(losses.value + expenses.value, premiums.value)
        available(
            "combined_ratio",
            result,
            input_ids,
            _calculation(
                "calc_insurance_combined_ratio_v1",
                "insurance-combined-ratio-v1",
                input_ids,
                source_by_id,
                result,
                "ratio",
            ),
        )

    net_income, net_income_reason = metric_evidence(
        "insurance_roe", "net_income", "insurance_roe_missing"
    )
    average_equity, equity_reason = metric_evidence(
        "insurance_roe", "average_equity", "insurance_roe_missing"
    )
    if net_income is None or average_equity is None:
        unavailable(
            "insurance_roe",
            net_income_reason or equity_reason or "insurance_roe_missing",
        )
    elif average_equity.value == 0:
        unavailable("insurance_roe", "zero_average_equity")
    else:
        input_ids = [net_income.evidence_id, average_equity.evidence_id]
        result = _divide(net_income.value, average_equity.value)
        available(
            "insurance_roe",
            result,
            input_ids,
            _calculation(
                "calc_insurance_roe_v1",
                "insurance-roe-v1",
                input_ids,
                source_by_id,
                result,
                "ratio",
            ),
        )

    common_equity, common_equity_reason = metric_evidence(
        "book_value_per_share", "common_equity", "book_value_per_share_missing"
    )
    shares, shares_reason = metric_evidence(
        "book_value_per_share",
        "diluted_weighted_average_shares",
        "book_value_per_share_missing",
    )
    if common_equity is None or shares is None:
        unavailable(
            "book_value_per_share",
            common_equity_reason or shares_reason or "book_value_per_share_missing",
        )
    elif shares.value == 0:
        unavailable("book_value_per_share", "zero_shares")
    else:
        input_ids = [common_equity.evidence_id, shares.evidence_id]
        result = _divide(common_equity.value, shares.value)
        available(
            "book_value_per_share",
            result,
            input_ids,
            _calculation(
                "calc_insurance_book_value_per_share_v1",
                "insurance-book-value-per-share-v1",
                input_ids,
                source_by_id,
                result,
                "currency/share",
            ),
        )

    investment_income, investment_income_reason = metric_evidence(
        "investment_income", "investment_income", "investment_income_missing"
    )
    if investment_income is None:
        unavailable("investment_income", investment_income_reason or "investment_income_missing")
    else:
        available(
            "investment_income",
            investment_income.value,
            [investment_income.evidence_id],
        )

    solvency_ratio, solvency_reason = metric_evidence(
        "solvency_ratio", "solvency_ratio", "solvency_ratio_missing"
    )
    if solvency_ratio is None:
        unavailable("solvency_ratio", solvency_reason or "solvency_ratio_missing")
    else:
        available("solvency_ratio", solvency_ratio.value, [solvency_ratio.evidence_id])

    market_price, market_price_reason = _resolve_market_price(
        market_price_records, source_status
    )
    common_equity, common_equity_reason = metric_evidence(
        "price_to_book", "common_equity", "price_to_book_missing"
    )
    shares, shares_reason = metric_evidence(
        "price_to_book",
        "diluted_weighted_average_shares",
        "price_to_book_missing",
    )
    if market_price is None or common_equity is None or shares is None:
        unavailable(
            "price_to_book",
            market_price_reason
            or common_equity_reason
            or shares_reason
            or "price_to_book_missing",
        )
    elif common_equity.value == 0:
        unavailable("price_to_book", "zero_common_equity")
    elif shares.value == 0:
        unavailable("price_to_book", "zero_shares")
    else:
        input_ids = [market_price.evidence_id, common_equity.evidence_id, shares.evidence_id]
        result = _divide(market_price.price * shares.value, common_equity.value)
        available(
            "price_to_book",
            result,
            input_ids,
            _calculation(
                "calc_insurance_price_to_book_v1",
                "insurance-price-to-book-v1",
                input_ids,
                source_by_id,
                result,
                "ratio",
            ),
        )

    diluted_eps, diluted_eps_reason = metric_evidence(
        "pe_ratio", "diluted_eps", "pe_ratio_missing"
    )
    if market_price is None or diluted_eps is None:
        unavailable("pe_ratio", market_price_reason or diluted_eps_reason or "pe_ratio_missing")
    elif diluted_eps.value <= 0:
        unavailable("pe_ratio", "non-positive-eps")
    else:
        input_ids = [market_price.evidence_id, diluted_eps.evidence_id]
        result = _divide(market_price.price, diluted_eps.value)
        available(
            "pe_ratio",
            result,
            input_ids,
            _calculation(
                "calc_insurance_pe_ratio_v1",
                "insurance-pe-ratio-v1",
                input_ids,
                source_by_id,
                result,
                "ratio",
            ),
        )

    values["fcf_yield"] = None
    decisions["fcf_yield"] = PolicyDecision(
        metric_id="fcf_yield",
        status="not_applicable",
        reason_code="insurance_fcf_not_applicable",
        blocking=False,
    )

    return (
        values,
        tuple(decisions[metric_id] for metric_id in INSURANCE_METRIC_IDS),
        tuple(calculations),
    )


__all__ = [
    "INSURANCE_METRIC_IDS",
    "POLICY_VERSION",
    "PROFILE_VERSION",
    "evaluate_insurance_profile",
]
