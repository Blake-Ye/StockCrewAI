from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictFloat,
    StringConstraints,
    field_validator,
    model_validator,
)


_NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class ValidationStatus(str, Enum):
    """权威记录的验证状态；不执行验证、Gate 或 LLM 判断。"""

    UNVALIDATED = "unvalidated"
    VALID = "valid"
    INVALID = "invalid"


def _finite_decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("金融数值必须使用 Decimal、整数或十进制字符串，不能使用 Python float")
    if isinstance(value, Decimal):
        result = value
    else:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("金融数值格式无效") from exc
    if not result.is_finite():
        raise ValueError("金融数值必须是有限值")
    return result


def _finite_decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return _finite_decimal(value)


_FiniteDecimal = Annotated[Decimal, BeforeValidator(_finite_decimal)]
_OptionalFiniteDecimal = Annotated[
    Decimal | None,
    BeforeValidator(_finite_decimal_or_none),
]


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间戳必须带时区")
    return value


class EvidenceRecord(BaseModel):
    """可审计的原始证据记录；不负责采集、计算或决定证据是否可信。"""

    model_config = ConfigDict(extra="forbid")

    evidence_id: _NonEmptyString
    source_reference: _NonEmptyString
    as_of: datetime
    filed_at: date
    period_start: date
    period_end: date
    unit: _NonEmptyString
    currency: _NonEmptyString
    value: _OptionalFiniteDecimal
    validation_status: ValidationStatus

    _as_of_must_be_aware = field_validator("as_of")(_aware_datetime)

    @model_validator(mode="after")
    def valid_record_requires_value(self) -> "EvidenceRecord":
        if self.validation_status is ValidationStatus.VALID and self.value is None:
            raise ValueError("valid EvidenceRecord 必须包含 value")
        return self


class CalculationRecord(BaseModel):
    """权威的确定性派生记录；来源和时间独立可追溯，不负责执行计算 Gate。"""

    model_config = ConfigDict(extra="forbid")

    calculation_id: _NonEmptyString
    formula_id: _NonEmptyString
    input_evidence_ids: list[_NonEmptyString] = Field(min_length=1)
    source_reference: _NonEmptyString
    as_of: datetime
    result: _OptionalFiniteDecimal
    unit: _NonEmptyString
    period_start: date
    period_end: date
    validation_status: ValidationStatus

    _as_of_must_be_aware = field_validator("as_of")(_aware_datetime)

    @model_validator(mode="after")
    def valid_record_requires_result(self) -> "CalculationRecord":
        if self.validation_status is ValidationStatus.VALID and self.result is None:
            raise ValueError("valid CalculationRecord 必须包含 result")
        return self


class ClaimRecord(BaseModel):
    """LLM 候选陈述，不是权威 Evidence/Calculation，也不宣称自身已验证。

    来源、时间和验证由引用的 Evidence/Calculation 及后续 Claim Gate 提供。
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: _NonEmptyString
    category: _NonEmptyString
    statement: _NonEmptyString
    evidence_ids: list[_NonEmptyString]
    calculation_ids: list[_NonEmptyString]
    confidence: StrictFloat = Field(ge=0, le=1)


class MarketPriceRecord(BaseModel):
    """可进入 Evidence allowlist 的权威行情记录；不负责采集、复权或估值。"""

    model_config = ConfigDict(extra="forbid")

    evidence_id: _NonEmptyString
    ticker: _NonEmptyString
    price: _FiniteDecimal
    currency: _NonEmptyString
    price_timestamp: datetime
    source_reference: _NonEmptyString
    adjustment_basis: Literal["raw", "split_adjusted", "total_return_adjusted"]
    validation_status: ValidationStatus

    _price_timestamp_must_be_aware = field_validator("price_timestamp")(_aware_datetime)

    @model_validator(mode="after")
    def price_must_be_positive(self) -> "MarketPriceRecord":
        if self.price <= 0:
            raise ValueError("MarketPriceRecord 的 price 必须大于 0")
        return self


__all__ = [
    "CalculationRecord",
    "ClaimRecord",
    "EvidenceRecord",
    "MarketPriceRecord",
    "ValidationStatus",
]
