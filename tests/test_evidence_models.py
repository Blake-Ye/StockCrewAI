from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json

from pydantic import ValidationError
import pytest

from stockcrewai.models.evidence import (
    CalculationRecord,
    ClaimRecord,
    EvidenceRecord,
    MarketPriceRecord,
    ValidationStatus,
)


AS_OF = datetime(2026, 8, 10, 1, 2, 3, tzinfo=timezone.utc)
FILED_AT = date(2026, 8, 9)
PERIOD_START = date(2025, 1, 1)
PERIOD_END = date(2025, 12, 31)


def evidence_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "evidence_id": "ev_revenue_2025",
        "source_reference": "fixture:sec-revenue-2025",
        "as_of": AS_OF,
        "filed_at": FILED_AT,
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "unit": "USD",
        "currency": "USD",
        "value": Decimal("123456789.012300"),
        "validation_status": "valid",
    }
    payload.update(overrides)
    return payload


def calculation_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "calculation_id": "calc_revenue_growth_2025",
        "formula_id": "revenue_growth:v1",
        "input_evidence_ids": ["ev_revenue_2025", "ev_revenue_2024"],
        "source_reference": "fixture:calculation-revenue-growth-2025",
        "as_of": AS_OF,
        "result": Decimal("0.12345678901234567890"),
        "unit": "ratio",
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "validation_status": "valid",
    }
    payload.update(overrides)
    return payload


def market_price_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "evidence_id": "price_aapl_20260810_010203",
        "ticker": "AAPL",
        "price": Decimal("219.750000000000000001"),
        "currency": "USD",
        "price_timestamp": AS_OF,
        "source_reference": "fixture:market-price",
        "adjustment_basis": "raw",
        "validation_status": "valid",
    }
    payload.update(overrides)
    return payload


def test_evidence_record_accepts_json_strings_and_preserves_decimal_precision() -> None:
    record = EvidenceRecord.model_validate(
        {
            **evidence_payload(),
            "as_of": "2026-08-10T01:02:03Z",
            "filed_at": "2026-08-09",
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
            "value": "123456789.012300",
        }
    )

    dumped = record.model_dump(mode="json")
    json.dumps(dumped, allow_nan=False)
    assert dumped["value"] == "123456789.012300"
    assert dumped["as_of"] == "2026-08-10T01:02:03Z"
    assert isinstance(record.value, Decimal)


@pytest.mark.parametrize(
    "field",
    [
        "evidence_id",
        "source_reference",
        "as_of",
        "filed_at",
        "period_start",
        "period_end",
        "validation_status",
    ],
)
def test_evidence_record_rejects_missing_provenance_time_or_validation(field: str) -> None:
    payload = evidence_payload()
    del payload[field]

    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(payload)


def test_evidence_record_rejects_extra_fields_and_blank_ids_or_sources() -> None:
    for field in ("evidence_id", "source_reference"):
        payload = evidence_payload(**{field: "  "})
        with pytest.raises(ValidationError):
            EvidenceRecord.model_validate(payload)

    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate({**evidence_payload(), "unexpected": "no"})


def test_unvalidated_evidence_may_have_null_value_but_valid_evidence_may_not() -> None:
    raw_record = EvidenceRecord.model_validate(
        evidence_payload(value=None, validation_status=ValidationStatus.UNVALIDATED)
    )
    assert raw_record.value is None

    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(evidence_payload(value=None))


@pytest.mark.parametrize("value", [0.1, float("nan"), float("inf"), Decimal("NaN")])
def test_evidence_record_rejects_binary_float_and_nonfinite_values(value: object) -> None:
    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(evidence_payload(value=value))


def test_calculation_record_accepts_ids_and_json_safe_decimal() -> None:
    record = CalculationRecord.model_validate(calculation_payload())

    dumped = record.model_dump(mode="json")
    json.dumps(dumped, allow_nan=False)
    assert dumped["result"] == "0.12345678901234567890"
    assert dumped["input_evidence_ids"] == ["ev_revenue_2025", "ev_revenue_2024"]
    assert dumped["source_reference"] == "fixture:calculation-revenue-growth-2025"
    assert dumped["as_of"] == "2026-08-10T01:02:03Z"


@pytest.mark.parametrize("field", ["source_reference", "as_of"])
def test_calculation_record_requires_source_reference_and_as_of(field: str) -> None:
    payload = calculation_payload()
    del payload[field]
    payload.pop("source_reference", None)
    payload.pop("as_of", None)

    with pytest.raises(ValidationError):
        CalculationRecord.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_reference", "  "),
        ("as_of", datetime(2026, 8, 10, 1, 2, 3)),
    ],
)
def test_calculation_record_rejects_blank_source_and_naive_as_of(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        CalculationRecord.model_validate(calculation_payload(**{field: value}))


@pytest.mark.parametrize(
    "input_ids",
    [[], ["ev_valid", "  "], ["ev_valid", ""]],
)
def test_calculation_record_requires_nonempty_input_evidence_ids(
    input_ids: list[str],
) -> None:
    with pytest.raises(ValidationError):
        CalculationRecord.model_validate(
            calculation_payload(input_evidence_ids=input_ids)
        )


@pytest.mark.parametrize("value", [0.1, float("nan"), float("inf"), Decimal("Infinity")])
def test_calculation_record_rejects_binary_float_and_nonfinite_results(value: object) -> None:
    with pytest.raises(ValidationError):
        CalculationRecord.model_validate(calculation_payload(result=value))


def test_unvalidated_calculation_may_have_null_result_but_valid_calculation_may_not() -> None:
    raw_record = CalculationRecord.model_validate(
        calculation_payload(result=None, validation_status="unvalidated")
    )
    assert raw_record.result is None

    with pytest.raises(ValidationError):
        CalculationRecord.model_validate(calculation_payload(result=None))


def test_claim_record_requires_nonempty_provenance_ids_and_bounded_confidence() -> None:
    claim = ClaimRecord.model_validate(
        {
            "claim_id": "claim_revenue_growth",
            "category": "financial_trend",
            "statement": "Revenue grew year over year.",
            "evidence_ids": ["ev_revenue_2025"],
            "calculation_ids": ["calc_revenue_growth_2025"],
            "confidence": 0.8,
        }
    )
    assert claim.model_dump(mode="json")["confidence"] == 0.8

    for field in ("evidence_ids", "calculation_ids"):
        with pytest.raises(ValidationError):
            ClaimRecord.model_validate(
                {
                    "claim_id": "claim_revenue_growth",
                    "category": "financial_trend",
                    "statement": "Revenue grew year over year.",
                    "evidence_ids": ["ev_revenue_2025"],
                    "calculation_ids": ["calc_revenue_growth_2025"],
                    "confidence": 0.8,
                    field: ["  "],
                }
            )

    for confidence in (-0.1, 1.1, "0.8", True):
        with pytest.raises(ValidationError):
            ClaimRecord.model_validate(
                {
                    "claim_id": "claim_revenue_growth",
                    "category": "financial_trend",
                    "statement": "Revenue grew year over year.",
                    "evidence_ids": ["ev_revenue_2025"],
                    "calculation_ids": ["calc_revenue_growth_2025"],
                    "confidence": confidence,
                }
            )


def test_claim_record_does_not_claim_authoritative_provenance() -> None:
    payload = {
        "claim_id": "claim_revenue_growth",
        "category": "financial_trend",
        "statement": "Revenue grew year over year.",
        "evidence_ids": ["ev_revenue_2025"],
        "calculation_ids": ["calc_revenue_growth_2025"],
        "confidence": 0.8,
    }

    claim = ClaimRecord.model_validate(payload)
    assert set(type(claim).model_fields) == {
        "claim_id",
        "category",
        "statement",
        "evidence_ids",
        "calculation_ids",
        "confidence",
    }

    for field, value in (
        ("source_reference", "fixture:claim-source"),
        ("as_of", AS_OF),
        ("validation_status", "valid"),
    ):
        with pytest.raises(ValidationError):
            ClaimRecord.model_validate({**payload, field: value})


def test_market_price_record_requires_aware_timestamp_positive_price_and_json_precision() -> None:
    record = MarketPriceRecord.model_validate(market_price_payload())

    dumped = record.model_dump(mode="json")
    json.dumps(dumped, allow_nan=False)
    assert dumped["evidence_id"] == "price_aapl_20260810_010203"
    assert dumped["price"] == "219.750000000000000001"
    assert dumped["price_timestamp"] == "2026-08-10T01:02:03Z"

    with pytest.raises(ValidationError):
        MarketPriceRecord.model_validate(
            market_price_payload(price_timestamp=datetime(2026, 8, 10, 1, 2, 3))
        )
    with pytest.raises(ValidationError):
        MarketPriceRecord.model_validate(market_price_payload(price=Decimal("0")))


@pytest.mark.parametrize("price", [0.1, float("nan"), float("inf"), Decimal("Infinity")])
def test_market_price_record_rejects_binary_float_and_nonfinite_price(price: object) -> None:
    with pytest.raises(ValidationError):
        MarketPriceRecord.model_validate(market_price_payload(price=price))


@pytest.mark.parametrize(
    "field",
    [
        "evidence_id",
        "price",
        "price_timestamp",
        "currency",
        "source_reference",
        "validation_status",
    ],
)
def test_market_price_record_rejects_missing_required_fields(field: str) -> None:
    payload = market_price_payload()
    del payload[field]

    with pytest.raises(ValidationError):
        MarketPriceRecord.model_validate(payload)


def test_market_price_record_rejects_blank_stable_evidence_id() -> None:
    with pytest.raises(ValidationError):
        MarketPriceRecord.model_validate(
            market_price_payload(evidence_id="  ")
        )


def test_evidence_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CalculationRecord.model_validate({**calculation_payload(), "unexpected": "no"})
    with pytest.raises(ValidationError):
        ClaimRecord.model_validate(
            {
                "claim_id": "claim_1",
                "category": "risk",
                "statement": "Risk is documented.",
                "evidence_ids": ["ev_1"],
                "calculation_ids": [],
                "confidence": 0.5,
                "unexpected": "no",
            }
        )
    with pytest.raises(ValidationError):
        MarketPriceRecord.model_validate({**market_price_payload(), "unexpected": "no"})
