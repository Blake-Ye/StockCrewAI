from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


VERDICT_RULES_VERSION = "v1"
_CURRENT_VALUATION_METRICS = frozenset(
    {"market_cap", "market_capitalization", "pe_ratio", "fcf_yield"}
)


class VerdictToolInput(BaseModel):
    validation_status: str = "unavailable"
    valuation: dict[str, Any] = Field(default_factory=dict)
    historical_valuation: dict[str, Any] = Field(default_factory=dict)
    reverse_dcf: dict[str, Any] = Field(default_factory=dict)
    risk_input: dict[str, Any] = Field(default_factory=dict)
    policy_context: dict[str, Any] = Field(default_factory=dict)


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
        policy_context: dict[str, Any] | None = None,
    ) -> VerdictResult:
        valuation = valuation or {}
        historical_valuation = historical_valuation or {}
        reverse_dcf = reverse_dcf or {}
        risk_input = risk_input or {}
        policy_context = policy_context or {}
        profile = policy_context.get("profile")
        issuer_profile = (
            profile.get("issuer_profile") if isinstance(profile, dict) else None
        )
        issuer_profile = getattr(issuer_profile, "value", issuer_profile)
        if str(issuer_profile).strip().casefold() == "holding_company":
            return VerdictResult(
                status="insufficient_data",
                policy_defined=False,
                is_investment_rating=False,
                business_quality="insufficient_data",
                financial_trend="insufficient_data",
                valuation="insufficient_data",
                risk_level="insufficient_data",
                overall_rating="insufficient_data",
                summary_code="HOLDING_COMPANY_NAV_ONLY",
                triggered_rules=["holding_company_nav_only"],
                reasons=["holding_company_nav_primary_valuation"],
            )
        reasons: list[str] = []
        gate = policy_context.get("gate")
        if isinstance(gate, dict) and gate.get("status") == "evidence_only":
            return VerdictResult(
                status="insufficient_data",
                policy_defined=False,
                is_investment_rating=False,
                overall_rating="insufficient_data",
                summary_code="FOREIGN_PROFILE_EVIDENCE_ONLY",
                triggered_rules=["foreign_profile_evidence_only"],
                reasons=["foreign_profile_evidence_only"],
            )
        policy_decisions = policy_context.get("policy_decisions", [])
        if not isinstance(policy_decisions, list):
            policy_decisions = []
        policy_aware = (
            isinstance(policy_context.get("policy_version"), str)
            and bool(policy_context["policy_version"].strip())
            and isinstance(policy_context.get("policy_decisions"), list)
        )
        not_applicable_metrics = {
            decision.get("metric_id")
            for decision in policy_decisions
            if policy_aware
            and isinstance(decision, dict)
            and decision.get("status") == "not_applicable"
            and isinstance(decision.get("metric_id"), str)
        }
        if policy_aware:
            blocking_metrics = {
                decision.get("metric_id")
                for decision in policy_decisions
                if isinstance(decision, dict)
                and decision.get("blocking") is True
                and decision.get("status") != "not_applicable"
                and isinstance(decision.get("metric_id"), str)
            }
            current_valuation_required = bool(
                blocking_metrics & _CURRENT_VALUATION_METRICS
            )
            required = (
                ("validation_status", validation_status == "valid"),
                (
                    "valuation",
                    not current_valuation_required
                    or (
                        valuation.get("readiness") == "ready"
                        and valuation.get("validation_status") == "valid"
                    ),
                ),
                (
                    "historical_valuation",
                    "historical_valuation" not in blocking_metrics
                    or (
                        historical_valuation.get("status") == "ok"
                        and historical_valuation.get("validation_status") == "valid"
                    ),
                ),
                (
                    "reverse_dcf",
                    "reverse_dcf" not in blocking_metrics
                    or (
                        reverse_dcf.get("status") == "ok"
                        and reverse_dcf.get("validation_status") == "valid"
                    ),
                ),
                (
                    "risk_input",
                    risk_input.get("status") == "available"
                    and risk_input.get("risk_level") in {"low", "medium", "high"},
                ),
            )
        else:
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

        pe = (
            None
            if "pe_ratio" in not_applicable_metrics
            else _calculation_value(valuation, "pe_ratio")
        )
        fcf_yield = (
            None
            if "fcf_yield" in not_applicable_metrics
            else _calculation_value(valuation, "fcf_yield")
        )
        percentile = (
            None
            if "historical_valuation" in not_applicable_metrics
            else _decimal(historical_valuation.get("current_percentile"))
        )
        risk_level = str(risk_input["risk_level"])
        triggered_rules: list[str] = []
        overall: Literal["attractive", "reasonable", "watchlist", "expensive"]
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
