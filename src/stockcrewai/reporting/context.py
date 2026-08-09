from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)


_REPORT_NARRATIVE_CATEGORIES = (
    "financial_quality",
    "financial_trend",
    "valuation",
    "risk",
)
_REPORT_NARRATIVE_CATEGORY_MAP = {
    "financial_quality": "financial_quality",
    "financial_trend": "financial_trend",
    "current_valuation": "valuation",
    "historical_valuation": "valuation",
    "reverse_dcf": "valuation",
    "risk": "risk",
}
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
    profile: dict[str, Any] | None = None
    coverage_level: StrictStr | None = None
    policy_version: StrictStr | None = None
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
    """把 Report Context 转成可安全交给 CrewAI 和 JSON 的值。"""
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
                    "financial_evidence_ids": _ids(point.get("financial_evidence_ids")),
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
    policy_context: Mapping[str, Any] | None = None,
    company_name: Any = None,
    ticker: Any = None,
    financial_calculations: Any = None,
    ttm: Any = None,
) -> dict[str, Any]:
    """构造 Report Crew 与 Renderer 共享的唯一 JSON-safe 输入。"""
    company_payload = dict(company or {})
    if not company_payload:
        company_payload = {
            key: value
            for key, value in (("name", company_name), ("ticker", ticker))
            if _text(value)
        }
    verdict_payload = dict(deterministic_verdict or {})
    source_payload = dict(source_metadata or {})
    policy_payload = dict(policy_context or {})
    valuation_payload = dict(valuation or {})
    historical_payload = dict(historical_valuation or {})
    reverse_payload = dict(reverse_dcf or {})
    policy_decisions = policy_payload.get("policy_decisions")
    if not isinstance(policy_decisions, list):
        policy_decisions = []
    not_applicable_metrics = {
        decision.get("metric_id")
        for decision in policy_decisions
        if isinstance(decision, Mapping)
        and decision.get("status") == "not_applicable"
        and isinstance(decision.get("metric_id"), str)
    }
    calculation_payload = calculations if calculations is not None else financial_calculations
    ttm_payload = ttm if ttm is not None else source_payload.get("ttm")

    claims = _validated_claims(validated_claims or [])
    verdict_status = _text(verdict_payload.get("status"))
    if not verdict_status:
        raise ValueError("ReportContext 缺少确定性 verdict status。")
    evidence_index = _evidence_index(source_payload)
    metrics: list[ReportMetric] = []

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

    valuation_calculations = _calculation_items(valuation_payload)
    applicable_valuation_calculations = [
        calculation
        for calculation in valuation_calculations
        if calculation.get("formula_id") not in not_applicable_metrics
    ]
    valuation_base_ready = (
        valuation_payload.get("validation_status") == "valid"
        and valuation_payload.get("readiness") == "ready"
    )
    valuation_ready = (
        valuation_base_ready
        and bool(applicable_valuation_calculations)
        and all(
            item.get("status") == "available"
            and item.get("validation_status") == "valid"
            and _text(item.get("source_reference"))
            for item in applicable_valuation_calculations
        )
    )
    if valuation_ready or (not_applicable_metrics and valuation_base_ready):
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
        if market_metric is not None and "market_price" not in not_applicable_metrics:
            metrics.append(market_metric)
        for calculation in applicable_valuation_calculations if valuation_ready else []:
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

    historical_ids = _ids(historical_payload.get("input_evidence_ids"))
    historical_calculation_id = _text(historical_payload.get("calculation_id"))
    historical_ready = (
        historical_payload.get("status") == "ok"
        and historical_payload.get("validation_status") == "valid"
        and bool(historical_calculation_id)
        and bool(historical_ids)
        and "historical_valuation" not in not_applicable_metrics
    )
    selected_dates = historical_payload.get("selected_dates", [])
    selected_date_values = (
        selected_dates
        if isinstance(selected_dates, Sequence) and not isinstance(selected_dates, (str, bytes))
        else []
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

    reverse_ids = _ids(reverse_payload.get("input_evidence_ids"))
    reverse_calculation_id = _text(reverse_payload.get("calculation_id"))
    if (
        reverse_payload.get("status") == "ok"
        and reverse_payload.get("validation_status") == "valid"
        and reverse_calculation_id
        and reverse_ids
        and "reverse_dcf" not in not_applicable_metrics
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
            direct_as_of=(
                reverse_payload.get("as_of"),
                reverse_payload.get("price_timestamp"),
            ),
        )
        if metric is not None:
            metrics.append(metric)

    policy_profile = policy_payload.get("profile")
    policy_fields: dict[str, Any] = {}
    if policy_payload:
        if isinstance(policy_profile, Mapping):
            policy_fields["profile"] = _json_safe_context(policy_profile)
        coverage_level = _text(policy_payload.get("coverage_level"))
        if coverage_level:
            policy_fields["coverage_level"] = coverage_level
        policy_version = _text(policy_payload.get("policy_version"))
        if policy_version:
            policy_fields["policy_version"] = policy_version

    context = ReportContext(
        company=_json_safe_context(company_payload),
        **policy_fields,
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
        historical_valuation=(
            {}
            if "historical_valuation" in not_applicable_metrics
            else _historical_visual_context(historical_payload)
        ),
        reverse_dcf=(
            {}
            if "reverse_dcf" in not_applicable_metrics
            else _verified_reverse_dcf_context(reverse_payload)
        ),
    )
    context_payload = context.model_dump(mode="json")
    if not policy_payload:
        for key in ("profile", "coverage_level", "policy_version"):
            context_payload.pop(key, None)
    return context_payload


__all__ = ["ReportContext", "ReportMetric", "build_report_context"]
