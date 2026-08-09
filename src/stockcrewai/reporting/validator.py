from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictStr,
    ValidationError,
    field_validator,
)


REPORT_DRAFT_FIELDS = (
    "execution_summary",
    "company_quality",
    "financial_trend",
    "current_valuation",
    "historical_valuation",
    "reverse_dcf",
    "key_risks",
    "sources_and_method",
    "non_investment_disclaimer",
)
REPORT_ERROR_CODES = (
    "report_draft_not_json",
    "report_draft_schema_invalid",
    "report_draft_extra_fields",
    "report_draft_forbidden_number",
    "report_draft_forbidden_rating",
    "report_draft_forbidden_advice",
    "report_guardrail_retries_exhausted",
    "report_provider_error",
    "report_renderer_error",
    "report_final_validation_error",
)

_REPORT_ADVICE_RE = re.compile(
    r"(?:建议|推荐|应当|应该|可以考虑|适合|不妨|请勿|不要|不应|避免)"
    r"(?:买入|卖出|持有|增持|减持)"
    r"|(?:买入|卖出|持有|增持|减持)(?:建议|推荐|信号|评级|仓位|操作)"
    r"|投资建议|投资推荐|买卖建议"
    r"|\b(?:buy|sell|hold)\b(?:\s+(?:the\s+)?(?:stock|shares|position|rating))?",
    re.IGNORECASE,
)
_REPORT_DRAFT_ADVICE_RE = re.compile(
    r"(?:建议|推荐|应当|应该|可以考虑|适合|不妨|请勿|不要|不应|避免)"
    r"(?:买入|卖出|持有|增持|减持)"
    r"|(?:买入|卖出|持有|增持|减持)(?:建议|推荐|信号|评级|仓位|操作)"
    r"|投资建议|投资推荐|买卖建议|"
    r"\b(?:buy|sell|hold)\b(?:\s+(?:the\s+)?(?:stock|shares|position|rating))?",
    re.IGNORECASE,
)
_REPORT_DRAFT_VERDICT_RE = re.compile(
    r"值得投资|(?:具备|具有)(?:较强|较弱|明显|一定)?投资价值|"
    r"估值(?:偏贵|偏?便宜)|"
    r"(?:市场|当前|公司|该公司)(?:可能)?(?:高估|低估)|"
    r"(?:明显|严重)(?:高估|低估)|"
    r"(?:具备|具有|缺乏|没有|存在)安全边际|"
    r"前景(?:乐观|悲观)"
)
_REPORT_DISCLAIMER_RE = re.compile(
    r"(?:不构成|不提供|不代表)[^。！？!?；;\n]{0,80}"
    r"(?:投资建议|投资推荐|买卖建议)"
)
_REPORT_RATING_RE = re.compile(r"评级|\brating\b", re.IGNORECASE)
_REPORT_CLAIM_ID_RE = re.compile(r"\bclaim_[A-Za-z0-9_-]+\b")
_REPORT_STATUS_RE = re.compile(
    r"(?:status|确定性状态|确定性结论)\s*[:=：]|"
    r"\b(?:ready|blocked|insufficient_data)\b",
    re.IGNORECASE,
)


class ReportDraftError(ValueError):
    """带稳定错误码的 ReportDraft 解析/校验错误。"""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


def _draft_error_code(message: Any) -> str:
    text = str(message)
    for code in REPORT_ERROR_CODES:
        if code in text:
            return code
    return "report_draft_schema_invalid"


def _draft_text_violation(value: str, *, allow_advice: bool = False) -> str | None:
    if not value.strip():
        return "report_draft_schema_invalid: 字段必须是非空字符串。"
    if re.search(r"[0-9]", value):
        return "report_draft_forbidden_number: 草稿正文不得包含阿拉伯数字。"
    if "```" in value:
        return "report_draft_schema_invalid: 草稿正文不得包含代码围栏。"
    if not allow_advice and _REPORT_DRAFT_ADVICE_RE.search(value):
        return "report_draft_forbidden_advice: 草稿正文不得包含投资建议。"
    if not allow_advice and _REPORT_DRAFT_VERDICT_RE.search(value):
        return "report_draft_forbidden_advice: 草稿正文不得表达投资结论。"
    if _REPORT_RATING_RE.search(value):
        return "report_draft_forbidden_rating: 草稿正文不得包含评级。"
    if _REPORT_CLAIM_ID_RE.search(value):
        return "report_draft_schema_invalid: 草稿正文不得包含 Claim ID。"
    if _REPORT_STATUS_RE.search(value):
        return "report_draft_schema_invalid: 确定性 status 只能由 Python Renderer 注入。"
    return None


class ReportDraft(BaseModel):
    """Report Agent 的无数字叙述草稿契约。"""

    model_config = ConfigDict(extra="forbid")

    execution_summary: StrictStr
    company_quality: StrictStr
    financial_trend: StrictStr
    current_valuation: StrictStr
    historical_valuation: StrictStr
    reverse_dcf: StrictStr
    key_risks: StrictStr
    sources_and_method: StrictStr
    non_investment_disclaimer: StrictStr

    @field_validator(*REPORT_DRAFT_FIELDS[:-1])
    @classmethod
    def validate_text(cls, value: str) -> str:
        violation = _draft_text_violation(value)
        if violation is not None:
            raise ValueError(violation)
        return value

    @field_validator("non_investment_disclaimer")
    @classmethod
    def validate_non_investment_disclaimer(cls, value: str) -> str:
        violation = _draft_text_violation(value, allow_advice=True)
        if violation is not None:
            raise ValueError(violation)
        if not _REPORT_DISCLAIMER_RE.search(value):
            raise ValueError(
                "非投资建议声明必须明确表达不构成、不提供或不代表投资建议、投资推荐或买卖建议。"
            )
        return value


class _DuplicateJsonKey(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey("ReportDraft JSON 不得包含重复字段。")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"ReportDraft JSON 不允许常量：{value}。")


def _report_payload(value: Any) -> Any:
    if isinstance(value, (str, Mapping, ReportDraft)):
        return value
    pydantic = getattr(value, "pydantic", None)
    if isinstance(pydantic, ReportDraft):
        return pydantic
    raw = getattr(value, "raw", None)
    if raw is not None:
        return raw
    return value


def parse_report_draft(value: Any) -> ReportDraft:
    """把唯一 JSON 对象解析为经过正文规则校验的 ReportDraft。"""
    payload = _report_payload(value)
    if isinstance(payload, ReportDraft):
        return payload
    if isinstance(payload, Mapping):
        decoded: Any = dict(payload)
    elif isinstance(payload, str) and payload.strip():
        try:
            decoded = json.loads(
                payload,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ReportDraftError(
                "report_draft_not_json", "必须是唯一且有效的 JSON 对象。"
            ) from exc
    else:
        raise ReportDraftError("report_draft_not_json", "必须是非空 JSON 对象。")

    if not isinstance(decoded, dict):
        raise ReportDraftError("report_draft_not_json", "顶层必须是唯一 JSON 对象。")
    extra = set(decoded) - set(REPORT_DRAFT_FIELDS)
    if extra:
        raise ReportDraftError("report_draft_extra_fields", "包含额外字段。")
    try:
        return ReportDraft.model_validate(decoded)
    except ValidationError as exc:
        code = _draft_error_code(" ".join(str(error.get("msg", "")) for error in exc.errors()))
        raise ReportDraftError(code, "字段不符合固定九字段契约。") from exc


def validate_report_draft(output: Any) -> tuple[bool, Any]:
    """Report Task Guardrail：只接受合法的无数字 ReportDraft JSON。"""
    payload = _report_payload(output)
    try:
        parse_report_draft(payload)
    except ValueError as exc:
        return False, getattr(exc, "code", _draft_error_code(exc))
    return True, payload


def validate_rendered_report(
    report: Any, deterministic_status: str | None = None
) -> tuple[bool, Any]:
    """最终 Markdown 的最小安全检查，不比较或推断报告数字。"""
    if not isinstance(report, str) or not report.strip():
        return False, "最终报告必须是非空字符串。"
    if "```" in report:
        return False, "最终报告不得包含代码围栏。"
    in_disclaimer = False
    for line in report.splitlines():
        heading = line.strip()
        if heading == "## 非投资建议声明":
            in_disclaimer = True
            continue
        if heading.startswith("## "):
            in_disclaimer = False
        if not in_disclaimer and _REPORT_ADVICE_RE.search(line):
            return False, "最终报告不得包含买入、卖出、持有或其他投资建议。"
    if deterministic_status is not None:
        marker = f"确定性状态：status={deterministic_status}"
        if marker not in report:
            return False, "最终报告必须保留确定性 status。"
    return True, report


def validate_report_output(output: Any) -> tuple[bool, Any]:
    """兼容旧调用名；仅执行最终 Markdown 的非数字安全检查。"""
    payload = _report_payload(output)
    return validate_rendered_report(payload)


__all__ = [
    "REPORT_DRAFT_FIELDS",
    "REPORT_ERROR_CODES",
    "ReportDraft",
    "ReportDraftError",
    "parse_report_draft",
    "validate_rendered_report",
    "validate_report_draft",
    "validate_report_output",
]
