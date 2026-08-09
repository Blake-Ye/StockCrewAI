import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from crewai import Agent, Crew, LLM, Process, Task, TaskOutput
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
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

_REPORT_ADVICE_RE = re.compile(
    r"买入|卖出|持有|增持|减持|推荐|\b(?:buy|sell|hold)\b",
    re.IGNORECASE,
)
_REPORT_DRAFT_ADVICE_RE = re.compile(
    r"买入|卖出|持有|增持|减持|推荐|投资建议|投资推荐|买卖建议|"
    r"\b(?:buy|sell|hold)\b",
    re.IGNORECASE,
)
_REPORT_DRAFT_VERDICT_RE = re.compile(
    r"投资价值|值得投资|估值(?:偏贵|偏?便宜)|高估|低估|安全边际|前景(?:乐观|悲观)"
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
_REPORT_CLAIM_FIELDS = frozenset(
    {
        "claim_id",
        "category",
        "statement",
        "evidence_ids",
        "calculation_ids",
        "confidence",
    }
)
_CLAIM_CATEGORY_TO_SECTION = {
    "financial_quality": "company_quality",
    "financial_trend": "financial_trend",
    "current_valuation": "current_valuation",
    "historical_valuation": "historical_valuation",
    "reverse_dcf": "reverse_dcf",
    "risk": "key_risks",
}
_REPORT_SECTIONS = (
    ("execution_summary", "执行摘要"),
    ("company_quality", "公司质量"),
    ("financial_trend", "财务趋势"),
    ("current_valuation", "当前估值"),
    ("historical_valuation", "历史估值"),
    ("reverse_dcf", "反向 DCF"),
    ("key_risks", "主要风险"),
    ("sources_and_method", "数据来源与方法"),
    ("non_investment_disclaimer", "非投资建议声明"),
)
_REPORT_METRIC_LABELS = {
    "market_price": "市场价格",
    "revenue_growth": "营业收入同比增长",
    "operating_margin": "营业利润率",
    "net_margin": "净利率",
    "free_cash_flow": "自由现金流",
    "free_cash_flow_margin": "自由现金流率",
    "cash_conversion": "现金转换率",
    "net_cash": "净现金",
    "current_ratio": "流动比率",
    "debt_to_equity": "债务权益比",
    "share_dilution": "股份稀释率",
    "market_capitalization": "市值",
    "pe_ratio": "P/E",
    "fcf_yield": "FCF Yield",
    "historical_pe_current": "历史当前 P/E",
    "historical_pe_median": "历史五年中位 P/E",
    "historical_pe_percentile_25": "历史 P/E 二十五分位",
    "historical_pe_percentile_75": "历史 P/E 七十五分位",
    "historical_percentile": "当前历史百分位",
    "reverse_dcf_implied_growth": "反向 DCF 隐含增长",
}
_REPORT_QUALITY_METRIC_IDS = frozenset(
    {
        "operating_margin",
        "net_margin",
        "free_cash_flow_margin",
        "cash_conversion",
        "net_cash",
        "current_ratio",
        "debt_to_equity",
    }
)
_REPORT_TREND_METRIC_IDS = frozenset(
    {"revenue_growth", "free_cash_flow", "share_dilution"}
)
_REPORT_PERCENT_METRIC_IDS = frozenset(
    {
        "revenue_growth",
        "operating_margin",
        "net_margin",
        "free_cash_flow_margin",
        "cash_conversion",
        "share_dilution",
        "historical_percentile",
        "reverse_dcf_implied_growth",
    }
)
_REPORT_AMOUNT_METRIC_IDS = frozenset(
    {"free_cash_flow", "net_cash", "market_capitalization"}
)
_VERDICT_RATING_LABELS = {
    "attractive": "估值吸引",
    "reasonable": "估值合理",
    "watchlist": "关注风险",
    "expensive": "估值偏贵",
    "insufficient_data": "数据不足",
}
_VERDICT_RISK_LABELS = {
    "low": "低风险",
    "medium": "中等风险",
    "high": "高风险",
    "insufficient_data": "数据不足",
}
_VERDICT_RULE_LABELS = {
    "high_risk_watchlist": "高风险观察规则触发",
    "low_multiple_high_fcf_yield": "低估值且高自由现金流收益率规则触发",
    "high_valuation": "估值偏高规则触发",
    "balanced_valuation": "估值均衡规则触发",
    "require_all_validated_components": "核心数据完整性规则触发",
}
_VERDICT_ACTION_LABELS = {
    "attractive": "继续核对证据完整性与估值假设",
    "reasonable": "继续观察后续数据与估值变化",
    "watchlist": "等待风险信息改善",
    "expensive": "等待更高安全边际",
    "insufficient_data": "补齐已验证数据后再评估",
}
_REPORT_NUMBER_RE = re.compile(
    r"(?<![A-Za-z])[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:\s*(?:[%％]|[xX]))?"
)


def _draft_text_violation(value: str, *, allow_advice: bool = False) -> str | None:
    if not value.strip():
        return "字段必须是非空字符串。"
    if re.search(r"[0-9]", value):
        return "草稿正文不得包含阿拉伯数字。"
    if "```" in value:
        return "草稿正文不得包含代码围栏。"
    if not allow_advice and _REPORT_DRAFT_ADVICE_RE.search(value):
        return "草稿正文不得包含买入、卖出、持有或其他投资建议。"
    if not allow_advice and _REPORT_DRAFT_VERDICT_RE.search(value):
        return "草稿正文不得表达投资结论；结论只能由确定性 Verdict 注入。"
    if _REPORT_RATING_RE.search(value):
        return "草稿正文不得包含评级。"
    if _REPORT_CLAIM_ID_RE.search(value):
        return "草稿正文不得包含 Claim ID。"
    if _REPORT_STATUS_RE.search(value):
        return "确定性 status 只能由 Python Renderer 注入。"
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


def build_deterministic_report_draft() -> ReportDraft:
    """构造不携带动态事实的安全 ReportDraft fallback。

    动态 Claims、指标、数字和来源仍只由确定性 Renderer 从
    ``report_context`` 注入；这个 fallback 仅在 Report Crew 不可用时提供
    固定九字段叙述，并继续经过同一个 Draft/Markdown 安全检查。
    """
    return ReportDraft(
        execution_summary="报告由已验证研究结果生成。",
        company_quality="公司质量部分由确定性 Renderer 注入已验证内容。",
        financial_trend="财务趋势部分由确定性 Renderer 注入已验证内容。",
        current_valuation="当前估值部分由确定性 Renderer 注入已验证内容。",
        historical_valuation="历史估值部分由确定性 Renderer 注入已验证内容。",
        reverse_dcf="反向 DCF 部分由确定性 Renderer 注入已验证内容。",
        key_risks="主要风险部分由确定性 Renderer 注入已验证内容。",
        sources_and_method="来源与方法部分由确定性 Renderer 注入已验证内容。",
        non_investment_disclaimer="本文不构成任何投资建议。",
    )


class ReportMetric(BaseModel):
    """报告 Renderer 唯一允许展示的规范化指标。"""

    model_config = ConfigDict(extra="forbid")

    section: StrictStr
    metric_id: StrictStr
    display_value: StrictStr
    unit: StrictStr
    as_of: StrictStr
    source_reference: StrictStr
    evidence_ids: list[StrictStr]
    calculation_id: StrictStr | None = None
    status: StrictStr = "unavailable"
    validation_status: StrictStr = "unknown"

    @field_validator(
        "section",
        "metric_id",
        "display_value",
        "unit",
        "as_of",
        "source_reference",
    )
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ReportMetric 文本字段不得为空。")
        return value.strip()

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not normalized:
            raise ValueError("ReportMetric 必须包含 Evidence ID。")
        return normalized

    @model_validator(mode="after")
    def validate_calculation_provenance(self) -> "ReportMetric":
        if self.metric_id != "market_price" and not self.calculation_id:
            raise ValueError("非市场价格指标必须包含 Calculation ID。")
        return self


class ReportContext(BaseModel):
    """Report Crew 与确定性 Renderer 共享的 JSON-safe 输入契约。"""

    model_config = ConfigDict(extra="forbid")

    company: dict[str, Any] = Field(default_factory=dict)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    verdict_status: StrictStr
    metrics: list[ReportMetric] = Field(default_factory=list)
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    verdict: dict[str, Any] = Field(default_factory=dict)
    ttm: dict[str, Any] = Field(default_factory=dict)
    historical_valuation: dict[str, Any] = Field(default_factory=dict)
    reverse_dcf: dict[str, Any] = Field(default_factory=dict)

    @field_validator("verdict_status")
    @classmethod
    def validate_verdict_status(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ReportContext 缺少确定性 verdict status。")
        return value.strip()


def _json_safe_context(value: Any) -> Any:
    """把 Report Context 转成可安全交给 CrewAI 和 JSON 的值。

    Report Crew 的输入必须是普通 JSON 数据，不能携带 Decimal、日期或
    Pydantic 对象。这个转换只负责序列化，不负责把缺失数据猜成数字。
    """
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return str(value)
    if isinstance(value, (date, datetime, Decimal)):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if isinstance(value, BaseModel):
        return _json_safe_context(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _json_safe_context(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_context(item) for item in value]
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return str(value)
    return value


def _text(value: Any) -> str | None:
    """返回非空文本；空值不作为报告数据继续传播。"""
    if value is None or isinstance(value, bool):
        return None
    result = str(value).strip()
    return result or None


def _ids(value: Any) -> list[str]:
    """规范化 Evidence/Calculation ID，并按出现顺序去重。"""
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    for item in values:
        item_text = _text(item)
        if item_text and item_text not in result:
            result.append(item_text)
    return result


def _evidence_index(source_metadata: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """建立 Evidence ID 到来源元数据的确定性索引。"""
    index: dict[str, Mapping[str, Any]] = {}

    def register(record: Any) -> None:
        if not isinstance(record, Mapping):
            return
        for key in ("evidence_id", "market_price_evidence_id"):
            evidence_id = _text(record.get(key))
            if evidence_id and evidence_id not in index:
                index[evidence_id] = record
        for nested_key in ("financial_evidence_ids", "input_evidence_ids"):
            nested_ids = record.get(nested_key, [])
            if isinstance(nested_ids, Sequence) and not isinstance(
                nested_ids, (str, bytes)
            ):
                for evidence_id in nested_ids:
                    evidence_text = _text(evidence_id)
                    if evidence_text and evidence_text not in index:
                        index[evidence_text] = record

    facts = source_metadata.get("facts", {})
    if isinstance(facts, Mapping):
        for fact in facts.values():
            register(fact)
    register(source_metadata.get("market_price"))
    for key in (
        "historical_prices",
        "risk_filings",
        "historical_financial_snapshots",
        "ttm_evidence",
        "ttm_metrics",
    ):
        records = source_metadata.get(key, [])
        if isinstance(records, Sequence) and not isinstance(records, (str, bytes)):
            for record in records:
                register(record)
    return index


def _evidence_source(
    evidence_ids: list[str],
    evidence_index: Mapping[str, Mapping[str, Any]],
    direct_source: Any = None,
    *,
    require_direct: bool = False,
) -> str | None:
    """从直接来源或 Evidence 元数据中取得唯一来源引用。"""
    direct = _text(direct_source)
    if direct:
        return direct
    if require_direct:
        return None
    for evidence_id in evidence_ids:
        source = _text(evidence_index.get(evidence_id, {}).get("source_reference"))
        if source:
            return source
    return None


def _evidence_as_of(
    evidence_ids: list[str],
    evidence_index: Mapping[str, Mapping[str, Any]],
    direct_values: Sequence[Any] = (),
) -> str | None:
    """取得指标的 point-in-time 日期或时间戳。"""
    for value in direct_values:
        result = _text(value)
        if result:
            return result
    for evidence_id in evidence_ids:
        record = evidence_index.get(evidence_id, {})
        for key in (
            "as_of",
            "price_timestamp",
            "period_end",
            "period",
            "filed_at",
            "date",
        ):
            result = _text(record.get(key))
            if result:
                return result
    return None


def _metric_from_payload(
    *,
    section: str,
    metric_id: Any,
    display_value: Any,
    unit: Any,
    evidence_ids: Any,
    calculation_id: Any,
    evidence_index: Mapping[str, Mapping[str, Any]],
    direct_source: Any = None,
    direct_as_of: Sequence[Any] = (),
    require_direct_source: bool = False,
) -> ReportMetric | None:
    """把一个确定性结果转换成可渲染指标；不完整时返回 None。"""
    metric_name = _text(metric_id)
    display = _text(display_value)
    unit_text = _text(unit)
    ids = _ids(evidence_ids)
    calculation = _text(calculation_id)
    if not metric_name or not display or not unit_text or not ids:
        return None
    if any(evidence_id not in evidence_index for evidence_id in ids):
        return None
    if metric_name != "market_price" and not calculation:
        return None
    source = _evidence_source(
        ids,
        evidence_index,
        direct_source,
        require_direct=require_direct_source,
    )
    as_of = _evidence_as_of(ids, evidence_index, direct_as_of)
    if not source or not as_of:
        return None
    try:
        return ReportMetric(
            section=section,
            metric_id=metric_name,
            display_value=display,
            unit=unit_text,
            as_of=as_of,
            source_reference=source,
            evidence_ids=ids,
            calculation_id=calculation,
            status="available",
            validation_status="valid",
        )
    except ValidationError:
        return None


def _calculation_items(value: Any) -> list[Mapping[str, Any]]:
    """兼容批量计算结果和直接计算列表。"""
    if isinstance(value, Mapping):
        value = value.get("calculations", [])
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _percent_display(value: Any) -> str | None:
    """把反向 DCF 的小数增长率转换成稳定的百分比显示文本。"""
    raw = _text(value)
    if not raw:
        return None
    try:
        decimal_value = Decimal(raw)
    except Exception:
        return raw
    if not decimal_value.is_finite():
        return None
    return f"{decimal_value * Decimal('100'):.2f}%"


def _currency_display(value: Any) -> str:
    """把 DCF 金额按报告单位显示，避免把长整数直接交给读者。"""
    raw = _text(value)
    if not raw:
        return "不可用"
    try:
        amount = Decimal(raw)
    except Exception:
        return raw
    if not amount.is_finite():
        return "不可用"
    if abs(amount) >= Decimal("1000000000000"):
        return f"{amount / Decimal('1000000000000'):.2f} 万亿美元"
    return f"{amount / Decimal('100000000'):.2f} 亿美元"


_TTM_CONTEXT_FIELDS = (
    "metric_id",
    "calculation_id",
    "formula_id",
    "raw_result",
    "normalized_result",
    "display_value",
    "unit",
    "status",
    "validation_status",
    "input_evidence_ids",
    "period_start",
    "period_end",
)


def _verified_ttm_context(value: Any) -> dict[str, Any]:
    """只把已验证 TTM 指标投影到 Renderer 的受控 JSON 字段。"""
    payload = value if isinstance(value, Mapping) else {"metrics": value}
    raw_metrics = payload.get("metrics", [])
    if isinstance(raw_metrics, Mapping):
        raw_metrics = list(raw_metrics.values())
    if not isinstance(raw_metrics, Sequence) or isinstance(raw_metrics, (str, bytes)):
        raw_metrics = []
    metrics: list[dict[str, Any]] = []
    for raw_metric in raw_metrics:
        if not isinstance(raw_metric, Mapping):
            continue
        if raw_metric.get("status") != "available" or raw_metric.get("validation_status") != "valid":
            continue
        metrics.append(
            {
                key: _json_safe_context(raw_metric[key])
                for key in _TTM_CONTEXT_FIELDS
                if key in raw_metric and raw_metric[key] is not None
            }
        )
    return {
        "status": _text(payload.get("status")) or ("ok" if metrics else "unavailable"),
        "metrics": metrics,
    }


def _historical_visual_context(value: Mapping[str, Any]) -> dict[str, Any]:
    """只保留历史估值图表需要的已验证逐月序列。"""
    if value.get("status") != "ok" or value.get("validation_status") != "valid":
        return {}
    series = value.get("series")
    current_date = _text(value.get("current_date"))
    if not isinstance(series, Sequence) or isinstance(series, (str, bytes)) or not current_date:
        return {}
    normalized_series = []
    for point in series:
        if not isinstance(point, Mapping):
            continue
        point_date = _text(point.get("date"))
        pe_ratio = _text(point.get("pe_ratio"))
        if point_date and pe_ratio:
            normalized_series.append(
                {
                    "date": point_date,
                    "pe_ratio": pe_ratio,
                    "ttm_eps": _text(point.get("ttm_eps")),
                    "financial_evidence_ids": _ids(
                        point.get("financial_evidence_ids")
                    ),
                }
            )
    if not normalized_series:
        return {}
    return {
        "status": "ok",
        "validation_status": "valid",
        "period_basis": "TTM",
        "series": normalized_series,
        "current_date": current_date,
    }


def _verified_reverse_dcf_context(value: Mapping[str, Any]) -> dict[str, Any]:
    """投影报告所需的反向 DCF 假设，不允许 Renderer 自己创造参数。"""
    if value.get("status") != "ok" or value.get("validation_status") != "valid":
        return {}
    scenario_matrix = value.get("scenario_matrix", [])
    scenarios = []
    if isinstance(scenario_matrix, Sequence) and not isinstance(
        scenario_matrix, (str, bytes)
    ):
        for scenario in scenario_matrix:
            if not isinstance(scenario, Mapping):
                continue
            scenarios.append(
                {
                    key: _json_safe_context(scenario[key])
                    for key in (
                        "discount_rate",
                        "terminal_growth",
                        "implied_growth",
                        "convergence_status",
                    )
                    if scenario.get(key) is not None
                }
            )
    return {
        key: _json_safe_context(value[key])
        for key in (
            "base_fcf",
            "period_basis",
            "forecast_years",
            "discount_rate",
            "terminal_growth",
            "implied_growth",
        )
        if value.get(key) is not None
    } | {"scenario_matrix": scenarios}


def build_report_context(
    *,
    company: Mapping[str, Any] | None = None,
    validated_claims: Any = None,
    deterministic_verdict: Mapping[str, Any] | None = None,
    calculations: Any = None,
    valuation: Mapping[str, Any] | None = None,
    historical_valuation: Mapping[str, Any] | None = None,
    reverse_dcf: Mapping[str, Any] | None = None,
    source_metadata: Mapping[str, Any] | None = None,
    # 这两个别名只用于兼容早期离线调用，不改变规范化输出结构。
    company_name: Any = None,
    ticker: Any = None,
    financial_calculations: Any = None,
    ttm: Any = None,
) -> dict[str, Any]:
    """构造 Report Crew 与 Renderer 共享的唯一 JSON-safe 输入。

    这里是报告数据的单一事实入口：只接受已通过 Claim/Validation Gate 的
    Claims 和确定性工具结果，只有同时具备验证状态、Evidence ID、来源、
    时间点和 Calculation ID 的指标才会进入 ``metrics``。报告 Agent 不再
    直接接触估值原始对象，也不能凭语言模型重新计算数字。
    """
    company_payload = dict(company or {})
    if not company_payload:
        company_payload = {
            key: value
            for key, value in (("name", company_name), ("ticker", ticker))
            if _text(value)
        }
    verdict_payload = dict(deterministic_verdict or {})
    source_payload = dict(source_metadata or {})
    valuation_payload = dict(valuation or {})
    historical_payload = dict(historical_valuation or {})
    reverse_payload = dict(reverse_dcf or {})
    calculation_payload = calculations if calculations is not None else financial_calculations
    ttm_payload = ttm if ttm is not None else source_payload.get("ttm")

    claims = _validated_claims(validated_claims or [])
    verdict_status = _text(verdict_payload.get("status"))
    if not verdict_status:
        raise ValueError("ReportContext 缺少确定性 verdict status。")
    evidence_index = _evidence_index(source_payload)
    metrics: list[ReportMetric] = []

    # 财务指标：Calculation 的验证状态由 Validation Tool 同步写回。
    for calculation in _calculation_items(calculation_payload):
        if (
            calculation.get("status") != "available"
            or calculation.get("validation_status") != "valid"
        ):
            continue
        metric = _metric_from_payload(
            section="financial",
            metric_id=calculation.get("formula_id"),
            display_value=calculation.get("display_result"),
            unit=calculation.get("unit"),
            evidence_ids=calculation.get("input_evidence_ids"),
            calculation_id=calculation.get("calculation_id"),
            evidence_index=evidence_index,
            direct_source=calculation.get("source_reference"),
            direct_as_of=(calculation.get("as_of"), calculation.get("period_end")),
        )
        if metric is not None:
            metrics.append(metric)

    # 当前估值作为一个完整组处理：任一可用计算缺少直接行情来源时，
    # 整组不展示，避免只展示价格却把不完整的 P/E/FCF Yield 误认为同批结果。
    valuation_calculations = _calculation_items(valuation_payload)
    valuation_ready = (
        valuation_payload.get("validation_status") == "valid"
        and valuation_payload.get("readiness") == "ready"
        and bool(valuation_calculations)
        and all(
            item.get("status") == "available"
            and item.get("validation_status") == "valid"
            and _text(item.get("source_reference"))
            for item in valuation_calculations
        )
    )
    if valuation_ready:
        market_metric = _metric_from_payload(
            section="current_valuation",
            metric_id="market_price",
            display_value=valuation_payload.get("market_price"),
            unit=valuation_payload.get("currency"),
            evidence_ids=valuation_payload.get("market_price_evidence_id"),
            calculation_id=None,
            evidence_index=evidence_index,
            direct_source=valuation_payload.get("source_reference"),
            direct_as_of=(valuation_payload.get("price_timestamp"),),
        )
        if market_metric is not None:
            metrics.append(market_metric)
        for calculation in valuation_calculations:
            metric = _metric_from_payload(
                section="current_valuation",
                metric_id=calculation.get("formula_id"),
                display_value=calculation.get("display_result"),
                unit=calculation.get("unit"),
                evidence_ids=calculation.get("input_evidence_ids"),
                calculation_id=calculation.get("calculation_id"),
                evidence_index=evidence_index,
                direct_source=calculation.get("source_reference"),
                direct_as_of=(
                    calculation.get("price_timestamp"),
                    valuation_payload.get("price_timestamp"),
                ),
                require_direct_source=True,
            )
            if metric is not None:
                metrics.append(metric)

    # 历史估值：每个历史派生值共享同一个稳定 calculation_id。
    historical_ids = _ids(historical_payload.get("input_evidence_ids"))
    historical_calculation_id = _text(historical_payload.get("calculation_id"))
    historical_ready = (
        historical_payload.get("status") == "ok"
        and historical_payload.get("validation_status") == "valid"
        and bool(historical_calculation_id)
        and bool(historical_ids)
    )
    selected_dates = historical_payload.get("selected_dates", [])
    selected_date_values = (
        selected_dates if isinstance(selected_dates, Sequence) and not isinstance(selected_dates, (str, bytes)) else []
    )
    historical_current_date = _text(historical_payload.get("current_date"))
    historical_as_of_values = (
        (historical_current_date,)
        if historical_current_date
        else tuple(selected_date_values[-1:])
    )
    if historical_ready:
        historical_metrics = (
            ("historical_pe_current", historical_payload.get("current_value"), "multiple"),
            ("historical_pe_median", historical_payload.get("five_year_median"), "multiple"),
            ("historical_pe_percentile_25", historical_payload.get("percentile_25"), "multiple"),
            ("historical_pe_percentile_75", historical_payload.get("percentile_75"), "multiple"),
            ("historical_percentile", historical_payload.get("current_percentile"), "percent"),
        )
        for metric_id, value, unit in historical_metrics:
            metric = _metric_from_payload(
                section="historical_valuation",
                metric_id=metric_id,
                display_value=value,
                unit=unit,
                evidence_ids=historical_ids,
                calculation_id=historical_calculation_id,
                evidence_index=evidence_index,
                direct_source=historical_payload.get("source_reference"),
                direct_as_of=historical_as_of_values,
            )
            if metric is not None:
                metrics.append(metric)

    # 反向 DCF：增长率由确定性求解器给出，Renderer 只做显示格式转换。
    reverse_ids = _ids(reverse_payload.get("input_evidence_ids"))
    reverse_calculation_id = _text(reverse_payload.get("calculation_id"))
    if (
        reverse_payload.get("status") == "ok"
        and reverse_payload.get("validation_status") == "valid"
        and reverse_calculation_id
        and reverse_ids
    ):
        metric = _metric_from_payload(
            section="reverse_dcf",
            metric_id="reverse_dcf_implied_growth",
            display_value=_percent_display(reverse_payload.get("implied_growth")),
            unit="percent",
            evidence_ids=reverse_ids,
            calculation_id=reverse_calculation_id,
            evidence_index=evidence_index,
            direct_source=reverse_payload.get("source_reference"),
            direct_as_of=(reverse_payload.get("as_of"), reverse_payload.get("price_timestamp")),
        )
        if metric is not None:
            metrics.append(metric)

    context = ReportContext(
        company=_json_safe_context(company_payload),
        claims=_json_safe_context(claims),
        verdict_status=verdict_status,
        metrics=metrics,
        source_metadata=_json_safe_context(source_payload),
        verdict=_json_safe_context(
            {
                key: verdict_payload[key]
                for key in ("status", "overall_rating", "risk_level", "triggered_rules")
                if key in verdict_payload
            }
        ),
        ttm=_verified_ttm_context(ttm_payload),
        historical_valuation=_historical_visual_context(historical_payload),
        reverse_dcf=_verified_reverse_dcf_context(reverse_payload),
    )
    return context.model_dump(mode="json")


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
            raise ValueError("ReportDraft 必须是唯一且有效的 JSON 对象。") from exc
    else:
        raise ValueError("ReportDraft 必须是非空 JSON 对象。")

    if not isinstance(decoded, dict):
        raise ValueError("ReportDraft 顶层必须是唯一 JSON 对象。")
    try:
        return ReportDraft.model_validate(decoded)
    except ValidationError as exc:
        raise ValueError("ReportDraft 字段不符合固定九字段契约。") from exc


def validate_report_draft(output: TaskOutput) -> tuple[bool, Any]:
    """CrewAI Task Guardrail：只接受合法的无数字 ReportDraft JSON。"""
    payload = _report_payload(output)
    try:
        parse_report_draft(payload)
    except ValueError as exc:
        return False, str(exc)
    return True, payload


def _validated_claims(claims: Any) -> list[dict[str, Any]]:
    if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)):
        raise ValueError("Renderer 只接受 Claim Gate 通过后的 Claims 列表。")

    normalized: list[dict[str, Any]] = []
    for claim in claims:
        if hasattr(claim, "model_dump"):
            claim = claim.model_dump(mode="json")
        if not isinstance(claim, Mapping) or set(claim) != _REPORT_CLAIM_FIELDS:
            raise ValueError("Renderer 拒绝原始或 rejected Claims。")
        if any(
            not isinstance(claim.get(field), str) or not claim[field].strip()
            for field in ("claim_id", "category", "statement")
        ):
            raise ValueError("Renderer 拒绝结构不完整的 Claim。")
        category = claim["category"].strip()
        if category not in _CLAIM_CATEGORY_TO_SECTION:
            raise ValueError("Renderer 拒绝未通过 Claim Gate 的 category。")
        if not isinstance(claim["evidence_ids"], list) or not isinstance(
            claim["calculation_ids"], list
        ):
            raise ValueError("Renderer 拒绝结构不完整的 Claim。")
        normalized.append(dict(claim))
    return normalized


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"Renderer 的 {name} 必须是确定性 Mapping。")
    return value


def _json_text(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Renderer 输入不是可序列化的已验证数据。") from exc


def _claim_text(claims: list[dict[str, Any]], category: str) -> str:
    """渲染已验证 Claim 的解释文本，跳过有规范化指标的数字原文。

    含 Calculation ID 的财务或估值 Claim，其数字只由下面的规范化
    ``ReportMetric`` 提供，避免同一报告出现两个互相矛盾的 P/E 或增长率。
    没有 Calculation ID 的风险 Claim 没有对应 Metric，必须保留已验证原文。
    """
    statements = []
    saw_claim = False
    skipped_numeric = False
    for claim in claims:
        if claim["category"] != category:
            continue
        saw_claim = True
        statement = claim["statement"]
        if claim["calculation_ids"] and _REPORT_NUMBER_RE.search(statement):
            skipped_numeric = True
            continue
        statements.append(statement)
    if statements:
        return "\n".join(f"- {statement}" for statement in statements)
    if skipped_numeric:
        return "数字已由规范化指标展示。"
    if saw_claim:
        return "已验证 Claim 没有可单独展示的文字内容。"
    return "未提供可单独展示的文字 Claim。"


def _decimal_from_text(value: Any) -> Decimal | None:
    raw = _text(value)
    if not raw:
        return None
    match = _REPORT_NUMBER_RE.search(raw.replace(",", ""))
    if match is None:
        return None
    try:
        result = Decimal(match.group(0).rstrip("%％xX").strip())
    except (ArithmeticError, ValueError):
        return None
    return result if result.is_finite() else None


def _formatted_metric_value(metric: Mapping[str, Any]) -> str:
    metric_id = str(metric.get("metric_id", ""))
    raw_value = metric.get("display_value")
    decimal_value = _decimal_from_text(raw_value)
    if decimal_value is None:
        return _text(raw_value) or "数据缺失"
    unit = _text(metric.get("unit")) or ""
    unit_lower = unit.lower()
    raw_text = _text(raw_value) or ""
    if metric_id in {"current_ratio", "debt_to_equity"}:
        # 这两个是资产负债表倍数，不是百分比；优先读取 raw_result
        # 避免旧数据里的 display_result=76.59% 把 0.7659 放大 100 倍。
        raw_numeric = metric.get("raw_result")
        ratio_value = _decimal_from_text(raw_numeric)
        if ratio_value is not None:
            decimal_value = ratio_value
        elif "%" in raw_text and decimal_value > Decimal("1"):
            decimal_value /= Decimal("100")
        return f"{decimal_value:.2f}x"
    if metric_id in _REPORT_PERCENT_METRIC_IDS or unit_lower in {
        "ratio",
        "percent",
        "percentage",
    }:
        if "%" not in raw_text and unit_lower == "ratio":
            decimal_value *= Decimal("100")
        return f"{decimal_value:.2f}%"
    if unit_lower in {"multiple", "x", "倍"} or metric_id in {
        "pe_ratio",
        "historical_pe_current",
        "historical_pe_median",
        "historical_pe_percentile_25",
        "historical_pe_percentile_75",
    }:
        return f"{decimal_value:.2f}x"
    if metric_id in _REPORT_AMOUNT_METRIC_IDS or unit_lower in {
        "currency",
        "usd",
    }:
        if metric_id == "market_price":
            return f"{decimal_value:.2f} {unit}".strip()
        absolute_value = abs(decimal_value)
        if absolute_value >= Decimal("1000000000000"):
            return f"{decimal_value / Decimal('1000000000000'):.2f} 万亿美元"
        return f"{decimal_value / Decimal('100000000'):.2f} 亿美元"
    return f"{decimal_value:.2f}"


def _metric_text(metric: Mapping[str, Any]) -> str:
    """把一个 ReportMetric 渲染成带时间和来源的可读行。"""
    label = _REPORT_METRIC_LABELS.get(metric["metric_id"], metric["metric_id"])
    value = _formatted_metric_value(metric)
    unit = metric["unit"]
    if unit and unit not in value and metric["metric_id"] == "market_price":
        value = f"{value} {unit}"
    return (
        f"- {label}：{value}（截至 {metric['as_of']}；"
        f"来源：{metric['source_reference']}）"
    )


def _metric_text_for_section(
    metrics: Sequence[Mapping[str, Any]],
    section: str,
    metric_ids: frozenset[str] | None = None,
) -> str:
    """按固定顺序输出某个报告章节的规范化指标。"""
    lines = [
        _metric_text(metric)
        for metric in metrics
        if metric["section"] == section
        and (metric_ids is None or metric["metric_id"] in metric_ids)
    ]
    return "\n".join(lines)


def _source_text(context: Mapping[str, Any]) -> str:
    """从指标中提取去重后的来源，避免把原始 metadata JSON 倾倒到报告。"""
    references: list[str] = []
    for metric in context.get("metrics", []):
        if not isinstance(metric, Mapping):
            continue
        reference = _text(metric.get("source_reference"))
        if reference and reference not in references:
            references.append(reference)
    if not references:
        return "无可渲染的来源引用。"
    return "\n".join(f"- {reference}" for reference in references)


def _verdict_display(verdict: Mapping[str, Any], status: str) -> tuple[str, str, str, str]:
    rating = _text(verdict.get("overall_rating")) or (
        "insufficient_data" if status != "ready" else "insufficient_data"
    )
    rating_label = _VERDICT_RATING_LABELS.get(rating, "数据不足")
    risk = _text(verdict.get("risk_level")) or "insufficient_data"
    risk_label = _VERDICT_RISK_LABELS.get(risk, "数据不足")
    raw_rules = verdict.get("triggered_rules", [])
    if isinstance(raw_rules, str):
        raw_rules = [raw_rules]
    rules = [
        _VERDICT_RULE_LABELS[rule]
        for rule in raw_rules
        if isinstance(rule, str) and rule in _VERDICT_RULE_LABELS
    ] if isinstance(raw_rules, Sequence) else []
    rule_label = "、".join(dict.fromkeys(rules)) or "无触发规则"
    action_label = _VERDICT_ACTION_LABELS.get(rating, _VERDICT_ACTION_LABELS["insufficient_data"])
    return rating_label, risk_label, rule_label, action_label


def _term_definitions() -> tuple[str, ...]:
    return (
        "### 术语说明",
        "- P/E（市盈率）：股价相对于每股收益的倍数，用于描述市场对盈利的定价。",
        "- FCF Yield（自由现金流收益率）：自由现金流相对于市值的收益率。",
        "- TTM（过去十二个月）：以最近连续十二个月为口径汇总经营数据。",
        "- DCF（现金流折现）：将未来现金流折算到当前价值的估值方法。",
        "- 反向 DCF（由市场价格倒推隐含增长）：从当前市场价格反推出模型所隐含的增长假设。",
    )


def _visual_markdown(visuals: Mapping[str, str], key: str, alt: str) -> str | None:
    uri = visuals.get(key)
    if not isinstance(uri, str) or not uri.startswith("data:image/png;base64,"):
        return None
    return f"![{alt}]({uri})"


def _reverse_dcf_markdown(payload: Mapping[str, Any]) -> str:
    """把确定性反向 DCF 参数渲染成外行可读的表格。"""
    if not payload:
        return "反向 DCF：缺少已验证的 TTM 自由现金流或模型结果，未生成参数表。"
    rows = [
        "| 参数 | 数值 | 含义 |",
        "|---|---:|---|",
        f"| 基础自由现金流（TTM） | {_currency_display(payload.get('base_fcf'))} | 最近十二个月自由现金流 |",
        f"| 预测年数 | {payload.get('forecast_years', '不可用')} 年 | 固定预测期限 |",
        f"| 基准折现率 | {_percent_display(payload.get('discount_rate')) or '不可用'} | 将未来现金流折算到今天 |",
        f"| 基准永续增长率 | {_percent_display(payload.get('terminal_growth')) or '不可用'} | 预测期后的稳定增长假设 |",
        f"| 基准隐含增长率 | {_percent_display(payload.get('implied_growth')) or '不可用'} | 市场价格反推出的增长要求 |",
    ]
    scenarios = payload.get("scenario_matrix", [])
    if isinstance(scenarios, Sequence) and scenarios:
        rows.extend(
            (
                "",
                "情景矩阵（折现率 / 永续增长率 → 隐含增长率）：",
                "",
                "| 折现率 | 永续增长率 | 隐含增长率 |",
                "|---:|---:|---:|",
            )
        )
        for scenario in scenarios:
            if isinstance(scenario, Mapping):
                rows.append(
                    "| {} | {} | {} |".format(
                        _percent_display(scenario.get("discount_rate")) or "不可用",
                        _percent_display(scenario.get("terminal_growth")) or "不可用",
                        _percent_display(scenario.get("implied_growth")) or "不可用",
                    )
                )
    return "\n".join(rows)


def _render_report_from_context(
    context: Mapping[str, Any], report_draft: ReportDraft
) -> str:
    """使用规范化 Context 渲染，不读取任何估值原始对象。"""
    try:
        validated_context = ReportContext.model_validate(_json_safe_context(context))
    except ValidationError as exc:
        raise ValueError("ReportContext 未通过本地来源和结构校验。") from exc
    context_payload = validated_context.model_dump(mode="json")
    claims = _validated_claims(context_payload["claims"])
    status = context_payload["verdict_status"]
    metrics = context_payload["metrics"]
    verdict = context_payload.get("verdict", {})
    if not isinstance(verdict, Mapping):
        verdict = {}
    rating_label, risk_label, rule_label, action_label = _verdict_display(verdict, status)
    from stockcrewai.report_visuals import build_report_visuals

    visuals = build_report_visuals(context=context_payload)

    sections: list[str] = ["# 投资研究报告", ""]
    for field, heading in _REPORT_SECTIONS:
        sections.extend((f"## {heading}", ""))
        if field == "execution_summary":
            sections.extend(
                (
                    f"确定性状态：status={status}",
                    "",
                    f"总体判断：{rating_label}",
                    f"风险等级：{risk_label}",
                    f"触发规则：{rule_label}",
                    f"行动参考：{action_label}",
                    "",
                    getattr(report_draft, field),
                    "",
                )
            )
            if chart := _visual_markdown(visuals, "financial_kpis", "核心财务指标"):
                sections.extend(
                    (
                        "读图：柱子高于 0 表示增长/利润率为正；股份变化为负表示股份减少。",
                        "",
                        chart,
                        "",
                    )
                )
        elif field == "company_quality":
            sections.extend(
                (
                    getattr(report_draft, field),
                    "",
                    _claim_text(claims, "financial_quality"),
                    "",
                    _metric_text_for_section(
                        metrics, "financial", _REPORT_QUALITY_METRIC_IDS
                    ),
                    "",
                )
            )
        elif field == "financial_trend":
            sections.extend(
                (
                    getattr(report_draft, field),
                    "",
                    _claim_text(claims, "financial_trend"),
                    "",
                    _metric_text_for_section(
                        metrics, "financial", _REPORT_TREND_METRIC_IDS
                    ),
                    "",
                )
            )
            if chart := _visual_markdown(visuals, "ttm_scale", "TTM 财务规模"):
                sections.extend(
                    (
                        "读图：所有柱子都使用最近十二个月口径，单位为十亿美元，便于比较规模而不是比较利润率。",
                        "",
                        chart,
                        "",
                    )
                )
        elif field == "current_valuation":
            sections.extend(
                (getattr(report_draft, field), "", _claim_text(claims, "current_valuation"), "", _metric_text_for_section(metrics, "current_valuation"), "")
            )
        elif field == "historical_valuation":
            sections.extend(
                (getattr(report_draft, field), "", _claim_text(claims, "historical_valuation"), "", _metric_text_for_section(metrics, "historical_valuation"), "")
            )
            if chart := _visual_markdown(visuals, "historical_pe", "五年历史 P/E"):
                sections.extend(
                    (
                        "读图：曲线高于中位数表示当前 TTM P/E 高于自身历史常态；最新点用于定位当前估值。",
                        "",
                        chart,
                        "",
                    )
                )
        elif field == "reverse_dcf":
            sections.extend(
                (
                    getattr(report_draft, field),
                    "",
                    _claim_text(claims, "reverse_dcf"),
                    "",
                    _metric_text_for_section(metrics, "reverse_dcf"),
                    "",
                    _reverse_dcf_markdown(context_payload.get("reverse_dcf", {})),
                    "",
                )
            )
        elif field == "key_risks":
            sections.extend((getattr(report_draft, field), "", _claim_text(claims, "risk"), ""))
        elif field == "sources_and_method":
            sections.extend(
                (
                    getattr(report_draft, field),
                    "",
                    _source_text(context_payload),
                    "",
                    *_term_definitions(),
                    "",
                )
            )
        else:
            sections.extend((getattr(report_draft, field), "", "本文不构成任何投资建议。", ""))

    report = "\n".join(sections).rstrip() + "\n"
    passed, message = validate_rendered_report(report, status)
    if not passed:
        raise ValueError(str(message))
    return report


def _render_legacy_report(
    validated_claims: Any,
    deterministic_verdict: Any,
    valuation: Any,
    historical_valuation: Any,
    reverse_dcf: Any,
    source_metadata: Any,
    report_draft: ReportDraft,
) -> str:
    """兼容旧调用方；主 Flow 不再使用这条原始对象渲染路径。"""
    claims = _validated_claims(validated_claims)
    verdict = _mapping(deterministic_verdict, "deterministic_verdict")
    status = verdict.get("status")
    if not isinstance(status, str) or not status.strip() or "\n" in status:
        raise ValueError("Renderer 缺少确定性 status。")
    if not isinstance(report_draft, ReportDraft):
        raise ValueError("Renderer 只接受经过 Draft Gate 的 ReportDraft。")
    rating_label, risk_label, rule_label, action_label = _verdict_display(verdict, status)
    valuation_payload = _mapping(valuation, "valuation")
    historical_payload = _mapping(historical_valuation, "historical_valuation")
    reverse_dcf_payload = _mapping(reverse_dcf, "reverse_dcf")
    source_payload = _mapping(source_metadata, "source_metadata")

    sections: list[str] = ["# 投资研究报告", ""]
    for field, heading in _REPORT_SECTIONS:
        sections.extend((f"## {heading}", ""))
        if field == "execution_summary":
            sections.extend(
                (
                    f"确定性状态：status={status}",
                    "",
                    f"总体判断：{rating_label}",
                    f"风险等级：{risk_label}",
                    f"触发规则：{rule_label}",
                    f"行动参考：{action_label}",
                    "",
                    getattr(report_draft, field),
                    "",
                )
            )
        elif field == "company_quality":
            sections.extend((getattr(report_draft, field), "", _claim_text(claims, "financial_quality"), ""))
        elif field == "financial_trend":
            sections.extend((getattr(report_draft, field), "", _claim_text(claims, "financial_trend"), ""))
        elif field == "current_valuation":
            sections.extend((getattr(report_draft, field), "", _claim_text(claims, "current_valuation"), "", f"确定性估值数据：{_json_text(valuation_payload)}", ""))
        elif field == "historical_valuation":
            sections.extend((getattr(report_draft, field), "", _claim_text(claims, "historical_valuation"), "", f"确定性历史估值数据：{_json_text(historical_payload)}", ""))
        elif field == "reverse_dcf":
            sections.extend((getattr(report_draft, field), "", _claim_text(claims, "reverse_dcf"), "", f"确定性反向 DCF 数据：{_json_text(reverse_dcf_payload)}", ""))
        elif field == "key_risks":
            sections.extend((getattr(report_draft, field), "", _claim_text(claims, "risk"), ""))
        elif field == "sources_and_method":
            sections.extend(
                (
                    getattr(report_draft, field),
                    "",
                    f"确定性来源元数据：{_json_text(source_payload)}",
                    "",
                    *_term_definitions(),
                    "",
                )
            )
        else:
            sections.extend((getattr(report_draft, field), "", "本文不构成任何投资建议。", ""))

    report = "\n".join(sections).rstrip() + "\n"
    passed, message = validate_rendered_report(report, status)
    if not passed:
        raise ValueError(str(message))
    return report


def render_validated_report(
    validated_claims: Any = None,
    deterministic_verdict: Any = None,
    valuation: Any = None,
    historical_valuation: Any = None,
    reverse_dcf: Any = None,
    source_metadata: Any = None,
    report_draft: ReportDraft | None = None,
    *,
    report_context: Any = None,
) -> str:
    """从唯一 ReportContext 和 ReportDraft 渲染最终中文 Markdown。

    新调用方式是 ``render_validated_report(report_context=context,
    report_draft=draft)``，或传入 ``(context, draft)``。旧的七参数形式仅
    保留兼容性；主流程明确不会走旧路径。
    """
    is_context_positional = (
        report_context is None
        and isinstance(validated_claims, Mapping)
        and "metrics" in validated_claims
        and isinstance(deterministic_verdict, ReportDraft)
        and valuation is None
        and historical_valuation is None
        and reverse_dcf is None
        and source_metadata is None
        and report_draft is None
    )
    if is_context_positional:
        report_context = validated_claims
        report_draft = deterministic_verdict
    if report_context is not None:
        if not isinstance(report_draft, ReportDraft):
            raise ValueError("Renderer 只接受经过 Draft Gate 的 ReportDraft。")
        return _render_report_from_context(report_context, report_draft)
    if report_draft is None:
        raise ValueError("Renderer 缺少 ReportDraft。")
    return _render_legacy_report(
        validated_claims,
        deterministic_verdict,
        valuation,
        historical_valuation,
        reverse_dcf,
        source_metadata,
        report_draft,
    )


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


def validate_report_output(output: TaskOutput) -> tuple[bool, Any]:
    """兼容旧调用名；仅执行最终 Markdown 的非数字安全检查。"""
    payload = _report_payload(output)
    return validate_rendered_report(payload)


@CrewBase
class ReportCrew:
    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def report_writer_agent(self) -> Agent:
        """装配 DeepSeek JSON Object 与本地 ReportDraft guardrail。"""
        config = self.agents_config["report_writer_agent"]  # type: ignore[index]
        return Agent(
            config=config,
            llm=LLM(
                model=config["llm"],  # type: ignore[index]
                response_format={"type": "json_object"},
            ),
        )

    @task
    def generate_validated_report_task(self) -> Task:
        """装配 DeepSeek JSON Object 与本地 ReportDraft guardrail。"""
        return Task(
            config=self.tasks_config["generate_validated_report_task"],  # type: ignore[index]
            guardrail=validate_report_draft,
            guardrail_max_retries=2,
        )

    @crew
    def crew(self) -> Crew:
        """组装报告 Crew，不在此处生成最终 Markdown。"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )
