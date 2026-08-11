"""流水线兼容边界。

Evidence、Analysis、Valuation 和 Claim Gate 的确定性实现分别位于
pipelines/* 与 validators/claim_gate.py。本模块只保留请求解析、CrewAI
运行时和 Crew 注入等尚未迁移的小工具，并 re-export 旧名称。

WP13-S06 兼容边界记录：canonical 实现已经在 pipelines/validators，当前保留
legacy re-export 是因为 main/flow 和测试仍有直接调用或 patch；只有未来
`rg -n 'pipeline_support' src tests` 为零且迁移测试通过，才可由独立任务删除。
本次不清理 `.env`、运行产物或 ignored cache。
"""

# This module intentionally re-exports the legacy public surface.
# ruff: noqa: F401

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from stockcrewai.crews.request_parser.crew import ParsedRequest, RequestParserCrew
from stockcrewai.pipelines.analysis_pipeline import (
    VERDICT_RISK_INPUT_POLICY_VERSION,
    _ANALYSIS_DOMAINS,
    _SENSITIVE_ENV_MARKERS,
    _SENSITIVE_FIELD_RE,
    _analysis_diagnostic,
    _analysis_gate,
    _analysis_raw_task_outputs,
    _blocked_analysis_result,
    _crew_output,
    _financial_analysis_input,
    _filter_analysis_claims,
    _filter_analysis_claims_with_diagnostics,
    _redact_sensitive_text,
    _redact_sensitive_value,
    _risk_analysis_input,
    _sensitive_environment_values,
    _verdict_risk_input,
)
from stockcrewai.pipelines.evidence_pipeline import (
    _calculation_facts,
    _edgar_error,
    _historical_financial_snapshots,
    _historical_prices,
    _json_safe,
    _market_price_kwargs,
    _policy_calculation_records,
    _policy_evidence_records,
    _profile_metadata_from_legacy,
    _profile_policy_gate,
    _profile_result,
    _synchronized_outputs,
    _ttm_unavailable,
    _validated_state,
    _with_validation_status,
    build_profile_policy_context,
    profile_metadata_from_edgar,
    sync_validation_status,
    validate_ttm_evidence,
)
from stockcrewai.pipelines.valuation_pipeline import (
    _CURRENT_VALUATION_CALCULATION_IDS,
    _VALUATION_CALCULATION_REGISTRY,
    _REVERSE_DCF_APPLICABILITY_REASONS,
    _current_valuation_gate,
    _deterministic_verdict,
    _numeric_policy_value,
    _policy_token,
    _reverse_dcf_inputs,
    _reverse_dcf_policy_reason,
    _reverse_dcf_reason_codes,
    _valuation_analysis_input,
    _valuation_facts,
    build_deterministic_valuation_claims,
)
from stockcrewai.validators.claim_gate import (
    ANALYSIS_DOMAIN_RULES,
    AnalysisClaim,
    AnalysisTaskOutput,
    Claim,
    ClaimSchema,
    validate_claim,
)


DEFAULT_REQUEST = "分析苹果公司未来 3 年投资价值"


class _NoopTaskOutputStorageHandler:
    """为单次运行提供不落 SQLite 的 CrewAI 任务输出存储替身。"""

    persistent = False

    def add(self, *args: Any, **kwargs: Any) -> None:
        """忽略 CrewAI 的任务输出写入。"""
        return None

    def update(self, *args: Any, **kwargs: Any) -> None:
        """忽略 CrewAI 的任务输出更新。"""
        return None

    def reset(self) -> None:
        """保持无状态，不执行清理。"""
        return None

    def load(self) -> list[dict[str, Any]]:
        """返回空历史，避免产生任务输出 artifact。"""
        return []


def _configure_crewai_runtime() -> None:
    """关闭 CrewAI 任务输出历史，但不替换 Flow persistence。"""
    from crewai.crew import Crew

    private_attribute = getattr(Crew, "__private_attributes__", {}).get(
        "_task_output_handler"
    )
    if private_attribute is not None:
        private_attribute.default_factory = _NoopTaskOutputStorageHandler


def run_request_parser(request: str = DEFAULT_REQUEST):
    """运行 Request Parser Crew 并返回原始 CrewAI 输出对象。"""
    _configure_crewai_runtime()
    return RequestParserCrew().crew().kickoff(inputs={"request": request})


def _first_value(value: Any) -> str | None:
    """把候选字段规范化为首个非空字符串。"""
    if isinstance(value, list):
        value = next((item for item in value if item), None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _input_requirements(parsed_request: Mapping[str, Any]) -> dict[str, Any]:
    """检查结构化请求是否包含投资期限。"""
    horizon = _first_value(parsed_request.get("investment_horizon"))
    unspecified_values = {"", "UNSPECIFIED", "UNKNOWN", "未指定", "未提供"}
    if horizon and horizon.upper() not in unspecified_values:
        return {
            "status": "ready",
            "missing": [],
            "provided": {"investment_horizon": horizon},
        }
    return {
        "status": "needs_input",
        "missing": ["investment_horizon"],
        "provided": {},
        "message": "请提供投资期限，例如 3 年或长期投资。",
    }


def _parser_payload(result: Any) -> dict[str, Any]:
    """从 Request Parser Crew 输出中提取并校验结构化请求。"""
    payload = getattr(result, "json_dict", None)
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump()
    if not isinstance(payload, Mapping):
        raw = str(getattr(result, "raw", "")).strip()
        if raw.startswith(chr(96) * 3):
            raw = raw.split("\n", 1)[-1].rsplit(chr(96) * 3, 1)[0].strip()
        payload = json.loads(raw)
    if not isinstance(payload, Mapping):
        raise ValueError("请求解析结果必须是 JSON 对象")
    try:
        parsed = ParsedRequest.model_validate(payload)
    except (TypeError, ValidationError) as exc:
        raise ValueError(
            "请求解析结果必须严格符合 ParsedRequest 九字段契约"
        ) from exc
    return parsed.model_dump(mode="json")


def _crew_instance(candidate: Any, crew_factory: Any) -> Any:
    """把注入值解析为可调用 kickoff 的 Crew 实例。"""
    if candidate is None:
        return crew_factory().crew()
    if hasattr(candidate, "kickoff"):
        return candidate
    if hasattr(candidate, "crew"):
        return candidate.crew()
    return candidate


__all__ = [
    "DEFAULT_REQUEST",
    "ANALYSIS_DOMAIN_RULES",
    "AnalysisClaim",
    "AnalysisTaskOutput",
    "Claim",
    "ClaimSchema",
    "build_deterministic_valuation_claims",
    "build_profile_policy_context",
    "profile_metadata_from_edgar",
    "run_request_parser",
    "sync_validation_status",
    "validate_claim",
    "validate_ttm_evidence",
]
