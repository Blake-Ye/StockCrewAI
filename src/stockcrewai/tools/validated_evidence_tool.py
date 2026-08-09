from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Annotated, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StringConstraints


_NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class ValidatedEvidenceToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_ids: list[_NonEmptyString] = Field(
        min_length=1,
        description="要查询的已验证指标 ID 列表；只能查询当前运行的 allowlist。",
    )
    periods: list[_NonEmptyString] = Field(
        default_factory=list,
        description="可选期间筛选列表，例如 FY2025；空列表表示不按期间筛选。",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="最多返回的证据条数，范围为 1 到 100。",
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"EvidenceStore result is not JSON-safe: {type(value).__name__}")


def _json_safe(value: Any) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.loads(json.dumps(value, default=_json_default, allow_nan=False))


class ValidatedEvidenceTool(BaseTool):
    name: str = "query_validated_evidence"
    description: str = (
        "查询当前运行 allowlist 内已验证的 Evidence；不联网、不计算、不修改状态。"
    )
    args_schema: Type[BaseModel] = ValidatedEvidenceToolInput

    _evidence_store: Any = PrivateAttr(default=None)

    def __init__(self, evidence_store: Any, **kwargs: Any) -> None:
        if evidence_store is None:
            raise ValueError("evidence_store is required")
        super().__init__(**kwargs)
        self._evidence_store = evidence_store

    def _run(
        self,
        metric_ids: list[str],
        periods: list[str],
        limit: int,
    ) -> Any:
        result = self._evidence_store.query_validated_evidence(metric_ids, periods, limit)
        return _json_safe(result)


__all__ = ["ValidatedEvidenceTool", "ValidatedEvidenceToolInput"]
