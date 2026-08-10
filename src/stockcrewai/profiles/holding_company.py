from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    Underflow,
    localcontext,
)
from typing import Literal

from stockcrewai.models.evidence import (
    CalculationRecord,
    EvidenceRecord,
    MarketPriceRecord,
    ValidationStatus,
)
from stockcrewai.models.policy import PolicyDecision


PROFILE_VERSION = "holding-company-profile:v1"
POLICY_VERSION = "metric-policy:holding-company:v1"
HOLDING_COMPANY_METRIC_IDS = (
    "attributable_holdings_value",
    "holding_company_nav",
    "holding_company_market_cap",
    "holding_company_nav_discount",
    "pe_ratio",
    "fcf_yield",
    "historical_valuation",
    "reverse_dcf",
)

_HOLDING_SCOPE = "standalone_equity_or_asset_value"
_DECIMAL_CONTEXT_PRECISION = 28
_DECIMAL_CONTEXT_EMAX = 999_999
_DECIMAL_CONTEXT_EMIN = -999_999
_DECIMAL_ARITHMETIC_FAILURE_REASON = "holding_decimal_arithmetic_failed"
_FIXED_DECIMAL_CONTEXT = Context(
    prec=_DECIMAL_CONTEXT_PRECISION,
    rounding=ROUND_HALF_EVEN,
    Emax=_DECIMAL_CONTEXT_EMAX,
    Emin=_DECIMAL_CONTEXT_EMIN,
    traps=[InvalidOperation, DivisionByZero, Overflow, Underflow],
)
_INVALID_REASONS = frozenset(
    {
        "holding_double_count_detected",
        "holding_parent_consolidated_value_disallowed",
        "currency_mismatch",
        "unit_mismatch",
        "holding_ownership_ratio_invalid",
    }
)


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _sequence(value: object) -> tuple[object, ...] | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(value)
    return None


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _normalized(value: object) -> str | None:
    text = _text(value)
    return text.upper() if text is not None else None


def _profile_as_of(profile_input: Mapping[str, object]) -> datetime | None:
    value = profile_input.get("as_of")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result


def _metric_inputs(profile_input: Mapping[str, object]) -> Mapping[str, object]:
    value = profile_input.get("metric_inputs")
    return value if isinstance(value, Mapping) else {}


def _input_id(metric_inputs: Mapping[str, object], key: str) -> str | None:
    envelope = _mapping(metric_inputs.get(key))
    if envelope is None:
        return None
    return _text(envelope.get("evidence_id"))


def _evidence_items(value: object) -> tuple[EvidenceRecord, ...]:
    items = _sequence(value)
    if items is None:
        return ()
    return tuple(item for item in items if isinstance(item, EvidenceRecord))


def _market_items(value: object) -> tuple[MarketPriceRecord, ...]:
    items = _sequence(value)
    if items is None:
        return ()
    return tuple(item for item in items if isinstance(item, MarketPriceRecord))


def _source_indexes(
    evidence_records: Sequence[EvidenceRecord],
    market_price_records: Sequence[MarketPriceRecord],
) -> tuple[
    dict[str, EvidenceRecord],
    dict[str, MarketPriceRecord],
    frozenset[str],
]:
    all_records = tuple(evidence_records) + tuple(market_price_records)
    counts = Counter(record.evidence_id for record in all_records)
    duplicate_ids = frozenset(
        evidence_id for evidence_id, count in counts.items() if count != 1
    )
    evidence_by_id = {
        record.evidence_id: record
        for record in evidence_records
        if record.evidence_id not in duplicate_ids
    }
    market_by_id = {
        record.evidence_id: record
        for record in market_price_records
        if record.evidence_id not in duplicate_ids
    }
    return evidence_by_id, market_by_id, duplicate_ids


def _evidence_status(
    record: EvidenceRecord,
    profile_as_of: datetime | None,
) -> str | None:
    if (
        record.validation_status is not ValidationStatus.VALID
        or record.value is None
        or not record.value.is_finite()
    ):
        return "unvalidated_evidence_id"
    if profile_as_of is None:
        return "profile_as_of_missing"
    if record.filed_at > profile_as_of.date() or record.as_of > profile_as_of:
        return "filed_after_as_of"
    return None


def _market_status(
    record: MarketPriceRecord,
    profile_as_of: datetime | None,
) -> str | None:
    if (
        record.validation_status is not ValidationStatus.VALID
        or not record.price.is_finite()
    ):
        return "unvalidated_evidence_id"
    if profile_as_of is None:
        return "profile_as_of_missing"
    if record.price_timestamp > profile_as_of:
        return "filed_after_as_of"
    return None


def _resolve_evidence(
    evidence_id: str | None,
    evidence_by_id: Mapping[str, EvidenceRecord],
    duplicate_ids: frozenset[str],
    statuses: Mapping[str, str],
    missing_reason: str,
) -> tuple[EvidenceRecord | None, str | None]:
    if evidence_id is None:
        return None, missing_reason
    if evidence_id in duplicate_ids:
        return None, "duplicate_evidence_id"
    record = evidence_by_id.get(evidence_id)
    if record is None:
        return None, missing_reason
    reason = statuses.get(evidence_id)
    if reason is not None:
        return None, reason
    return record, None


def _failure_status(reason: str) -> Literal["unavailable", "invalid"]:
    return "invalid" if reason in _INVALID_REASONS else "unavailable"


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
    records: Sequence[EvidenceRecord | MarketPriceRecord],
    result: Decimal,
    unit: str,
) -> CalculationRecord:
    source_records = tuple(records)
    timestamps = [
        record.as_of if isinstance(record, EvidenceRecord) else record.price_timestamp
        for record in source_records
    ]
    evidence_records = [
        record for record in source_records if isinstance(record, EvidenceRecord)
    ]
    fallback_as_of = datetime(1970, 1, 1, tzinfo=timezone.utc)
    as_of = max(timestamps, default=fallback_as_of)
    return CalculationRecord(
        calculation_id=calculation_id,
        formula_id=formula_id,
        input_evidence_ids=[record.evidence_id for record in source_records],
        source_reference=f"derived:{formula_id}",
        as_of=as_of,
        result=result,
        unit=unit,
        period_start=min(
            (record.period_start for record in evidence_records),
            default=as_of.date(),
        ),
        period_end=max(
            (record.period_end for record in evidence_records),
            default=as_of.date(),
        ),
        validation_status=ValidationStatus.VALID,
    )


def _holding_inputs(
    metric_inputs: Mapping[str, object],
) -> tuple[list[tuple[str, str, str]], str | None]:
    raw_holdings = _sequence(metric_inputs.get("holdings"))
    if not raw_holdings:
        return [], "holding_components_missing"

    holdings: list[tuple[str, str, str]] = []
    holding_ids: list[str] = []
    evidence_ids: list[str] = []
    for raw_holding in raw_holdings:
        holding = _mapping(raw_holding)
        if holding is None:
            return [], "holding_components_missing"
        holding_id = _text(holding.get("holding_id"))
        fair_value_id = _text(holding.get("fair_value_evidence_id"))
        ownership_id = _text(holding.get("ownership_ratio_evidence_id"))
        if holding_id is None:
            return [], "holding_components_missing"
        if fair_value_id is None:
            return [], "holding_fair_value_missing"
        if ownership_id is None:
            return [], "holding_ownership_ratio_missing"
        if _text(holding.get("value_scope")) != _HOLDING_SCOPE:
            return [], "holding_parent_consolidated_value_disallowed"
        holdings.append((holding_id, fair_value_id, ownership_id))
        holding_ids.append(holding_id)
        evidence_ids.extend((fair_value_id, ownership_id))

    if len(holding_ids) != len(set(holding_ids)):
        return [], "holding_double_count_detected"
    if len(evidence_ids) != len(set(evidence_ids)):
        return [], "holding_double_count_detected"
    return holdings, None


def _cross_role_evidence_reason(
    metric_inputs: Mapping[str, object],
    holdings: Sequence[tuple[str, str, str]],
) -> str | None:
    evidence_ids = [
        evidence_id
        for _, fair_value_id, ownership_id in holdings
        for evidence_id in (fair_value_id, ownership_id)
    ]
    evidence_ids.extend(
        evidence_id
        for key in (
            "parent_net_debt",
            "other_adjustments",
            "parent_shares_outstanding",
        )
        if (evidence_id := _input_id(metric_inputs, key)) is not None
    )
    if len(evidence_ids) != len(set(evidence_ids)):
        return "holding_double_count_detected"
    return None


def _point_in_time_reason(
    records: Sequence[EvidenceRecord | MarketPriceRecord],
) -> str | None:
    iterator = iter(records)
    reference = next(iterator, None)
    if reference is None:
        return None
    reference_as_of = (
        reference.as_of
        if isinstance(reference, EvidenceRecord)
        else reference.price_timestamp
    )
    reference_period = (
        (reference.period_start, reference.period_end)
        if isinstance(reference, EvidenceRecord)
        else None
    )
    for record in iterator:
        record_as_of = (
            record.as_of if isinstance(record, EvidenceRecord) else record.price_timestamp
        )
        record_period = (
            (record.period_start, record.period_end)
            if isinstance(record, EvidenceRecord)
            else None
        )
        if record_as_of != reference_as_of:
            return "point_in_time_mismatch"
        if (
            isinstance(reference, EvidenceRecord)
            and isinstance(record, EvidenceRecord)
            and record_period != reference_period
        ):
            return "point_in_time_mismatch"
    return None


def _monetary_reason(
    record: EvidenceRecord,
    valuation_currency: str | None,
) -> str | None:
    if _normalized(record.currency) != valuation_currency:
        return "currency_mismatch"
    if _normalized(record.unit) != valuation_currency:
        return "unit_mismatch"
    return None


def _ratio_reason(record: EvidenceRecord) -> str | None:
    if _normalized(record.currency) != "RATIO":
        return "currency_mismatch"
    if _normalized(record.unit) != "RATIO":
        return "unit_mismatch"
    if record.value is None or record.value < 0 or record.value > 1:
        return "holding_ownership_ratio_invalid"
    return None


def _shares_reason(
    record: EvidenceRecord,
) -> str | None:
    if _normalized(record.currency) != "SHARES":
        return "currency_mismatch"
    if _normalized(record.unit) != "SHARES":
        return "unit_mismatch"
    if record.value is None or record.value <= 0:
        return "parent_shares_invalid"
    return None


def _market_price(
    market_price_records: Sequence[MarketPriceRecord],
    market_by_id: Mapping[str, MarketPriceRecord],
    duplicate_ids: frozenset[str],
    profile_as_of: datetime | None,
    valuation_currency: str | None,
) -> tuple[MarketPriceRecord | None, str | None]:
    if not market_price_records:
        return None, "market_price_missing"
    if len(market_price_records) != 1:
        if any(record.evidence_id in duplicate_ids for record in market_price_records):
            return None, "duplicate_evidence_id"
        for record in market_price_records:
            reason = _market_status(record, profile_as_of)
            if reason is not None:
                return None, reason
        return None, "market_price_missing"

    record = next(iter(market_price_records), None)
    if record is None:
        return None, "market_price_missing"
    if record.evidence_id in duplicate_ids:
        return None, "duplicate_evidence_id"
    if market_by_id.get(record.evidence_id) is None:
        return None, "unvalidated_evidence_id"
    reason = _market_status(record, profile_as_of)
    if reason is not None:
        return None, reason
    if _normalized(record.currency) != valuation_currency:
        return None, "currency_mismatch"
    return record, None


def evaluate_holding_company_profile(
    profile_input: Mapping[str, object],
    evidence_records: Sequence[EvidenceRecord],
    market_price_records: Sequence[MarketPriceRecord] = (),
) -> tuple[
    dict[str, Decimal | None],
    tuple[PolicyDecision, ...],
    tuple[CalculationRecord, ...],
]:
    values: dict[str, Decimal | None] = {
        metric_id: None for metric_id in HOLDING_COMPANY_METRIC_IDS
    }
    profile = _mapping(profile_input) or {}
    metric_inputs = _metric_inputs(profile)
    profile_as_of = _profile_as_of(profile)
    evidence = _evidence_items(evidence_records)
    market_records = _market_items(market_price_records)
    evidence_by_id, market_by_id, duplicate_ids = _source_indexes(
        evidence,
        market_records,
    )
    evidence_status = {
        record.evidence_id: reason
        for record in evidence
        if (reason := _evidence_status(record, profile_as_of)) is not None
    }
    valuation_currency = _normalized(profile.get("valuation_currency"))
    calculations: list[CalculationRecord] = []
    decisions: dict[str, PolicyDecision] = {}

    holdings, holding_reason = _holding_inputs(metric_inputs)
    holding_records: list[tuple[EvidenceRecord, EvidenceRecord]] = []
    holding_flat_records: list[EvidenceRecord] = []
    holding_evidence_ids: list[str] = []
    nav_records_for_discount: list[EvidenceRecord | MarketPriceRecord] = []
    cross_role_reason: str | None = None

    if holding_reason is None:
        cross_role_reason = _cross_role_evidence_reason(metric_inputs, holdings)
        if cross_role_reason is not None:
            holding_reason = cross_role_reason

    if holding_reason is None:
        for _, fair_value_id, ownership_id in holdings:
            fair_value, reason = _resolve_evidence(
                fair_value_id,
                evidence_by_id,
                duplicate_ids,
                evidence_status,
                "holding_fair_value_missing",
            )
            if fair_value is None:
                holding_reason = reason or "holding_fair_value_missing"
                break
            ownership, reason = _resolve_evidence(
                ownership_id,
                evidence_by_id,
                duplicate_ids,
                evidence_status,
                "holding_ownership_ratio_missing",
            )
            if ownership is None:
                holding_reason = reason or "holding_ownership_ratio_missing"
                break
            fair_reason = _monetary_reason(fair_value, valuation_currency)
            ownership_reason = _ratio_reason(ownership)
            if fair_reason is not None:
                holding_reason = fair_reason
                break
            if ownership_reason is not None:
                holding_reason = ownership_reason
                break
            holding_records.append((fair_value, ownership))
            holding_flat_records.extend((fair_value, ownership))
            holding_evidence_ids.extend((fair_value.evidence_id, ownership.evidence_id))

        if holding_reason is None:
            holding_reason = _point_in_time_reason(holding_flat_records)

    attributable_holdings_value = Decimal("0")
    first_component_value: Decimal | None = None
    if holding_reason is None:
        try:
            with localcontext(_FIXED_DECIMAL_CONTEXT):
                for fair_value, ownership in holding_records:
                    if fair_value.value is not None and ownership.value is not None:
                        component_value = fair_value.value * ownership.value
                        if first_component_value is None:
                            first_component_value = component_value
                        attributable_holdings_value += component_value
        except DecimalException:
            holding_reason = _DECIMAL_ARITHMETIC_FAILURE_REASON
    if holding_reason is None and (
        first_component_value is None
        or not first_component_value.is_finite()
        or not attributable_holdings_value.is_finite()
    ):
        holding_reason = _DECIMAL_ARITHMETIC_FAILURE_REASON

    if holding_reason is not None:
        core_status = _failure_status(holding_reason)
        decisions["attributable_holdings_value"] = _decision(
            "attributable_holdings_value",
            core_status,
            holding_reason,
            True,
        )
        decisions["holding_company_nav"] = _decision(
            "holding_company_nav",
            core_status,
            holding_reason,
            True,
        )
    else:
        first_pair = next(iter(holding_records), None)
        if first_pair is not None and first_component_value is not None:
            first_fair_value, first_ownership = first_pair
            calculations.append(
                _calculation(
                    "calc_holding_attributable_component_value_v1",
                    "holding-attributable-component-value-v1",
                    [first_fair_value, first_ownership],
                    first_component_value,
                    valuation_currency or first_fair_value.unit,
                )
            )
        attributable_calculation = _calculation(
            "calc_holding_attributable_holdings_value_v1",
            "holding-attributable-holdings-value-v1",
            holding_flat_records,
            attributable_holdings_value,
            valuation_currency or "USD",
        )
        calculations.append(attributable_calculation)
        values["attributable_holdings_value"] = attributable_holdings_value
        decisions["attributable_holdings_value"] = _decision(
            "attributable_holdings_value",
            "available",
            "calculated",
            True,
            evidence_ids=holding_evidence_ids,
            calculation_ids=[attributable_calculation.calculation_id],
        )

        parent_net_debt, parent_reason = _resolve_evidence(
            _input_id(metric_inputs, "parent_net_debt"),
            evidence_by_id,
            duplicate_ids,
            evidence_status,
            "parent_net_debt_missing",
        )
        other_adjustments, adjustment_reason = _resolve_evidence(
            _input_id(metric_inputs, "other_adjustments"),
            evidence_by_id,
            duplicate_ids,
            evidence_status,
            "other_adjustments_missing",
        )
        nav_reason = parent_reason or adjustment_reason
        if nav_reason is None and parent_net_debt is not None and other_adjustments is not None:
            nav_reason = _monetary_reason(parent_net_debt, valuation_currency)
            if nav_reason is None:
                nav_reason = _monetary_reason(other_adjustments, valuation_currency)
            if nav_reason is None:
                nav_reason = _point_in_time_reason(
                    [
                        *holding_flat_records,
                        parent_net_debt,
                        other_adjustments,
                    ]
                )

        if nav_reason is not None or parent_net_debt is None or other_adjustments is None:
            final_nav_reason = nav_reason or "holding_company_nav_missing"
            decisions["holding_company_nav"] = _decision(
                "holding_company_nav",
                _failure_status(final_nav_reason),
                final_nav_reason,
                True,
            )
        else:
            final_nav_reason: str | None = None
            try:
                with localcontext(_FIXED_DECIMAL_CONTEXT):
                    holding_company_nav = (
                        attributable_holdings_value
                        - parent_net_debt.value
                        + other_adjustments.value
                    )
            except DecimalException:
                final_nav_reason = _DECIMAL_ARITHMETIC_FAILURE_REASON

            if (
                final_nav_reason is None
                and not holding_company_nav.is_finite()
            ):
                final_nav_reason = _DECIMAL_ARITHMETIC_FAILURE_REASON

            if final_nav_reason is not None:
                decisions["holding_company_nav"] = _decision(
                    "holding_company_nav",
                    "unavailable",
                    final_nav_reason,
                    True,
                )
            else:
                nav_records = [
                    *holding_flat_records,
                    parent_net_debt,
                    other_adjustments,
                ]
                nav_records_for_discount = nav_records
                nav_calculation = _calculation(
                    "calc_holding_company_nav_v1",
                    "holding-company-nav-v1",
                    nav_records,
                    holding_company_nav,
                    valuation_currency or "USD",
                )
                calculations.append(nav_calculation)
                values["holding_company_nav"] = holding_company_nav
                nav_evidence_ids = [
                    *holding_evidence_ids,
                    parent_net_debt.evidence_id,
                    other_adjustments.evidence_id,
                ]
                decisions["holding_company_nav"] = _decision(
                    "holding_company_nav",
                    "available",
                    "calculated",
                    True,
                    evidence_ids=nav_evidence_ids,
                    calculation_ids=[nav_calculation.calculation_id],
                )

    parent_shares, shares_reason = _resolve_evidence(
        _input_id(metric_inputs, "parent_shares_outstanding"),
        evidence_by_id,
        duplicate_ids,
        evidence_status,
        "parent_shares_missing",
    )
    if shares_reason is None and parent_shares is not None:
        shares_reason = _shares_reason(parent_shares)

    market_price, market_reason = _market_price(
        market_records,
        market_by_id,
        duplicate_ids,
        profile_as_of,
        valuation_currency,
    )
    if cross_role_reason is not None:
        market_cap_reason = cross_role_reason
    elif shares_reason is not None:
        market_cap_reason = shares_reason
    elif market_reason is not None:
        market_cap_reason = market_reason
    elif parent_shares is None or market_price is None:
        market_cap_reason = "market_price_missing"
    elif holding_records:
        market_cap_reason = _point_in_time_reason([*holding_flat_records, parent_shares, market_price])
    else:
        market_cap_reason = None

    if market_cap_reason is None:
        try:
            with localcontext(_FIXED_DECIMAL_CONTEXT):
                holding_company_market_cap = market_price.price * parent_shares.value
        except DecimalException:
            market_cap_reason = _DECIMAL_ARITHMETIC_FAILURE_REASON
        else:
            if not holding_company_market_cap.is_finite():
                market_cap_reason = _DECIMAL_ARITHMETIC_FAILURE_REASON

    if market_cap_reason is not None:
        decisions["holding_company_market_cap"] = _decision(
            "holding_company_market_cap",
            _failure_status(market_cap_reason),
            market_cap_reason,
            False,
        )
    else:
        market_cap_calculation = _calculation(
            "calc_holding_company_market_cap_v1",
            "holding-company-market-cap-v1",
            [market_price, parent_shares],
            holding_company_market_cap,
            valuation_currency or "USD",
        )
        calculations.append(market_cap_calculation)
        values["holding_company_market_cap"] = holding_company_market_cap
        decisions["holding_company_market_cap"] = _decision(
            "holding_company_market_cap",
            "available",
            "calculated",
            False,
            evidence_ids=[market_price.evidence_id, parent_shares.evidence_id],
            calculation_ids=[market_cap_calculation.calculation_id],
        )

    nav_decision = decisions.get("holding_company_nav")
    nav_value = values.get("holding_company_nav")
    market_cap_value = values.get("holding_company_market_cap")
    discount_reason: str | None = None
    if nav_decision is None or nav_decision.status != "available" or nav_value is None:
        discount_reason = (
            nav_decision.reason_code
            if nav_decision is not None
            else "holding_company_nav_missing"
        )
    elif market_cap_value is None:
        discount_reason = market_cap_reason or "market_price_missing"
    elif nav_value <= 0:
        discount_reason = "holding_company_nav_non_positive"

    if discount_reason is not None:
        decisions["holding_company_nav_discount"] = _decision(
            "holding_company_nav_discount",
            _failure_status(discount_reason),
            discount_reason,
            False,
        )
    else:
        nav_records = [*nav_records_for_discount, market_price, parent_shares]
        try:
            with localcontext(_FIXED_DECIMAL_CONTEXT):
                holding_company_nav_discount = (nav_value - market_cap_value) / nav_value
        except DecimalException:
            discount_reason = _DECIMAL_ARITHMETIC_FAILURE_REASON
        else:
            if not holding_company_nav_discount.is_finite():
                discount_reason = _DECIMAL_ARITHMETIC_FAILURE_REASON
            else:
                discount_calculation = _calculation(
                    "calc_holding_company_nav_discount_v1",
                    "holding-company-nav-discount-v1",
                    nav_records,
                    holding_company_nav_discount,
                    "ratio",
                )
                calculations.append(discount_calculation)
                values["holding_company_nav_discount"] = holding_company_nav_discount
                decisions["holding_company_nav_discount"] = _decision(
                    "holding_company_nav_discount",
                    "available",
                    "calculated",
                    False,
                    evidence_ids=[record.evidence_id for record in nav_records],
                    calculation_ids=[discount_calculation.calculation_id],
                )

    if discount_reason is not None and "holding_company_nav_discount" not in decisions:
        decisions["holding_company_nav_discount"] = _decision(
            "holding_company_nav_discount",
            _failure_status(discount_reason),
            discount_reason,
            False,
        )

    not_applicable = {
        "pe_ratio": "holding_company_pe_not_applicable",
        "fcf_yield": "holding_company_fcf_not_applicable",
        "historical_valuation": "holding_company_historical_valuation_not_applicable",
        "reverse_dcf": "holding_company_reverse_dcf_not_applicable",
    }
    for metric_id, reason_code in not_applicable.items():
        decisions[metric_id] = _decision(
            metric_id,
            "not_applicable",
            reason_code,
            False,
        )

    return (
        values,
        tuple(
            decisions.get(
                metric_id,
                _decision(metric_id, "unavailable", "holding_components_missing", metric_id in {
                    "attributable_holdings_value",
                    "holding_company_nav",
                }),
            )
            for metric_id in HOLDING_COMPANY_METRIC_IDS
        ),
        tuple(calculations),
    )


__all__ = [
    "HOLDING_COMPANY_METRIC_IDS",
    "POLICY_VERSION",
    "PROFILE_VERSION",
    "evaluate_holding_company_profile",
]
