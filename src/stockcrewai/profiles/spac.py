from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Literal, cast

from stockcrewai.models.evidence import (
    CalculationRecord,
    EvidenceRecord,
    MarketPriceRecord,
    ValidationStatus,
)
from stockcrewai.models.policy import PolicyDecision


PROFILE_VERSION = "spac-profile:v1"
POLICY_VERSION = "metric-policy:spac:v1"
SPAC_METRIC_IDS = (
    "spac_trust_cash",
    "spac_warrant_dilution_ratio",
    "spac_pro_forma_shares",
    "spac_cash_per_pro_forma_share",
)

_FORMULAS = {
    "spac_warrant_dilution_ratio": "spac-warrant-dilution-ratio-v1",
    "spac_pro_forma_shares": "spac-pro-forma-shares-v1",
    "spac_cash_per_pro_forma_share": "spac-cash-per-pro-forma-share-v1",
}


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _profile_as_of(profile_input: Mapping[str, object]) -> datetime | None:
    value = profile_input.get("as_of")
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


def _metric_inputs(profile_input: Mapping[str, object]) -> Mapping[str, object]:
    return _mapping(profile_input.get("metric_inputs"))


def _input_evidence_id(metric_inputs: Mapping[str, object], key: str) -> str | None:
    envelope = metric_inputs.get(key)
    if not isinstance(envelope, Mapping) or set(envelope) != {"evidence_id"}:
        return None
    evidence_id = envelope.get("evidence_id")
    if isinstance(evidence_id, str) and evidence_id.strip():
        return evidence_id
    return None


def _evidence_values(
    evidence_records: Sequence[EvidenceRecord],
) -> tuple[dict[str, EvidenceRecord], frozenset[str]]:
    records = tuple(record for record in evidence_records if isinstance(record, EvidenceRecord))
    counts = Counter(record.evidence_id for record in records)
    duplicate_ids = frozenset(
        evidence_id for evidence_id, count in counts.items() if count != 1
    )
    return (
        {
            record.evidence_id: record
            for record in records
            if record.evidence_id not in duplicate_ids
        },
        duplicate_ids,
    )


def _finite_decimal(value: object) -> Decimal | None:
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


def _evidence_status(
    record: EvidenceRecord,
    profile_as_of: datetime,
) -> str:
    if record.validation_status is not ValidationStatus.VALID:
        return "unvalidated_evidence_id"
    if _finite_decimal(record.value) is None:
        return "unvalidated_evidence_id"
    try:
        if record.filed_at > profile_as_of.date() or record.as_of > profile_as_of:
            return "filed_after_as_of"
    except (AttributeError, TypeError):
        return "unvalidated_evidence_id"
    return "available"


def _resolve_evidence(
    metric_inputs: Mapping[str, object],
    key: str,
    domain: Literal["cash", "shares"],
    evidence_by_id: Mapping[str, EvidenceRecord],
    duplicate_ids: frozenset[str],
    evidence_status: Mapping[str, str],
) -> tuple[EvidenceRecord | None, str]:
    if key not in metric_inputs:
        return None, "missing_input"
    evidence_id = _input_evidence_id(metric_inputs, key)
    if evidence_id is None:
        return None, "missing_input"
    if evidence_id in duplicate_ids:
        return None, "duplicate_evidence_id"
    record = evidence_by_id.get(evidence_id)
    if record is None:
        return None, "unvalidated_evidence_id"
    reason = evidence_status.get(evidence_id, "unvalidated_evidence_id")
    if reason != "available":
        return None, reason

    value = _finite_decimal(record.value)
    unit = record.unit.strip().casefold()
    currency = record.currency.strip().casefold()
    if domain == "cash":
        if unit != "usd" or currency != "usd":
            return None, "unit_mismatch"
        if value is None or value <= 0:
            return None, "spac_trust_cash_invalid"
    elif unit not in {"share", "shares"} or currency not in {"share", "shares"}:
        return None, "unit_mismatch"
    elif value is None or value <= 0:
        return None, "zero_denominator"
    return record, ""


def _divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        return numerator / denominator


def _add(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        return left + right


def _decision(
    metric_id: str,
    status: Literal["available", "unavailable", "not_applicable", "invalid"],
    reason_code: str,
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
        blocking=False,
    )


def _calculation(
    formula_id: str,
    records: Sequence[EvidenceRecord],
    result: Decimal,
    unit: str,
) -> CalculationRecord:
    return CalculationRecord(
        calculation_id=f"calc_{formula_id.replace('-', '_')}",
        formula_id=formula_id,
        input_evidence_ids=[record.evidence_id for record in records],
        source_reference=f"derived:{formula_id}",
        as_of=max(record.as_of for record in records),
        result=result,
        unit=unit,
        period_start=min(record.period_start for record in records),
        period_end=max(record.period_end for record in records),
        validation_status=ValidationStatus.VALID,
    )


def _unavailable(
    decisions: dict[str, PolicyDecision],
    metric_id: str,
    reason_code: str,
) -> None:
    decisions[metric_id] = _decision(metric_id, "unavailable", reason_code)


def evaluate_spac_profile(
    profile_input: Mapping[str, object],
    evidence_records: Sequence[EvidenceRecord],
    market_price_records: Sequence[MarketPriceRecord] = (),
) -> tuple[
    dict[str, Decimal | None],
    tuple[PolicyDecision, ...],
    tuple[CalculationRecord, ...],
]:
    del market_price_records
    values: dict[str, Decimal | None] = {
        metric_id: None for metric_id in SPAC_METRIC_IDS
    }
    decisions: dict[str, PolicyDecision] = {}
    calculations: list[CalculationRecord] = []
    profile = _mapping(profile_input)

    if profile.get("security_profile") != "spac":
        for metric_id in SPAC_METRIC_IDS:
            _unavailable(decisions, metric_id, "spac_security_profile_invalid")
        return values, tuple(decisions[metric_id] for metric_id in SPAC_METRIC_IDS), ()
    if profile.get("transaction_status") not in {"pre_merger", "post_merger"}:
        for metric_id in SPAC_METRIC_IDS:
            _unavailable(decisions, metric_id, "spac_transaction_status_invalid")
        return values, tuple(decisions[metric_id] for metric_id in SPAC_METRIC_IDS), ()

    profile_as_of = _profile_as_of(profile)
    if profile_as_of is None:
        for metric_id in SPAC_METRIC_IDS:
            _unavailable(decisions, metric_id, "missing_input")
        return values, tuple(decisions[metric_id] for metric_id in SPAC_METRIC_IDS), ()

    metric_inputs = _metric_inputs(profile)
    evidence_by_id, duplicate_ids = _evidence_values(evidence_records)
    evidence_status = {
        record_id: _evidence_status(record, profile_as_of)
        for record_id, record in evidence_by_id.items()
    }

    trust_cash, trust_cash_reason = _resolve_evidence(
        metric_inputs,
        "trust_cash",
        "cash",
        evidence_by_id,
        duplicate_ids,
        evidence_status,
    )
    basic_shares, basic_shares_reason = _resolve_evidence(
        metric_inputs,
        "basic_shares",
        "shares",
        evidence_by_id,
        duplicate_ids,
        evidence_status,
    )
    warrants, warrants_reason = _resolve_evidence(
        metric_inputs,
        "warrants_outstanding",
        "shares",
        evidence_by_id,
        duplicate_ids,
        evidence_status,
    )

    if trust_cash is None:
        _unavailable(decisions, "spac_trust_cash", trust_cash_reason)
    else:
        values["spac_trust_cash"] = _finite_decimal(trust_cash.value)
        decisions["spac_trust_cash"] = _decision(
            "spac_trust_cash",
            "available",
            "validated_evidence",
            evidence_ids=[trust_cash.evidence_id],
        )

    pro_forma_shares: Decimal | None = None
    if warrants is None:
        _unavailable(
            decisions,
            "spac_warrant_dilution_ratio",
            warrants_reason,
        )
    elif basic_shares is None:
        _unavailable(
            decisions,
            "spac_warrant_dilution_ratio",
            basic_shares_reason,
        )
    else:
        try:
            ratio = _divide(
                cast(Decimal, _finite_decimal(warrants.value)),
                cast(Decimal, _finite_decimal(basic_shares.value)),
            )
            calculation = _calculation(
                _FORMULAS["spac_warrant_dilution_ratio"],
                [warrants, basic_shares],
                ratio,
                "ratio",
            )
        except (ArithmeticError, TypeError, ValueError):
            _unavailable(
                decisions,
                "spac_warrant_dilution_ratio",
                "zero_denominator",
            )
        else:
            calculations.append(calculation)
            values["spac_warrant_dilution_ratio"] = ratio
            decisions["spac_warrant_dilution_ratio"] = _decision(
                "spac_warrant_dilution_ratio",
                "available",
                "calculated",
                evidence_ids=[warrants.evidence_id, basic_shares.evidence_id],
                calculation_ids=[calculation.calculation_id],
            )

    if basic_shares is None:
        _unavailable(decisions, "spac_pro_forma_shares", basic_shares_reason)
    elif warrants is None:
        _unavailable(decisions, "spac_pro_forma_shares", warrants_reason)
    else:
        try:
            pro_forma_shares = _add(
                cast(Decimal, _finite_decimal(basic_shares.value)),
                cast(Decimal, _finite_decimal(warrants.value)),
            )
            calculation = _calculation(
                _FORMULAS["spac_pro_forma_shares"],
                [basic_shares, warrants],
                pro_forma_shares,
                "shares",
            )
        except (ArithmeticError, TypeError, ValueError):
            pro_forma_shares = None
            _unavailable(
                decisions,
                "spac_pro_forma_shares",
                "zero_denominator",
            )
        else:
            calculations.append(calculation)
            values["spac_pro_forma_shares"] = pro_forma_shares
            decisions["spac_pro_forma_shares"] = _decision(
                "spac_pro_forma_shares",
                "available",
                "calculated",
                evidence_ids=[basic_shares.evidence_id, warrants.evidence_id],
                calculation_ids=[calculation.calculation_id],
            )

    if trust_cash is None:
        _unavailable(
            decisions,
            "spac_cash_per_pro_forma_share",
            trust_cash_reason,
        )
    elif basic_shares is None:
        _unavailable(
            decisions,
            "spac_cash_per_pro_forma_share",
            basic_shares_reason,
        )
    elif warrants is None:
        _unavailable(
            decisions,
            "spac_cash_per_pro_forma_share",
            warrants_reason,
        )
    elif pro_forma_shares is None or pro_forma_shares <= 0:
        _unavailable(
            decisions,
            "spac_cash_per_pro_forma_share",
            "zero_denominator",
        )
    else:
        try:
            cash_per_share = _divide(
                cast(Decimal, _finite_decimal(trust_cash.value)),
                pro_forma_shares,
            )
            calculation = _calculation(
                _FORMULAS["spac_cash_per_pro_forma_share"],
                [trust_cash, basic_shares, warrants],
                cash_per_share,
                "USD/share",
            )
        except (ArithmeticError, TypeError, ValueError):
            _unavailable(
                decisions,
                "spac_cash_per_pro_forma_share",
                "zero_denominator",
            )
        else:
            calculations.append(calculation)
            values["spac_cash_per_pro_forma_share"] = cash_per_share
            decisions["spac_cash_per_pro_forma_share"] = _decision(
                "spac_cash_per_pro_forma_share",
                "available",
                "calculated",
                evidence_ids=[
                    trust_cash.evidence_id,
                    basic_shares.evidence_id,
                    warrants.evidence_id,
                ],
                calculation_ids=[calculation.calculation_id],
            )

    return (
        values,
        tuple(decisions[metric_id] for metric_id in SPAC_METRIC_IDS),
        tuple(calculations),
    )


__all__ = [
    "POLICY_VERSION",
    "PROFILE_VERSION",
    "SPAC_METRIC_IDS",
    "evaluate_spac_profile",
]
