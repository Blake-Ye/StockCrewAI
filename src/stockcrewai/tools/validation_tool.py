from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field, model_validator

from stockcrewai.tools.calculator_tool import CalculationResult, calculate_formula


class ValidationToolInput(BaseModel):
    company_name: str | None = Field(default=None, description="公司名称")
    ticker: str | None = Field(default=None, description="股票代码")
    facts: dict[str, Any] = Field(default_factory=dict)
    calculations: list[dict[str, Any] | CalculationResult] = Field(default_factory=list)
    required_fact_ids: list[str] = Field(default_factory=list)
    corporate_action_scan_status: Literal["checked", "unavailable"] = "unavailable"
    corporate_actions: list[dict[str, Any]] = Field(default_factory=list)

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


def _payload(raw: Any) -> Mapping[str, Any]:
    if isinstance(raw, BaseModel):
        return raw.model_dump()
    if isinstance(raw, Mapping):
        return raw
    return {}


def _collect_evidence_ids(raw: Any, evidence_ids: set[str]) -> None:
    """Collect IDs from fact records and any embedded filing evidence records."""
    if isinstance(raw, BaseModel):
        raw = raw.model_dump()
    if isinstance(raw, Mapping):
        evidence_id = raw.get("evidence_id")
        if isinstance(evidence_id, str) and evidence_id.strip():
            evidence_ids.add(evidence_id.strip())
        for value in raw.values():
            _collect_evidence_ids(value, evidence_ids)
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        for value in raw:
            _collect_evidence_ids(value, evidence_ids)


def _non_empty_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    result: list[str] = []
    for item in value:
        normalized = _non_empty_string(item)
        if normalized is None:
            return None
        result.append(normalized)
    return result or None


def _valid_iso_date(value: Any) -> bool:
    if isinstance(value, date):
        return True
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        date.fromisoformat(value.strip())
    except ValueError:
        return False
    return True


def _calculation_field(
    calculation: CalculationResult,
    payload: Mapping[str, Any],
    field: str,
    default: Any = None,
) -> Any:
    if field in payload:
        return payload[field]
    value = getattr(calculation, field, default)
    if value != default:
        return value
    raw_inputs = getattr(calculation, "raw_inputs", None)
    if isinstance(raw_inputs, Mapping) and field in raw_inputs:
        return raw_inputs[field]
    return value


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
        corporate_action_scan_status: Literal["checked", "unavailable"] = "unavailable",
        corporate_actions: list[dict[str, Any]] | None = None,
    ) -> ValidationResult:
        issues: list[ValidationIssue] = []
        facts = facts or {}
        calculations = calculations or []
        required_fact_ids = required_fact_ids or []
        corporate_actions = corporate_actions or []
        scan_status = corporate_action_scan_status or "unavailable"
        if not company_name and not ticker:
            issues.append(
                ValidationIssue(
                    code="missing_identity",
                    severity="error",
                    field="company_name/ticker",
                    message="缺少公司名称和 ticker",
                )
            )
        if scan_status not in {"checked", "unavailable"}:
            issues.append(
                ValidationIssue(
                    code="invalid_corporate_action_scan_status",
                    severity="error",
                    field="corporate_action_scan_status",
                    message="公司行动扫描状态无效",
                )
            )
        evidence_ids: set[str] = set()
        _collect_evidence_ids(facts, evidence_ids)
        values: dict[str, Decimal] = {}
        for fact_id, raw in facts.items():
            raw_payload = _payload(raw)
            is_filing_container = fact_id in {
                "filings",
                "filing_evidence",
                "filing_evidences",
            }
            is_filing_record = (
                "evidence_id" in raw_payload
                and "source_reference" in raw_payload
                and not {"value", "numeric_value"} & set(raw_payload)
                and bool(
                    {"form", "cik", "text", "accession_number", "risk_sections"}
                    & set(raw_payload)
                )
            )
            if is_filing_container or is_filing_record:
                continue
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

        action_ids: set[str] = set()
        valid_actions: dict[str, tuple[Decimal, list[str]]] = {}
        validated_action_evidence_ids: set[str] = set()
        for index, raw_action in enumerate(corporate_actions):
            prefix = f"corporate_actions[{index}]"
            action = _payload(raw_action)
            if not action:
                issues.append(
                    ValidationIssue(
                        code="invalid_corporate_action",
                        severity="error",
                        field=prefix,
                        message="公司行动必须是对象",
                    )
                )
                continue
            action_valid = True
            action_id = _non_empty_string(action.get("action_id"))
            if action_id is None:
                action_valid = False
                issues.append(
                    ValidationIssue(
                        code="missing_corporate_action_id",
                        severity="error",
                        field=f"{prefix}.action_id",
                        message="公司行动缺少 action_id",
                    )
                )
            elif action_id in action_ids:
                action_valid = False
                issues.append(
                    ValidationIssue(
                        code="duplicate_corporate_action_id",
                        severity="error",
                        field=f"{prefix}.action_id",
                        message=f"重复的 action_id：{action_id}",
                    )
                )
            else:
                action_ids.add(action_id)

            if action.get("action_type") != "stock_split":
                action_valid = False
                issues.append(
                    ValidationIssue(
                        code="invalid_corporate_action_type",
                        severity="error",
                        field=f"{prefix}.action_type",
                        message="公司行动类型必须是 stock_split",
                    )
                )
            direction = action.get("direction")
            if direction not in {"forward", "reverse"}:
                action_valid = False
                issues.append(
                    ValidationIssue(
                        code="invalid_corporate_action_direction",
                        severity="error",
                        field=f"{prefix}.direction",
                        message="公司行动 direction 必须是 forward 或 reverse",
                    )
                )
            if not _valid_iso_date(action.get("effective_date")):
                action_valid = False
                issues.append(
                    ValidationIssue(
                        code="invalid_corporate_action_date",
                        severity="error",
                        field=f"{prefix}.effective_date",
                        message="公司行动 effective_date 必须是有效日期",
                    )
                )

            old_shares: Decimal | None = None
            new_shares: Decimal | None = None
            for shares_field in ("old_shares", "new_shares"):
                try:
                    shares_value = _decimal(action.get(shares_field))
                    if shares_value <= 0:
                        raise ValueError(f"{shares_field} 必须是正数")
                    if shares_field == "old_shares":
                        old_shares = shares_value
                    else:
                        new_shares = shares_value
                except ValueError as exc:
                    action_valid = False
                    issues.append(
                        ValidationIssue(
                            code="invalid_corporate_action_shares",
                            severity="error",
                            field=f"{prefix}.{shares_field}",
                            message=str(exc),
                        )
                    )

            factor: Decimal | None = None
            try:
                factor = _decimal(action.get("adjustment_factor"))
                if factor <= 0:
                    raise ValueError("adjustment_factor 必须是正数")
            except ValueError as exc:
                action_valid = False
                issues.append(
                    ValidationIssue(
                        code="invalid_corporate_action_factor",
                        severity="error",
                        field=f"{prefix}.adjustment_factor",
                        message=str(exc),
                    )
                )
            if (
                old_shares is not None
                and new_shares is not None
                and factor is not None
                and factor != new_shares / old_shares
            ):
                action_valid = False
                issues.append(
                    ValidationIssue(
                        code="corporate_action_factor_mismatch",
                        severity="error",
                        field=f"{prefix}.adjustment_factor",
                        message="adjustment_factor 必须等于 new_shares / old_shares",
                    )
                )
            if (
                direction == "forward"
                and old_shares is not None
                and new_shares is not None
                and new_shares <= old_shares
            ) or (
                direction == "reverse"
                and old_shares is not None
                and new_shares is not None
                and new_shares >= old_shares
            ):
                action_valid = False
                issues.append(
                    ValidationIssue(
                        code="invalid_corporate_action_direction",
                        severity="error",
                        field=f"{prefix}.direction",
                        message=(
                            "forward 必须 new_shares > old_shares，"
                            "reverse 必须 new_shares < old_shares"
                        ),
                    )
                )

            action_evidence_ids = _string_list(action.get("evidence_ids"))
            if action_evidence_ids is None:
                action_valid = False
                issues.append(
                    ValidationIssue(
                        code="invalid_corporate_action_evidence_ids",
                        severity="error",
                        field=f"{prefix}.evidence_ids",
                        message="公司行动必须包含非空 evidence_ids",
                    )
                )
            else:
                for evidence_id in action_evidence_ids:
                    if evidence_id not in evidence_ids:
                        action_valid = False
                        issues.append(
                            ValidationIssue(
                                code="unknown_corporate_action_evidence_id",
                                severity="error",
                                field=f"{prefix}.evidence_ids",
                                message=f"找不到公司行动 Evidence ID：{evidence_id}",
                            )
                        )

            source_references = _string_list(action.get("source_references"))
            if source_references is None:
                action_valid = False
                issues.append(
                    ValidationIssue(
                        code="invalid_corporate_action_sources",
                        severity="error",
                        field=f"{prefix}.source_references",
                        message="公司行动必须包含非空 source_references",
                    )
                )
            if action.get("validation_status") != "valid":
                action_valid = False
                issues.append(
                    ValidationIssue(
                        code="invalid_corporate_action_status",
                        severity="error",
                        field=f"{prefix}.validation_status",
                        message="公司行动 validation_status 必须是 valid",
                    )
                )
            if action_id is not None and action_valid and factor is not None:
                valid_actions[action_id] = (factor, action_evidence_ids or [])
                validated_action_evidence_ids.update(action_evidence_ids or [])

        checked: list[str] = []
        available_calculation_ids: list[str] = []
        parsed_calculations: list[CalculationResult] = []
        for raw_calculation in calculations:
            calculation = CalculationResult.model_validate(raw_calculation)
            parsed_calculations.append(calculation)
            calculation_payload = _payload(raw_calculation)
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

            adjustment_basis = _calculation_field(
                calculation,
                calculation_payload,
                "adjustment_basis",
            )
            split_adjusted = (
                calculation.formula_id == "share_dilution"
                and adjustment_basis == "split_adjusted"
            )
            split_metadata_valid = True
            expected_adjustment_factor: Decimal | None = None
            reported_prior_comparable: Decimal | None = None
            if split_adjusted:
                if scan_status != "checked":
                    split_metadata_valid = False
                    issues.append(
                        ValidationIssue(
                            code="split_scan_unavailable",
                            severity="error",
                            field=calculation.calculation_id,
                            message="split_adjusted calculation 需要 checked 的公司行动扫描",
                        )
                    )
                referenced_action_ids = _string_list(
                    _calculation_field(
                        calculation,
                        calculation_payload,
                        "corporate_action_ids",
                    )
                )
                if referenced_action_ids is None:
                    split_metadata_valid = False
                    issues.append(
                        ValidationIssue(
                            code="missing_corporate_action_ids",
                            severity="error",
                            field=f"{calculation.calculation_id}.corporate_action_ids",
                            message="split_adjusted calculation 必须声明 corporate_action_ids",
                        )
                    )
                else:
                    if len(referenced_action_ids) != len(set(referenced_action_ids)):
                        split_metadata_valid = False
                        issues.append(
                            ValidationIssue(
                                code="duplicate_corporate_action_reference",
                                severity="error",
                                field=f"{calculation.calculation_id}.corporate_action_ids",
                                message="corporate_action_ids 不能重复",
                            )
                        )
                    if all(action_id in valid_actions for action_id in referenced_action_ids):
                        expected_adjustment_factor = Decimal("1")
                        for action_id in referenced_action_ids:
                            expected_adjustment_factor *= valid_actions[action_id][0]
                    else:
                        split_metadata_valid = False
                        for action_id in referenced_action_ids:
                            if action_id not in action_ids:
                                code = "unknown_corporate_action_id"
                                message = f"找不到公司行动 ID：{action_id}"
                            elif action_id not in valid_actions:
                                code = "invalid_corporate_action_reference"
                                message = f"公司行动无效，不能用于计算：{action_id}"
                            else:
                                continue
                            issues.append(
                                ValidationIssue(
                                    code=code,
                                    severity="error",
                                    field=f"{calculation.calculation_id}.corporate_action_ids",
                                    message=message,
                                )
                            )

                reported_factor: Decimal | None = None
                try:
                    reported_factor = _decimal(
                        _calculation_field(
                            calculation,
                            calculation_payload,
                            "adjustment_factor",
                        )
                    )
                    if reported_factor <= 0:
                        raise ValueError("adjustment_factor 必须是正数")
                except ValueError as exc:
                    split_metadata_valid = False
                    issues.append(
                        ValidationIssue(
                            code="invalid_calculation_adjustment_factor",
                            severity="error",
                            field=f"{calculation.calculation_id}.adjustment_factor",
                            message=str(exc),
                        )
                    )
                if (
                    expected_adjustment_factor is not None
                    and reported_factor is not None
                    and reported_factor != expected_adjustment_factor
                ):
                    split_metadata_valid = False
                    issues.append(
                        ValidationIssue(
                            code="calculation_adjustment_factor_mismatch",
                            severity="error",
                            field=f"{calculation.calculation_id}.adjustment_factor",
                            message=(
                                f"报告调整因子 {reported_factor} 与公司行动重算值 "
                                f"{expected_adjustment_factor} 不一致"
                            ),
                        )
                    )
                try:
                    reported_prior_comparable = _decimal(
                        _calculation_field(
                            calculation,
                            calculation_payload,
                            "shares_prior_comparable",
                        )
                    )
                    if reported_prior_comparable <= 0:
                        raise ValueError("shares_prior_comparable 必须是正数")
                except ValueError as exc:
                    split_metadata_valid = False
                    issues.append(
                        ValidationIssue(
                            code="invalid_shares_prior_comparable",
                            severity="error",
                            field=f"{calculation.calculation_id}.shares_prior_comparable",
                            message=str(exc),
                        )
                    )
            if calculation.status != "available" or calculation.raw_result is None:
                if (
                    calculation.formula_id == "share_dilution"
                    and scan_status == "unavailable"
                ):
                    code = "share_count_comparability_unverified"
                    message = "公司行动扫描不可用，跨期股份可比性未验证"
                else:
                    code = "unavailable_calculation"
                    message = "计算结果不可用，跳过重算"
                issues.append(
                    ValidationIssue(
                        code=code,
                        severity="warning",
                        field=calculation.calculation_id,
                        message=message,
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
                if split_adjusted:
                    if not split_metadata_valid or expected_adjustment_factor is None:
                        # Never fall back to raw prior shares after split metadata fails.
                        continue
                    if reported_prior_comparable is None:
                        continue
                    expected_prior_comparable = (
                        raw_inputs["shares_prior"] * expected_adjustment_factor
                    )
                    if reported_prior_comparable != expected_prior_comparable:
                        issues.append(
                            ValidationIssue(
                                code="shares_prior_comparable_mismatch",
                                severity="error",
                                field=(
                                    f"{calculation.calculation_id}."
                                    "shares_prior_comparable"
                                ),
                                message=(
                                    f"报告可比 prior {reported_prior_comparable} 与重算值 "
                                    f"{expected_prior_comparable} 不一致"
                                ),
                            )
                        )
                        continue
                    recomputed, _ = calculate_formula(
                        calculation.formula_id,
                        {
                            "shares_current": raw_inputs["shares_current"],
                            "shares_prior": expected_prior_comparable,
                        },
                    )
                else:
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
        has_available = any(item.status == "available" for item in parsed_calculations)
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
        validated_evidence_ids = (
            []
            if fact_errors
            else sorted(evidence_ids | validated_action_evidence_ids)
        )
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
