"""Analysis Claim 的共享 schema 与确定性白名单校验。"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stockcrewai.models.evidence import ClaimRecord


ANALYSIS_DOMAIN_RULES = {
    "financial": (frozenset({"financial_quality", "financial_trend"}), True),
    "risk": (frozenset({"risk"}), False),
    "valuation": (
        frozenset({"current_valuation", "historical_valuation", "reverse_dcf"}),
        True,
    ),
}


Claim = ClaimRecord
ClaimSchema = ClaimRecord
AnalysisClaim = ClaimRecord


class AnalysisTaskOutput(BaseModel):
    """Analysis Task 的顶层输出，只允许包含 Claims 集合。"""

    model_config = ConfigDict(extra="forbid")

    claims: list[ClaimRecord] = Field(default_factory=list)


def validate_claim(
    item: Any,
    *,
    allowed_categories: Collection[str],
    evidence_allowlist: Collection[str],
    calculation_allowlist: Collection[str],
    requires_calculations: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    """校验单条 Claim 的 schema、category、Evidence 和 Calculation ID。"""
    if not isinstance(item, Mapping):
        return None, "claim_schema_invalid"

    statement = item.get("statement")
    if isinstance(statement, str) and not statement.strip():
        return None, "claim_text_empty"
    category_value = item.get("category")
    if isinstance(category_value, str) and (
        not category_value.strip() or category_value.strip() not in allowed_categories
    ):
        return None, "category_invalid"
    evidence_values = item.get("evidence_ids")
    if evidence_values is not None and (
        not isinstance(evidence_values, list)
        or any(not isinstance(identifier, str) for identifier in evidence_values)
    ):
        return None, "evidence_ids_invalid"
    calculation_values = item.get("calculation_ids")
    if calculation_values is not None and (
        not isinstance(calculation_values, list)
        or any(not isinstance(identifier, str) for identifier in calculation_values)
    ):
        return None, "calculation_ids_invalid"

    try:
        validated_claim = ClaimRecord.model_validate(item)
    except (TypeError, ValueError):
        return None, "claim_schema_invalid"

    text_fields = ("claim_id", "category", "statement")
    if any(not getattr(validated_claim, field).strip() for field in text_fields):
        return None, "claim_schema_invalid"
    category = validated_claim.category.strip()
    if category not in allowed_categories:
        return None, "category_invalid"

    evidence_ids = list(validated_claim.evidence_ids)
    if not evidence_ids or any(
        not identifier.strip() or identifier not in evidence_allowlist
        for identifier in evidence_ids
    ):
        return None, "evidence_ids_invalid"

    calculation_ids = list(validated_claim.calculation_ids)
    if requires_calculations and (
        not calculation_ids
        or any(
            not identifier.strip() or identifier not in calculation_allowlist
            for identifier in calculation_ids
        )
    ):
        return None, "calculation_ids_invalid"
    if not requires_calculations and calculation_ids:
        return None, "calculation_ids_invalid"

    return (
        {
            "claim_id": validated_claim.claim_id.strip(),
            "category": category,
            "statement": validated_claim.statement.strip(),
            "evidence_ids": evidence_ids,
            "calculation_ids": calculation_ids,
            "confidence": validated_claim.confidence,
        },
        None,
    )


__all__ = [
    "ANALYSIS_DOMAIN_RULES",
    "AnalysisClaim",
    "AnalysisTaskOutput",
    "Claim",
    "ClaimSchema",
    "validate_claim",
]
