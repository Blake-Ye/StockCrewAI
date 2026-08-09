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


class ValidatedCalculationToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calculation_ids: list[_NonEmptyString] = Field(
        min_length=1,
        description="要查询的已验证 Calculation ID 列表；只能查询当前运行的 allowlist。",
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


class ValidatedCalculationTool(BaseTool):
    name: str = "get_validated_calculations"
    description: str = (
        "查询当前运行 allowlist 内已验证的 Calculation；不联网、不重新计算、不修改状态。"
    )
    args_schema: Type[BaseModel] = ValidatedCalculationToolInput

    _evidence_store: Any = PrivateAttr(default=None)

    def __init__(self, evidence_store: Any, **kwargs: Any) -> None:
        if evidence_store is None:
            raise ValueError("evidence_store is required")
        super().__init__(**kwargs)
        self._evidence_store = evidence_store

    def _run(self, calculation_ids: list[str]) -> Any:
        result = self._evidence_store.get_validated_calculations(calculation_ids)
        return _json_safe(result)


__all__ = ["ValidatedCalculationTool", "ValidatedCalculationToolInput"]
