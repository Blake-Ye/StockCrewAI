from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from typing import Literal, cast

from stockcrewai.models.evidence import (
    CalculationRecord,
    EvidenceRecord,
    MarketPriceRecord,
    ValidationStatus,
)
from stockcrewai.models.policy import PolicyDecision


PROFILE_VERSION = "reit-profile:v1"
POLICY_VERSION = "metric-policy:v2"
REIT_METRIC_IDS = (
    "ffo_total",
    "ffo_per_share",
    "affo",
    "same_store_noi",
    "occupancy",
    "net_debt_to_ebitda",
    "dividend_coverage",
    "price_to_ffo",
    "pe",
    "fcf_yield",
)


def _mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return None


def _sequence(value: object) -> list[object] | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return None


def _non_empty_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, (bool, float)):
        return None
    if isinstance(value, Decimal):
        result = value
    else:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
    return result if result.is_finite() else None


def _profile_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _profile_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result


def _profile_period(value: object) -> tuple[object, ...] | None:
    period = _mapping(value)
    if period is None:
        return None
    required = ("period_start", "period_end", "fiscal_year", "fiscal_period", "audited", "basis")
    if any(key not in period or period[key] is None for key in required):
        return None
    period_start = _profile_date(period["period_start"])
    period_end = _profile_date(period["period_end"])
    if period_start is None or period_end is None:
        return None
    if not isinstance(period["basis"], str) or not period["basis"].strip():
        return None
    return (
        period_start,
        period_end,
        period["fiscal_year"],
        period["fiscal_period"],
        period["audited"],
        period["basis"],
    )


def _index_records(
    evidence_records: Sequence[EvidenceRecord],
    market_price_records: Sequence[MarketPriceRecord],
) -> tuple[dict[str, EvidenceRecord], frozenset[str]]:
    counts: dict[str, int] = {}
    for record in evidence_records:
        counts[record.evidence_id] = counts.get(record.evidence_id, 0) + 1
    for market_record in market_price_records:
        counts[market_record.evidence_id] = counts.get(market_record.evidence_id, 0) + 1
    duplicate_ids = frozenset(record_id for record_id, count in counts.items() if count != 1)
    evidence_by_id = {
        record.evidence_id: record
        for record in evidence_records
        if record.evidence_id not in duplicate_ids
    }
    return evidence_by_id, duplicate_ids


def _valid_evidence(
    evidence_id: object,
    evidence_by_id: Mapping[str, EvidenceRecord],
    duplicate_ids: frozenset[str],
) -> EvidenceRecord | None:
    if not isinstance(evidence_id, str) or not evidence_id.strip() or evidence_id in duplicate_ids:
        return None
    record = evidence_by_id.get(evidence_id)
    if record is None or record.validation_status is not ValidationStatus.VALID:
        return None
    if record.value is None or not record.value.is_finite():
        return None
    if record.filed_at > record.as_of.date():
        return None
    return record


def _decision(
    metric_id: str,
    status: Literal["available", "unavailable", "not_applicable", "invalid"],
    reason_code: str,
    blocking: bool,
    *,
    evidence_ids: Sequence[str] = (),
    calculation_ids: Sequence[str] = (),
) -> PolicyDecision:
    return PolicyDecision(
        metric_id=metric_id,
        status=status,
        evidence_ids=list(evidence_ids),
        calculation_ids=list(calculation_ids),
        reason_code=reason_code,
        blocking=blocking,
    )


def _calculation(
    calculation_id: str,
    formula_id: str,
    input_evidence_ids: Sequence[str],
    as_of: datetime,
    result: Decimal,
    unit: str,
    period_start: date,
    period_end: date,
) -> CalculationRecord:
    return CalculationRecord(
        calculation_id=calculation_id,
        formula_id=formula_id,
        input_evidence_ids=list(input_evidence_ids),
        source_reference=f"stockcrewai://formula/{formula_id}",
        as_of=as_of,
        result=result,
        unit=unit,
        period_start=period_start,
        period_end=period_end,
        validation_status=ValidationStatus.VALID,
    )


def _ffo_line_reason(
    line: Mapping[str, object],
    expected_line_type: str,
    record: EvidenceRecord,
) -> str | None:
    if line.get("line_type") != expected_line_type:
        return "ffo_adjustment_missing"
    if _non_empty_text(line.get("label")) is None:
        return "ffo_adjustment_missing"
    amount = _decimal(line.get("signed_amount"))
    if amount is None or record.value is None or amount != record.value:
        return "ffo_adjustment_missing"

    source_reference = _non_empty_text(line.get("source_reference"))
    if source_reference is None or source_reference != record.source_reference:
        return "missing_source_url"
    line_period = _profile_period(line.get("period"))
    if (
        line_period is None
        or line_period[0] != record.period_start
        or line_period[1] != record.period_end
    ):
        return "ffo_period_mismatch"
    if line.get("unit") != record.unit:
        return "ffo_unit_mismatch"
    if line.get("currency") != record.currency:
        return "ffo_currency_mismatch"

    filed_at = _profile_date(line.get("filed_at"))
    as_of = _profile_datetime(line.get("as_of"))
    status = line.get("validation_status")
    if isinstance(status, ValidationStatus):
        status = status.value
    if (
        filed_at != record.filed_at
        or as_of != record.as_of
        or status != ValidationStatus.VALID.value
    ):
        return "unvalidated_evidence_id"
    return None


def _evaluate_ffo(
    profile_input: Mapping[str, object],
    evidence_by_id: Mapping[str, EvidenceRecord],
    duplicate_ids: frozenset[str],
) -> tuple[Decimal | None, PolicyDecision, CalculationRecord | None, EvidenceRecord | None]:
    reconciliation = _mapping(profile_input.get("ffo_reconciliation"))
    if reconciliation is None:
        return None, _decision("ffo_total", "unavailable", "ffo_reconciliation_not_disclosed", True), None, None

    gaap_line = _mapping(reconciliation.get("gaap_net_income"))
    if gaap_line is None:
        return None, _decision("ffo_total", "unavailable", "ffo_reconciliation_not_disclosed", True), None, None

    total_line = _mapping(reconciliation.get("disclosed_ffo_total"))
    if total_line is None:
        return None, _decision("ffo_total", "unavailable", "ffo_total_missing", True), None, None

    adjustment_values = _sequence(reconciliation.get("adjustments"))
    if not adjustment_values:
        return None, _decision("ffo_total", "unavailable", "ffo_adjustment_missing", True), None, None
    adjustment_lines = [_mapping(value) for value in adjustment_values]
    if any(line is None for line in adjustment_lines):
        return None, _decision("ffo_total", "unavailable", "ffo_adjustment_missing", True), None, None

    lines: list[tuple[Mapping[str, object], str]] = [(gaap_line, "gaap_net_income")]
    lines.extend((cast(Mapping[str, object], line), "ffo_adjustment") for line in adjustment_lines)
    lines.append((total_line, "disclosed_ffo_total"))

    evidence_ids: list[str] = []
    for line, _ in lines:
        evidence_id = _non_empty_text(line.get("evidence_id"))
        if evidence_id is None:
            return None, _decision("ffo_total", "unavailable", "unvalidated_evidence_id", True), None, None
        evidence_ids.append(evidence_id)
    if len(evidence_ids) != len(set(evidence_ids)):
        return None, _decision("ffo_total", "unavailable", "unvalidated_evidence_id", True), None, None

    records: list[EvidenceRecord] = []
    amounts: list[Decimal] = []
    periods: list[tuple[object, ...]] = []
    units: list[str] = []
    currencies: list[str] = []
    for line, expected_line_type in lines:
        evidence_id = cast(str, line["evidence_id"])
        record = _valid_evidence(evidence_id, evidence_by_id, duplicate_ids)
        if record is None:
            return None, _decision("ffo_total", "unavailable", "unvalidated_evidence_id", True), None, None
        reason = _ffo_line_reason(line, expected_line_type, record)
        if reason is not None:
            return None, _decision("ffo_total", "unavailable", reason, True), None, None
        period = _profile_period(line["period"])
        amount = _decimal(line["signed_amount"])
        if period is None or amount is None:
            return None, _decision("ffo_total", "unavailable", "ffo_adjustment_missing", True), None, None
        records.append(record)
        amounts.append(amount)
        periods.append(period)
        units.append(record.unit)
        currencies.append(record.currency)

    if any(period != periods[0] for period in periods[1:]):
        return None, _decision("ffo_total", "unavailable", "ffo_period_mismatch", True), None, None
    if any(unit != units[0] for unit in units[1:]):
        return None, _decision("ffo_total", "unavailable", "ffo_unit_mismatch", True), None, None
    if any(currency != currencies[0] for currency in currencies[1:]):
        return None, _decision("ffo_total", "unavailable", "ffo_currency_mismatch", True), None, None

    disclosed_total = amounts[-1]
    if amounts[0] + sum(amounts[1:-1], Decimal(0)) != disclosed_total:
        return None, _decision("ffo_total", "unavailable", "ffo_adjustment_missing", True), None, None

    total_record = records[-1]
    calculation = _calculation(
        "calc_reit_ffo_reconciliation_v1",
        "reit-ffo-reconciliation-v1",
        evidence_ids,
        max(record.as_of for record in records),
        disclosed_total,
        total_record.unit,
        total_record.period_start,
        total_record.period_end,
    )
    decision = _decision(
        "ffo_total",
        "available",
        "validated_calculation",
        False,
        evidence_ids=evidence_ids,
        calculation_ids=[calculation.calculation_id],
    )
    return disclosed_total, decision, calculation, total_record


def _evaluate_ffo_per_share(
    profile_input: Mapping[str, object],
    evidence_by_id: Mapping[str, EvidenceRecord],
    duplicate_ids: frozenset[str],
    ffo_value: Decimal | None,
    ffo_record: EvidenceRecord | None,
) -> tuple[Decimal | None, PolicyDecision, CalculationRecord | None, EvidenceRecord | None]:
    if ffo_value is None or ffo_record is None:
        return None, _decision("ffo_per_share", "unavailable", "ffo_total_missing", True), None, None

    metric_inputs = _mapping(profile_input.get("metric_inputs"))
    shares_input = _mapping(
        None if metric_inputs is None else metric_inputs.get("diluted_weighted_average_shares")
    )
    shares_id = None if shares_input is None else _non_empty_text(shares_input.get("evidence_id"))
    if shares_id is None:
        return (
            None,
            _decision(
                "ffo_per_share",
                "unavailable",
                "diluted_weighted_average_shares_missing",
                True,
            ),
            None,
            None,
        )
    shares_record = _valid_evidence(shares_id, evidence_by_id, duplicate_ids)
    if shares_record is None:
        return None, _decision("ffo_per_share", "unavailable", "unvalidated_evidence_id", True), None, None
    if (
        shares_record.value is None
        or shares_record.unit.strip().lower() != "shares"
        or shares_record.value <= 0
    ):
        return None, _decision("ffo_per_share", "unavailable", "ffo_per_share_unit_mismatch", True), None, None
    if (
        shares_record.period_start != ffo_record.period_start
        or shares_record.period_end != ffo_record.period_end
    ):
        return None, _decision("ffo_per_share", "unavailable", "ffo_per_share_period_mismatch", True), None, None

    result = ffo_value / shares_record.value
    input_ids = [ffo_record.evidence_id, shares_record.evidence_id]
    calculation = _calculation(
        "calc_reit_ffo_per_share_v1",
        "reit-ffo-per-share-v1",
        input_ids,
        max(ffo_record.as_of, shares_record.as_of),
        result,
        f"{ffo_record.currency}/share",
        ffo_record.period_start,
        ffo_record.period_end,
    )
    decision = _decision(
        "ffo_per_share",
        "available",
        "validated_calculation",
        False,
        evidence_ids=input_ids,
        calculation_ids=[calculation.calculation_id],
    )
    return result, decision, calculation, shares_record


def _evaluate_affo(
    profile_input: Mapping[str, object],
    evidence_by_id: Mapping[str, EvidenceRecord],
    duplicate_ids: frozenset[str],
) -> tuple[Decimal | None, PolicyDecision, CalculationRecord | None]:
    metric_inputs = _mapping(profile_input.get("metric_inputs"))
    affo_input = _mapping(None if metric_inputs is None else metric_inputs.get("affo"))
    if affo_input is None:
        return None, _decision("affo", "unavailable", "affo_reconciliation_not_disclosed", False), None

    base_id = _non_empty_text(affo_input.get("base_ffo_evidence_id"))
    adjustments = _sequence(affo_input.get("adjustments"))
    disclosed = _mapping(affo_input.get("disclosed_affo_total"))
    if base_id is None or not adjustments or disclosed is None:
        return None, _decision("affo", "unavailable", "affo_reconciliation_not_disclosed", False), None

    adjustment_lines = [_mapping(value) for value in adjustments]
    if any(line is None for line in adjustment_lines):
        return None, _decision("affo", "unavailable", "affo_reconciliation_not_disclosed", False), None
    disclosed_id = _non_empty_text(disclosed.get("evidence_id"))
    disclosed_value = _decimal(disclosed.get("value"))
    if disclosed_id is None or disclosed_value is None:
        return None, _decision("affo", "unavailable", "affo_reconciliation_not_disclosed", False), None

    adjustment_ids: list[str] = []
    adjustment_amounts: list[Decimal] = []
    for line in adjustment_lines:
        adjustment_line = cast(Mapping[str, object], line)
        label = _non_empty_text(adjustment_line.get("label"))
        adjustment_id = _non_empty_text(adjustment_line.get("evidence_id"))
        amount = _decimal(adjustment_line.get("signed_amount"))
        if label is None or adjustment_id is None or amount is None:
            return None, _decision("affo", "unavailable", "affo_reconciliation_not_disclosed", False), None
        adjustment_ids.append(adjustment_id)
        adjustment_amounts.append(amount)

    input_ids = [base_id, *adjustment_ids, disclosed_id]
    if len(input_ids) != len(set(input_ids)):
        return None, _decision("affo", "unavailable", "affo_reconciliation_not_disclosed", False), None
    records: list[EvidenceRecord] = []
    for evidence_id in input_ids:
        record = _valid_evidence(evidence_id, evidence_by_id, duplicate_ids)
        if record is None:
            return None, _decision("affo", "unavailable", "affo_reconciliation_not_disclosed", False), None
        records.append(record)

    if any(
        record.period_start != records[0].period_start
        or record.period_end != records[0].period_end
        or record.unit != records[0].unit
        or record.currency != records[0].currency
        for record in records[1:]
    ):
        return None, _decision("affo", "unavailable", "affo_reconciliation_not_disclosed", False), None

    for amount, record in zip(adjustment_amounts, records[1:-1]):
        if record.value is None or amount != record.value:
            return None, _decision("affo", "unavailable", "affo_reconciliation_not_disclosed", False), None
    if records[0].value is None or records[-1].value is None or disclosed_value != records[-1].value:
        return None, _decision("affo", "unavailable", "affo_reconciliation_not_disclosed", False), None
    if records[0].value + sum(adjustment_amounts, Decimal(0)) != disclosed_value:
        return None, _decision("affo", "unavailable", "affo_reconciliation_not_disclosed", False), None

    total_record = records[-1]
    calculation = _calculation(
        "calc_company_disclosed_affo_reconciliation_v1",
        "company-disclosed-affo-reconciliation-v1",
        input_ids,
        max(record.as_of for record in records),
        disclosed_value,
        total_record.unit,
        total_record.period_start,
        total_record.period_end,
    )
    decision = _decision(
        "affo",
        "available",
        "validated_calculation",
        False,
        evidence_ids=input_ids,
        calculation_ids=[calculation.calculation_id],
    )
    return disclosed_value, decision, calculation


def _direct_metric(
    metric_id: str,
    missing_reason: str,
    profile_input: Mapping[str, object],
    evidence_by_id: Mapping[str, EvidenceRecord],
    duplicate_ids: frozenset[str],
) -> tuple[Decimal | None, PolicyDecision]:
    metric_inputs = _mapping(profile_input.get("metric_inputs"))
    metric_input = _mapping(None if metric_inputs is None else metric_inputs.get(metric_id))
    evidence_id = None if metric_input is None else _non_empty_text(metric_input.get("evidence_id"))
    if evidence_id is None:
        return None, _decision(metric_id, "unavailable", missing_reason, False)
    record = _valid_evidence(evidence_id, evidence_by_id, duplicate_ids)
    if record is None or record.value is None:
        return None, _decision(metric_id, "unavailable", "unvalidated_evidence_id", False)
    return record.value, _decision(
        metric_id,
        "available",
        "validated_evidence",
        False,
        evidence_ids=[record.evidence_id],
    )


def _evaluate_net_debt_to_ebitda(
    profile_input: Mapping[str, object],
    evidence_by_id: Mapping[str, EvidenceRecord],
    duplicate_ids: frozenset[str],
) -> tuple[Decimal | None, PolicyDecision, CalculationRecord | None]:
    metric_inputs = _mapping(profile_input.get("metric_inputs"))
    metric_input = _mapping(
        None if metric_inputs is None else metric_inputs.get("net_debt_to_ebitda")
    )
    if metric_input is None:
        return None, _decision("net_debt_to_ebitda", "unavailable", "net_debt_to_ebitda_not_disclosed", False), None
    net_debt_id = _non_empty_text(metric_input.get("net_debt_evidence_id"))
    ebitda_id = _non_empty_text(metric_input.get("ebitda_evidence_id"))
    if net_debt_id is None or ebitda_id is None or net_debt_id == ebitda_id:
        return None, _decision("net_debt_to_ebitda", "unavailable", "net_debt_to_ebitda_not_disclosed", False), None
    net_debt = _valid_evidence(net_debt_id, evidence_by_id, duplicate_ids)
    ebitda = _valid_evidence(ebitda_id, evidence_by_id, duplicate_ids)
    if net_debt is None or ebitda is None or net_debt.value is None or ebitda.value is None:
        return None, _decision("net_debt_to_ebitda", "unavailable", "net_debt_to_ebitda_not_disclosed", False), None
    if net_debt.period_start != ebitda.period_start or net_debt.period_end != ebitda.period_end:
        return None, _decision("net_debt_to_ebitda", "unavailable", "net_debt_to_ebitda_period_ambiguous", False), None
    if net_debt.unit != ebitda.unit or net_debt.currency != ebitda.currency or ebitda.value == 0:
        return None, _decision("net_debt_to_ebitda", "unavailable", "net_debt_to_ebitda_not_disclosed", False), None

    input_ids = [net_debt.evidence_id, ebitda.evidence_id]
    result = net_debt.value / ebitda.value
    calculation = _calculation(
        "calc_reit_net_debt_to_ebitda_v1",
        "reit-net-debt-to-ebitda-v1",
        input_ids,
        max(net_debt.as_of, ebitda.as_of),
        result,
        "multiple",
        net_debt.period_start,
        net_debt.period_end,
    )
    return result, _decision(
        "net_debt_to_ebitda",
        "available",
        "validated_calculation",
        False,
        evidence_ids=input_ids,
        calculation_ids=[calculation.calculation_id],
    ), calculation


def _evaluate_dividend_coverage(
    profile_input: Mapping[str, object],
    evidence_by_id: Mapping[str, EvidenceRecord],
    duplicate_ids: frozenset[str],
) -> tuple[Decimal | None, PolicyDecision, CalculationRecord | None]:
    metric_inputs = _mapping(profile_input.get("metric_inputs"))
    metric_input = _mapping(
        None if metric_inputs is None else metric_inputs.get("dividend_coverage")
    )
    if metric_input is None:
        return None, _decision("dividend_coverage", "unavailable", "dividend_coverage_not_disclosed", False), None
    ffo_id = _non_empty_text(metric_input.get("ffo_attributable_to_common_evidence_id"))
    dividends_id = _non_empty_text(metric_input.get("common_dividends_evidence_id"))
    if ffo_id is None or dividends_id is None or ffo_id == dividends_id:
        return None, _decision("dividend_coverage", "unavailable", "dividend_coverage_not_disclosed", False), None
    ffo = _valid_evidence(ffo_id, evidence_by_id, duplicate_ids)
    dividends = _valid_evidence(dividends_id, evidence_by_id, duplicate_ids)
    if ffo is None or dividends is None or ffo.value is None or dividends.value is None:
        return None, _decision("dividend_coverage", "unavailable", "dividend_coverage_not_disclosed", False), None
    if dividends.value == 0:
        return None, _decision("dividend_coverage", "unavailable", "zero_common_dividends", False), None
    if (
        ffo.period_start != dividends.period_start
        or ffo.period_end != dividends.period_end
        or ffo.unit != dividends.unit
        or ffo.currency != dividends.currency
        or dividends.value < 0
    ):
        return None, _decision("dividend_coverage", "unavailable", "dividend_coverage_not_disclosed", False), None

    input_ids = [ffo.evidence_id, dividends.evidence_id]
    result = ffo.value / dividends.value
    calculation = _calculation(
        "calc_reit_dividend_coverage_v1",
        "reit-dividend-coverage-v1",
        input_ids,
        max(ffo.as_of, dividends.as_of),
        result,
        "ratio",
        ffo.period_start,
        ffo.period_end,
    )
    return result, _decision(
        "dividend_coverage",
        "available",
        "validated_calculation",
        False,
        evidence_ids=input_ids,
        calculation_ids=[calculation.calculation_id],
    ), calculation


def _evaluate_price_to_ffo(
    ffo_per_share: Decimal | None,
    ffo_record: EvidenceRecord | None,
    shares_record: EvidenceRecord | None,
    ffo_calculation: CalculationRecord | None,
    duplicate_ids: frozenset[str],
    market_price_records: Sequence[MarketPriceRecord],
) -> tuple[Decimal | None, PolicyDecision, CalculationRecord | None]:
    if ffo_per_share is not None and ffo_per_share <= 0:
        return None, _decision("price_to_ffo", "unavailable", "non_positive_ffo_per_share", False), None
    valid_prices = [
        record
        for record in market_price_records
        if record.evidence_id not in duplicate_ids
        and record.validation_status is ValidationStatus.VALID
        and record.price.is_finite()
    ]
    if (
        ffo_per_share is None
        or ffo_record is None
        or shares_record is None
        or len(valid_prices) != 1
    ):
        return None, _decision("price_to_ffo", "unavailable", "market_price_missing", False), None
    price_record = valid_prices[0]
    financial_as_of = max(ffo_record.as_of, shares_record.as_of)
    if ffo_calculation is not None:
        financial_as_of = max(financial_as_of, ffo_calculation.as_of)
    if price_record.price_timestamp < financial_as_of:
        return None, _decision("price_to_ffo", "unavailable", "market_price_missing", False), None
    if price_record.currency != ffo_record.currency:
        return None, _decision("price_to_ffo", "unavailable", "price_to_ffo_currency_mismatch", False), None

    input_ids = [price_record.evidence_id, ffo_record.evidence_id, shares_record.evidence_id]
    result = price_record.price / ffo_per_share
    calculation = _calculation(
        "calc_reit_price_to_ffo_v1",
        "reit-price-to-ffo-v1",
        input_ids,
        max(financial_as_of, price_record.price_timestamp),
        result,
        "multiple",
        ffo_record.period_start,
        ffo_record.period_end,
    )
    return result, _decision(
        "price_to_ffo",
        "available",
        "validated_calculation",
        False,
        evidence_ids=input_ids,
        calculation_ids=[calculation.calculation_id],
    ), calculation


def evaluate_reit_profile(
    profile_input: Mapping[str, object],
    evidence_records: Sequence[EvidenceRecord],
    market_price_records: Sequence[MarketPriceRecord] = (),
) -> tuple[
    dict[str, Decimal | None],
    tuple[PolicyDecision, ...],
    tuple[CalculationRecord, ...],
]:
    """Evaluate the frozen REIT profile contract from already validated records."""
    profile = cast(Mapping[str, object], profile_input)
    values: dict[str, Decimal | None] = {metric_id: None for metric_id in REIT_METRIC_IDS}

    if (
        profile.get("profile_version") != PROFILE_VERSION
        or profile.get("issuer_profile") != "reit"
        or profile.get("policy_version") != POLICY_VERSION
    ):
        decisions: list[PolicyDecision] = []
        for metric_id in REIT_METRIC_IDS:
            if metric_id in ("ffo_total", "ffo_per_share"):
                decisions.append(
                    _decision(metric_id, "unavailable", "missing_required_field", True)
                )
            elif metric_id == "pe":
                decisions.append(
                    _decision(metric_id, "not_applicable", "reit_primary_valuation_not_pe", False)
                )
            elif metric_id == "fcf_yield":
                decisions.append(
                    _decision(
                        metric_id,
                        "not_applicable",
                        "reit_primary_cash_metric_not_fcf",
                        False,
                    )
                )
            else:
                decisions.append(
                    _decision(metric_id, "unavailable", "missing_required_field", False)
                )
        return values, tuple(decisions), ()

    calculations: list[CalculationRecord] = []

    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        evidence_by_id, duplicate_ids = _index_records(evidence_records, market_price_records)

        ffo_value, ffo_decision, ffo_calculation, ffo_record = _evaluate_ffo(
            profile, evidence_by_id, duplicate_ids
        )
        values["ffo_total"] = ffo_value
        if ffo_calculation is not None:
            calculations.append(ffo_calculation)

        ffo_per_share, ffo_per_share_decision, per_share_calculation, shares_record = _evaluate_ffo_per_share(
            profile, evidence_by_id, duplicate_ids, ffo_value, ffo_record
        )
        values["ffo_per_share"] = ffo_per_share
        if per_share_calculation is not None:
            calculations.append(per_share_calculation)

        affo_value, affo_decision, affo_calculation = _evaluate_affo(
            profile, evidence_by_id, duplicate_ids
        )
        values["affo"] = affo_value
        if affo_calculation is not None:
            calculations.append(affo_calculation)

        same_store_noi, same_store_decision = _direct_metric(
            "same_store_noi", "same_store_noi_not_disclosed", profile, evidence_by_id, duplicate_ids
        )
        values["same_store_noi"] = same_store_noi

        occupancy, occupancy_decision = _direct_metric(
            "occupancy", "occupancy_not_disclosed", profile, evidence_by_id, duplicate_ids
        )
        values["occupancy"] = occupancy

        net_debt_to_ebitda, net_debt_decision, net_debt_calculation = _evaluate_net_debt_to_ebitda(
            profile, evidence_by_id, duplicate_ids
        )
        values["net_debt_to_ebitda"] = net_debt_to_ebitda
        if net_debt_calculation is not None:
            calculations.append(net_debt_calculation)

        dividend_coverage, dividend_decision, dividend_calculation = _evaluate_dividend_coverage(
            profile, evidence_by_id, duplicate_ids
        )
        values["dividend_coverage"] = dividend_coverage
        if dividend_calculation is not None:
            calculations.append(dividend_calculation)

        price_to_ffo, price_decision, price_calculation = _evaluate_price_to_ffo(
            ffo_per_share,
            ffo_record,
            shares_record,
            ffo_calculation,
            duplicate_ids,
            market_price_records,
        )
        values["price_to_ffo"] = price_to_ffo
        if price_calculation is not None:
            calculations.append(price_calculation)

        pe_decision = _decision(
            "pe", "not_applicable", "reit_primary_valuation_not_pe", False
        )
        fcf_yield_decision = _decision(
            "fcf_yield", "not_applicable", "reit_primary_cash_metric_not_fcf", False
        )

    decisions_by_metric = {
        "ffo_total": ffo_decision,
        "ffo_per_share": ffo_per_share_decision,
        "affo": affo_decision,
        "same_store_noi": same_store_decision,
        "occupancy": occupancy_decision,
        "net_debt_to_ebitda": net_debt_decision,
        "dividend_coverage": dividend_decision,
        "price_to_ffo": price_decision,
        "pe": pe_decision,
        "fcf_yield": fcf_yield_decision,
    }
    return values, tuple(decisions_by_metric[metric_id] for metric_id in REIT_METRIC_IDS), tuple(calculations)


__all__ = [
    "POLICY_VERSION",
    "PROFILE_VERSION",
    "REIT_METRIC_IDS",
    "evaluate_reit_profile",
]
