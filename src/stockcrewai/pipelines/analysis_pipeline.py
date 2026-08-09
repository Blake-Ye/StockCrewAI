"""Analysis 阶段的纯输入、诊断和 Claim 汇总函数。"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from stockcrewai.pipelines.valuation_pipeline import (
    _REVERSE_DCF_APPLICABILITY_REASONS,
    _current_valuation_gate,
    _reverse_dcf_policy_reason,
    _reverse_dcf_reason_codes,
)
from stockcrewai.tools.edgar_tool import EdgarResult
from stockcrewai.validators.claim_gate import ANALYSIS_DOMAIN_RULES, validate_claim


_ANALYSIS_DOMAINS = ("financial", "risk", "valuation")
VERDICT_RISK_INPUT_POLICY_VERSION = "risk_claim_presence_v1"
_SENSITIVE_FIELD_RE = re.compile(
    r"(?P<field>[\"']?[\w.-]*(?:API[_-]?KEY|KEY|TOKEN|SECRET|PASSWORD)[\w.-]*[\"']?\s*[:=]\s*)"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)",
    re.IGNORECASE,
)
_SENSITIVE_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def _json_safe(value: Any) -> Any:
    """递归把模型、日期、Decimal 和容器转换为 JSON-safe 值。"""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (date, datetime, Decimal)):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return str(value)
    return value


def _crew_output(result: Any) -> Any:
    """统一读取结构化输出、Pydantic 输出或原始文本。"""
    json_dict = getattr(result, "json_dict", None)
    if isinstance(json_dict, (Mapping, list, tuple)):
        return _json_safe(json_dict)
    pydantic = getattr(result, "pydantic", None)
    if hasattr(pydantic, "model_dump"):
        return _json_safe(pydantic)
    raw = getattr(result, "raw", None)
    if raw is not None:
        return _json_safe(raw)
    return _json_safe(result)


def _sensitive_environment_values() -> tuple[str, ...]:
    """收集当前进程中可能属于密钥的非空环境变量值。"""
    values = {
        value
        for name, value in os.environ.items()
        if value and any(marker in name.upper() for marker in _SENSITIVE_ENV_MARKERS)
    }
    return tuple(sorted(values, key=lambda value: (-len(value), value)))


def _redact_sensitive_text(value: str, sensitive_values: tuple[str, ...]) -> str:
    """对文本中的配置密钥和环境变量值执行确定性脱敏。"""

    def replace_field(match: re.Match[str]) -> str:
        raw_value = match.group("value")
        quote = raw_value[0] if raw_value[:1] in {"\"", "'"} else ""
        return f"{match.group('field')}{quote}[REDACTED]{quote}"

    redacted = _SENSITIVE_FIELD_RE.sub(replace_field, value)
    for sensitive_value in sensitive_values:
        redacted = redacted.replace(sensitive_value, "[REDACTED]")
    return redacted


def _redact_sensitive_value(value: Any, sensitive_values: tuple[str, ...]) -> Any:
    """递归遍历任意结果并脱敏，同时保持 JSON-safe 结构。"""
    safe_value = _json_safe(value)
    if isinstance(safe_value, str):
        return _redact_sensitive_text(safe_value, sensitive_values)
    if isinstance(safe_value, Mapping):
        return {
            str(key): _redact_sensitive_value(item, sensitive_values)
            for key, item in safe_value.items()
        }
    if isinstance(safe_value, list):
        return [_redact_sensitive_value(item, sensitive_values) for item in safe_value]
    return safe_value


def _analysis_raw_task_outputs(task_outputs: Any) -> dict[str, Any]:
    """按固定财务、风险、估值顺序保存并脱敏任务原始输出。"""
    if not isinstance(task_outputs, (list, tuple)):
        task_outputs = ()
    sensitive_values = _sensitive_environment_values()
    return {
        domain: _redact_sensitive_value(
            getattr(task_outputs[index], "raw", None)
            if index < len(task_outputs)
            else None,
            sensitive_values,
        )
        for index, domain in enumerate(_ANALYSIS_DOMAINS)
    }


def _analysis_diagnostic(
    task_outputs: Any,
    domain: str,
    reason_code: str,
) -> dict[str, Any]:
    """构造安全、稳定的 Analysis 诊断对象。"""
    domain_names = {
        "financial": "财务",
        "risk": "风险",
        "valuation": "估值",
        "pipeline": "流程",
    }
    prefix = domain_names.get(domain, "Analysis")
    reason_templates = {
        "task_output_count_invalid": "Analysis 任务输出数量不是 3 个。",
        "raw_json_invalid": f"{prefix}分析任务输出不是有效 JSON。",
        "payload_shape_invalid": f"{prefix}分析任务输出不是 claims 对象。",
        "claim_schema_invalid": f"{prefix} Claim 字段结构无效。",
        "claim_text_empty": f"{prefix} Claim 文本为空。",
        "category_invalid": f"{prefix} Claim 类别不在允许范围内。",
        "evidence_ids_invalid": f"{prefix} Claim 的 Evidence ID 无效。",
        "calculation_ids_invalid": f"{prefix} Claim 的 Calculation ID 无效。",
        "required_categories_missing": f"{prefix} Claim 缺少必需类别。",
        "claims_empty": f"{prefix}未生成 Claim。",
        "analysis_output_invalid": "Analysis 输出无法归类。",
    }
    return {
        "domain": domain,
        "reason_code": reason_code,
        "reason": reason_templates.get(reason_code, "Analysis 输出无法归类。"),
        "raw_task_outputs": _analysis_raw_task_outputs(task_outputs),
    }


def _filter_analysis_claims_with_diagnostics(
    output: Any,
    financial_evidence_ids: list[str],
    risk_filing_evidence_ids: list[str],
    valuation_evidence_ids: list[str],
    validated_calculation_ids: list[str],
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None]:
    """解析并按固定域规则验证 Analysis Crew 的三个输出。"""
    calculation_allowlist = set(validated_calculation_ids)
    task_outputs = getattr(output, "tasks_output", None)
    if not isinstance(task_outputs, (list, tuple)) or len(task_outputs) != 3:
        return (
            [],
            ["analysis_output_invalid"],
            _analysis_diagnostic(task_outputs, "pipeline", "task_output_count_invalid"),
        )

    financial_categories, financial_requires_calculations = ANALYSIS_DOMAIN_RULES[
        "financial"
    ]
    risk_categories, risk_requires_calculations = ANALYSIS_DOMAIN_RULES["risk"]
    valuation_categories, valuation_requires_calculations = ANALYSIS_DOMAIN_RULES[
        "valuation"
    ]
    domain_specs = (
        (
            "financial",
            set(financial_categories),
            set(financial_categories),
            "financial_analysis_claims_required",
            financial_requires_calculations,
            set(financial_evidence_ids),
        ),
        (
            "risk",
            set(risk_categories),
            set(risk_categories),
            "risk_analysis_claims_required",
            risk_requires_calculations,
            set(risk_filing_evidence_ids),
        ),
        (
            "valuation",
            set(valuation_categories),
            set(),
            "valuation_analysis_claims_required",
            valuation_requires_calculations,
            set(valuation_evidence_ids),
        ),
    )
    claims: list[dict[str, Any]] = []
    for task_output, (
        domain,
        allowed_categories,
        required_categories,
        missing_code,
        requires_calculations,
        evidence_allowlist,
    ) in zip(task_outputs, domain_specs):
        try:
            payload = _crew_output(task_output)
        except Exception:
            return (
                [],
                ["analysis_output_invalid"],
                _analysis_diagnostic(task_outputs, domain, "analysis_output_invalid"),
            )
        if isinstance(payload, str):
            raw = payload.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                payload = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                return (
                    [],
                    ["analysis_output_invalid"],
                    _analysis_diagnostic(task_outputs, domain, "raw_json_invalid"),
                )
        if (
            not isinstance(payload, Mapping)
            or set(payload) != {"claims"}
            or not isinstance(payload.get("claims"), list)
        ):
            return (
                [],
                ["analysis_output_invalid"],
                _analysis_diagnostic(task_outputs, domain, "payload_shape_invalid"),
            )
        raw_claims = payload["claims"]
        if not raw_claims:
            return (
                [],
                [missing_code],
                _analysis_diagnostic(task_outputs, domain, "claims_empty"),
            )
        domain_claims: list[dict[str, Any]] = []
        categories: set[str] = set()
        for item in raw_claims:
            validated_claim, reason_code = validate_claim(
                item,
                allowed_categories=allowed_categories,
                evidence_allowlist=evidence_allowlist,
                calculation_allowlist=calculation_allowlist,
                requires_calculations=requires_calculations,
            )
            if validated_claim is None:
                return (
                    [],
                    ["analysis_output_invalid"],
                    _analysis_diagnostic(
                        task_outputs,
                        domain,
                        reason_code or "analysis_output_invalid",
                    ),
                )
            categories.add(validated_claim["category"])
            domain_claims.append(validated_claim)
        if not required_categories.issubset(categories):
            return (
                [],
                [missing_code],
                _analysis_diagnostic(
                    task_outputs, domain, "required_categories_missing"
                ),
            )
        claims.extend(domain_claims)
    return claims, [], None


def _filter_analysis_claims(
    output: Any,
    financial_evidence_ids: list[str],
    risk_filing_evidence_ids: list[str],
    valuation_evidence_ids: list[str],
    validated_calculation_ids: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """提供不带诊断返回值的 Claim Gate 兼容接口。"""
    claims, required_data, _ = _filter_analysis_claims_with_diagnostics(
        output,
        financial_evidence_ids,
        risk_filing_evidence_ids,
        valuation_evidence_ids,
        validated_calculation_ids,
    )
    return claims, required_data


def _financial_analysis_input(state: dict[str, Any]) -> dict[str, Any]:
    """构造 FinancialQualityAgent 的最小、已验证输入包。"""
    return {
        "company_name": state.get("company_name"),
        "ticker": state.get("ticker"),
        "facts": _json_safe(state.get("facts", {})),
        "calculations": _json_safe(state.get("calculations", [])),
        "validated_evidence_ids": list(state.get("validated_evidence_ids", [])),
        "validated_calculation_ids": list(state.get("validated_calculation_ids", [])),
        "policy_context": _json_safe(state.get("policy_context", {})),
    }


def _risk_analysis_input(
    edgar_result: EdgarResult, state: dict[str, Any]
) -> dict[str, Any]:
    """构造 RiskAnalysisAgent 的可审计 filing 输入包。"""
    validated_filing_ids = {
        evidence_id
        for evidence_id in state.get("validated_filing_ids", [])
        if isinstance(evidence_id, str) and evidence_id
    }
    filings: list[dict[str, Any]] = []
    for filing in edgar_result.filings:
        if filing.evidence_id not in validated_filing_ids:
            continue
        eligibility = _json_safe(filing.risk_eligibility)
        sections = _json_safe(filing.risk_sections)
        if not (
            isinstance(eligibility, Mapping)
            and eligibility.get("eligibility") == "eligible"
            and eligibility.get("evidence_id") == filing.evidence_id
            and eligibility.get("evidence_kind") in {"item_1a", "substantive_8k_event"}
            and eligibility.get("source_reference")
            and filing.text_retrieval_status == "available"
            and isinstance(sections, list)
            and sections
            and all(
                isinstance(section, Mapping)
                and section.get("complete") is True
                and isinstance(section.get("text"), str)
                and bool(section["text"].strip())
                for section in sections
            )
        ):
            continue
        if isinstance(sections, list):
            sections = [
                {
                    key: section.get(key)
                    for key in ("section_type", "section_title", "text", "complete")
                    if section.get(key) is not None
                }
                for section in sections
            ]
        payload = _json_safe(filing)
        if isinstance(payload, dict) and isinstance(sections, list):
            filings.append(
                {
                    key: payload.get(key)
                    for key in (
                        "evidence_id",
                        "cik",
                        "form",
                        "filed_at",
                        "period_end",
                        "accession_number",
                        "source_reference",
                        "text_source_reference",
                        "risk_eligibility",
                    )
                    if payload.get(key) is not None
                }
                | {"risk_sections": sections}
            )
    return {
        "status": "available" if filings else "unavailable",
        "company_name": _json_safe(edgar_result.company_name),
        "ticker": _json_safe(edgar_result.ticker),
        "filings": filings,
        "validated_filing_ids": sorted(
            str(filing["evidence_id"])
            for filing in filings
            if filing.get("evidence_id")
        ),
        "policy_context": _json_safe(state.get("policy_context", {})),
    }


def _verdict_risk_input(analysis: Any) -> dict[str, Any]:
    """从 Claim Gate 已接受的风险 Claims 构造确定性 Verdict 输入。"""
    if not isinstance(analysis, (list, tuple)):
        return {"status": "unavailable"}

    risk_claims = [
        claim
        for claim in analysis
        if isinstance(claim, Mapping) and claim.get("category") == "risk"
    ]
    claim_ids = sorted(
        {
            claim_id.strip()
            for claim in risk_claims
            if isinstance(claim_id := claim.get("claim_id"), str)
            and claim_id.strip()
        }
    )
    evidence_ids = sorted(
        {
            evidence_id.strip()
            for claim in risk_claims
            for evidence_id in claim.get("evidence_ids", [])
            if isinstance(evidence_id, str) and evidence_id.strip()
        }
    )
    if not claim_ids or not evidence_ids:
        return {"status": "unavailable"}
    return _json_safe(
        {
            "status": "available",
            "risk_level": "medium",
            "claim_ids": claim_ids,
            "evidence_ids": evidence_ids,
            "policy_version": VERDICT_RISK_INPUT_POLICY_VERSION,
        }
    )


def _analysis_gate(
    validation_result: Any,
    state: dict[str, Any],
    risk_input: dict[str, Any],
    valuation: dict[str, Any],
    historical_valuation: dict[str, Any],
    reverse_dcf: dict[str, Any],
) -> dict[str, Any]:
    """执行 Analysis Crew 之前的确定性完整性门禁。"""
    required_data: list[str] = []
    limitations: list[str] = []
    applicability: dict[str, dict[str, Any]] = {}
    validated_facts = any(
        isinstance(fact, Mapping) and fact.get("validation_status") == "valid"
        for fact in state.get("facts", {}).values()
    )
    validated_calculations = any(
        isinstance(calculation, Mapping)
        and calculation.get("validation_status") == "valid"
        for calculation in state.get("calculations", [])
    )
    if not (
        validation_result.status == "valid"
        and validation_result.validated
        and validated_facts
        and validated_calculations
        and state.get("validated_evidence_ids")
        and state.get("validated_calculation_ids")
    ):
        required_data.append("financial_evidence_and_calculations_required")

    risk_ids = risk_input.get("validated_filing_ids")
    risk_filings = risk_input.get("filings")
    risk_id_allowlist = set(risk_ids) if isinstance(risk_ids, list) else set()
    risk_evidence_ready = (
        isinstance(risk_ids, list)
        and bool(risk_ids)
        and isinstance(risk_filings, list)
        and any(
            isinstance(filing, Mapping)
            and filing.get("evidence_id") in risk_id_allowlist
            and isinstance(filing.get("risk_eligibility"), Mapping)
            and filing["risk_eligibility"].get("eligibility") == "eligible"
            and isinstance(filing.get("risk_sections"), list)
            and filing["risk_sections"]
            and all(
                isinstance(section, Mapping)
                and section.get("complete") is True
                and isinstance(section.get("text"), str)
                and bool(section["text"].strip())
                for section in filing["risk_sections"]
            )
            for filing in risk_filings
        )
    )
    if not risk_evidence_ready:
        required_data.append("risk_evidence_missing")

    current_valuation = _current_valuation_gate(valuation)
    applicability["current_valuation"] = current_valuation
    if current_valuation["status"] == "partial":
        metrics = ", ".join(current_valuation["audited_metrics"])
        limitations.append(
            f"当前估值为部分可用：P/E 可能不可用，但已保留可审计指标（{metrics}）。"
        )
    elif current_valuation["status"] == "required":
        required_data.append("current_valuation_required")
    if not (
        historical_valuation.get("status") == "ok"
        and historical_valuation.get("validation_status") == "valid"
    ):
        required_data.append("historical_valuation_required")

    reverse_reason_codes = _reverse_dcf_reason_codes(reverse_dcf)
    reverse_dcf_status = (
        "applicable"
        if reverse_dcf.get("status") == "ok"
        and reverse_dcf.get("validation_status") == "valid"
        else "required"
    )
    reverse_applicability: dict[str, Any] = {
        "status": reverse_dcf_status,
        "reason_codes": reverse_reason_codes,
    }
    if reverse_dcf_status == "required":
        applicable_reason = _reverse_dcf_policy_reason(state, reverse_dcf)
        if applicable_reason and set(reverse_reason_codes) & _REVERSE_DCF_APPLICABILITY_REASONS:
            reverse_applicability.update(
                {
                    "status": "not_applicable",
                    "reason_code": applicable_reason,
                    "policy": "deterministic",
                }
            )
            reason_text = ", ".join(reverse_reason_codes) or "unavailable"
            limitations.append(
                f"反向 DCF 不适用（确定性 policy={applicable_reason}；工具原因={reason_text}）。"
            )
        else:
            required_data.append("reverse_dcf_required")
    applicability["reverse_dcf"] = reverse_applicability
    return {
        "status": "blocked" if required_data else "ready",
        "required_data": required_data,
        "limitations": limitations,
        "applicability": applicability,
    }


def _blocked_analysis_result(
    deterministic_outputs: dict[str, Any],
    required_data: list[str],
    analysis_diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造标准化的 Analysis 阻断结果。"""
    result = {
        **deterministic_outputs,
        "status": "blocked",
        "stage": "analysis",
        "analysis": None,
        "report": None,
        "required_data": required_data,
        "next_action": "补齐 required_data 后重新运行",
    }
    if analysis_diagnostics is not None:
        result["analysis_diagnostics"] = _json_safe(analysis_diagnostics)
    return result


__all__ = [
    "VERDICT_RISK_INPUT_POLICY_VERSION",
    "_analysis_diagnostic",
    "_analysis_gate",
    "_analysis_raw_task_outputs",
    "_blocked_analysis_result",
    "_crew_output",
    "_financial_analysis_input",
    "_filter_analysis_claims",
    "_filter_analysis_claims_with_diagnostics",
    "_json_safe",
    "_redact_sensitive_text",
    "_redact_sensitive_value",
    "_risk_analysis_input",
    "_sensitive_environment_values",
    "_verdict_risk_input",
]
