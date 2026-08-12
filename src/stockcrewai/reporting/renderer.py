from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from .context import (
    _CLAIM_CATEGORY_TO_SECTION,
    _REPORT_NARRATIVE_CATEGORIES,
    _REPORT_NARRATIVE_CATEGORY_MAP,
    _REPORT_QUALITY_METRIC_IDS,
    _REPORT_SECTIONS,
    _REPORT_TREND_METRIC_IDS,
    _currency_display,
    _json_safe_context,
    _normalized_amount,
    _percent_display,
    _text,
    _validated_claims,
    ReportContext,
    ReportMetric,
    build_report_context,
)
from .validator import (
    ReportDraft,
    validate_rendered_report,
)
from .visuals import build_report_visuals


_REPORT_METRIC_LABELS = {
    "market_price": "市场价格",
    "revenue": "营业收入",
    "operating_income": "营业利润",
    "net_income": "净利润",
    "operating_cash_flow": "经营现金流",
    "capex": "资本开支",
    "diluted_eps": "稀释后每股收益",
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
    "attributable_holdings_value": "归属持仓价值",
    "holding_company_nav": "控股公司 NAV（净资产价值）",
    "holding_company_market_cap": "控股公司市值",
    "holding_company_nav_discount": "NAV 折价/溢价",
    "adr_ratio": "ADR 换普通股比例",
    "adr_equivalent_shares": "ADR 等价股数",
    "adr_market_cap": "ADR 等价市值",
    "bank_roa": "银行 ROA",
    "bank_roe": "银行 ROE",
    "net_interest_margin": "NIM",
    "efficiency_ratio": "效率比率",
    "cet1_ratio": "CET1",
    "loan_to_deposit": "贷存比",
    "nonperforming_loan_ratio": "不良贷款率",
    "provision_coverage": "拨备覆盖率",
    "loss_ratio": "赔付率",
    "expense_ratio": "费用率",
    "combined_ratio": "综合成本率",
    "insurance_roe": "保险 ROE",
    "book_value_per_share": "每股账面价值",
    "investment_income": "投资收益",
    "solvency_ratio": "偿付能力",
    "utility_operating_margin": "公用事业营业利润率",
    "rate_base": "Rate Base（费率基数）",
    "capex_intensity": "CapEx 强度",
    "interest_coverage": "利息保障倍数",
    "utility_roe": "公用事业 ROE",
    "realized_price": "商品实现价格",
    "production": "商品产量",
    "realized_price_change": "商品实现价格变化率",
    "production_change": "商品产量变化率",
    "proved_reserves": "探明储量",
    "reserve_life_years": "储量寿命",
    "impairment_charge": "商品资产减值损失",
    "impairment_to_commodity_revenue": "减值/商品收入",
    "price_to_book": "P/B",
    "pe_ratio": "P/E",
    "fcf_yield": "FCF Yield",
    "ffo_total": "FFO 总额",
    "ffo_per_share": "FFO/股",
    "affo": "AFFO",
    "net_debt_to_ebitda": "净债务/EBITDA",
    "dividend_coverage": "股息覆盖",
    "price_to_ffo": "P/FFO",
    "historical_pe_current": "历史当前 P/E",
    "historical_pe_median": "历史五年中位 P/E",
    "historical_pe_percentile_25": "历史 P/E 二十五分位",
    "historical_pe_percentile_75": "历史 P/E 七十五分位",
    "historical_percentile": "当前历史百分位",
    "historical_valuation": "历史估值",
    "reverse_dcf": "反向 DCF",
    "reverse_dcf_implied_growth": "反向 DCF 隐含增长",
    "spac_trust_cash": "SPAC 信托现金",
    "spac_warrant_dilution_ratio": "SPAC 认股权证稀释率",
    "spac_pro_forma_shares": "SPAC 备考股数",
    "spac_cash_per_pro_forma_share": "SPAC 每备考股信托现金",
}
_REPORT_PERCENT_METRIC_IDS = frozenset(
    {
        "revenue_growth",
        "operating_margin",
        "utility_operating_margin",
        "capex_intensity",
        "utility_roe",
        "net_margin",
        "free_cash_flow_margin",
        "cash_conversion",
        "share_dilution",
        "realized_price_change",
        "production_change",
        "impairment_to_commodity_revenue",
        "historical_percentile",
        "reverse_dcf_implied_growth",
        "spac_warrant_dilution_ratio",
    }
)
_REPORT_AMOUNT_METRIC_IDS = frozenset(
    {"free_cash_flow", "net_cash", "market_capitalization", "rate_base"}
)
_REPORT_MULTIPLE_METRIC_IDS = frozenset(
    {
        "adr_ratio",
        "net_debt_to_ebitda",
        "dividend_coverage",
        "price_to_ffo",
        "price_to_book",
        "interest_coverage",
    }
)
_COMMODITY_METRIC_IDS = frozenset(
    {
        "realized_price",
        "production",
        "realized_price_change",
        "production_change",
        "proved_reserves",
        "reserve_life_years",
        "impairment_charge",
        "impairment_to_commodity_revenue",
        "pe_ratio",
    }
)
_FOREIGN_METRIC_IDS = frozenset(
    {"adr_ratio", "adr_equivalent_shares", "adr_market_cap"}
)
_HOLDING_METRIC_IDS = frozenset(
    {
        "attributable_holdings_value",
        "holding_company_nav",
        "holding_company_market_cap",
        "holding_company_nav_discount",
    }
)
_HOLDING_AMOUNT_METRIC_IDS = frozenset(
    {
        "attributable_holdings_value",
        "holding_company_nav",
        "holding_company_market_cap",
    }
)
_HOLDING_NOT_APPLICABLE_METRIC_IDS = frozenset(
    {"pe_ratio", "fcf_yield", "historical_valuation", "reverse_dcf"}
)
_SPAC_METRIC_IDS = frozenset(
    {
        "spac_trust_cash",
        "spac_warrant_dilution_ratio",
        "spac_pro_forma_shares",
        "spac_cash_per_pro_forma_share",
    }
)
_REIT_METRIC_LABELS = {
    "ffo_total": "FFO 总额",
    "ffo_per_share": "FFO/股",
    "affo": "AFFO",
    "same_store_noi": "same-store NOI",
    "occupancy": "Occupancy",
    "net_debt_to_ebitda": "净债务/EBITDA",
    "dividend_coverage": "股息覆盖",
    "price_to_ffo": "P/FFO",
}
_REIT_APPLICABILITY_REASONS = {
    "pe": "reit_primary_valuation_not_pe",
    "fcf_yield": "reit_primary_cash_metric_not_fcf",
}
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
_MARKDOWN_CONTROL_TRANSLATION = str.maketrans(
    {character: " " for character in r"\\`*_[]()>#|~"}
)
_SHARE_ADJUSTMENT_BASES = frozenset({"raw", "split_adjusted"})
_FINANCIAL_BASIS_METRIC_IDS = frozenset(
    {
        "revenue_growth",
        "operating_margin",
        "net_margin",
        "free_cash_flow_margin",
        "cash_conversion",
        "share_dilution",
    }
)
_SOURCE_METHOD_NOTE = (
    "来源口径：SEC 申报中的年度及季度数据、已验证计算和市场数据；季度数据可能未经审计。"
)


def build_deterministic_report_draft() -> ReportDraft:
    """构造不携带动态事实的安全 ReportDraft fallback。"""
    return ReportDraft(
        execution_summary="报告由已验证研究结果生成。",
        company_quality="公司质量部分由确定性 Renderer 注入已验证内容。",
        financial_trend="财务趋势部分由确定性 Renderer 注入已验证内容。",
        current_valuation="当前估值部分由确定性 Renderer 注入已验证内容。",
        historical_valuation="历史估值部分由确定性 Renderer 注入已验证内容。",
        reverse_dcf="反向 DCF 部分由确定性 Renderer 注入已验证内容。",
        key_risks="主要风险部分由确定性 Renderer 注入已验证内容。",
        sources_and_method="结论仅基于已验证数据和 Claim；缺失输入不作推断。",
        non_investment_disclaimer="本文不构成任何投资建议。",
    )


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


def _report_title(company: Mapping[str, Any]) -> str:
    """为正式报告注入公司身份，避免不同运行结果在预览中混淆。"""
    def plain_text(value: Any) -> str | None:
        text = _text(value)
        if not text:
            return None
        text = "".join(character if character.isprintable() else " " for character in text)
        return " ".join(text.translate(_MARKDOWN_CONTROL_TRANSLATION).split()) or None

    name = plain_text(company.get("name")) or plain_text(company.get("company"))
    ticker = plain_text(company.get("ticker"))
    if name and ticker:
        return f"# 投资研究报告：{name}（{ticker}）"
    if name:
        return f"# 投资研究报告：{name}"
    if ticker:
        return f"# 投资研究报告：{ticker}"
    return "# 投资研究报告"


def _build_chart_context(report_context: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """从已验证指标投影无数字的图表关系事实。"""
    unavailable = {"available": False, "observations": []}

    def records(value: Any) -> dict[str, Mapping[str, Any]]:
        if isinstance(value, Mapping):
            value = value.get("metrics", value)
        if isinstance(value, Mapping):
            value = list(value.values())
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return {}
        result: dict[str, Mapping[str, Any]] = {}
        for record in value:
            if isinstance(record, Mapping) and (metric_id := _text(record.get("metric_id"))):
                result[metric_id] = record
        return result

    def verified(record: Mapping[str, Any], *, ttm: bool = False) -> bool:
        return (
            record.get("status") == "available"
            and record.get("validation_status") == "valid"
            and (not ttm or record.get("period_basis") == "TTM")
        )

    def ratio_value(record: Mapping[str, Any]) -> Decimal | None:
        raw = record.get("display_value", record.get("raw_result"))
        value = _decimal_from_text(raw)
        if value is None:
            return None
        unit = (_text(record.get("unit")) or "").lower()
        raw_text = _text(raw) or ""
        if "%" not in raw_text and unit in {"ratio", "percent", "percentage"}:
            value *= Decimal("100")
        return value

    def amount_value(record: Mapping[str, Any]) -> Decimal | None:
        return _normalized_amount(
            record.get("raw_result", record.get("display_value")), record.get("unit")
        )

    financial_records = records(report_context.get("metrics", []))
    financial_required = (
        "revenue_growth",
        "operating_margin",
        "net_margin",
        "free_cash_flow_margin",
        "cash_conversion",
    )
    if all(
        metric_id in financial_records
        and verified(financial_records[metric_id])
        and ratio_value(financial_records[metric_id]) is not None
        for metric_id in financial_required
    ):
        observations: list[str] = []
        revenue_growth = ratio_value(financial_records["revenue_growth"])
        if revenue_growth is not None and revenue_growth != 0:
            observations.append(
                "收入同比保持正增长" if revenue_growth > 0 else "收入同比出现负增长"
            )
        share_dilution = financial_records.get("share_dilution")
        if share_dilution is not None and verified(share_dilution):
            share_value = ratio_value(share_dilution)
            if (
                share_value is not None
                and share_dilution.get("adjustment_basis") in _SHARE_ADJUSTMENT_BASES
                and share_value != 0
            ):
                observations.append(
                    "股份数量同比减少" if share_value < 0 else "股份数量同比增加"
                )
        operating_margin = ratio_value(financial_records["operating_margin"])
        net_margin = ratio_value(financial_records["net_margin"])
        if (
            operating_margin is not None
            and net_margin is not None
            and operating_margin > 0
            and net_margin > 0
        ):
            observations.append("营业利润率和净利率均为正")
        cash_conversion = ratio_value(financial_records["cash_conversion"])
        if cash_conversion is not None and cash_conversion > 100:
            observations.append("经营现金流高于净利润")
        financial_chart = {"available": True, "observations": observations}
    else:
        financial_chart = unavailable.copy()

    ttm_records = records(report_context.get("ttm", {}))
    ttm_required = (
        "revenue",
        "operating_income",
        "net_income",
        "operating_cash_flow",
        "free_cash_flow",
    )
    if all(
        metric_id in ttm_records
        and verified(ttm_records[metric_id], ttm=True)
        and amount_value(ttm_records[metric_id]) is not None
        for metric_id in ttm_required
    ):
        operating_cash_flow = amount_value(ttm_records["operating_cash_flow"])
        net_income = amount_value(ttm_records["net_income"])
        free_cash_flow = amount_value(ttm_records["free_cash_flow"])
        ttm_observations: list[str] = []
        if operating_cash_flow is not None and net_income is not None:
            if operating_cash_flow > net_income:
                ttm_observations.append("经营现金流高于净利润")
            elif operating_cash_flow < net_income:
                ttm_observations.append("经营现金流低于净利润")
        if free_cash_flow is not None and free_cash_flow > 0:
            ttm_observations.append("自由现金流保持为正")
        if (
            free_cash_flow is not None
            and operating_cash_flow is not None
            and free_cash_flow < operating_cash_flow
        ):
            ttm_observations.append("自由现金流低于经营现金流")
        ttm_chart = {"available": True, "observations": ttm_observations}
    else:
        ttm_chart = unavailable.copy()

    historical = report_context.get("historical_valuation", {})
    historical = historical if isinstance(historical, Mapping) else {}
    historical_values = {
        key: _decimal_from_text(historical.get(key))
        for key in (
            "current_value",
            "five_year_median",
            "percentile_25",
            "percentile_75",
            "current_percentile",
        )
    }
    if (
        historical.get("status") == "ok"
        and historical.get("validation_status") == "valid"
        and all(value is not None for value in historical_values.values())
    ):
        current_value = historical_values["current_value"]
        median = historical_values["five_year_median"]
        percentile_25 = historical_values["percentile_25"]
        percentile_75 = historical_values["percentile_75"]
        current_percentile = historical_values["current_percentile"]
        historical_observations: list[str] = []
        if current_value is not None and median is not None:
            if current_value > median:
                historical_observations.append("当前市盈率高于五年中位数")
            elif current_value < median:
                historical_observations.append("当前市盈率低于五年中位数")
        if (
            current_value is not None
            and percentile_25 is not None
            and percentile_75 is not None
        ):
            if current_value >= percentile_75:
                historical_observations.append("当前市盈率位于或高于历史上四分位")
            elif percentile_25 < current_value < percentile_75:
                historical_observations.append("当前市盈率位于历史中间区间")
        if current_percentile is not None and current_percentile > 50:
            historical_observations.append("当前估值位于历史样本上半区")
        historical_chart = {"available": True, "observations": historical_observations}
    else:
        historical_chart = unavailable.copy()

    return {
        "financial_kpis": financial_chart,
        "ttm_scale": ttm_chart,
        "historical_pe": historical_chart,
    }


def build_narrative_context(
    report_context: Mapping[str, Any], max_bytes: int = 24 * 1024
) -> dict[str, Any]:
    """压缩 Report Context，只把有限叙述摘要交给 Report Crew。"""
    company = report_context.get("company", {})
    company = company if isinstance(company, Mapping) else {}
    verdict = report_context.get("verdict", {})
    verdict = verdict if isinstance(verdict, Mapping) else {}
    status = _text(report_context.get("verdict_status")) or _text(verdict.get("status")) or "unavailable"
    rating, risk, rule, action = _verdict_display(verdict, status)
    identity = {
        "company": (_text(company.get("name")) or _text(company.get("company")) or "unavailable")[:256],
        "ticker": (_text(company.get("ticker")) or "unavailable")[:64],
        "horizon": (
            _text(report_context.get("horizon"))
            or _text(company.get("horizon"))
            or _text(company.get("investment_horizon"))
            or "unavailable"
        )[:128],
    }
    claims = report_context.get("claims", [])
    claims = claims if isinstance(claims, Sequence) and not isinstance(claims, (str, bytes)) else []
    summaries = {category: [] for category in _REPORT_NARRATIVE_CATEGORIES}
    claim_sections: set[str] = set()
    for claim in claims:
        if not isinstance(claim, Mapping):
            continue
        category = _REPORT_NARRATIVE_CATEGORY_MAP.get(str(claim.get("category", "")).strip())
        statement = _text(claim.get("statement"))
        if category and statement:
            summaries[category].append(" ".join(statement.split())[:512])
        section = _CLAIM_CATEGORY_TO_SECTION.get(str(claim.get("category", "")).strip())
        if section:
            claim_sections.add(section)
    metrics = report_context.get("metrics", [])
    metrics = metrics if isinstance(metrics, Sequence) and not isinstance(metrics, (str, bytes)) else []
    metric_sections = set()
    for metric in metrics:
        if not isinstance(metric, Mapping):
            continue
        section = metric.get("section")
        metric_id = metric.get("metric_id")
        if section == "financial":
            section = (
                "company_quality"
                if metric_id in _REPORT_QUALITY_METRIC_IDS
                else "financial_trend"
                if metric_id in _REPORT_TREND_METRIC_IDS
                else None
            )
        if section:
            metric_sections.add(str(section))
    profile_metrics = report_context.get("profile_metrics")
    if isinstance(profile_metrics, Mapping):
        profile_issuer = _text((report_context.get("profile") or {}).get("issuer_profile"))
        if profile_issuer in {
            "bank",
            "insurance",
            "utility",
            "commodity_producer",
        } or (
            isinstance(report_context.get("profile"), Mapping)
            and report_context["profile"].get("reporting_profile")
            == "foreign_private_issuer_ifrs"
        ) or (
            isinstance(report_context.get("profile"), Mapping)
            and report_context["profile"].get("security_profile") == "spac"
        ):
            metric_sections.add("company_quality")
    source_metadata = report_context.get("source_metadata", {})
    source_metadata = source_metadata if isinstance(source_metadata, Mapping) else {}

    def _count(value: Any) -> int:
        return len(value) if isinstance(value, (Mapping, list, tuple, set)) else 0

    counts = {
        "claims": len(claims),
        "accepted_claims": len(claims),
        "metrics": len(metrics),
        "facts": _count(source_metadata.get("facts")),
        "risk_filings": _count(source_metadata.get("risk_filings")),
        "historical_prices": _count(source_metadata.get("historical_prices")),
        "ttm_metrics": (
            _count((report_context.get("ttm") or {}).get("metrics"))
            if isinstance(report_context.get("ttm"), Mapping)
            else 0
        ),
    }
    available_sections = [
        section
        for section, _ in _REPORT_SECTIONS
        if section not in {"execution_summary", "sources_and_method", "non_investment_disclaimer"}
        and section in claim_sections | metric_sections
    ]
    narrative = {
        **identity,
        "verdict": {
            "status": status,
            "rating": rating[:128],
            "risk": risk[:128],
            "rule": rule[:128],
            "action": action[:128],
        },
        "accepted_claim_summaries": summaries,
        "chart_context": _build_chart_context(report_context),
        "counts": counts,
        "available_sections": available_sections,
    }
    for key in ("profile", "coverage_level", "policy_version"):
        if key in report_context and report_context[key] is not None:
            narrative[key] = _json_safe_context(report_context[key])

    def _size() -> int:
        return len(json.dumps(narrative, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    while _size() > max_bytes:
        candidates = [
            (group, index, value)
            for group in _REPORT_NARRATIVE_CATEGORIES
            for index, value in enumerate(summaries[group])
            if len(value) > 1
        ]
        if not candidates:
            break
        group, index, value = max(
            candidates,
            key=lambda item: (
                len(item[2]),
                -_REPORT_NARRATIVE_CATEGORIES.index(item[0]),
                -item[1],
            ),
        )
        summaries[group][index] = value[: max(1, len(value) // 2)]
    return narrative


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
    """渲染已验证 Claim 的解释文本，跳过有规范化指标的数字原文。"""
    statements = []
    for claim in claims:
        if claim["category"] != category:
            continue
        statement = claim["statement"]
        if claim["calculation_ids"] and _REPORT_NUMBER_RE.search(statement):
            continue
        statements.append(statement)
    if statements:
        return "\n".join(f"- {statement}" for statement in statements)
    return ""


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
    if metric_id in {
        "spac_trust_cash",
        "spac_pro_forma_shares",
        "spac_cash_per_pro_forma_share",
    }:
        return f"{decimal_value:.2f} {unit}".strip()
    if metric_id in {"current_ratio", "debt_to_equity"}:
        raw_numeric = metric.get("raw_result")
        ratio_value = _decimal_from_text(raw_numeric)
        if ratio_value is not None:
            decimal_value = ratio_value
        elif "%" in raw_text and decimal_value > Decimal("1"):
            decimal_value /= Decimal("100")
        return f"{decimal_value:.2f}x"
    if metric_id == "rate_base":
        return f"{decimal_value:.2f} {unit}".strip()
    if metric_id in {
        "realized_price",
        "production",
        "proved_reserves",
        "reserve_life_years",
        "impairment_charge",
    }:
        return f"{decimal_value:.2f} {unit}".strip()
    if metric_id in _REPORT_MULTIPLE_METRIC_IDS:
        return f"{decimal_value:.2f}x"
    if metric_id in {"ffo_total", "ffo_per_share", "affo"}:
        return f"{decimal_value:.2f} {unit}".strip()
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
    if metric_id in _HOLDING_AMOUNT_METRIC_IDS:
        return f"{decimal_value:.2f} {unit}".strip()
    normalized_unit = unit_lower.replace(" ", "")
    is_currency_amount = metric_id in _REPORT_AMOUNT_METRIC_IDS or (
        normalized_unit == "currency"
        or ("/" not in normalized_unit and ("usd" in normalized_unit or "美元" in normalized_unit))
    )
    if is_currency_amount:
        if metric_id == "market_price":
            return f"{decimal_value:.2f} {unit}".strip()
        decimal_value = _normalized_amount(decimal_value, unit) or decimal_value
        absolute_value = abs(decimal_value)
        if absolute_value >= Decimal("1000000000000"):
            return f"{decimal_value / Decimal('1000000000000'):.2f} 万亿美元"
        return f"{decimal_value / Decimal('100000000'):.2f} 亿美元"
    return f"{decimal_value:.2f}"


def _metric_text(metric: Mapping[str, Any]) -> str:
    """把一个 ReportMetric 渲染成带时间和来源的可读行。"""
    metric_id = metric["metric_id"]
    if (
        metric_id == "share_dilution"
        and metric.get("adjustment_basis") not in _SHARE_ADJUSTMENT_BASES
    ):
        return ""
    label = _REPORT_METRIC_LABELS.get(metric_id, metric_id)
    if metric_id == "share_dilution" and metric.get("adjustment_basis") == "split_adjusted":
        label += "（拆分调整）"
    value = _formatted_metric_value(metric)
    unit = metric["unit"]
    if unit and unit not in value and metric["metric_id"] == "market_price":
        value = f"{value} {unit}"
    period_end = _text(metric.get("period_end"))
    provenance = (
        "；直接披露证据"
        if metric.get("provenance_type") == "direct_evidence"
        else ""
    )
    if period_end:
        return (
            f"- {label}：{value}（期间截至 {period_end}；截至 {metric['as_of']}；"
            f"来源：{metric['source_reference']}{provenance}）"
        )
    return f"- {label}：{value}（截至 {metric['as_of']}；来源：{metric['source_reference']}{provenance}）"


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
        and (
            metric["metric_id"] != "share_dilution"
            or metric.get("adjustment_basis") in _SHARE_ADJUSTMENT_BASES
        )
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


def _sec_source_reference(value: Any) -> str | None:
    reference = _text(value)
    if not reference:
        return None
    normalized = reference.lower()
    if normalized.startswith("sec:"):
        return reference
    try:
        parsed = urlsplit(reference)
        hostname = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme.lower() in {"http", "https"} and hostname and hostname.lower() in {
        "sec.gov",
        "www.sec.gov",
        "data.sec.gov",
    }:
        return reference
    return None


def _source_reference_index(source_metadata: Any) -> dict[str, str]:
    """索引已有 source_metadata 中的 SEC 引用，不创造新的来源字段。"""
    references: dict[str, str] = {}

    def visit(value: Any, fallback_evidence_id: str | None = None) -> None:
        if isinstance(value, Mapping):
            evidence_id = _text(value.get("evidence_id")) or fallback_evidence_id
            reference = _sec_source_reference(value.get("source_reference"))
            if evidence_id and reference:
                references.setdefault(evidence_id, reference)
            for key, nested in value.items():
                visit(nested, _text(key) if isinstance(nested, Mapping) else None)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for nested in value:
                visit(nested)

    visit(source_metadata)
    return references


def _valuation_explanation_lines(context: Mapping[str, Any]) -> list[str]:
    historical = context.get("historical_valuation", {})
    historical = historical if isinstance(historical, Mapping) else {}
    current = _decimal_from_text(historical.get("current_value"))
    median = _decimal_from_text(historical.get("five_year_median"))
    percentile = _decimal_from_text(historical.get("current_percentile"))
    lines: list[str] = []
    if current is not None and median is not None:
        relation = "高于" if current > median else "低于" if current < median else "等于"
        sentence = f"当前 P/E 为 {current:.2f}x，{relation}五年中位数 {median:.2f}x"
        if percentile is not None:
            sentence += f"，处于过去五年估值样本的 {percentile:.2f}% 分位"
        lines.append(f"{sentence}。")
    elif percentile is not None:
        lines.append(f"当前 P/E 处于过去五年估值样本的 {percentile:.2f}% 分位。")

    reverse_dcf = context.get("reverse_dcf", {})
    reverse_dcf = reverse_dcf if isinstance(reverse_dcf, Mapping) else {}
    implied_growth = _percent_display(reverse_dcf.get("implied_growth"))
    if implied_growth is not None:
        forecast_years = _text(reverse_dcf.get("forecast_years"))
        growth_label = (
            f"未来 {forecast_years} 年自由现金流年复合增长要求"
            if forecast_years
            else "自由现金流年复合增长要求"
        )
        lines.append(f"反向 DCF 显示，{growth_label}约为 {implied_growth}。")
    return lines


def _financial_basis_note(metrics: Sequence[Mapping[str, Any]]) -> str:
    dates: list[str] = []
    saw_financial_metric = False
    for metric in metrics:
        if not isinstance(metric, Mapping) or metric.get("metric_id") not in _FINANCIAL_BASIS_METRIC_IDS:
            continue
        saw_financial_metric = True
        metric_date = _text(metric.get("period_end")) or _text(metric.get("as_of"))
        if metric_date and metric_date not in dates:
            dates.append(metric_date)
    if not saw_financial_metric:
        return ""
    date_clause = f"截至 {'、'.join(dates)} 的" if dates else ""
    return (
        "*口径说明：收入增长、利润率和现金流质量是"
        f"{date_clause}财年年初至今累计（YTD）；股份变化是同比时点比较；"
        "后文 TTM 是最近十二个月。*"
    )


def _execution_summary_lines(
    rating_label: str,
    risk_label: str,
    action_label: str,
    context: Mapping[str, Any],
) -> list[str]:
    """把确定性 Verdict 与估值关系整理成面向读者的紧凑摘要。"""
    historical = context.get("historical_valuation", {})
    historical = historical if isinstance(historical, Mapping) else {}
    current = _decimal_from_text(historical.get("current_value"))
    median = _decimal_from_text(historical.get("five_year_median"))
    reverse_dcf = context.get("reverse_dcf", {})
    reverse_dcf = reverse_dcf if isinstance(reverse_dcf, Mapping) else {}

    if current is not None and median is not None:
        if current > median:
            conclusion = "当前估值高于过去五年中位水平，估值并不便宜。"
        elif current < median:
            conclusion = "当前估值低于过去五年中位水平。"
        else:
            conclusion = "当前估值接近过去五年中位水平。"
    else:
        conclusion = rating_label
    lines = [
        f"- **结论：** {conclusion}",
    ]
    claims = context.get("claims", [])
    if isinstance(claims, Sequence) and not isinstance(claims, (str, bytes)) and any(
        isinstance(claim, Mapping)
        and claim.get("category") == "risk"
        and _text(claim.get("statement"))
        for claim in claims
    ):
        lines.append("- 风险状态：已识别风险，但尚未建立量化评分。")
    if valuation_lines := _valuation_explanation_lines(context):
        lines.append(f"- **关键数据：** {' '.join(valuation_lines)}")
    verdict = context.get("verdict", {})
    verdict = verdict if isinstance(verdict, Mapping) else {}
    research_status = _VERDICT_RATING_LABELS.get(_text(verdict.get("overall_rating")) or "")
    if research_status:
        lines.append(f"- **研究状态：** {research_status}")
    if current is not None and median is not None:
        conditions = [f"P/E 回落至 {median:.2f}x 附近"]
        lines.append(f"- **重新评估条件：** {'；或 '.join(conditions)}。")
    else:
        lines.append(f"- **行动参考：** {action_label}")
    company = context.get("company", {})
    company = company if isinstance(company, Mapping) else {}
    request_horizon = _text(context.get("horizon")) or _text(company.get("horizon"))
    request_horizon = request_horizon or _text(company.get("investment_horizon"))
    forecast_years = _text(reverse_dcf.get("forecast_years"))
    if request_horizon and forecast_years:
        lines.append(
            f"- **期限说明：** 用户关注期限是 {request_horizon}；{forecast_years} 年反向 DCF "
            "只是长期估值参照，不是该请求期限的预测。"
        )
    return lines


def _ttm_metrics_markdown(context: Mapping[str, Any]) -> str:
    ttm = context.get("ttm", {})
    ttm = ttm if isinstance(ttm, Mapping) else {}
    raw_metrics = ttm.get("metrics", [])
    if isinstance(raw_metrics, Mapping):
        raw_metrics = list(raw_metrics.values())
    if not isinstance(raw_metrics, Sequence) or isinstance(raw_metrics, (str, bytes)):
        return ""

    lines: list[str] = []
    for raw_metric in raw_metrics:
        if not isinstance(raw_metric, Mapping):
            continue
        if (
            raw_metric.get("status") != "available"
            or raw_metric.get("validation_status") != "valid"
        ):
            continue
        metric_id = _text(raw_metric.get("metric_id"))
        value = raw_metric.get("display_value", raw_metric.get("raw_result"))
        if not metric_id or value is None:
            continue
        metric = dict(raw_metric)
        metric["metric_id"] = metric_id
        metric["display_value"] = value
        metric.setdefault("unit", "")
        period_start = _text(metric.get("period_start"))
        period_end = _text(metric.get("period_end"))
        period = "TTM"
        if period_start and period_end:
            period = f"TTM；期间 {period_start} 至 {period_end}"
        elif period_end:
            period = f"TTM；截至 {period_end}"
        label = _REPORT_METRIC_LABELS.get(metric_id, metric_id)
        lines.append(
            f"- {label}：{_formatted_metric_value(metric)}（{period}）"
        )
    if not lines:
        return ""
    return "\n".join(("### TTM 财务规模（已验证）", *lines))


def _risk_claim_markdown(
    claims: Sequence[Mapping[str, Any]], source_metadata: Any = None
) -> str:
    displayable_claims = [
        dict(claim)
        for claim in claims
        if claim.get("category") == "risk"
        and _text(claim.get("statement"))
        and not (
            claim.get("calculation_ids")
            and _REPORT_NUMBER_RE.search(_text(claim.get("statement")) or "")
        )
    ]
    if not displayable_claims:
        return "未提供可单独展示的文字风险 Claim。"
    source_index = _source_reference_index(source_metadata)

    def render_claim(claim: Mapping[str, Any]) -> str:
        references = []
        evidence_ids = claim.get("evidence_ids", [])
        if isinstance(evidence_ids, Sequence) and not isinstance(
            evidence_ids, (str, bytes)
        ):
            for evidence_id in evidence_ids:
                reference = source_index.get(_text(evidence_id) or "")
                if reference and reference not in references:
                    references.append(reference)
        source_suffix = f"（来源：{'、'.join(references)}）" if references else ""
        return f"- {claim['statement']}{source_suffix}"

    main = "\n".join(render_claim(claim) for claim in displayable_claims[:5])
    if len(displayable_claims) <= 5:
        return main
    remaining_claims = displayable_claims[5:]
    appendix = "\n".join(render_claim(claim) for claim in remaining_claims)
    return "\n".join(
        (
            main,
            "",
            "### 风险附录",
            "",
            "<details>",
            f"<summary>展开查看其余风险（{len(remaining_claims)} 项）</summary>",
            "",
            appendix,
            "",
            "</details>",
        )
    )


def _audit_metadata_markdown(
    context: Mapping[str, Any], status: str, rule_label: str
) -> str:
    lines = ["### 方法与审计元数据", f"- 确定性状态：status={status}"]
    profile = context.get("profile")
    if isinstance(profile, Mapping):
        profile_values = {
            key: _text(profile.get(key))
            for key in ("issuer_profile", "security_profile", "reporting_profile")
        }
        profile_values = {key: value for key, value in profile_values.items() if value}
        if profile_values:
            lines.append(
                "- Profile："
                + "; ".join(
                    f"{key.removesuffix('_profile')}={value}"
                    for key, value in profile_values.items()
                )
            )
    if coverage_level := _text(context.get("coverage_level")):
        lines.append(f"- 覆盖范围：{coverage_level}")
    if policy_version := _text(context.get("policy_version")):
        lines.append(f"- Policy version：{policy_version}")
    verdict = context.get("verdict", {})
    verdict = verdict if isinstance(verdict, Mapping) else {}
    raw_rules = verdict.get("triggered_rules", [])
    if isinstance(raw_rules, str):
        raw_rules = [raw_rules]
    rule_codes = (
        [rule for rule in raw_rules if isinstance(rule, str) and rule.strip()]
        if isinstance(raw_rules, Sequence)
        else []
    )
    if rule_codes:
        lines.append(f"- 触发规则：{rule_label}")
        lines.append(f"- 触发规则代码：{'、'.join(dict.fromkeys(rule_codes))}")
    else:
        lines.append("- 触发规则：无触发规则")
    return "\n".join(lines)


def _term_definitions(
    *, reit: bool = False, profile: str | None = None
) -> tuple[str, ...]:
    if profile == "spac":
        return (
            "### 术语说明",
            "- SPAC evidence-only：仅呈现已验证的证券结构证据，不构成评级。",
            "- 认股权证稀释率：认股权证数量除以基础股数。",
            "- 备考股数：基础股数与认股权证数量之和。",
        )
    definitions = [
        "### 术语说明",
        "- P/E（市盈率）：股价相对于每股收益的倍数，用于描述市场对盈利的定价。",
        "- FCF Yield（自由现金流收益率）：自由现金流相对于市值的收益率。",
        "- TTM（过去十二个月）：以最近连续十二个月为口径汇总经营数据。",
        "- DCF（现金流折现）：将未来现金流折算到当前价值的估值方法。",
        "- 反向 DCF（由市场价格倒推隐含增长）：在给定模型期限内，从当前市场价格反推出自由现金流年复合增长要求。",
    ]
    if reit:
        definitions.extend(
            (
                "- FFO（运营资金）：REIT 用于补充说明物业经营表现的行业指标，不能替代 GAAP 净利润。",
                "- AFFO（调整后运营资金）：仅采用公司明确披露并可追溯的 AFFO reconciliation，没有统一通用公式。",
                "- P/FFO：市场价格相对于每股 FFO 的倍数，是 REIT 的主要估值参考之一。",
            )
        )
    if profile == "bank":
        definitions.extend(
            (
                "- ROA（资产回报率）：净利润相对于平均资产的比例，衡量银行资产创造利润的效率。",
                "- ROE（净资产收益率）：净利润相对于平均股东权益的比例，衡量股东资本回报。",
                "- NIM（净息差）：净利息收入相对于平均生息资产的比例，反映银行核心息差。",
                "- 效率比率：非利息费用相对于净利息收入与非利息收入之和的比例，通常越低表示效率越高。",
                "- CET1（普通股权一级资本充足率）：直接披露的核心资本相对风险承担的监管比率。",
                "- 贷存比：贷款总额相对于存款总额的比例。",
                "- 不良贷款率：不良贷款相对于贷款总额的比例。",
                "- 拨备覆盖率：信用损失准备相对于不良贷款的比例。",
                "- P/B（市净率）：股价相对于每股账面价值的倍数。",
            )
        )
    if profile == "insurance":
        definitions.extend(
            (
                "- ROA/ROE：分别表示资产回报率和净资产收益率，用于衡量保险公司的盈利效率与股东回报。",
                "- 赔付率：已发生赔付相对于已赚保费的比例。",
                "- 费用率：承保费用相对于已赚保费的比例。",
                "- 综合成本率：赔付率与费用率按固定口径相加，衡量承保业务成本。",
                "- 偿付能力：公司或法定口径直接披露的资本/偿付能力比率。",
                "- P/B（市净率）：股价相对于每股账面价值的倍数。",
            )
        )
    if profile == "utility":
        definitions.extend(
            (
                "- Rate Base（费率基数）：公用事业公司或监管机构直接披露的受监管资本基数，不由资产负债表字段推断。",
                "- CapEx Intensity（资本开支强度）：资本开支相对于营业收入的比例。",
                "- Interest Coverage（利息保障倍数）：营业利润相对于利息费用的比例。",
                "- FCF Yield（自由现金流收益率）：公用事业自由现金流相对于直接披露市值的收益率，不由价格与股数推算。",
            )
        )
    if profile == "commodity_producer":
        definitions.extend(
            (
                "- 商品实现价格：公司按主商品披露的单位实现价格，不用股票市场价格替代。",
                "- 商品产量：主商品的公司披露产量，变化率只比较可比期间。",
                "- 探明储量：已证明可采的储量，不用资源量或 probable/total reserves 替代。",
                "- 储量寿命：探明储量除以年度产量，表示按该产量水平的理论年数。",
                "- 商品资产减值损失：公司明确披露的商品相关减值，不能用经营亏损或重组费用替代。",
            )
        )
    if profile == "foreign_private_issuer_ifrs":
        definitions.extend(
            (
                "- 20-F/6-K：外国私人发行人向 SEC 提交的固定范围申报；未同时验证 20-F 与 IFRS taxonomy 时不宣称 IFRS profile。",
                "- ADR ratio：仅使用已验证的普通股/ADR 兑换比例；缺失时不默认 1:1，也不从价格或市值反推。",
                "- ADR 等价市值：只使用 USD ADR 价格与 ADR 等价股数；原币财务数据不与 USD 跨币种混算。",
            )
        )
    return tuple(definitions)


def _profile_decisions(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    decisions = payload.get("policy_decisions", [])
    if not isinstance(decisions, Sequence) or isinstance(decisions, (str, bytes)):
        return {}
    return {
        decision["metric_id"]: decision
        for decision in decisions
        if isinstance(decision, Mapping) and _text(decision.get("metric_id"))
    }


def _profile_metrics_markdown(
    profile: str | None,
    payload: Mapping[str, Any] | None,
    metrics: Sequence[Mapping[str, Any]],
) -> str:
    if profile not in {
        "bank",
        "insurance",
        "utility",
        "commodity_producer",
        "foreign_private_issuer_ifrs",
        "holding_company",
        "spac",
    } or not isinstance(payload, Mapping):
        return ""
    metric_ids = payload.get("metric_ids", [])
    if not isinstance(metric_ids, Sequence) or isinstance(metric_ids, (str, bytes)):
        metric_ids = []
    if profile == "commodity_producer":
        metric_ids = [metric_id for metric_id in metric_ids if metric_id in _COMMODITY_METRIC_IDS]
    if profile == "foreign_private_issuer_ifrs":
        metric_ids = [metric_id for metric_id in metric_ids if metric_id in _FOREIGN_METRIC_IDS]
    if profile == "holding_company":
        allowed_metric_ids = _HOLDING_METRIC_IDS | _HOLDING_NOT_APPLICABLE_METRIC_IDS
        metric_ids = [metric_id for metric_id in metric_ids if metric_id in allowed_metric_ids]
    if profile == "spac":
        metric_ids = [metric_id for metric_id in metric_ids if metric_id in _SPAC_METRIC_IDS]
    metric_map = {
        metric.get("metric_id"): metric
        for metric in metrics
        if isinstance(metric, Mapping) and metric.get("metric_id") in metric_ids
    }
    decisions = _profile_decisions(payload)
    heading = {
        "bank": "### 银行专用指标",
        "insurance": "### 保险专用指标",
        "utility": "### 公用事业专用指标",
        "commodity_producer": "### 商品生产商专用指标",
        "foreign_private_issuer_ifrs": "### 外国发行人/ADR 指标",
        "holding_company": "### 控股公司专用指标",
        "spac": "### SPAC 证券结构指标",
    }[profile]
    lines = [heading]
    if profile == "foreign_private_issuer_ifrs":
        metadata = payload.get("foreign_metadata", {})
        if isinstance(metadata, Mapping):
            forms = metadata.get("filing_forms", [])
            if isinstance(forms, Sequence) and not isinstance(forms, (str, bytes)):
                forms = [form for form in forms if form in {"20-F", "6-K"}]
                if forms:
                    lines.append(f"- SEC foreign filings：{', '.join(forms)}")
            taxonomies = metadata.get("ifrs_taxonomy", [])
            if isinstance(taxonomies, Sequence) and not isinstance(taxonomies, (str, bytes)):
                taxonomies = [taxonomy for taxonomy in taxonomies if isinstance(taxonomy, str)]
                if taxonomies:
                    lines.append(f"- IFRS taxonomy：{', '.join(taxonomies)}")
            for key, label in (
                ("reporting_currency", "财务报告币种"),
                ("market_currency", "市场价格币种"),
                ("adr_ratio_status", "ADR ratio 状态"),
            ):
                value = _text(metadata.get(key))
                if value:
                    lines.append(f"- {label}：{value}")
    for metric_id in metric_ids:
        metric_id = _text(metric_id)
        if not metric_id:
            continue
        metric = metric_map.get(metric_id)
        if metric is not None:
            lines.append(_metric_text(metric))
            continue
        decision = decisions.get(metric_id, {})
        status = _text(decision.get("status")) or "unavailable"
        reason = _text(decision.get("reason_code")) or "reason_code_missing"
        label = _REPORT_METRIC_LABELS.get(metric_id, metric_id)
        if status == "not_applicable" and metric_id == "fcf_yield":
            explanation = {
                "bank": "银行不计算普通企业 FCF Yield。",
                "insurance": "保险不计算普通企业 FCF Yield。",
                "holding_company": "控股公司不计算普通企业 FCF Yield。",
            }.get(profile)
            if explanation:
                lines.append(f"- FCF Yield：not_applicable（{reason}）；{explanation}")
            else:
                lines.append(f"- {label}：{status}（{reason}）")
        else:
            lines.append(f"- {label}：{status}（{reason}）")
    return "\n".join(lines)


def _reit_decisions(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    decisions = payload.get("policy_decisions", [])
    if not isinstance(decisions, Sequence) or isinstance(decisions, (str, bytes)):
        return {}
    return {
        decision["metric_id"]: decision
        for decision in decisions
        if isinstance(decision, Mapping) and _text(decision.get("metric_id"))
    }


def _reit_applicability_markdown(payload: Mapping[str, Any]) -> str:
    decisions = _reit_decisions(payload)
    pe = decisions.get("pe", {})
    fcf_yield = decisions.get("fcf_yield", {})
    pe_status = _text(pe.get("status")) or "not_applicable"
    pe_reason = _text(pe.get("reason_code")) or _REIT_APPLICABILITY_REASONS["pe"]
    fcf_status = _text(fcf_yield.get("status")) or "not_applicable"
    fcf_reason = _text(fcf_yield.get("reason_code")) or _REIT_APPLICABILITY_REASONS["fcf_yield"]
    return "\n".join(
        (
            "### REIT 估值与现金指标适用性",
            f"- P/E：{pe_status}（{pe_reason}）；REIT 主估值看 FFO/AFFO/P-FFO。",
            f"- FCF Yield：{fcf_status}（{fcf_reason}）；不能用普通企业 FCF Yield 替代。",
        )
    )


def _reit_unavailable_markdown(payload: Mapping[str, Any]) -> str:
    decisions = _reit_decisions(payload)
    lines = []
    for metric_id, decision in decisions.items():
        if _text(decision.get("status")) != "unavailable":
            continue
        reason_code = _text(decision.get("reason_code")) or "reason_code_missing"
        lines.append(
            f"- {_REIT_METRIC_LABELS.get(metric_id, metric_id)}：unavailable（{reason_code}）"
        )
    return "\n".join(lines)


def _visual_markdown(visuals: Mapping[str, str], key: str, alt: str) -> str | None:
    uri = visuals.get(key)
    if not isinstance(uri, str) or not uri.startswith("data:image/png;base64,"):
        return None
    return f"![{alt}]({uri})"


def _reverse_dcf_markdown(payload: Mapping[str, Any]) -> str:
    """把确定性反向 DCF 参数渲染成外行可读的表格。"""
    if not payload:
        return "反向 DCF：缺少已验证的 TTM 自由现金流或模型结果，未生成参数表。"
    forecast_years = _text(payload.get("forecast_years"))
    rows = [
        "| 参数 | 数值 | 含义 |",
        "|---|---:|---|",
        f"| 基础自由现金流（TTM） | {_currency_display(_normalized_amount(payload.get('base_fcf'), payload.get('base_fcf_unit') or payload.get('unit')))} | 最近十二个月自由现金流 |",
        f"| 基准折现率 | {_percent_display(payload.get('discount_rate')) or '不可用'} | 将未来现金流折算到今天 |",
        f"| 基准永续增长率 | {_percent_display(payload.get('terminal_growth')) or '不可用'} | 预测期后的稳定增长假设 |",
        f"| 基准隐含增长率 | {_percent_display(payload.get('implied_growth')) or '不可用'} | {'未来 ' + forecast_years + ' 年' if forecast_years else ''}自由现金流年复合增长要求 |",
    ]
    if forecast_years:
        rows.insert(4, f"| 预测年数 | {forecast_years} 年 | 固定预测期限 |")
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
    except Exception as exc:
        raise ValueError("ReportContext 未通过本地来源和结构校验。") from exc
    context_payload = validated_context.model_dump(mode="json")
    chart_context = _build_chart_context(context_payload)
    claims = _validated_claims(context_payload["claims"])
    status = context_payload["verdict_status"]
    metrics = context_payload["metrics"]
    reit_metrics = context_payload.get("reit_metrics")
    is_reit = isinstance(reit_metrics, Mapping)
    profile = context_payload.get("profile", {})
    profile_issuer = (
        _text(profile.get("issuer_profile")) if isinstance(profile, Mapping) else None
    )
    profile_kind = profile_issuer
    if (
        isinstance(profile, Mapping)
        and profile.get("security_profile") == "spac"
    ):
        profile_kind = "spac"
    if (
        isinstance(profile, Mapping)
        and profile.get("reporting_profile") == "foreign_private_issuer_ifrs"
    ):
        profile_kind = "foreign_private_issuer_ifrs"
    profile_metrics = context_payload.get("profile_metrics")
    verdict = context_payload.get("verdict", {})
    if not isinstance(verdict, Mapping):
        verdict = {}
    rating_label, risk_label, rule_label, action_label = _verdict_display(verdict, status)
    visuals = {} if profile_kind == "spac" else build_report_visuals(context=context_payload)

    company_payload = context_payload.get("company", {})
    company_payload = company_payload if isinstance(company_payload, Mapping) else {}
    sections: list[str] = [_report_title(company_payload), ""]
    for field, heading in _REPORT_SECTIONS:
        if profile_kind == "spac" and field in {
            "financial_trend",
            "current_valuation",
            "historical_valuation",
            "reverse_dcf",
        }:
            continue
        if profile_kind == "holding_company" and field in {
            "historical_valuation",
            "reverse_dcf",
        }:
            continue
        sections.extend((f"## {heading}", ""))
        if field == "execution_summary":
            sections.extend(
                (*_execution_summary_lines(
                    rating_label, risk_label, action_label, context_payload
                ), "")
            )
            if profile_kind == "spac":
                sections.append(
                    "SPAC evidence-only：仅呈现证券结构证据，不构成评级。"
                )
                sections.append("")
            if basis_note := _financial_basis_note(metrics):
                sections.extend((basis_note, ""))
        elif field == "company_quality":
            claim_text = _claim_text(claims, "financial_quality")
            sections.extend(
                (
                    *([claim_text, ""] if claim_text else []),
                    _metric_text_for_section(
                        metrics, "financial", _REPORT_QUALITY_METRIC_IDS
                    ),
                    "",
                )
            )
            if profile_markdown := _profile_metrics_markdown(
                profile_kind, profile_metrics, metrics
            ):
                sections.extend((profile_markdown, ""))
            if is_reit and (unavailable := _reit_unavailable_markdown(reit_metrics)):
                sections.extend((unavailable, ""))
            chart = _visual_markdown(visuals, "financial_kpis", "核心财务指标")
            if chart:
                sections.extend(
                    (
                        chart,
                        "",
                        "*图表说明：三个面板分别展示增长与资本配置、盈利能力和现金流质量，并使用独立刻度。*",
                        "",
                    )
                )
            prefix = (
                "**图表推导：** 根据上图及其对应的已验证数据，"
                if chart and chart_context["financial_kpis"]["available"]
                else "**数据解读：** 根据已验证数据，"
            )
            sections.extend((f"{prefix}{getattr(report_draft, field)}", ""))
        elif field == "financial_trend":
            claim_text = _claim_text(claims, "financial_trend")
            sections.extend(
                (
                    *([claim_text, ""] if claim_text else []),
                    _metric_text_for_section(
                        metrics,
                        "financial",
                        _REPORT_TREND_METRIC_IDS - {"free_cash_flow"},
                    ),
                    "",
                )
            )
            if ttm_markdown := _ttm_metrics_markdown(context_payload):
                sections.extend((ttm_markdown, ""))
            chart = _visual_markdown(visuals, "ttm_scale", "TTM 财务规模")
            if chart:
                sections.extend(
                    (
                        chart,
                        "",
                        "*图表说明：各柱均为最近十二个月数据，单位为十亿美元，用于比较业务规模。*",
                        "",
                    )
                )
            prefix = (
                "**图表推导：** 根据上图及其对应的已验证数据，"
                if chart and chart_context["ttm_scale"]["available"]
                else "**数据解读：** 根据已验证数据，"
            )
            sections.extend((f"{prefix}{getattr(report_draft, field)}", ""))
        elif field == "current_valuation":
            sections.extend(
                (
                    "以下指标使用同一时点的市场价格和已验证财务数据计算。",
                    "",
                    _metric_text_for_section(metrics, "current_valuation"),
                    "",
                )
            )
            if is_reit:
                sections.extend((_reit_applicability_markdown(reit_metrics), ""))
        elif field == "historical_valuation":
            sections.extend(
                (
                    "以下数据将当前 P/E 与过去五年的估值分布进行比较。",
                    "",
                    _metric_text_for_section(metrics, "historical_valuation"),
                    "",
                )
            )
            chart = _visual_markdown(visuals, "historical_pe", "五年历史 P/E")
            if chart:
                sections.extend(
                    (
                        chart,
                        "",
                        "*图表说明：曲线展示 TTM P/E 的历史变化，参考线用于判断当前估值相对过去五年的位置。*",
                        "",
                    )
                )
            prefix = (
                "**图表推导：** 根据上图及其对应的已验证数据，"
                if chart and chart_context["historical_pe"]["available"]
                else "**数据解读：** 根据已验证数据，"
            )
            sections.extend((f"{prefix}{getattr(report_draft, field)}", ""))
        elif field == "reverse_dcf":
            forecast_years = _text(context_payload.get("reverse_dcf", {}).get("forecast_years"))
            dcf_growth_label = (
                f"未来 {forecast_years} 年自由现金流年复合增长要求"
                if forecast_years
                else "自由现金流年复合增长要求"
            )
            sections.extend(
                (
                    f"反向 DCF 从当前市场价格倒推出{dcf_growth_label}。",
                    "",
                    _metric_text_for_section(metrics, "reverse_dcf"),
                    "",
                    _reverse_dcf_markdown(context_payload.get("reverse_dcf", {})),
                    "",
                )
            )
        elif field == "key_risks":
            sections.extend(
                (
                    getattr(report_draft, field),
                    "",
                    _risk_claim_markdown(
                        claims, context_payload.get("source_metadata", {})
                    ),
                    "",
                )
            )
        elif field == "sources_and_method":
            sections.extend(
                (
                    _SOURCE_METHOD_NOTE,
                    "",
                    _source_text(context_payload),
                    "",
                    _audit_metadata_markdown(context_payload, status, rule_label),
                    "",
                    *_term_definitions(reit=is_reit, profile=profile_kind),
                    "",
                )
            )
        else:
            sections.extend((getattr(report_draft, field), ""))

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
            summary_context = {
                "historical_valuation": historical_payload,
                "reverse_dcf": reverse_dcf_payload,
                "claims": claims,
                "verdict": verdict,
            }
            sections.extend(
                (*_execution_summary_lines(
                    rating_label, risk_label, action_label, summary_context
                ), "")
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
            sections.extend(
                (
                    getattr(report_draft, field),
                    "",
                    _risk_claim_markdown(claims, source_payload),
                    "",
                )
            )
        elif field == "sources_and_method":
            sections.extend(
                (
                    _SOURCE_METHOD_NOTE,
                    "",
                    f"确定性来源元数据：{_json_text(source_payload)}",
                    "",
                    _audit_metadata_markdown(
                        {"verdict": verdict}, status, rule_label
                    ),
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
    """从唯一 ReportContext 和 ReportDraft 渲染最终中文 Markdown。"""
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


__all__ = [
    "ReportContext",
    "ReportDraft",
    "ReportMetric",
    "build_deterministic_report_draft",
    "build_narrative_context",
    "build_report_context",
    "render_validated_report",
    "validate_rendered_report",
]
