from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StringConstraints


_NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class CompanyIdentity(BaseModel):
    """公司身份候选记录；不负责解析来源、消歧或选择最终证券。"""

    model_config = ConfigDict(extra="forbid")

    company_name: _NonEmptyString
    ticker: _NonEmptyString
    cik: _NonEmptyString
    exchange: _NonEmptyString
    security_type: _NonEmptyString
    source_reference: _NonEmptyString
    status: Literal["resolved", "ambiguous", "unsupported", "unavailable"]
    reason_code: _NonEmptyString


class ParsedResearchRequest(BaseModel):
    """请求解析候选记录；只约束九个输入字段，不负责查证公司或生成研究结论。"""

    model_config = ConfigDict(extra="forbid")

    company_mention: _NonEmptyString
    company_name_guess: _NonEmptyString | None
    ticker_guess: _NonEmptyString | None
    exchange_guess: _NonEmptyString | None
    request_type: _NonEmptyString
    investment_horizon: _NonEmptyString | None
    requested_focus: list[_NonEmptyString]
    language: _NonEmptyString
    confidence: StrictFloat = Field(ge=0, le=1)


class ParsedRequest(ParsedResearchRequest):
    """旧 ParsedRequest 名称的无副作用兼容类型，不接入现有 Request Parser Crew。"""


__all__ = ["CompanyIdentity", "ParsedResearchRequest", "ParsedRequest"]
