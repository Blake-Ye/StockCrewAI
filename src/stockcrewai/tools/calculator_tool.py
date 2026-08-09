from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_EVEN
from typing import Any, Literal, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


DEFAULT_FORMULAS = (
    "revenue_growth",
    "operating_margin",
    "net_margin",
    "free_cash_flow",
    "free_cash_flow_margin",
    "cash_conversion",
    "net_cash",
    "current_ratio",
    "debt_to_equity",
    "share_dilution",
)


class CalculatorToolInput(BaseModel):
    company_name: str | None = Field(default=None, description="公司名称")
    ticker: str | None = Field(default=None, description="股票代码")
    facts: dict[str, Any] = Field(
        default_factory=dict,
        description="EDGAR facts 映射；值可以是数字或包含 value/evidence_id 的对象",
    )
    formulas: list[str] = Field(
        default_factory=lambda: list(DEFAULT_FORMULAS),
        description="要执行的确定性公式 ID",
    )


class CalculationResult(BaseModel):
    calculation_id: str
    formula_id: str
    formula_version: str = "v1"
    input_evidence_ids: list[str] = Field(default_factory=list)
    raw_inputs: dict[str, str] = Field(default_factory=dict)
    raw_result: str | None = None
    normalized_result: str | None = None
    display_result: str | None = None
    unit: str | None = None
    status: Literal["available", "unavailable"]
    validation_status: Literal["unvalidated", "valid", "invalid"] = "unvalidated"
    warnings: list[str] = Field(default_factory=list)


class CalculationBatch(BaseModel):
    status: Literal["ok", "partial", "error"]
    company_name: str | None = None
    ticker: str | None = None
    calculations: list[CalculationResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("数值缺失或类型不支持")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("数值格式无效") from exc
    if not result.is_finite():
        raise ValueError("数值必须是有限值")
    return result


def _scientific(value: Decimal) -> str:
    return format(value, ".5E")


def _plain(value: Decimal) -> str:
    return format(value, "f")


def _fact_value(facts: dict[str, Any], key: str) -> tuple[Decimal, str | None]:
    if key not in facts or facts[key] is None:
        raise KeyError(key)
    raw = facts[key]
    evidence_id = None
    if isinstance(raw, BaseModel):
        raw = raw.model_dump()
    if isinstance(raw, dict):
        evidence_id = raw.get("evidence_id")
        raw = raw.get("value", raw.get("numeric_value"))
    return _as_decimal(raw), str(evidence_id) if evidence_id else None


def calculate_formula(formula_id: str, values: dict[str, Decimal]) -> tuple[Decimal, str]:
    if formula_id == "revenue_growth":
        return (values["revenue_current"] - values["revenue_prior"]) / values["revenue_prior"], "ratio"
    if formula_id == "operating_margin":
        return values["operating_income"] / values["revenue_current"], "ratio"
    if formula_id == "net_margin":
        return values["net_income"] / values["revenue_current"], "ratio"
    if formula_id == "free_cash_flow":
        return values["operating_cash_flow"] - values["capex"], "currency"
    if formula_id == "free_cash_flow_margin":
        return (
            values["operating_cash_flow"] - values["capex"]
        ) / values["revenue_current"], "ratio"
    if formula_id == "cash_conversion":
        return values["operating_cash_flow"] / values["net_income"], "ratio"
    if formula_id == "net_cash":
        return (
            values["cash_and_equivalents"]
            + values["short_term_investments"]
            - values["short_term_debt"]
            - values["long_term_debt"]
        ), "currency"
    if formula_id == "current_ratio":
        return values["total_current_assets"] / values["total_current_liabilities"], "ratio"
    if formula_id == "debt_to_equity":
        return (
            values["short_term_debt"] + values["long_term_debt"]
        ) / values["stockholders_equity"], "ratio"
    if formula_id == "share_dilution":
        return (
            values["shares_current"] - values["shares_prior"]
        ) / values["shares_prior"], "ratio"
    raise KeyError(f"不支持的公式：{formula_id}")


FORMULA_INPUTS = {
    "revenue_growth": ("revenue_current", "revenue_prior"),
    "operating_margin": ("operating_income", "revenue_current"),
    "net_margin": ("net_income", "revenue_current"),
    "free_cash_flow": ("operating_cash_flow", "capex"),
    "free_cash_flow_margin": ("operating_cash_flow", "capex", "revenue_current"),
    "cash_conversion": ("operating_cash_flow", "net_income"),
    "net_cash": (
        "cash_and_equivalents",
        "short_term_investments",
        "short_term_debt",
        "long_term_debt",
    ),
    "current_ratio": ("total_current_assets", "total_current_liabilities"),
    "debt_to_equity": ("short_term_debt", "long_term_debt", "stockholders_equity"),
    "share_dilution": ("shares_current", "shares_prior"),
}


class FinancialCalculatorTool(BaseTool):
    name: str = "deterministic_financial_calculator"
    description: str = (
        "使用 EDGAR facts 执行 Decimal 财务公式。只接受结构化 facts，"
        "返回带公式版本、输入 Evidence ID 和可审计结果的 CalculationBatch。"
    )
    args_schema: Type[BaseModel] = CalculatorToolInput

    def _unavailable(
        self,
        formula_id: str,
        warning: str,
        raw_inputs: dict[str, str] | None = None,
        evidence_ids: list[str] | None = None,
    ) -> CalculationResult:
        return CalculationResult(
            calculation_id=f"calc_{formula_id}",
            formula_id=formula_id,
            input_evidence_ids=evidence_ids or [],
            raw_inputs=raw_inputs or {},
            status="unavailable",
            warnings=[warning],
        )

    def _run(
        self,
        company_name: str | None = None,
        ticker: str | None = None,
        facts: dict[str, Any] | None = None,
        formulas: list[str] | None = None,
    ) -> CalculationBatch:
        facts = facts or {}
        selected_formulas = formulas or list(DEFAULT_FORMULAS)
        calculations: list[CalculationResult] = []
        batch_warnings: list[str] = []
        with localcontext() as context:
            context.prec = 28
            context.rounding = ROUND_HALF_EVEN
            for formula_id in selected_formulas:
                formula_id = formula_id.strip()
                required = FORMULA_INPUTS.get(formula_id)
                if required is None:
                    calculations.append(
                        self._unavailable(formula_id, f"不支持的公式：{formula_id}")
                    )
                    continue
                values: dict[str, Decimal] = {}
                raw_inputs: dict[str, str] = {}
                evidence_ids: list[str] = []
                missing: list[str] = []
                for key in required:
                    try:
                        value, evidence_id = _fact_value(facts, key)
                    except (KeyError, ValueError):
                        missing.append(key)
                        continue
                    values[key] = value
                    raw_inputs[key] = _plain(value)
                    if evidence_id:
                        evidence_ids.append(evidence_id)
                if missing:
                    calculations.append(
                        self._unavailable(
                            formula_id,
                            "缺少输入：" + ", ".join(missing),
                            raw_inputs,
                            evidence_ids,
                        )
                    )
                    continue
                try:
                    result, unit = calculate_formula(formula_id, values)
                except (ArithmeticError, KeyError) as exc:
                    calculations.append(
                        self._unavailable(
                            formula_id,
                            f"公式无法计算：{type(exc).__name__}",
                            raw_inputs,
                            evidence_ids,
                        )
                    )
                    continue
                warnings: list[str] = []
                if len(evidence_ids) != len(required):
                    warnings.append("至少一个输入缺少 Evidence ID")
                if formula_id in {"current_ratio", "debt_to_equity"}:
                    display = f"{result:.2f}x"
                elif unit == "ratio":
                    display = f"{result * 100:.2f}%"
                else:
                    display = _plain(result)
                calculations.append(
                    CalculationResult(
                        calculation_id=f"calc_{formula_id}",
                        formula_id=formula_id,
                        input_evidence_ids=evidence_ids,
                        raw_inputs=raw_inputs,
                        raw_result=_plain(result),
                        normalized_result=_scientific(result),
                        display_result=display,
                        unit=unit,
                        status="available",
                        warnings=warnings,
                    )
                )
                batch_warnings.extend(warnings)
        status = "ok" if all(item.status == "available" for item in calculations) else "partial"
        return CalculationBatch(
            status=status,
            company_name=company_name,
            ticker=ticker.upper() if ticker else None,
            calculations=calculations,
            warnings=batch_warnings,
        )
