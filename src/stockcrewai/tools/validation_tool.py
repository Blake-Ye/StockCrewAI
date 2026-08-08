from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field, model_validator

from stockcrewai.tools.calculator_tool import CalculationResult, calculate_formula


class ValidationToolInput(BaseModel):
    company_name: str | None = Field(default=None, description="公司名称")
    ticker: str | None = Field(default=None, description="股票代码")
    facts: dict[str, Any] = Field(default_factory=dict)
    calculations: list[CalculationResult | dict[str, Any]] = Field(default_factory=list)
    required_fact_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_identity(self) -> "ValidationToolInput":
        if self.company_name:
            self.company_name = self.company_name.strip()
        if self.ticker:
            self.ticker = self.ticker.strip().upper()
        return self


class ValidationIssue(BaseModel):
    code: str
    severity: Literal["error", "warning"]
    field: str
    message: str


class ValidationResult(BaseModel):
    status: Literal["valid", "invalid", "unavailable"]
    validated: bool
    company_name: str | None = None
    ticker: str | None = None
    validated_evidence_ids: list[str] = Field(default_factory=list)
    validated_calculation_ids: list[str] = Field(default_factory=list)
    checked_calculation_ids: list[str] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("数值缺失")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("数值格式无效") from exc
    if not result.is_finite():
        raise ValueError("数值必须是有限值")
    return result


def _fact_value(raw: Any) -> tuple[Any, str | None]:
    if isinstance(raw, BaseModel):
        raw = raw.model_dump()
    if isinstance(raw, dict):
        return raw.get("value", raw.get("numeric_value")), raw.get("evidence_id")
    return raw, None


def sync_validation_status(
    facts: Mapping[str, Any],
    calculations: Sequence[CalculationResult | Mapping[str, Any]],
    validation_result: ValidationResult,
) -> dict[str, Any]:
    """Serialize facts and calculations with the validation result projected onto them."""
    validation_is_valid = (
        validation_result.status == "valid" and validation_result.validated
    )
    validated_evidence_ids = (
        set(validation_result.validated_evidence_ids) if validation_is_valid else set()
    )
    validated_calculation_ids = (
        set(validation_result.validated_calculation_ids)
        if validation_is_valid
        else set()
    )

    def serialize_and_sync(
        raw: Any, identifier_key: str, validated_ids: set[str]
    ) -> Any:
        if isinstance(raw, BaseModel):
            payload = raw.model_dump(mode="json")
        elif isinstance(raw, Mapping):
            payload = deepcopy(dict(raw))
        else:
            payload = deepcopy(raw)
        if not isinstance(payload, dict):
            return payload
        if payload.get(identifier_key) in validated_ids:
            payload["validation_status"] = "valid"
        elif payload.get("validation_status") == "valid":
            payload["validation_status"] = "unvalidated"
        return payload

    return {
        "facts": {
            fact_id: serialize_and_sync(
                raw_fact, "evidence_id", validated_evidence_ids
            )
            for fact_id, raw_fact in facts.items()
        },
        "calculations": [
            serialize_and_sync(
                raw_calculation, "calculation_id", validated_calculation_ids
            )
            for raw_calculation in calculations
        ],
    }


class FinancialValidationTool(BaseTool):
    name: str = "financial_evidence_calculation_validator"
    description: str = (
        "验证公司身份、Evidence ID、数值有限性和 CalculationResult；"
        "会使用相同公式重新计算，返回 ValidationResult。"
    )
    args_schema: Type[BaseModel] = ValidationToolInput

    def _run(
        self,
        company_name: str | None = None,
        ticker: str | None = None,
        facts: dict[str, Any] | None = None,
        calculations: list[CalculationResult | dict[str, Any]] | None = None,
        required_fact_ids: list[str] | None = None,
    ) -> ValidationResult:
        issues: list[ValidationIssue] = []
        facts = facts or {}
        calculations = calculations or []
        required_fact_ids = required_fact_ids or []
        if not company_name and not ticker:
            issues.append(
                ValidationIssue(
                    code="missing_identity",
                    severity="error",
                    field="company_name/ticker",
                    message="缺少公司名称和 ticker",
                )
            )
        evidence_ids: set[str] = set()
        values: dict[str, Decimal] = {}
        for fact_id, raw in facts.items():
            value, evidence_id = _fact_value(raw)
            if evidence_id:
                evidence_ids.add(str(evidence_id))
            else:
                issues.append(
                    ValidationIssue(
                        code="missing_evidence_id",
                        severity="error",
                        field=f"facts.{fact_id}.evidence_id",
                        message="事实必须绑定 Evidence ID",
                    )
                )
            if isinstance(raw, BaseModel):
                raw = raw.model_dump()
            if isinstance(raw, dict) and "source_reference" in raw and not raw.get(
                "source_reference"
            ):
                issues.append(
                    ValidationIssue(
                        code="missing_source_reference",
                        severity="error",
                        field=f"facts.{fact_id}.source_reference",
                        message="事实缺少 SEC source_reference",
                    )
                )
            try:
                values[fact_id] = _decimal(value)
            except ValueError as exc:
                issues.append(
                    ValidationIssue(
                        code="invalid_fact_value",
                        severity="error",
                        field=f"facts.{fact_id}",
                        message=str(exc),
                    )
                )
                continue
            if fact_id == "capex" and values[fact_id] < 0:
                issues.append(
                    ValidationIssue(
                        code="capex_sign",
                        severity="error",
                        field="facts.capex",
                        message="资本开支必须使用正数现金流出约定",
                    )
                )
        for fact_id in required_fact_ids:
            if fact_id not in facts:
                issues.append(
                    ValidationIssue(
                        code="missing_required_fact",
                        severity="error",
                        field=f"facts.{fact_id}",
                        message="缺少必需 Evidence",
                    )
                )
        checked: list[str] = []
        available_calculation_ids: list[str] = []
        for raw_calculation in calculations:
            calculation = CalculationResult.model_validate(raw_calculation)
            checked.append(calculation.calculation_id)
            for input_evidence_id in calculation.input_evidence_ids:
                if input_evidence_id not in evidence_ids:
                    issues.append(
                        ValidationIssue(
                            code="unknown_evidence_id",
                            severity="error",
                            field=f"{calculation.calculation_id}.input_evidence_ids",
                            message=f"找不到 Evidence ID：{input_evidence_id}",
                        )
                    )
            if calculation.status != "available" or calculation.raw_result is None:
                issues.append(
                    ValidationIssue(
                        code="unavailable_calculation",
                        severity="warning",
                        field=calculation.calculation_id,
                        message="计算结果不可用，跳过重算",
                    )
                )
                continue
            try:
                raw_inputs = {
                    key: _decimal(value)
                    for key, value in calculation.raw_inputs.items()
                }
                for input_key, input_value in raw_inputs.items():
                    if input_key in values and input_value != values[input_key]:
                        issues.append(
                            ValidationIssue(
                                code="calculation_input_mismatch",
                                severity="error",
                                field=f"{calculation.calculation_id}.raw_inputs.{input_key}",
                                message=(
                                    f"计算输入 {input_value} 与 Evidence 值 "
                                    f"{values[input_key]} 不一致"
                                ),
                            )
                        )
                recomputed, _ = calculate_formula(calculation.formula_id, raw_inputs)
                reported = _decimal(calculation.raw_result)
            except (ArithmeticError, KeyError, ValueError) as exc:
                issues.append(
                    ValidationIssue(
                        code="calculation_input_error",
                        severity="error",
                        field=calculation.calculation_id,
                        message=str(exc),
                    )
                )
                continue
            if abs(recomputed - reported) > Decimal("1E-12"):
                issues.append(
                    ValidationIssue(
                        code="calculation_mismatch",
                        severity="error",
                        field=calculation.calculation_id,
                        message=f"报告结果 {reported} 与重算结果 {recomputed} 不一致",
                    )
                )
            available_calculation_ids.append(calculation.calculation_id)
        errors = [issue for issue in issues if issue.severity == "error"]
        has_available = any(
            CalculationResult.model_validate(item).status == "available"
            for item in calculations
        )
        if errors:
            status: Literal["valid", "invalid", "unavailable"] = "invalid"
        elif calculations and not has_available:
            status = "unavailable"
        else:
            status = "valid"
        fact_errors = any(
            issue.severity == "error" and issue.field.startswith("facts.")
            for issue in issues
        )
        validated_evidence_ids = [] if fact_errors else sorted(evidence_ids)
        validated_calculation_ids = (
            available_calculation_ids if status == "valid" else []
        )
        return ValidationResult(
            status=status,
            validated=status == "valid",
            company_name=company_name,
            ticker=ticker.upper() if ticker else None,
            validated_evidence_ids=validated_evidence_ids,
            validated_calculation_ids=validated_calculation_ids,
            checked_calculation_ids=checked,
            issues=issues,
        )
