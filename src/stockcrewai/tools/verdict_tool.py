from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


VERDICT_RULES_VERSION = "v1"


class VerdictToolInput(BaseModel):
    validation_status: str = "unavailable"
    valuation: dict[str, Any] = Field(default_factory=dict)
    historical_valuation: dict[str, Any] = Field(default_factory=dict)
    reverse_dcf: dict[str, Any] = Field(default_factory=dict)
    risk_input: dict[str, Any] = Field(default_factory=dict)


class VerdictResult(BaseModel):
    status: Literal["ready", "insufficient_data"]
    policy_defined: bool = True
    is_investment_rating: bool = False
    business_quality: str = "insufficient_data"
    financial_trend: str = "insufficient_data"
    valuation: str = "insufficient_data"
    risk_level: str = "insufficient_data"
    overall_rating: Literal[
        "attractive", "reasonable", "watchlist", "expensive", "insufficient_data"
    ] = "insufficient_data"
    summary_code: str
    triggered_rules: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    rules_version: str = VERDICT_RULES_VERSION


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _calculation_value(result: dict[str, Any], formula_id: str) -> Decimal | None:
    for calculation in result.get("calculations", []):
        if (
            isinstance(calculation, dict)
            and calculation.get("formula_id") == formula_id
            and calculation.get("validation_status") == "valid"
        ):
            return _decimal(calculation.get("raw_result"))
    return None


class DeterministicVerdictTool(BaseTool):
    name: str = "deterministic_verdict_policy"
    description: str = (
        "根据明确的 v1 数据完备性和估值阈值规则生成确定性 Verdict；"
        "数据不足时返回 insufficient_data，不生成买卖建议。"
    )
    args_schema: Type[BaseModel] = VerdictToolInput
    result_schema: Type[BaseModel] = VerdictResult

    def _run(
        self,
        validation_status: str = "unavailable",
        valuation: dict[str, Any] | None = None,
        historical_valuation: dict[str, Any] | None = None,
        reverse_dcf: dict[str, Any] | None = None,
        risk_input: dict[str, Any] | None = None,
    ) -> VerdictResult:
        valuation = valuation or {}
        historical_valuation = historical_valuation or {}
        reverse_dcf = reverse_dcf or {}
        risk_input = risk_input or {}
        reasons: list[str] = []
        required = (
            ("validation_status", validation_status == "valid"),
            (
                "valuation",
                valuation.get("readiness") == "ready"
                and valuation.get("validation_status") == "valid",
            ),
            (
                "historical_valuation",
                historical_valuation.get("status") == "ok"
                and historical_valuation.get("validation_status") == "valid",
            ),
            (
                "reverse_dcf",
                reverse_dcf.get("status") == "ok"
                and reverse_dcf.get("validation_status") == "valid",
            ),
            (
                "risk_input",
                risk_input.get("status") == "available"
                and risk_input.get("risk_level") in {"low", "medium", "high"},
            ),
        )
        for name, ready in required:
            if not ready:
                reasons.append(f"{name} unavailable or unvalidated")
        if reasons:
            return VerdictResult(
                status="insufficient_data",
                summary_code="INSUFFICIENT_DATA",
                triggered_rules=["require_all_validated_components"],
                reasons=reasons,
            )

        pe = _calculation_value(valuation, "pe_ratio")
        fcf_yield = _calculation_value(valuation, "fcf_yield")
        percentile = _decimal(historical_valuation.get("current_percentile"))
        risk_level = str(risk_input["risk_level"])
        triggered_rules: list[str] = []
        if risk_level == "high":
            overall = "watchlist"
            triggered_rules.append("high_risk_watchlist")
        elif pe is not None and fcf_yield is not None and pe <= 20 and fcf_yield >= Decimal("0.05"):
            overall = "attractive"
            triggered_rules.append("low_multiple_high_fcf_yield")
        elif (
            pe is not None
            and (pe >= 35 or (fcf_yield is not None and fcf_yield < Decimal("0.02")))
        ) or (percentile is not None and percentile >= 75):
            overall = "expensive"
            triggered_rules.append("high_valuation")
        else:
            overall = "reasonable"
            triggered_rules.append("balanced_valuation")
        return VerdictResult(
            status="ready",
            is_investment_rating=True,
            business_quality="available",
            financial_trend="available",
            valuation=overall,
            risk_level=risk_level,
            overall_rating=overall,
            summary_code="POLICY_EVALUATED",
            triggered_rules=triggered_rules,
        )
