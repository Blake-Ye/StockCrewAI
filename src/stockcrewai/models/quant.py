from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    Strict,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from stockcrewai.models.evidence import _OptionalFiniteDecimal, _aware_datetime
from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ReportingProfile,
    SecurityProfile,
)


_NonEmptyString = Annotated[
    str,
    Strict(),
    StringConstraints(strip_whitespace=True, min_length=1),
]
_NumericMapping = dict[_NonEmptyString, _OptionalFiniteDecimal]


def _json_scalar(value: object) -> object:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("JSON 标量字符串不能为空")
        return value
    if isinstance(value, Decimal) and value.is_finite():
        return value
    raise ValueError("JSON 标量必须是非空字符串、有限 Decimal、bool 或 None")


_JsonScalar = Annotated[
    _NonEmptyString | Decimal | bool | None,
    BeforeValidator(_json_scalar),
]
_JsonMapping = dict[_NonEmptyString, _JsonScalar]


class PointInTimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: _NonEmptyString
    as_of: datetime
    cik: _NonEmptyString
    ticker: _NonEmptyString
    issuer_profile: IssuerProfile
    security_profile: SecurityProfile
    reporting_profile: ReportingProfile
    filing_cutoff: datetime
    price_cutoff: datetime
    available_evidence_ids: list[_NonEmptyString]
    available_calculation_ids: list[_NonEmptyString]
    financial_features: _NumericMapping
    market_features: _NumericMapping
    data_quality: _JsonMapping
    builder_version: _NonEmptyString

    _datetimes_must_be_aware = field_validator(
        "as_of", "filing_cutoff", "price_cutoff"
    )(_aware_datetime)


class FactorObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor_id: _NonEmptyString
    formula_version: _NonEmptyString
    snapshot_id: _NonEmptyString
    as_of: datetime
    ticker: _NonEmptyString
    raw_value: _OptionalFiniteDecimal
    normalized_value: _OptionalFiniteDecimal
    peer_group: _NonEmptyString
    peer_count: StrictInt = Field(ge=0)
    evidence_ids: list[_NonEmptyString]
    calculation_ids: list[_NonEmptyString]
    status: Literal["available", "unavailable", "not_applicable", "invalid"]
    reason_code: _NonEmptyString

    _as_of_must_be_aware = field_validator("as_of")(_aware_datetime)


class QuantResearchPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: datetime
    universe_id: _NonEmptyString
    strategy_version: _NonEmptyString
    coverage: CoverageLevel
    factor_summary: _JsonMapping
    ranking_summary: _JsonMapping
    backtest_summary: _JsonMapping
    benchmark_summary: _JsonMapping
    data_quality: _JsonMapping
    limitations: list[_NonEmptyString]
    artifact_ids: list[_NonEmptyString]

    _as_of_must_be_aware = field_validator("as_of")(_aware_datetime)


class UniverseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_id: _NonEmptyString
    tickers: list[_NonEmptyString] = Field(min_length=1)
    selection_as_of: datetime
    membership_source: _NonEmptyString
    membership_basis: _NonEmptyString
    known_biases: list[_NonEmptyString] = Field(min_length=1)
    manifest_version: _NonEmptyString

    _selection_as_of_must_be_aware = field_validator("selection_as_of")(_aware_datetime)

    @model_validator(mode="after")
    def requires_survivorship_bias_disclosure(self) -> "UniverseManifest":
        if "survivorship_bias_known" not in self.known_biases:
            raise ValueError("known_biases 必须包含 survivorship_bias_known")
        return self


__all__ = [
    "FactorObservation",
    "PointInTimeSnapshot",
    "QuantResearchPacket",
    "UniverseManifest",
]
