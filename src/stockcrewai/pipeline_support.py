"""StockCrewAI Flow 使用的确定性流水线支持函数。

本模块只承载从旧版 ``main.py`` 机械迁移的边界适配器：请求解析输出、
SEC/计算/验证状态、估值输入、Analysis Claim Gate、Verdict 调用以及
JSON 安全和敏感信息脱敏。来源选择、金融计算、验证、Claim Gate 和
Verdict 规则仍由 Python 工具和本模块的既有逻辑控制，不由 LLM 改写。
模块不导入 ``main`` 或 Flow，避免支持层与编排入口形成循环依赖。
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from stockcrewai.crews.analysis.crew import ANALYSIS_DOMAIN_RULES, AnalysisClaim
from stockcrewai.crews.request_parser.crew import ParsedRequest, RequestParserCrew
from stockcrewai.tools.edgar_tool import EdgarError, EdgarResult
from stockcrewai.tools.historical_valuation_tool import (
    HISTORICAL_VALUATION_CALCULATION_ID,
)
from stockcrewai.tools.reverse_dcf_tool import REVERSE_DCF_CALCULATION_ID
from stockcrewai.tools.validation_tool import sync_validation_status
from stockcrewai.tools.verdict_tool import DeterministicVerdictTool
from stockcrewai.tools.valuation_tool import VALUATION_FORMULAS
from pydantic import ValidationError


DEFAULT_REQUEST = "分析苹果公司未来 3 年投资价值"
_ANALYSIS_DOMAINS = ("financial", "risk", "valuation")
VERDICT_RISK_INPUT_POLICY_VERSION = "risk_claim_presence_v1"
_CURRENT_VALUATION_CALCULATION_IDS = frozenset(
    f"calc_{formula_id}" for formula_id in VALUATION_FORMULAS
)
_VALUATION_CALCULATION_REGISTRY = frozenset(
    {
        *_CURRENT_VALUATION_CALCULATION_IDS,
        HISTORICAL_VALUATION_CALCULATION_ID,
        REVERSE_DCF_CALCULATION_ID,
    }
)
_SENSITIVE_FIELD_RE = re.compile(
    r"(?P<field>[\"']?[\w.-]*(?:API[_-]?KEY|KEY|TOKEN|SECRET|PASSWORD)[\w.-]*[\"']?\s*[:=]\s*)"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)",
    re.IGNORECASE,
)
_SENSITIVE_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


class _NoopTaskOutputStorageHandler:
    """为本次运行提供一个不落 SQLite 的 CrewAI 任务输出历史存储替身。

    CrewAI 某些版本会在创建 Crew 时初始化任务输出存储。当前项目要求
    单次运行保持无状态，因此主流程在启动时把 CrewAI 的内部处理器替换
    为这个对象。该类只满足 CrewAI 需要的最小方法集合，不保存任何结果；
    它不参与、也不会关闭 CrewAI Flow 自身的 persistence。
    """

    persistent = False

    def add(self, *args: Any, **kwargs: Any) -> None:
        """忽略任务输出写入。

        参数由 CrewAI 内部调用方决定；这里保留可变参数是为了兼容不同
        CrewAI 版本的调用签名。返回值始终为 ``None``，不会改变外部状态。
        """
        return None

    def update(self, *args: Any, **kwargs: Any) -> None:
        """忽略已有任务输出的更新。

        该方法是无状态存储接口的兼容实现，接受但不处理 CrewAI 传入的
        标识或内容，避免产生 SQLite 文件或历史回放数据。
        """
        return None

    def reset(self) -> None:
        """执行空操作的重置。

        因为本类根本不保存任务输出，所以重置不需要删除任何数据。
        """
        return None

    def load(self) -> list[dict[str, Any]]:
        """返回空列表，表示没有可恢复的历史任务输出。

        返回值：
            空的字典列表，符合 CrewAI 对任务输出存储读取接口的预期。
        """
        return []


def _configure_crewai_runtime() -> None:
    """只关闭 CrewAI 任务输出历史，不伪装成 Flow persistence 配置。

    CrewAI 的任务输出处理器是内部私有属性，因此这里通过已安装版本的
    ``Crew.__private_attributes__`` 找到处理器并替换其默认工厂。若当前
    CrewAI 版本没有该属性，函数会安全地跳过配置，不影响主流程继续执行。

    该设置只影响 Crew 任务输出历史；``ResearchFlow`` 的 state/persistence
    由 CrewAI Flow API 单独决定，本函数不创建、删除或替换 Flow 的 SQLite
    后端。保留它是为了兼容现有无历史任务输出的运行约定和注入测试。

    副作用：
        修改当前 Python 进程中 CrewAI ``Crew`` 类的默认处理器；不会修改
        项目文件，也不会创建或清理已有数据库文件。
    """
    from crewai.crew import Crew

    private_attribute = getattr(Crew, "__private_attributes__", {}).get(
        "_task_output_handler"
    )
    if private_attribute is not None:
        private_attribute.default_factory = _NoopTaskOutputStorageHandler


def run_request_parser(request: str = DEFAULT_REQUEST):
    """运行 Request Parser Crew 并返回原始 CrewAI 输出对象。

    参数：
        request：用户希望分析的自然语言问题，例如“分析苹果公司未来 3 年
            投资价值”。
    返回：
        CrewAI 的 Crew 输出对象；调用方再通过 ``_parser_payload`` 提取
        结构化 JSON。该函数只负责请求解析，不执行 SEC 查询或估值。
    """
    _configure_crewai_runtime()
    return RequestParserCrew().crew().kickoff(inputs={"request": request})


def _first_value(value: Any) -> str | None:
    """把候选字段规范化为首个非空字符串。

    请求解析模型有时会返回单个值，也可能返回候选列表。该函数统一两种
    形态：列表取第一个真值，其他类型转为字符串，空字符串和 ``None``
    转换为 ``None``，方便后续身份和期限门禁判断。

    参数：
        value：单值、候选列表或缺失值。
    返回：
        清理后的字符串；没有有效内容时返回 ``None``。
    """
    if isinstance(value, list):
        value = next((item for item in value if item), None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _input_requirements(parsed_request: Mapping[str, Any]) -> dict[str, Any]:
    """检查结构化请求是否包含可用于期限匹配的投资期限。

    参数：
        parsed_request：Request Parser Crew 生成的请求对象。
    返回：
        包含 ``status``、``missing``、``provided`` 的门禁结果。期限缺失或
        被解析为未知时返回 ``needs_input``，但本函数不主动猜测期限。
    """
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
    """从 Request Parser Crew 输出中提取字典形式的请求结构。

    读取优先级是 CrewAI 的 ``json_dict``，其次是 Pydantic 模型，再其次是
    ``raw`` 文本。对 Markdown JSON 代码围栏做最小剥离后解析；如果结果不
    是 JSON 对象则抛出 ``ValueError``，交由上层 Flow 或兼容入口转换为可
    记录的流程错误。

    参数：
        result：CrewAI 输出对象或兼容的测试替身。
    返回：
        非空的请求字典。
    """
    payload = getattr(result, "json_dict", None)
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump()
    if not isinstance(payload, Mapping):
        raw = str(getattr(result, "raw", "")).strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
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


def _calculation_facts(edgar_result: EdgarResult) -> dict[str, Any]:
    """把 EDGAR 返回的事实整理成计算器使用的显式字段集合。

    该适配只补充别名，不改变原始 SEC 事实：将 ``revenue`` 映射为
    ``revenue_current``，将 ``common_shares_outstanding`` 映射为
    ``shares_current``。这样计算器可以使用稳定字段名，同时仍保留原始
    证据 ID、期间、单位和来源信息。

    参数：
        edgar_result：EDGAR 工具返回的公司、事实和 filing 结果。
    返回：
        可传给 ``FinancialCalculatorTool`` 的事实字典。
    """
    facts: dict[str, Any] = dict(edgar_result.facts)
    if "revenue" in facts and "revenue_current" not in facts:
        facts["revenue_current"] = facts["revenue"]
    if "common_shares_outstanding" in facts and "shares_current" not in facts:
        facts["shares_current"] = facts["common_shares_outstanding"]
    return facts


def _edgar_error(
    code: str,
    message: str,
    company_name: str | None = None,
    ticker: str | None = None,
) -> EdgarResult:
    """构造统一格式的 EDGAR 错误结果。

    参数：
        code：稳定的机器可读错误代码。
        message：面向日志和用户的错误说明。
        company_name：已知的公司名候选，可选。
        ticker：已知的股票代码候选，可选。
    返回：
        ``status='error'`` 的 ``EdgarResult``，使失败也能沿用主流程的
        JSON 输出契约，而不是直接抛出未结构化异常。
    """
    return EdgarResult(
        status="error",
        input_company_name=company_name,
        input_ticker=ticker,
        errors=[EdgarError(code=code, message=message)],
    )


def _ttm_unavailable(
    company_name: str | None,
    ticker: str | None,
    reason_code: str,
) -> dict[str, Any]:
    """构造不会阻断既有流水线的结构化 TTM unavailable 结果。"""
    return {
        "status": "unavailable",
        "company_name": company_name,
        "ticker": ticker,
        "metrics": [],
        "warnings": [],
        "reason_code": reason_code,
    }


def validate_ttm_evidence(
    ttm_inputs: Mapping[str, Mapping[str, Any]] | None,
    *,
    company_name: str | None,
    ticker: str | None,
    validation_tool: Any,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """独立验证 TTM Evidence，并把验证状态投影回三层输入字典。

    TTM 输入的 ``metric_id`` 和角色不能直接作为验证器顶层字典的同一键，
    因此先使用 ``metric_id:role`` 组成唯一 fact key。验证器只接收这些
    Evidence，不接收基础 Calculation；返回的 Evidence ID 白名单随后由
    ``sync_validation_status`` 投影回 ``metric -> role -> fact``，避免 Flow
    直接信任 SEC 原始 ``unvalidated`` 状态。
    """
    raw_inputs = ttm_inputs if isinstance(ttm_inputs, Mapping) else {}
    flattened: dict[str, Any] = {}
    locations: dict[str, tuple[str, str]] = {}
    for metric_id, by_role in raw_inputs.items():
        if not isinstance(by_role, Mapping):
            continue
        for role, raw_fact in by_role.items():
            metric_key = str(metric_id)
            role_key = str(role)
            fact_key = f"{metric_key}:{role_key}"
            flattened[fact_key] = raw_fact
            locations[fact_key] = (metric_key, role_key)

    fact_keys = list(flattened)
    if not flattened:
        return _json_safe(raw_inputs), {
            "status": "unavailable",
            "validated": False,
            "validated_evidence_ids": [],
            "validated_calculation_ids": [],
            "fact_keys": fact_keys,
            "reason_code": "ttm_evidence_missing",
        }

    try:
        validation_result = validation_tool.run(
            company_name=company_name,
            ticker=ticker,
            facts=flattened,
            calculations=[],
        )
        synced = sync_validation_status(flattened, [], validation_result)
        projected: dict[str, dict[str, Any]] = {}
        for fact_key, raw_fact in synced["facts"].items():
            metric_key, role_key = locations[fact_key]
            projected.setdefault(metric_key, {})[role_key] = raw_fact
        diagnostic = _json_safe(validation_result)
        if not isinstance(diagnostic, dict):
            diagnostic = {
                "status": "unavailable",
                "validated": False,
                "validated_evidence_ids": [],
                "validated_calculation_ids": [],
            }
        diagnostic["fact_keys"] = fact_keys
        diagnostic["fact_count"] = len(flattened)
        return projected, diagnostic
    except Exception as exc:
        return _json_safe(raw_inputs), {
            "status": "unavailable",
            "validated": False,
            "validated_evidence_ids": [],
            "validated_calculation_ids": [],
            "fact_keys": fact_keys,
            "fact_count": len(flattened),
            "issues": [
                {
                    "code": "ttm_validation_error",
                    "severity": "error",
                    "field": "ttm_inputs",
                    "message": f"TTM Evidence 验证失败：{type(exc).__name__}",
                }
            ],
        }


def _json_safe(value: Any) -> Any:
    """递归把模型、日期、Decimal 和容器转换为安全的 JSON 值。

    该函数是所有 Crew/工具边界的序列化适配器：Pydantic 模型使用
    ``model_dump``，日期和 Decimal 使用 ISO/字符串表示，非有限浮点数
    转为文本，无法由标准 JSON 编码器处理的对象也转为字符串。它不负责
    验证业务数值是否正确，只负责让结果可以安全打印、保存和再次传递。

    参数：
        value：任意工具、Crew 或模型输出。
    返回：
        只包含 JSON 可表达类型的等价结构。
    """
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


def _synchronized_outputs(
    edgar_result: EdgarResult,
    calculation_result: Any,
    validation_result: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """把批量验证器给出的状态回写到事实和计算结果。

    验证工具可能只返回允许的 evidence/calculation ID 集合，因此这里调用
    ``sync_validation_status`` 给每条事实和计算附加一致的验证状态，再做
    JSON 安全转换。该步骤保证后续 Analysis 输入和最终报告使用同一份验证
    结果，而不是一份带状态、一份不带状态。

    参数：
        edgar_result：包含原始事实的 EDGAR 结果。
        calculation_result：包含确定性计算结果的工具输出。
        validation_result：批量验证工具输出。
    返回：
        ``(edgar_output, calculation_output)`` 两个 JSON 安全字典。
    """
    synced = sync_validation_status(
        edgar_result.facts,
        calculation_result.calculations,
        validation_result,
    )
    edgar_output = _json_safe(edgar_result)
    calculation_output = _json_safe(calculation_result)
    if isinstance(edgar_output, dict):
        edgar_output["facts"] = synced["facts"]
    if isinstance(calculation_output, dict):
        calculation_output["calculations"] = synced["calculations"]
    return edgar_output, calculation_output


def _crew_output(result: Any) -> Any:
    """统一读取 CrewAI 输出，并尽可能保留结构化结果。

    读取顺序为 ``json_dict``、Pydantic 输出、``raw`` 原文，最后才把整个
    对象交给 ``_json_safe``。这样 Claim Gate 可以接收结构化输出，也能在
    模型没有按结构化接口返回时对原始 JSON 文本进行后续解析。

    参数：
        result：CrewAI 的 Crew/Task 输出对象或测试替身。
    返回：
        JSON 安全的字典、列表或文本。
    """
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
    """收集当前进程中可能属于密钥的非空环境变量值。

    只检查进程已经加载的 ``os.environ``，不会主动读取 ``.env`` 或其他
    文件。变量名包含 ``KEY``、``TOKEN``、``SECRET`` 或 ``PASSWORD`` 时，
    其值会加入脱敏列表；结果按长度从长到短排序，避免短值先替换导致
    长值只被部分替换。

    返回：
        去重后的敏感字符串元组；空值不会进入结果。
    """
    values = {
        value
        for name, value in os.environ.items()
        if value and any(marker in name.upper() for marker in _SENSITIVE_ENV_MARKERS)
    }
    return tuple(sorted(values, key=lambda value: (-len(value), value)))


def _redact_sensitive_text(value: str, sensitive_values: tuple[str, ...]) -> str:
    """对文本中的配置密钥和环境变量值执行确定性脱敏。

    函数先用 ``_SENSITIVE_FIELD_RE`` 处理形如 ``API_KEY=...`` 的字段，
    再替换当前进程中发现的敏感值。这样既能遮盖模型可能回显的配置片段，
    也能遮盖工具输出中的真实环境变量内容。不会修改输入字符串本身。

    参数：
        value：待输出或记录的原始文本。
        sensitive_values：由 ``_sensitive_environment_values`` 生成的值列表。
    返回：
        将敏感内容替换为 ``[REDACTED]`` 的文本。
    """

    def replace_field(match: re.Match[str]) -> str:
        """保留字段名和引号，只替换字段对应的值。"""
        raw_value = match.group("value")
        quote = raw_value[0] if raw_value[:1] in {"\"", "'"} else ""
        return f"{match.group('field')}{quote}[REDACTED]{quote}"

    redacted = _SENSITIVE_FIELD_RE.sub(replace_field, value)
    for sensitive_value in sensitive_values:
        redacted = redacted.replace(sensitive_value, "[REDACTED]")
    return redacted


def _redact_sensitive_value(value: Any, sensitive_values: tuple[str, ...]) -> Any:
    """递归遍历任意结果并脱敏，同时保持 JSON 安全结构。

    字符串交给 ``_redact_sensitive_text``，字典递归处理每个值，列表也
    逐项处理；其他类型先通过 ``_json_safe`` 转换。该函数主要用于记录
    Analysis 原始输出，防止诊断信息把 Prompt 或环境密钥写入日志文件。

    参数：
        value：任意 Crew/工具结果。
        sensitive_values：需要替换的敏感字符串。
    返回：
        结构不变但已完成脱敏的 JSON 安全值。
    """
    safe_value = _json_safe(value)
    if isinstance(safe_value, str):
        return _redact_sensitive_text(safe_value, sensitive_values)
    if isinstance(safe_value, Mapping):
        return {
            str(key): _redact_sensitive_value(item, sensitive_values)
            for key, item in safe_value.items()
        }
    if isinstance(safe_value, list):
        return [
            _redact_sensitive_value(item, sensitive_values) for item in safe_value
        ]
    return safe_value


def _analysis_raw_task_outputs(task_outputs: Any) -> dict[str, Any]:
    """按固定的财务、风险、确定性估值顺序保存 Analysis 任务输出。

    Analysis Crew 返回两个 LLM Task；主流程随后把 Python 生成的估值 Claims
    作为第三项输出加入 ``tasks_output``。本函数不让 LLM 决定域名称，而是
    按 ``_ANALYSIS_DOMAINS`` 的固定索引映射，缺失的位置填入 ``None``，并
    在保存前执行敏感信息脱敏。该结果只用于内部诊断，不会直接作为最终
    报告内容。

    参数：
        task_outputs：CrewAI 返回的 Task 输出列表或非列表值。
    返回：
        ``financial``、``risk``、``valuation`` 三个键组成的诊断字典。
    """
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
    """根据失败域和错误码构造安全、稳定的 Analysis 诊断对象。

    诊断只使用固定错误模板和经过脱敏的原始 Task 输出，不包含系统提示词、
    环境变量或密钥。错误码面向程序，中文 ``reason`` 面向用户；两者分开
    便于测试和后续界面展示。

    参数：
        task_outputs：发生错误时的原始 Task 输出集合。
        domain：失败所属域，可为 ``financial``、``risk``、``valuation`` 或
            ``pipeline``。
        reason_code：固定的失败原因代码。
    返回：
        包含域、错误码、中文说明和脱敏原始输出的字典。
    """
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
    """解析并验证 Analysis Crew 的三个域输出。

    这是 Analysis 阶段的 Claim Gate。函数按照固定顺序读取财务、风险两个
    Agent Task 和一个确定性估值输出，要求每个输出严格是
    ``{"claims": [...]}``，再逐项检查：

    - Claim 的 Pydantic 字段和文本是否完整；
    - category 是否属于当前域允许的类别；
    - evidence ID 是否存在于该域的验证白名单；
    - 需要计算的域是否提供有效 calculation ID；
    - 是否覆盖当前域要求的类别集合。

    任意一个域失败都会返回空 Claims、稳定的 ``required_data`` 错误码和
    脱敏诊断，不把部分 Claim 继续送入 Verdict 或 Report。这样可以避免
    “部分输出看起来可用”却绕过整体门禁。

    参数：
        output：Analysis Crew 的最终输出对象。
        financial_evidence_ids：财务域允许引用的已验证 Evidence ID。
        risk_filing_evidence_ids：风险域允许引用的 filing Evidence ID。
        valuation_evidence_ids：估值域允许引用的 Evidence ID。
        validated_calculation_ids：允许引用的已验证 Calculation ID。
    返回：
        ``(claims, required_data, diagnostics)``：已接受 Claims、阻断码列表、
        可选的内部诊断对象。
    """
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
            if not isinstance(item, Mapping):
                return (
                    [],
                    ["analysis_output_invalid"],
                    _analysis_diagnostic(task_outputs, domain, "claim_schema_invalid"),
                )
            statement = item.get("statement")
            if isinstance(statement, str) and not statement.strip():
                return (
                    [],
                    ["analysis_output_invalid"],
                    _analysis_diagnostic(task_outputs, domain, "claim_text_empty"),
                )
            category_value = item.get("category")
            if isinstance(category_value, str) and (
                not category_value.strip()
                or category_value.strip() not in allowed_categories
            ):
                return (
                    [],
                    ["analysis_output_invalid"],
                    _analysis_diagnostic(task_outputs, domain, "category_invalid"),
                )
            evidence_values = item.get("evidence_ids")
            if evidence_values is not None and (
                not isinstance(evidence_values, list)
                or any(not isinstance(identifier, str) for identifier in evidence_values)
            ):
                return (
                    [],
                    ["analysis_output_invalid"],
                    _analysis_diagnostic(task_outputs, domain, "evidence_ids_invalid"),
                )
            calculation_values = item.get("calculation_ids")
            if calculation_values is not None and (
                not isinstance(calculation_values, list)
                or any(
                    not isinstance(identifier, str)
                    for identifier in calculation_values
                )
            ):
                return (
                    [],
                    ["analysis_output_invalid"],
                    _analysis_diagnostic(task_outputs, domain, "calculation_ids_invalid"),
                )
            try:
                validated_claim = AnalysisClaim.model_validate(item)
            except (TypeError, ValueError):
                return (
                    [],
                    ["analysis_output_invalid"],
                    _analysis_diagnostic(task_outputs, domain, "claim_schema_invalid"),
                )
            except Exception:
                return (
                    [],
                    ["analysis_output_invalid"],
                    _analysis_diagnostic(
                        task_outputs, domain, "analysis_output_invalid"
                    ),
                )
            text_fields = ("claim_id", "category", "statement")
            if any(
                not getattr(validated_claim, field).strip()
                for field in text_fields
            ):
                return (
                    [],
                    ["analysis_output_invalid"],
                    _analysis_diagnostic(task_outputs, domain, "claim_schema_invalid"),
                )
            category = validated_claim.category.strip()
            if category not in allowed_categories:
                return (
                    [],
                    ["analysis_output_invalid"],
                    _analysis_diagnostic(task_outputs, domain, "category_invalid"),
                )
            evidence_ids = list(validated_claim.evidence_ids)
            calculation_ids = list(validated_claim.calculation_ids)
            if not evidence_ids or any(
                not identifier.strip() or identifier not in evidence_allowlist
                for identifier in evidence_ids
            ):
                return (
                    [],
                    ["analysis_output_invalid"],
                    _analysis_diagnostic(task_outputs, domain, "evidence_ids_invalid"),
                )
            if requires_calculations and (
                not calculation_ids
                or any(
                    not identifier.strip() or identifier not in calculation_allowlist
                    for identifier in calculation_ids
                )
            ):
                return (
                    [],
                    ["analysis_output_invalid"],
                    _analysis_diagnostic(
                        task_outputs, domain, "calculation_ids_invalid"
                    ),
                )
            if not requires_calculations and calculation_ids:
                return (
                    [],
                    ["analysis_output_invalid"],
                    _analysis_diagnostic(
                        task_outputs, domain, "calculation_ids_invalid"
                    ),
                )
            categories.add(category)
            domain_claims.append(
                {
                    "claim_id": validated_claim.claim_id.strip(),
                    "category": category,
                    "statement": validated_claim.statement.strip(),
                    "evidence_ids": list(evidence_ids),
                    "calculation_ids": list(calculation_ids),
                    "confidence": validated_claim.confidence,
                }
            )
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
    """提供不带诊断返回值的 Claim Gate 兼容接口。

    旧测试或旧调用方只需要 Claims 和阻断码，不需要内部诊断，因此本函数
    委托给 ``_filter_analysis_claims_with_diagnostics`` 并丢弃第三个返回值。
    真正的校验规则只维护在带诊断的实现中，避免两个入口产生不同的门禁行为。

    参数与返回值：
        参数含义与 ``_filter_analysis_claims_with_diagnostics`` 相同；返回
        ``(claims, required_data)``。
    """
    claims, required_data, _ = _filter_analysis_claims_with_diagnostics(
        output,
        financial_evidence_ids,
        risk_filing_evidence_ids,
        valuation_evidence_ids,
        validated_calculation_ids,
    )
    return claims, required_data


def _crew_instance(candidate: Any, crew_factory: Any) -> Any:
    """把依赖注入值解析为可调用 ``kickoff`` 的 Crew 实例。

    Flow 和兼容入口为测试和离线运行支持三种注入形态：直接传入 Crew 实例、
    传入具有 ``crew()`` 方法的 CrewBase 工厂，或不传值而使用默认工厂。本
    函数统一这三种形态，避免编排层中出现重复的类型判断。

    参数：
        candidate：调用方注入的 Crew、CrewBase 对象或 ``None``。
        crew_factory：默认 CrewBase 类，例如 ``AnalysisCrew``。
    返回：
        具有 ``kickoff`` 方法的 Crew 对象，或原样返回无法识别的替身，方便
        测试注入最小接口。
    """
    if candidate is None:
        return crew_factory().crew()
    if hasattr(candidate, "kickoff"):
        return candidate
    if hasattr(candidate, "crew"):
        return candidate.crew()
    return candidate


def _financial_analysis_input(state: dict[str, Any]) -> dict[str, Any]:
    """构造 FinancialQualityAgent 的最小、已验证输入包。

    只暴露公司身份、已验证事实、已验证计算结果及对应 ID 白名单，不把
    原始工具对象或未验证数据直接交给财务分析 Agent。Agent 的职责是解释
    这些事实，不是重新查询来源或重新计算指标。

    参数：
        state：由 ``_validated_state`` 构造并经过状态同步的主流程状态。
    返回：
        与 ``financial_analysis_input`` Task 占位符匹配的 JSON 安全字典。
    """
    return {
        "company_name": state.get("company_name"),
        "ticker": state.get("ticker"),
        "facts": _json_safe(state.get("facts", {})),
        "calculations": _json_safe(state.get("calculations", [])),
        "validated_evidence_ids": list(state.get("validated_evidence_ids", [])),
        "validated_calculation_ids": list(
            state.get("validated_calculation_ids", [])
        ),
    }


def _risk_analysis_input(
    edgar_result: EdgarResult, state: dict[str, Any]
) -> dict[str, Any]:
    """构造 RiskAnalysisAgent 的可审计 filing 输入包。

    函数只保留通过 Task 1 资格结果、正文检索成功且所有风险章节完整的
    申报文件，并把它们裁剪为 Agent 需要的来源、资格字段和风险文本。截断
    的 filing 预览不影响已经独立提取且标记完整的章节；没有满足条件的文件
    时返回 ``status='unavailable'``，而不是让 Agent 根据文件元数据猜测
    风险内容。

    参数：
        edgar_result：包含 filings 的 EDGAR 结果。
        state：包含已验证 filing ID 白名单的主流程状态。
    返回：
        ``status``、可审计 filings 和 ``validated_filing_ids`` 组成的字典。
    """
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
            and eligibility.get("evidence_kind")
            in {"item_1a", "substantive_8k_event"}
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
    }


def _verdict_risk_input(analysis: Any) -> dict[str, Any]:
    """从 Claim Gate 已接受的风险 Claims 构造确定性 Verdict 输入。

    该函数只读取 ``state.analysis`` 中的 JSON-safe Claim，不回看 Analysis
    原始输出。v1 仅按“存在已验证风险 Claim”映射为 ``medium``，表示数据
    完整性默认档，不表示对自然语言风险严重程度的评级。
    """
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


def _valuation_analysis_input(
    state: dict[str, Any],
    valuation: dict[str, Any],
    historical_valuation: dict[str, Any],
    reverse_dcf: dict[str, Any],
    trusted_evidence_ids: set[str] | None = None,
) -> dict[str, Any]:
    """构造确定性估值 Claim 构建器使用的已验证估值输入包。

    Evidence 白名单只来自调用方提供的独立 ``trusted_evidence_ids``，并与
    三类估值结果实际引用的 ID 做安全交集；未提供 trusted set 时只回退到
    state 原有的基础验证 Evidence ID，不会把待验证结果中的新 ID 自行加入。
    Calculation 白名单来自固定估值 Calculation 注册表与 state 原有已验证
    Calculation ID 的 union，也不会采纳估值结果自报的 Calculation ID。当前
    实现不把该 payload 交给 LLM，而是由构建器读取并继续交给 Claim Gate
    校验。

    参数：
        state：已验证的公司事实和基础计算状态。
        valuation：当前估值工具结果。
        historical_valuation：历史估值工具结果。
        reverse_dcf：反向 DCF 工具结果。
        trusted_evidence_ids：由 ``prepare_valuation`` 根据独立上游来源构造
            的可信 Evidence ID 集合；缺省时只使用 state 原有验证集合。
    返回：
        与确定性估值 Claim 构建器契约匹配的 JSON 安全字典。
    """
    state_evidence_ids = {
        item
        for item in state.get("validated_evidence_ids", [])
        if isinstance(item, str) and item
    }
    if trusted_evidence_ids is None:
        trusted_ids = state_evidence_ids
    elif isinstance(trusted_evidence_ids, (set, list, tuple)):
        trusted_ids = {
            item for item in trusted_evidence_ids if isinstance(item, str) and item
        }
    else:
        trusted_ids = set()

    def referenced_evidence_ids(payload: Any) -> set[str]:
        if not isinstance(payload, Mapping):
            return set()
        referenced: set[str] = set()
        for key in (
            "market_price_evidence_id",
            "price_evidence_id",
            "input_evidence_ids",
            "evidence_ids",
        ):
            values = payload.get(key)
            if isinstance(values, str):
                values = [values]
            if isinstance(values, list):
                referenced.update(
                    item for item in values if isinstance(item, str) and item
                )
        calculations = payload.get("calculations", [])
        if isinstance(calculations, list):
            for calculation in calculations:
                if not isinstance(calculation, Mapping):
                    continue
                values = calculation.get("input_evidence_ids", [])
                if isinstance(values, list):
                    referenced.update(
                        item for item in values if isinstance(item, str) and item
                    )
        return referenced

    referenced_ids = set().union(
        *(referenced_evidence_ids(payload) for payload in (valuation, historical_valuation, reverse_dcf))
    )
    evidence_ids = trusted_ids & referenced_ids
    calculation_ids = {
        item
        for item in state.get("validated_calculation_ids", [])
        if isinstance(item, str) and item
    }
    calculation_ids.update(_VALUATION_CALCULATION_REGISTRY)
    return {
        "company_name": state.get("company_name"),
        "ticker": state.get("ticker"),
        "facts": _json_safe(_valuation_facts(state)),
        "calculations": _json_safe(state.get("calculations", [])),
        "valuation_result": _json_safe(valuation),
        "historical_valuation_result": _json_safe(historical_valuation),
        "reverse_dcf_result": _json_safe(reverse_dcf),
        "validated_evidence_ids": sorted(evidence_ids),
        "validated_calculation_ids": sorted(calculation_ids),
    }


def build_deterministic_valuation_claims(
    valuation_input: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """从已验证估值 payload 按可用域生成固定顺序的 Claims。

    当前估值、历史估值和反向 DCF 的存在性由 Python 根据各自状态和
    payload 白名单决定，不由 LLM 决定。当前估值逐项跳过不可用 calculation；
    历史估值和反向 DCF 各自独立判断，某个估值域不可用不会清空其他域。
    调用方仍必须把该列表作为第三项任务输出交给现有
    ``_filter_analysis_claims_with_diagnostics``，由 Claim Gate 最终校验。

    参数：
        valuation_input：由 ``_valuation_analysis_input`` 产生的 JSON-safe 包。
    返回：
        通过 ``AnalysisClaim`` schema 的 JSON-safe Claim 字典列表，顺序固定
        为 current_valuation、historical_valuation、reverse_dcf 顺序返回可用域；
        没有可审计估值域时返回空列表。
    """
    if not isinstance(valuation_input, Mapping):
        return []

    validated_evidence_ids = valuation_input.get("validated_evidence_ids")
    validated_calculation_ids = valuation_input.get("validated_calculation_ids")
    if not (
        isinstance(validated_evidence_ids, list)
        and isinstance(validated_calculation_ids, list)
        and all(
            isinstance(identifier, str) and identifier.strip()
            for identifier in (*validated_evidence_ids, *validated_calculation_ids)
        )
    ):
        return []
    evidence_allowlist = set(validated_evidence_ids)
    calculation_allowlist = set(validated_calculation_ids)

    def evidence_ids_from(value: Any) -> list[str] | None:
        if not isinstance(value, list) or not value:
            return None
        result: list[str] = []
        for identifier in value:
            if (
                not isinstance(identifier, str)
                or not identifier.strip()
                or identifier not in evidence_allowlist
            ):
                return None
            if identifier not in result:
                result.append(identifier)
        return result or None

    claim_specs: list[tuple[str, str, str, list[str], list[str]]] = []
    current_result = valuation_input.get("valuation_result")
    current_calculation_ids: list[str] = []
    current_evidence_ids: list[str] = []
    current_calculations = (
        current_result.get("calculations")
        if isinstance(current_result, Mapping)
        else None
    )
    if (
        isinstance(current_result, Mapping)
        and current_result.get("readiness") == "ready"
        and current_result.get("validation_status") == "valid"
        and isinstance(current_calculations, list)
    ):
        for calculation in current_calculations:
            if not isinstance(calculation, Mapping):
                continue
            calculation_id = calculation.get("calculation_id")
            input_evidence_ids = evidence_ids_from(
                calculation.get("input_evidence_ids")
            )
            if not (
                calculation.get("status") == "available"
                and calculation.get("validation_status") == "valid"
                and isinstance(calculation_id, str)
                and calculation_id.strip()
                and calculation_id in calculation_allowlist
                and input_evidence_ids
            ):
                continue
            if calculation_id not in current_calculation_ids:
                current_calculation_ids.append(calculation_id)
            for evidence_id in input_evidence_ids:
                if evidence_id not in current_evidence_ids:
                    current_evidence_ids.append(evidence_id)

    if current_calculation_ids and current_evidence_ids:
        claim_specs.append(
            (
                "claim_current_valuation",
                "current_valuation",
                "当前估值结果由已验证计算及输入证据支持。",
                current_evidence_ids,
                current_calculation_ids,
            )
        )

    def auxiliary_ids(result: Any) -> tuple[list[str], list[str]] | None:
        if not isinstance(result, Mapping):
            return None
        calculation_id = result.get("calculation_id")
        evidence_ids = evidence_ids_from(result.get("input_evidence_ids"))
        if not (
            isinstance(calculation_id, str)
            and calculation_id.strip()
            and calculation_id in calculation_allowlist
            and evidence_ids
        ):
            return None
        return [calculation_id], evidence_ids

    historical_result = valuation_input.get("historical_valuation_result")
    if (
        isinstance(historical_result, Mapping)
        and historical_result.get("status") == "ok"
        and historical_result.get("validation_status") == "valid"
    ):
        historical_ids = auxiliary_ids(historical_result)
        if historical_ids is not None:
            claim_specs.append(
                (
                    "claim_historical_valuation",
                    "historical_valuation",
                    "历史估值结果由已验证计算及输入证据支持。",
                    historical_ids[1],
                    historical_ids[0],
                )
            )

    def reason_codes(result: Mapping[str, Any]) -> set[str]:
        values: list[str] = []
        for value in (result.get("reason_code"), result.get("reasons")):
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                values.extend(
                    item.strip()
                    for item in value
                    if isinstance(item, str) and item.strip()
                )
        return set(values)

    reverse_dcf_result = valuation_input.get("reverse_dcf_result")
    reverse_dcf_not_applicable_reasons = {
        "invalid_fcf",
        "ttm_fcf_required",
        "policy_not_applicable",
    }
    if (
        isinstance(reverse_dcf_result, Mapping)
        and reverse_dcf_result.get("status") == "ok"
        and reverse_dcf_result.get("validation_status") == "valid"
        and not (
            reason_codes(reverse_dcf_result)
            & reverse_dcf_not_applicable_reasons
        )
    ):
        reverse_dcf_ids = auxiliary_ids(reverse_dcf_result)
        if reverse_dcf_ids is not None:
            claim_specs.append(
                (
                    "claim_reverse_dcf",
                    "reverse_dcf",
                    "反向 DCF 结果由已验证计算及输入证据支持。",
                    reverse_dcf_ids[1],
                    reverse_dcf_ids[0],
                )
            )
    return [
        AnalysisClaim(
            claim_id=claim_id,
            category=category,
            statement=statement,
            evidence_ids=evidence_ids,
            calculation_ids=calculation_ids,
            confidence=1.0,
        ).model_dump(mode="json")
        for claim_id, category, statement, evidence_ids, calculation_ids in claim_specs
    ]

_REVERSE_DCF_APPLICABILITY_REASONS = frozenset(
    {
        "invalid_fcf",
        "negative_fcf",
        "negative_eps",
        "policy_not_applicable",
        "ttm_fcf_required",
    }
)
_REVERSE_DCF_POLICY_FIELDS = (
    "issuer_type",
    "company_type",
    "industry",
    "industry_name",
    "sector",
)
_REVERSE_DCF_NON_APPLICABLE_GROUPS = {
    "bank": {"bank", "banking", "commercialbank", "financialinstitution", "银行"},
    "reit": {"reit", "realestateinvestmenttrust", "房地产投资信托"},
}


def _reverse_dcf_reason_codes(reverse_dcf: Mapping[str, Any]) -> list[str]:
    """读取反向 DCF 工具的机器原因码，不从 warning 文本推断状态。"""
    values: list[str] = []
    for value in (reverse_dcf.get("reason_code"), reverse_dcf.get("reasons", [])):
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            values.extend(item.strip() for item in value if isinstance(item, str) and item.strip())
    return list(dict.fromkeys(values))


def _policy_token(value: Any) -> str:
    return re.sub(r"[\s_\-/]+", "", value.strip().lower()) if isinstance(value, str) else ""


def _numeric_policy_value(payload: Any, keys: tuple[str, ...]) -> Decimal | None:
    if not isinstance(payload, Mapping):
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, Mapping):
            if value.get("validation_status") not in (None, "valid"):
                continue
            value = next(
                (value.get(name) for name in ("value", "raw_result", "numeric_value") if value.get(name) is not None),
                None,
            )
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
        if parsed.is_finite():
            return parsed
    return None


def _reverse_dcf_policy_reason(
    state: Mapping[str, Any], reverse_dcf: Mapping[str, Any]
) -> str | None:
    """只从结构化 policy、issuer/industry 和已验证数值判断不适用。"""
    for key in ("reverse_dcf_applicability", "reverse_dcf_policy"):
        policy = state.get(key)
        if isinstance(policy, Mapping):
            status = _policy_token(policy.get("status") or policy.get("applicability"))
            if status == "notapplicable" or policy.get("applicable") is False:
                return str(policy.get("reason_code") or "policy_not_applicable")
        elif _policy_token(policy) == "notapplicable":
            return "policy_not_applicable"

    for field in ("is_bank", "is_financial_institution", "is_reit"):
        if state.get(field) is True:
            return f"{field}_policy"
    for field in _REVERSE_DCF_POLICY_FIELDS:
        token = _policy_token(state.get(field))
        for policy_name, aliases in _REVERSE_DCF_NON_APPLICABLE_GROUPS.items():
            if token in aliases:
                return f"{field}_{policy_name}"

    facts = state.get("facts")
    fcf = _numeric_policy_value(facts, ("current_fcf", "free_cash_flow", "fcf"))
    fcf = fcf if fcf is not None else _numeric_policy_value(reverse_dcf, ("base_fcf",))
    if fcf is not None and fcf <= 0:
        return "negative_fcf"
    eps = _numeric_policy_value(facts, ("diluted_eps", "earnings_per_share_diluted", "ttm_eps"))
    return "negative_eps" if eps is not None and eps <= 0 else None


def _current_valuation_gate(valuation: Mapping[str, Any]) -> dict[str, Any]:
    """接受完整估值，或至少一个带有效 Evidence 的确定性指标。"""
    fully_ready = valuation.get("readiness") == "ready" and valuation.get("validation_status") == "valid"
    audited_metrics: list[str] = []
    for calculation in valuation.get("calculations", []):
        if not isinstance(calculation, Mapping):
            continue
        formula_id = calculation.get("formula_id")
        evidence_ids = calculation.get("input_evidence_ids")
        result = next(
            (calculation.get(key) for key in ("raw_result", "normalized_result", "display_result") if calculation.get(key) not in (None, "")),
            None,
        )
        if (
            formula_id in VALUATION_FORMULAS
            and calculation.get("calculation_id") == f"calc_{formula_id}"
            and calculation.get("status") == "available"
            and calculation.get("validation_status") == "valid"
            and isinstance(evidence_ids, list)
            and evidence_ids
            and all(isinstance(item, str) and item.strip() for item in evidence_ids)
            and result is not None
        ):
            audited_metrics.append(formula_id)
    status = "ready" if fully_ready else "partial" if audited_metrics else "required"
    return {"status": status, "audited_metrics": audited_metrics}


def _analysis_gate(
    validation_result: Any,
    state: dict[str, Any],
    risk_input: dict[str, Any],
    valuation: dict[str, Any],
    historical_valuation: dict[str, Any],
    reverse_dcf: dict[str, Any],
) -> dict[str, Any]:
    """执行 Analysis Crew 之前的确定性完整性门禁。

    该门禁检查五类前置条件：财务事实和基础计算、可读取的风险章节、
    当前估值、历史估值以及反向 DCF。只有确实需要补数据的项目才进入
    ``required_data``；确定性 policy 判定不适用的估值模型会写入
    ``limitations`` 和 ``applicability``，不会伪装成已完成的计算。

    参数：
        validation_result：批量事实/计算验证结果。
        state：已验证状态。
        risk_input：风险域输入包。
        valuation：当前估值结果。
        historical_valuation：历史估值结果。
        reverse_dcf：反向 DCF 结果。
    返回：
        ``status`` 为 ``ready`` 或 ``blocked``，以及 required_data、limitations
        和每个估值域的 applicability 状态。
    """
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
    """构造标准化的 Analysis 阻断结果，并明确停止位置。

    该函数用于 Analysis preflight 或 Claim Gate 失败的情况。它保留已经
    完成的确定性输出，明确设置 ``analysis`` 和 ``report`` 为 ``None``，
    以便调用方知道 Report Crew 没有被执行，同时提供补齐数据后重跑的动作
    指引。可选诊断只在调用方提供时写入。

    参数：
        deterministic_outputs：阻断前已经生成的 SEC、计算、验证和估值结果。
        required_data：导致流程停止的稳定缺失项代码。
        analysis_diagnostics：可选的脱敏 Analysis 输出诊断。
    返回：
        ``status='blocked'`` 且 ``stage='analysis'`` 的完整结果字典。
    """
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


def _validated_state(
    edgar_result: EdgarResult,
    calculation_result: Any,
    validation_result: Any,
) -> dict[str, Any]:
    """从原始工具结果筛出主流程允许使用的已验证状态。

    函数使用验证工具返回的 Evidence/Calculation ID 白名单过滤事实和计算，
    同时只保留具备来源、文本和可用检索状态的 filing。它是从“工具原始
    输出”进入“Analysis 输入”的边界，防止未验证数据通过普通字典传播到
    后续 Crew。

    参数：
        edgar_result：EDGAR 原始结果。
        calculation_result：计算器原始结果。
        validation_result：验证器返回的允许 ID 集合。
    返回：
        包含公司身份、验证 ID、事实、计算和 filings 的状态字典。
    """
    validated_evidence_ids = list(validation_result.validated_evidence_ids)
    validated_calculation_ids = list(validation_result.validated_calculation_ids)
    evidence_ids = set(validated_evidence_ids)
    calculation_ids = set(validated_calculation_ids)

    facts: dict[str, Any] = {}
    for fact_id, raw_fact in edgar_result.facts.items():
        payload = _json_safe(raw_fact)
        if isinstance(payload, dict) and payload.get("evidence_id") in evidence_ids:
            facts[fact_id] = payload

    calculations: list[dict[str, Any]] = []
    for raw_calculation in calculation_result.calculations:
        payload = _json_safe(raw_calculation)
        if (
            isinstance(payload, dict)
            and payload.get("calculation_id") in calculation_ids
        ):
            calculations.append(payload)

    filings: list[dict[str, Any]] = []
    for raw_filing in edgar_result.filings:
        payload = _json_safe(raw_filing)
        if (
            isinstance(payload, dict)
            and payload.get("evidence_id")
            and payload.get("form")
            and payload.get("source_reference")
            and payload.get("text")
            and payload.get("text_retrieval_status") == "available"
        ):
            filings.append(payload)

    return {
        "company_name": edgar_result.company_name,
        "ticker": edgar_result.ticker,
        "validated_evidence_ids": validated_evidence_ids,
        "validated_calculation_ids": validated_calculation_ids,
        "validated_filing_ids": [filing["evidence_id"] for filing in filings],
        "facts": facts,
        "calculations": calculations,
        "filings": filings,
    }


def _valuation_facts(validated_state: dict[str, Any]) -> dict[str, Any]:
    """补充估值工具所需的自由现金流事实，并继承输入证据。

    基础事实中可能没有独立的 ``current_fcf`` 字段，但计算器可能已经用
    已验证输入计算出自由现金流。函数从对应的 ``free_cash_flow`` 计算中
    构造兼容事实，并尽可能从输入事实推导统一单位；原始 facts 仍被保留。

    参数：
        validated_state：包含已验证事实和计算结果的状态。
    返回：
        可传给当前估值、历史估值和反向 DCF 工具的事实字典。
    """
    facts = dict(validated_state.get("facts", {}))
    # SEC 的原始 diluted EPS 可能是单季度或 YTD，不能直接交给当前
    # 估值。先移除这些 legacy 别名，再只从已验证 TTM Builder 输出投影
    # diluted_eps/current_fcf，避免下游把旧字段误当作 TTM。
    for legacy_key in ("diluted_eps", "earnings_per_share_diluted", "current_fcf"):
        facts.pop(legacy_key, None)

    ttm_payload = validated_state.get("ttm", {})
    ttm_metrics = (
        ttm_payload.get("metrics", [])
        if isinstance(ttm_payload, Mapping)
        else []
    )
    if isinstance(ttm_metrics, Mapping):
        ttm_metrics = list(ttm_metrics.values())
    if isinstance(ttm_metrics, list):
        for metric in ttm_metrics:
            if not isinstance(metric, Mapping):
                continue
            metric_id = metric.get("metric_id")
            if metric_id not in {"diluted_eps", "free_cash_flow"}:
                continue
            if metric.get("status") != "available":
                continue
            payload = {
                "raw_result": metric.get("raw_result"),
                "unit": metric.get("unit"),
                "period_basis": metric.get("period_basis") or "TTM",
                "validation_status": metric.get("validation_status"),
                "input_evidence_ids": metric.get("input_evidence_ids", []),
            }
            if metric_id == "diluted_eps":
                facts["diluted_eps"] = payload
            else:
                facts["current_fcf"] = payload

    for calculation in validated_state.get("calculations", []):
        if (
            calculation.get("formula_id") == "free_cash_flow"
            and calculation.get("raw_result") is not None
            and "current_fcf" not in facts
        ):
            fcf_fact = {
                "raw_result": calculation["raw_result"],
                "evidence_ids": calculation.get("input_evidence_ids", []),
                "period_basis": calculation.get("period_basis"),
                "validation_status": calculation.get("validation_status"),
            }
            input_evidence_ids = set(calculation.get("input_evidence_ids", []))
            source_units = {
                fact.get("unit")
                for fact in facts.values()
                if isinstance(fact, Mapping)
                and fact.get("evidence_id") in input_evidence_ids
                and fact.get("unit")
            }
            if len(source_units) == 1:
                fcf_fact["unit"] = next(iter(source_units))
            # 只有明确标记 TTM 且已验证的计算才可进入估值；旧的
            # financial calculator 结果不再作为隐式回退。
            if (
                fcf_fact.get("period_basis") == "TTM"
                and fcf_fact.get("validation_status") == "valid"
            ):
                facts["current_fcf"] = fcf_fact
    return facts


def _market_price_kwargs(market_price_data: Any) -> dict[str, Any]:
    """把市场价格对象规范化为当前估值工具的关键字参数。

    市场价格可以来自 yfinance 的 Pydantic 模型、字典或测试中的简单数值。
    本函数兼容这些形态，并统一映射价格、时间戳、币种和来源 URL；不负责
    判断价格是否真实有效，具体就绪和验证状态由 ``ValuationTool`` 及主流程
    后续门禁决定。

    参数：
        market_price_data：市场价格工具输出、字典、单值或 ``None``。
    返回：
        可直接使用 ``**kwargs`` 传给估值工具的字典。
    """
    if market_price_data is None:
        return {}
    if hasattr(market_price_data, "model_dump"):
        market_price_data = market_price_data.model_dump(mode="json")
    if not isinstance(market_price_data, Mapping):
        return {"market_price": market_price_data}
    return {
        "market_price": market_price_data.get(
            "market_price", market_price_data.get("price")
        ),
        "price_timestamp": market_price_data.get(
            "price_timestamp", market_price_data.get("timestamp")
        ),
        "currency": market_price_data.get("currency"),
        "source_reference": market_price_data.get(
            "source_reference", market_price_data.get("source")
        ),
    }


def _historical_prices(market_price_data: Any) -> list[dict[str, Any]]:
    """从市场价格结果中提取结构化历史价格记录。

    该函数只保留列表中的映射项，并把每项复制为普通字典，避免历史估值
    工具依赖上游 Pydantic 对象的生命周期。缺少历史价格或输入类型不符合
    约定时返回空列表，由历史估值工具负责给出 ``missing_historical_prices``
    等状态。

    参数：
        market_price_data：包含 ``historical_prices`` 的模型或字典。
    返回：
        历史价格字典列表。
    """
    if hasattr(market_price_data, "model_dump"):
        market_price_data = market_price_data.model_dump(mode="json")
    if not isinstance(market_price_data, Mapping):
        return []
    prices = market_price_data.get("historical_prices", [])
    return [dict(item) for item in prices if isinstance(item, Mapping)] if isinstance(prices, list) else []


def _historical_financial_snapshots(edgar_result: EdgarResult) -> list[dict[str, Any]]:
    """从 EDGAR 结果提取历史财务快照供历史估值使用。

    EDGAR 工具可能返回空值、非列表或包含模型对象的列表。本函数只输出
    普通字典记录，不在这里做期间选择或财务指标计算；历史估值工具负责
    将这些快照与历史价格按日期对齐。

    参数：
        edgar_result：EDGAR 工具返回的结果对象。
    返回：
        历史财务快照字典列表；没有可用快照时返回空列表。
    """
    snapshots = getattr(edgar_result, "historical_financial_snapshots", [])
    return [dict(item) for item in snapshots if isinstance(item, Mapping)] if isinstance(snapshots, list) else []


def _deterministic_verdict(
    *,
    validation_status: str = "unavailable",
    valuation: Mapping[str, Any] | None = None,
    historical_valuation: Mapping[str, Any] | None = None,
    reverse_dcf: Mapping[str, Any] | None = None,
    risk_input: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """调用确定性 Verdict 工具生成 JSON 安全的决策状态。

    本函数是主流程与 ``DeterministicVerdictTool`` 之间的适配层。它只负责
    组装已验证的当前估值、历史估值、反向 DCF 和风险输入；是否可以输出
    Verdict、是否为 ``not_ready`` 以及原因均由工具内固定政策决定，不由
    LLM 或报告 Agent 自由判断。

    参数：
        validation_status：基础事实/计算的验证状态。
        valuation：当前估值结果，可选。
        historical_valuation：历史估值结果，可选。
        reverse_dcf：反向 DCF 结果，可选。
        risk_input：风险域输入，可选。
    返回：
        JSON 安全的 Verdict 字典。
    """
    result = DeterministicVerdictTool().run(
        validation_status=validation_status,
        valuation=dict(valuation or {}),
        historical_valuation=dict(historical_valuation or {}),
        reverse_dcf=dict(reverse_dcf or {}),
        risk_input=dict(risk_input or {}),
    )
    return _json_safe(result)


def _with_validation_status(
    result: Any,
    *,
    allowed_evidence_ids: set[str],
    base_valid: bool,
) -> dict[str, Any]:
    """按照统一证据白名单为辅助估值结果补充验证状态。

    历史估值和反向 DCF 使用的 Evidence ID 可能来自市场价格快照或历史
    财务快照，不能只看工具自身的 ``status``。函数同时检查基础验证是否
    成功、结果状态是否可用、输入 Evidence ID 是否非空且全部属于白名单，
    只有全部满足才标记 ``validation_status='valid'``。

    参数：
        result：历史估值或反向 DCF 工具输出。
        allowed_evidence_ids：主流程认可的 Evidence ID 集合。
        base_valid：基础 SEC/计算验证是否成功。
    返回：
        JSON 安全的结果字典，并带有 ``valid`` 或 ``unvalidated`` 状态。
    """
    payload = _json_safe(result)
    if not isinstance(payload, dict):
        return {"status": "unavailable", "validation_status": "unvalidated"}
    input_ids = payload.get("input_evidence_ids", [])
    input_ids_are_valid = isinstance(input_ids, list) and all(
        isinstance(item, str) and item in allowed_evidence_ids for item in input_ids
    )
    payload["validation_status"] = (
        "valid"
        if (
            base_valid
            and payload.get("status") == "ok"
            and bool(input_ids)
            and input_ids_are_valid
        )
        else "unvalidated"
    )
    return payload


def _reverse_dcf_inputs(
    state: Mapping[str, Any], valuation: Mapping[str, Any]
) -> dict[str, Any]:
    """构造反向 DCF 工具需要的价格、自由现金流和股数输入。

    市场价格只有在同时存在价格值和对应 Evidence ID 时才会传给反向 DCF；
    自由现金流来自 ``_valuation_facts``，流通股优先使用期末流通股字段，
    再回退到统一的 ``shares_current``。函数不设置折现率、永续增长率等
    假设，避免主流程凭空创造模型前提。

    参数：
        state：已验证财务状态。
        valuation：当前估值结果，包含市场价格及其 Evidence ID。
    返回：
        可传给 ``ReverseDCFTool.run`` 的输入字典。
    """
    valuation_facts = _valuation_facts(dict(state))
    price = valuation.get("market_price")
    price_evidence_id = valuation.get("market_price_evidence_id")
    market_price = (
        {"value": price, "evidence_id": price_evidence_id}
        if price is not None and price_evidence_id
        else None
    )
    fcf = valuation_facts.get("current_fcf")
    shares = next(
        (
            valuation_facts.get(fact_id)
            for fact_id in ("common_shares_outstanding", "shares_current")
            if valuation_facts.get(fact_id) is not None
        ),
        None,
    )
    return {
        "market_price": market_price,
        "fcf": fcf,
        "shares_outstanding": shares,
    }
