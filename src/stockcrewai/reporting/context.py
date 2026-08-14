from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
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
from stockcrewai.profiles.reit import PROFILE_VERSION as REIT_PROFILE_VERSION


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
        "ffo_total",
        "ffo_per_share",
        "affo",
        "net_debt_to_ebitda",
        "dividend_coverage",
    }
)
_REPORT_TREND_METRIC_IDS = frozenset(
    {"revenue_growth", "free_cash_flow", "share_dilution"}
)
_REIT_FORMULA_TO_METRIC = {
    "reit-ffo-reconciliation-v1": "ffo_total",
    "reit-ffo-per-share-v1": "ffo_per_share",
    "company-disclosed-affo-reconciliation-v1": "affo",
    "reit-net-debt-to-ebitda-v1": "net_debt_to_ebitda",
    "reit-dividend-coverage-v1": "dividend_coverage",
    "reit-price-to-ffo-v1": "price_to_ffo",
}
_PROFILE_ISSUERS = frozenset(
    {
        "bank",
        "insurance",
        "utility",
        "commodity_producer",
        "holding_company",
        "spac",
    }
)
_FOREIGN_REPORTING_PROFILE = "foreign_private_issuer_ifrs"
_FOREIGN_ADR_FORMULA_IDS = frozenset(
    {
        "foreign-adr-ratio-direct-v1",
        "foreign-adr-equivalent-shares-v1",
        "foreign-adr-market-cap-v1",
    }
)


def _normalized_amount(value: Any, unit: Any = None) -> Decimal | None:
    raw = _text(value)
    if raw is None:
        return None
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None

    normalized_unit = (_text(unit) or "").lower().replace(" ", "")
    if "万亿" in normalized_unit or "trillion" in normalized_unit:
        amount *= Decimal("1000000000000")
    elif "亿美元" in normalized_unit:
        amount *= Decimal("100000000")
    elif "十亿" in normalized_unit or "billion" in normalized_unit:
        amount *= Decimal("1000000000")
    elif "百万" in normalized_unit or "million" in normalized_unit:
        amount *= Decimal("1000000")
    elif "千" in normalized_unit or "thousand" in normalized_unit:
        amount *= Decimal("1000")
    return amount


def _validated_ttm_fcf(value: Any) -> Decimal | None:
    payload = value if isinstance(value, Mapping) else {"metrics": value}
    metrics = payload.get("metrics", [])
    if isinstance(metrics, Mapping):
        metrics = list(metrics.values())
    if not isinstance(metrics, Sequence) or isinstance(metrics, (str, bytes)):
        return None
    for metric in metrics:
        if not isinstance(metric, Mapping):
            continue
        if (
            metric.get("metric_id") == "free_cash_flow"
            and metric.get("period_basis") == "TTM"
            and metric.get("status") == "available"
            and metric.get("validation_status") == "valid"
        ):
            return _normalized_amount(metric.get("raw_result"), metric.get("unit"))
    return None


def _verified_annual_financial_history(value: Any) -> dict[str, Any]:
    """只接受已验证的五期共同 FY 历史，拒绝在报告层补算或补值。"""
    if not isinstance(value, Mapping):
        return {}
    if value.get("status") != "ok" or value.get("validation_status") != "valid":
        return {}
    currency = _text(value.get("currency"))
    raw_periods = value.get("periods")
    if (
        not currency
        or currency.upper() != "USD"
        or not isinstance(raw_periods, Sequence)
        or isinstance(
        raw_periods, (str, bytes)
        )
        or len(raw_periods) != 5
    ):
        return {}
    currency = "USD"

    normalized_periods: list[dict[str, Any]] = []
    fiscal_years: set[int] = set()
    for raw_period in raw_periods:
        if not isinstance(raw_period, Mapping):
            return {}
        try:
            fiscal_year = int(raw_period.get("fiscal_year"))
        except (TypeError, ValueError):
            return {}
        if fiscal_year in fiscal_years or raw_period.get("period_basis") != "FY":
            return {}
        if (
            raw_period.get("validation_status") != "valid"
            or (_text(raw_period.get("currency")) or "").upper() != currency
            or not all(
                _text(raw_period.get(key))
                for key in ("period_start", "period_end", "filed_at")
            )
        ):
            return {}
        evidence_ids = _ids(raw_period.get("evidence_ids"))
        calculation_id = _text(raw_period.get("calculation_id"))
        provenance = raw_period.get("calculation_provenance")
        if (
            len(evidence_ids) != 4
            or not calculation_id
            or calculation_id != f"calc_annual_fcf_{fiscal_year}"
            or not isinstance(provenance, Mapping)
            or _text(provenance.get("formula"))
            != "free_cash_flow = operating_cash_flow - positive_capex"
            or _ids(provenance.get("input_evidence_ids")) != evidence_ids
        ):
            return {}
        decimals: dict[str, Decimal] = {}
        for key in (
            "revenue",
            "net_income",
            "operating_cash_flow",
            "capex",
            "free_cash_flow",
        ):
            try:
                decimal_value = Decimal(str(raw_period.get(key)))
            except (InvalidOperation, TypeError, ValueError):
                return {}
            if not decimal_value.is_finite():
                return {}
            decimals[key] = decimal_value
        if decimals["capex"] < 0:
            return {}
        if (
            decimals["operating_cash_flow"] - decimals["capex"]
            != decimals["free_cash_flow"]
        ):
            return {}
        fiscal_years.add(fiscal_year)
        normalized_periods.append(
            {
                "fiscal_year": fiscal_year,
                "period_start": _text(raw_period.get("period_start")),
                "period_end": _text(raw_period.get("period_end")),
                "filed_at": _text(raw_period.get("filed_at")),
                "period_basis": "FY",
                "currency": currency,
                **{
                    key: format(decimal_value, "f")
                    for key, decimal_value in decimals.items()
                },
                "evidence_ids": evidence_ids,
                "calculation_id": calculation_id,
                "calculation_provenance": _json_safe_context(provenance),
                "validation_status": "valid",
            }
        )
    normalized_periods.sort(key=lambda period: period["fiscal_year"])
    return {
        "status": "ok",
        "reason_code": None,
        "currency": currency,
        "periods": normalized_periods,
        "validation_status": "valid",
    }


def _annual_cagr(start: Decimal, end: Decimal) -> Decimal | None:
    if start <= 0 or end <= 0:
        return None
    try:
        with localcontext() as context:
            context.prec = 40
            return (end / start) ** (Decimal(1) / Decimal(4)) - Decimal(1)
    except (ArithmeticError, ValueError):
        return None


def _annual_financial_summary(
    annual_payload: Mapping[str, Any], reverse_payload: Mapping[str, Any]
) -> dict[str, Any]:
    periods = annual_payload.get("periods")
    if (
        annual_payload.get("status") != "ok"
        or annual_payload.get("validation_status") != "valid"
        or not isinstance(periods, Sequence)
        or isinstance(periods, (str, bytes))
        or len(periods) != 5
    ):
        return {}

    values: list[dict[str, Decimal]] = []
    for period in periods:
        if not isinstance(period, Mapping) or period.get("period_basis") != "FY":
            return {}
        period_values: dict[str, Decimal] = {}
        for metric_id in ("revenue", "net_income", "free_cash_flow"):
            raw_value = period.get(metric_id)
            if raw_value is None or isinstance(raw_value, (bool, float)):
                return {}
            try:
                value = Decimal(str(raw_value))
            except (InvalidOperation, TypeError, ValueError):
                return {}
            if not value.is_finite():
                return {}
            period_values[metric_id] = value
        values.append(period_values)

    cagr = {
        metric_id: _annual_cagr(values[0][metric_id], values[-1][metric_id])
        for metric_id in ("revenue", "net_income", "free_cash_flow")
    }
    if any(value is None for value in cagr.values()):
        return {}

    summary: dict[str, Any] = {
        "start_fiscal_year": periods[0]["fiscal_year"],
        "end_fiscal_year": periods[-1]["fiscal_year"],
        "revenue_cagr": _percentage_text(cagr["revenue"]),
        "net_income_cagr": _percentage_text(cagr["net_income"]),
        "free_cash_flow_cagr": _percentage_text(cagr["free_cash_flow"]),
        "latest_fcf_direction": (
            "up"
            if values[-1]["free_cash_flow"] > values[-2]["free_cash_flow"]
            else "down"
            if values[-1]["free_cash_flow"] < values[-2]["free_cash_flow"]
            else "flat"
        ),
        "validation_status": "valid",
        "basis_note": (
            "CAGR 基于五个完整 FY 历史；反向 DCF 以 TTM FCF 为起点，"
            "二者口径不同，仅作方向比较，不是预测。"
        ),
    }

    implied_growth_raw = reverse_payload.get("implied_growth")
    if (
        reverse_payload.get("status") == "ok"
        and reverse_payload.get("validation_status") == "valid"
        and implied_growth_raw is not None
        and not isinstance(implied_growth_raw, (bool, float))
    ):
        try:
            implied_growth = Decimal(str(implied_growth_raw))
        except (InvalidOperation, TypeError, ValueError):
            implied_growth = None
        if implied_growth is not None and implied_growth.is_finite():
            summary["expectation_gap_percentage_points"] = _percentage_text(
                implied_growth - cagr["free_cash_flow"]
            )
    return summary


def _validated_reverse_dcf_fcf(value: Mapping[str, Any]) -> Decimal | None:
    if (
        value.get("status") != "ok"
        or value.get("validation_status") != "valid"
        or value.get("period_basis") != "TTM"
    ):
        return None
    unit = value.get("base_fcf_unit", value.get("unit"))
    return _normalized_amount(value.get("base_fcf"), unit)


def _validate_ttm_fcf_consistency(
    ttm_payload: Any, reverse_payload: Mapping[str, Any]
) -> None:
    ttm_fcf = _validated_ttm_fcf(ttm_payload)
    reverse_fcf = _validated_reverse_dcf_fcf(reverse_payload)
    if ttm_fcf is not None and reverse_fcf is not None and ttm_fcf != reverse_fcf:
        raise ValueError("report_ttm_fcf_mismatch: TTM FCF and reverse DCF base FCF differ")


class ReportMetric(BaseModel):
    """报告 Renderer 唯一允许展示的规范化指标。"""

    model_config = ConfigDict(extra="forbid")

    section: StrictStr
    metric_id: StrictStr
    display_value: StrictStr
    unit: StrictStr
    as_of: StrictStr
    period_end: StrictStr | None = None
    period_basis: StrictStr | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    source_reference: StrictStr
    evidence_ids: list[StrictStr]
    calculation_id: StrictStr | None = None
    status: StrictStr = "unavailable"
    validation_status: StrictStr = "unknown"
    adjustment_basis: StrictStr | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    provenance_type: StrictStr = Field(
        default="calculation", exclude_if=lambda value: value == "calculation"
    )

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
        if self.provenance_type not in {"calculation", "direct_evidence"}:
            raise ValueError("ReportMetric provenance_type 无效。")
        if (
            self.metric_id != "market_price"
            and not self.calculation_id
            and self.provenance_type != "direct_evidence"
        ):
            raise ValueError("非市场价格指标必须包含 Calculation ID。")
        if self.provenance_type == "direct_evidence" and self.calculation_id:
            raise ValueError("direct_evidence 指标不得包含 Calculation ID。")
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
    annual_financial_history: dict[str, Any] = Field(
        default_factory=dict,
        exclude_if=lambda value: not value,
    )
    annual_financial_summary: dict[str, Any] = Field(default_factory=dict)
    historical_valuation: dict[str, Any] = Field(default_factory=dict)
    reverse_dcf: dict[str, Any] = Field(default_factory=dict)
    reit_metrics: dict[str, Any] | None = None
    profile_metrics: dict[str, Any] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

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
        for nested_key in (
            "financial_evidence_ids",
            "input_evidence_ids",
            "evidence_ids",
        ):
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
        "annual_financial_history",
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


def _evidence_period_basis(
    evidence_ids: list[str], evidence_index: Mapping[str, Mapping[str, Any]]
) -> str | None:
    for evidence_id in evidence_ids:
        period_basis = _text(evidence_index.get(evidence_id, {}).get("period_basis"))
        if period_basis:
            return period_basis
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
    period_basis: Any = None,
    require_direct_source: bool = False,
    provenance_type: str = "calculation",
    adjustment_basis: Any = None,
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
    if (
        metric_name != "market_price"
        and not calculation
        and provenance_type != "direct_evidence"
    ):
        return None
    source = _evidence_source(
        ids,
        evidence_index,
        direct_source,
        require_direct=require_direct_source,
    )
    as_of = _evidence_as_of(ids, evidence_index, direct_as_of)
    basis = _text(period_basis) or _evidence_period_basis(ids, evidence_index)
    if not source or not as_of:
        return None
    try:
        return ReportMetric(
            section=section,
            metric_id=metric_name,
            display_value=display,
            unit=unit_text,
            as_of=as_of,
            period_basis=basis,
            source_reference=source,
            evidence_ids=ids,
            calculation_id=calculation,
            status="available",
            validation_status="valid",
            adjustment_basis=_text(adjustment_basis),
            provenance_type=provenance_type,
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


def _reit_metric_from_calculation(
    calculation: Mapping[str, Any],
    evidence_index: Mapping[str, Mapping[str, Any]],
) -> ReportMetric | None:
    """把 S04 的已验证 REIT CalculationRecord 投影成报告指标。"""
    if calculation.get("validation_status") != "valid":
        return None
    formula_id = _text(calculation.get("formula_id"))
    metric_id = _REIT_FORMULA_TO_METRIC.get(formula_id or "")
    result = calculation.get("result")
    if metric_id is None or _text(result) is None:
        return None
    evidence_ids = _ids(calculation.get("input_evidence_ids"))
    period_end = _text(calculation.get("period_end"))
    if not evidence_ids or period_end is None:
        return None
    if any(evidence_id not in evidence_index for evidence_id in evidence_ids):
        return None
    display_value = calculation.get("display_result", result)
    metric = _metric_from_payload(
        section="current_valuation" if metric_id == "price_to_ffo" else "financial",
        metric_id=metric_id,
        display_value=_json_safe_context(display_value),
        unit=calculation.get("unit"),
        evidence_ids=evidence_ids,
        calculation_id=calculation.get("calculation_id"),
        evidence_index=evidence_index,
        direct_as_of=(calculation.get("as_of"),),
    )
    return metric.model_copy(update={"period_end": period_end}) if metric else None


def _foreign_base_metric_from_calculation(
    calculation: Mapping[str, Any],
    evidence_index: Mapping[str, Mapping[str, Any]],
) -> ReportMetric | None:
    """把 foreign profile 中已验证的基础 CalculationRecord 投影到报告。"""
    if calculation.get("validation_status") != "valid":
        return None
    formula_id = _text(calculation.get("formula_id"))
    if not formula_id or formula_id in _FOREIGN_ADR_FORMULA_IDS:
        return None
    if (
        calculation.get("result") is None
        or not _text(calculation.get("calculation_id"))
        or not _text(calculation.get("source_reference"))
        or not _text(calculation.get("as_of"))
    ):
        return None
    return _metric_from_payload(
        section="financial",
        metric_id=formula_id.removesuffix(":v1"),
        display_value=calculation.get("result"),
        unit=calculation.get("unit"),
        evidence_ids=calculation.get("input_evidence_ids"),
        calculation_id=calculation.get("calculation_id"),
        evidence_index=evidence_index,
        direct_source=calculation.get("source_reference"),
        direct_as_of=(calculation.get("as_of"),),
        require_direct_source=True,
    )


def _profile_metric_from_decision(
    decision: Mapping[str, Any],
    values: Mapping[str, Any],
    calculations: Sequence[Mapping[str, Any]],
    evidence_index: Mapping[str, Mapping[str, Any]],
) -> ReportMetric | None:
    """把银行/保险 adapter 的 verified decision 投影成报告指标。"""
    if decision.get("status") != "available":
        return None
    metric_id = _text(decision.get("metric_id"))
    value = values.get(metric_id) if metric_id else None
    evidence_ids = _ids(decision.get("evidence_ids"))
    calculation_ids = _ids(decision.get("calculation_ids"))
    if not metric_id or value is None:
        return None
    if calculation_ids:
        calculation = next(
            (
                item
                for item in calculations
                if item.get("calculation_id") == calculation_ids[0]
            ),
            None,
        )
        if calculation is None or calculation.get("validation_status") != "valid":
            return None
        input_evidence_ids = _ids(calculation.get("input_evidence_ids"))
        if not input_evidence_ids:
            return None
        metric = _metric_from_payload(
            section="financial",
            metric_id=metric_id,
            display_value=value,
            unit=calculation.get("unit"),
            evidence_ids=input_evidence_ids,
            calculation_id=calculation.get("calculation_id"),
            evidence_index=evidence_index,
            direct_source=calculation.get("source_reference"),
            direct_as_of=(calculation.get("as_of"), calculation.get("period_end")),
        )
        return metric
    if not evidence_ids:
        return None
    source = evidence_index.get(evidence_ids[0], {})
    return _metric_from_payload(
        section="financial",
        metric_id=metric_id,
        display_value=value,
        unit=source.get("unit"),
        evidence_ids=evidence_ids,
        calculation_id=None,
        evidence_index=evidence_index,
        direct_source=source.get("source_reference"),
        direct_as_of=(source.get("as_of"), source.get("period_end")),
        require_direct_source=True,
        provenance_type="direct_evidence",
    )


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


def _percentage_text(value: Decimal) -> str:
    return f"{value * Decimal('100'):.2f}"


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
    "period_basis",
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
        period_basis = raw_metric.get("period_basis")
        if (
            period_basis != "TTM"
            or raw_metric.get("status") != "available"
            or raw_metric.get("validation_status") != "valid"
        ):
            continue
        metric = {
            key: _json_safe_context(raw_metric[key])
            for key in _TTM_CONTEXT_FIELDS
            if key in raw_metric and raw_metric[key] is not None
        }
        metric["period_basis"] = "TTM"
        metrics.append(metric)
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
    summary = {
        key: _json_safe_context(value[key])
        for key in (
            "current_value",
            "percentile_25",
            "five_year_median",
            "percentile_75",
            "current_percentile",
        )
        if value.get(key) is not None
    }
    return {
        "status": "ok",
        "validation_status": "valid",
        "period_basis": "TTM",
        "series": normalized_series,
        "current_date": current_date,
        **summary,
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
            "base_fcf_unit",
            "unit",
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
    annual_financial_history: Any = None,
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
    policy_profile = policy_payload.get("profile")
    profile_issuer = (
        _text(policy_profile.get("issuer_profile"))
        if isinstance(policy_profile, Mapping)
        else None
    )
    profile_reporting = (
        _text(policy_profile.get("reporting_profile"))
        if isinstance(policy_profile, Mapping)
        else None
    )
    profile_security = (
        _text(policy_profile.get("security_profile"))
        if isinstance(policy_profile, Mapping)
        else None
    )
    is_reit_profile = (
        profile_issuer == "reit"
    )
    is_holding_profile = profile_issuer == "holding_company"
    is_spac_profile = profile_security == "spac"
    is_financial_profile = (
        profile_issuer in _PROFILE_ISSUERS
        or is_spac_profile
        or profile_reporting == _FOREIGN_REPORTING_PROFILE
    )
    annual_input = (
        annual_financial_history
        if annual_financial_history is not None
        else source_payload.get("annual_financial_history")
    )
    annual_payload = _verified_annual_financial_history(annual_input)
    if isinstance(policy_profile, Mapping) and profile_issuer != "standard_operating":
        annual_payload = {}
    annual_summary = _annual_financial_summary(annual_payload, reverse_payload)
    if annual_payload:
        source_payload["annual_financial_history"] = annual_payload
    else:
        source_payload.pop("annual_financial_history", None)
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
    canonical_ttm_fcf = _validated_ttm_fcf(ttm_payload)
    reverse_dcf_fcf = _validated_reverse_dcf_fcf(reverse_payload)
    reverse_dcf_base_present = _text(reverse_payload.get("base_fcf")) is not None
    reverse_dcf_base_ready = (
        reverse_dcf_base_present
        and canonical_ttm_fcf is not None
        and reverse_dcf_fcf is not None
    )
    if "reverse_dcf" not in not_applicable_metrics:
        _validate_ttm_fcf_consistency(ttm_payload, reverse_payload)
    profile_evidence_records = policy_payload.get("evidence_records", [])
    if isinstance(profile_evidence_records, Sequence) and not isinstance(
        profile_evidence_records, (str, bytes)
    ):
        existing_facts = source_payload.get("facts", {})
        existing_facts = existing_facts if isinstance(existing_facts, Mapping) else {}
        source_payload["facts"] = {
            **existing_facts,
            **{
                str(record.get("evidence_id")): record
                for record in profile_evidence_records
                if isinstance(record, Mapping) and _text(record.get("evidence_id"))
            },
        }
    profile_market_records = policy_payload.get("market_price_records", [])
    if profile_market_records and not source_payload.get("market_price"):
        if isinstance(profile_market_records, Sequence) and not isinstance(
            profile_market_records, (str, bytes)
        ):
            source_payload["market_price"] = next(
                (
                    record
                    for record in profile_market_records
                    if isinstance(record, Mapping)
                ),
                {},
            )

    claims = _validated_claims(validated_claims or [])
    verdict_status = _text(verdict_payload.get("status"))
    if not verdict_status:
        raise ValueError("ReportContext 缺少确定性 verdict status。")
    evidence_index = _evidence_index(source_payload)
    metrics: list[ReportMetric] = []

    if is_reit_profile:
        for calculation in _calculation_items(policy_payload.get("calculation_records")):
            metric = _reit_metric_from_calculation(calculation, evidence_index)
            if metric is not None:
                metrics.append(metric)
    elif is_financial_profile:
        profile_values = policy_payload.get("values", {})
        profile_values = profile_values if isinstance(profile_values, Mapping) else {}
        profile_calculations = _calculation_items(
            policy_payload.get("calculation_records")
        )
        for decision in policy_decisions:
            if isinstance(decision, Mapping):
                metric = _profile_metric_from_decision(
                    decision,
                    profile_values,
                    profile_calculations,
                    evidence_index,
                )
                if metric is not None:
                    metrics.append(metric)
        if profile_reporting == _FOREIGN_REPORTING_PROFILE:
            for calculation in profile_calculations:
                metric = _foreign_base_metric_from_calculation(
                    calculation, evidence_index
                )
                if metric is not None:
                    metrics.append(metric)
    else:
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
                period_basis=calculation.get("period_basis"),
                adjustment_basis=(
                    calculation.get("adjustment_basis")
                    if calculation.get("formula_id") == "share_dilution"
                    else None
                ),
            )
            if metric is not None:
                metrics.append(metric)

    valuation_calculations = (
        []
        if is_reit_profile or is_holding_profile
        else _calculation_items(valuation_payload)
    )
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
    if (
        not is_holding_profile
        and (valuation_ready or (not_applicable_metrics and valuation_base_ready))
    ):
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
        and reverse_dcf_base_ready
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

    reit_metrics_payload: dict[str, Any] | None = None
    if is_reit_profile:
        raw_reit_calculations = _calculation_items(policy_payload.get("calculation_records"))
        reit_metrics_payload = {
            "profile_version": _json_safe_context(
                policy_profile.get("profile_version")
                or policy_payload.get("profile_version")
                or REIT_PROFILE_VERSION
            ),
            "policy_version": _json_safe_context(policy_payload.get("policy_version")),
            "values": _json_safe_context(policy_payload.get("values", {})),
            "policy_decisions": _json_safe_context(policy_decisions),
            "calculation_records": _json_safe_context(raw_reit_calculations),
        }

    profile_metrics_payload: dict[str, Any] | None = None
    if is_financial_profile:
        profile_metrics_profile_version = (
            policy_profile.get("profile_version")
            if isinstance(policy_profile, Mapping)
            else None
        )
        if (is_holding_profile or is_spac_profile) and not _text(
            profile_metrics_profile_version
        ):
            profile_metrics_profile_version = policy_payload.get("profile_version")
        profile_metrics_payload = {
            "profile_version": _json_safe_context(profile_metrics_profile_version),
            "policy_version": _json_safe_context(policy_payload.get("policy_version")),
            "metric_ids": [
                decision.get("metric_id")
                for decision in policy_decisions
                if isinstance(decision, Mapping) and _text(decision.get("metric_id"))
            ],
            "values": _json_safe_context(policy_payload.get("values", {})),
            "policy_decisions": _json_safe_context(policy_decisions),
            "calculation_records": _json_safe_context(
                _calculation_items(policy_payload.get("calculation_records"))
            ),
        }
        if profile_reporting == _FOREIGN_REPORTING_PROFILE:
            profile_metrics_payload["foreign_metadata"] = _json_safe_context(
                policy_payload.get("foreign_metadata", {})
            )

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
        annual_financial_history=annual_payload,
        annual_financial_summary=annual_summary,
        historical_valuation=(
            {}
            if "historical_valuation" in not_applicable_metrics
            else _historical_visual_context(historical_payload)
        ),
        reverse_dcf=(
            {}
            if (
                "reverse_dcf" in not_applicable_metrics
                or not reverse_dcf_base_ready
            )
            else _verified_reverse_dcf_context(reverse_payload)
        ),
        reit_metrics=reit_metrics_payload,
        profile_metrics=profile_metrics_payload,
    )
    context_payload = context.model_dump(mode="json")
    for metric in context_payload.get("metrics", []):
        if isinstance(metric, Mapping) and metric.get("period_end") is None:
            metric.pop("period_end", None)
    if not policy_payload:
        for key in ("profile", "coverage_level", "policy_version"):
            context_payload.pop(key, None)
    if not is_reit_profile:
        context_payload.pop("reit_metrics", None)
    return context_payload


__all__ = [
    "ReportContext",
    "ReportMetric",
    "build_report_context",
]
