from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
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
_RISK_IMPACT_RULES = (
    (
        ("trade", "tariff", "关税", "贸易限制"),
        ("成本上升可能压低毛利率并推高库存压力", "毛利率、库存周转与关税披露"),
    ),
    (
        ("regulation", "antitrust", "app store", "监管", "反垄断"),
        ("监管变化可能影响服务收入并增加合规成本", "服务收入增速、佣金政策与合规费用"),
    ),
    (
        ("macro", "宏观"),
        ("需求变化可能影响收入增长与利润率", "收入增速、订单或销量与利润率"),
    ),
    (
        (
            "supply chain",
            "供应链",
            "关键组件",
            "有限来源",
            "供应中断",
        ),
        ("成本上升可能压低毛利率并推高库存压力", "毛利率、库存周转与关税披露"),
    ),
    (
        (
            "cyber",
            "privacy",
            "网络攻击",
            "网络安全",
            "数据泄露",
            "未经授权访问",
            "隐私",
        ),
        ("事件与监管要求可能增加安全及合规费用", "重大安全事件、诉讼与合规费用"),
    ),
    (
        ("ai", "compute", "semiconductor", "人工智能", "算力", "半导体"),
        ("投入需求可能推高资本开支并影响毛利率", "资本开支、折旧与毛利率"),
    ),
)
_QUALITY_LABELS = {
    "strong": "强",
    "average": "一般",
    "weak": "弱",
    "insufficient": "数据不足",
}
_HISTORICAL_VALUATION_LABELS = {
    "high": "偏高",
    "neutral": "中性",
    "low": "偏低",
    "insufficient": "数据不足",
}
_EXPECTATION_LABELS = {
    "high": "高",
    "neutral": "中性",
    "low": "低",
    "insufficient": "数据不足",
}


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

    annual = report_context.get("annual_financial_history", {})
    profile = report_context.get("profile")
    annual_allowed = not isinstance(profile, Mapping) or (
        _text(profile.get("issuer_profile")) == "standard_operating"
    )
    raw_periods = annual.get("periods") if isinstance(annual, Mapping) else None
    annual_values: list[dict[str, Decimal]] = []
    if (
        annual_allowed
        and isinstance(annual, Mapping)
        and annual.get("status") == "ok"
        and annual.get("validation_status") == "valid"
        and isinstance(raw_periods, Sequence)
        and not isinstance(raw_periods, (str, bytes))
        and len(raw_periods) == 5
    ):
        for period in raw_periods:
            if not isinstance(period, Mapping) or period.get("period_basis") != "FY":
                annual_values = []
                break
            try:
                values = {
                    metric_id: Decimal(str(period.get(metric_id)))
                    for metric_id in ("revenue", "net_income", "free_cash_flow")
                }
            except (InvalidOperation, TypeError, ValueError):
                annual_values = []
                break
            if any(not value.is_finite() for value in values.values()):
                annual_values = []
                break
            annual_values.append(values)
    if len(annual_values) == 5:
        observations: list[str] = []
        for metric_id, label in (
            ("revenue", "营业收入"),
            ("net_income", "净利润"),
            ("free_cash_flow", "自由现金流"),
        ):
            first = annual_values[0][metric_id]
            latest = annual_values[-1][metric_id]
            if latest > first:
                direction = "总体增长"
            elif latest < first:
                direction = "总体下降"
            else:
                direction = "总体持平"
            observations.append(f"{label}五年{direction}")
        observations.append(
            "五年自由现金流全部为正"
            if all(period["free_cash_flow"] > 0 for period in annual_values)
            else "五年自由现金流并非全部为正"
        )
        latest_fcf = annual_values[-1]["free_cash_flow"]
        latest_net_income = annual_values[-1]["net_income"]
        if latest_fcf > latest_net_income:
            relative = "高于"
        elif latest_fcf < latest_net_income:
            relative = "低于"
        else:
            relative = "等于"
        observations.append(f"最新自由现金流{relative}最新净利润")
        annual_chart = {"available": True, "observations": observations}
    else:
        annual_chart = unavailable.copy()

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
        "annual_financial_trend": annual_chart,
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


def _metric_ratio_percentage(metric: Mapping[str, Any]) -> Decimal | None:
    raw = metric.get("display_value", metric.get("raw_result"))
    value = _decimal_from_text(raw)
    if value is None:
        return None
    raw_text = _text(raw) or ""
    unit = (_text(metric.get("unit")) or "").lower()
    if "%" not in raw_text and unit == "ratio":
        value *= Decimal("100")
    return value


def _percentage_points(value: Any, *, ratio: bool = False) -> Decimal | None:
    raw = _text(value)
    parsed = _decimal_from_text(raw)
    if parsed is None:
        return None
    if raw and "%" not in raw and ratio:
        parsed *= Decimal("100")
    return parsed


def _financial_metric_map(context: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    metrics = context.get("metrics", [])
    if isinstance(metrics, Mapping):
        metrics = list(metrics.values())
    if not isinstance(metrics, Sequence) or isinstance(metrics, (str, bytes)):
        return {}
    return {
        _text(metric.get("metric_id")): metric
        for metric in metrics
        if isinstance(metric, Mapping)
        and metric.get("section") == "financial"
        and _text(metric.get("metric_id"))
        and metric.get("status") == "available"
        and metric.get("validation_status") == "valid"
    }


def _deterministic_quality(context: Mapping[str, Any]) -> str:
    metrics = _financial_metric_map(context)
    required = ("operating_margin", "net_margin", "free_cash_flow_margin", "cash_conversion")
    values = {
        metric_id: _metric_ratio_percentage(metrics[metric_id])
        for metric_id in required
        if metric_id in metrics
    }
    if len(values) != len(required) or any(value is None for value in values.values()):
        return "insufficient"
    margins = [values[metric_id] for metric_id in required[:3]]
    if any(value <= 0 for value in margins if value is not None):
        return "weak"
    return "strong" if values["cash_conversion"] >= Decimal("100") else "average"


def _deterministic_historical_valuation(context: Mapping[str, Any]) -> str:
    historical = context.get("historical_valuation", {})
    if not isinstance(historical, Mapping):
        return "insufficient"
    if historical.get("status") != "ok" or historical.get("validation_status") != "valid":
        return "insufficient"
    current = _decimal_from_text(historical.get("current_value"))
    percentile_25 = _decimal_from_text(historical.get("percentile_25"))
    percentile_75 = _decimal_from_text(historical.get("percentile_75"))
    if current is None or percentile_25 is None or percentile_75 is None:
        return "insufficient"
    if current >= percentile_75:
        return "high"
    if current <= percentile_25:
        return "low"
    return "neutral"


def _deterministic_expectations(context: Mapping[str, Any]) -> str:
    summary = context.get("annual_financial_summary", {})
    reverse_dcf = context.get("reverse_dcf", {})
    if not isinstance(summary, Mapping) or not isinstance(reverse_dcf, Mapping):
        return "insufficient"
    if (
        summary.get("validation_status") != "valid"
        or reverse_dcf.get("implied_growth") is None
    ):
        return "insufficient"
    historical_cagr = _percentage_points(summary.get("free_cash_flow_cagr"))
    implied_growth = _percentage_points(reverse_dcf.get("implied_growth"), ratio=True)
    if historical_cagr is None or implied_growth is None:
        return "insufficient"
    gap = implied_growth - historical_cagr
    if gap >= Decimal("5"):
        return "high"
    if gap <= Decimal("-5"):
        return "low"
    return "neutral"


def _deterministic_summary(context: Mapping[str, Any]) -> dict[str, str]:
    quality = _deterministic_quality(context)
    valuation = _deterministic_historical_valuation(context)
    expectations = _deterministic_expectations(context)
    if quality == "weak":
        action = "停止深入研究"
    elif "insufficient" in {quality, valuation, expectations}:
        action = "等待补充证据"
    elif quality == "strong" and valuation == "high" and expectations == "high":
        action = "加入观察名单，等待估值回落或经营数据进一步验证"
    else:
        action = "加入观察名单并跟踪关键指标"
    return {
        "quality": _QUALITY_LABELS[quality],
        "valuation": _HISTORICAL_VALUATION_LABELS[valuation],
        "expectations": _EXPECTATION_LABELS[expectations],
        "action": action,
    }


def _annual_period_range(context: Mapping[str, Any]) -> str | None:
    summary = context.get("annual_financial_summary", {})
    annual = context.get("annual_financial_history", {})
    periods = annual.get("periods") if isinstance(annual, Mapping) else None
    if not isinstance(periods, Sequence) or isinstance(periods, (str, bytes)) or not periods:
        return None
    first = periods[0] if isinstance(periods[0], Mapping) else {}
    last = periods[-1] if isinstance(periods[-1], Mapping) else {}
    start_year = _text(summary.get("start_fiscal_year")) if isinstance(summary, Mapping) else None
    end_year = _text(summary.get("end_fiscal_year")) if isinstance(summary, Mapping) else None
    start_year = start_year or _text(first.get("fiscal_year"))
    end_year = end_year or _text(last.get("fiscal_year"))
    if not start_year or not end_year:
        return None
    start_date = _text(first.get("period_start"))
    end_date = _text(last.get("period_end"))
    if start_date and end_date:
        return f"FY{start_year}（{start_date}）至 FY{end_year}（{end_date}）"
    return f"FY{start_year} 至 FY{end_year}"


def _annual_financial_trend_markdown(context: Mapping[str, Any]) -> str:
    summary = context.get("annual_financial_summary", {})
    if not isinstance(summary, Mapping) or summary.get("validation_status") != "valid":
        return "### 完整财年趋势\n\n数据不足。"
    values = {
        key: _percentage_points(summary.get(key))
        for key in ("revenue_cagr", "net_income_cagr", "free_cash_flow_cagr")
    }
    if any(value is None for value in values.values()):
        return "### 完整财年趋势\n\n数据不足。"
    period_range = _annual_period_range(context) or "完整财年起止不可用"
    lines = [
        "### 完整财年趋势（确定性汇总）",
        f"- 完整财年起止：{period_range}",
        f"- 收入 CAGR：{values['revenue_cagr']:.2f}%",
        f"- 净利润 CAGR：{values['net_income_cagr']:.2f}%",
        f"- FCF CAGR（自由现金流）：{values['free_cash_flow_cagr']:.2f}%",
        "- 期间判断：整体增长但存在波动。",
    ]
    if summary.get("latest_fcf_direction") == "down":
        lines.append("- 最新 FY 自由现金流较上一 FY 下降。")
    return "\n".join(lines)


def _reevaluation_conditions_markdown(context: Mapping[str, Any]) -> str:
    historical = context.get("historical_valuation", {})
    median = (
        _decimal_from_text(historical.get("five_year_median"))
        if isinstance(historical, Mapping)
        else None
    )
    median_text = f"（{median:.2f}x）" if median is not None else ""
    return "\n".join(
        (
            "### 重新评估条件",
            f"- P/E 回到历史中位数{median_text}附近。",
            "- 出现收入/FCF 增长证据（收入增长与自由现金流增长）。",
        )
    )


def _judgment_rules_markdown(profile: str | None = None) -> str:
    if profile == "spac":
        return "\n".join(
            (
                "### 判断规则",
                "- 经营质量：缺少关键数据时为数据不足。",
                "- 估值适用性：SPAC 结构型证券不适用普通经营公司估值指标。",
                "- 市场预期评估：SPAC evidence-only 报告不评估市场隐含预期。",
                "- 研究动作：关键数据不足时等待补充证据。",
            )
        )
    return "\n".join(
        (
            "### 判断规则",
            "- 经营质量：营业利润率、净利率、FCF margin 均大于 0 且现金转换率至少为 100% 为强；三项率均大于 0 但现金转换率低于 100% 为一般；任一率不大于 0 为弱；缺少关键数据为数据不足。",
            "- 相对自身历史估值：当前 P/E 达到或超过历史 75 分位为偏高，达到或低于 25 分位为偏低，其余为中性；缺少数据为数据不足。",
            "- 市场隐含预期：反向 DCF 隐含增长率减去历史 FY FCF CAGR，至少 5 个百分点为高，至多负 5 个百分点为低，其余为中性；缺少数据为数据不足。该差值仅作方向性对照，不是预测。",
            "- 研究动作：经营质量弱时停止深入研究；关键数据不足时等待补充证据；其余情况加入观察名单并跟踪关键指标。",
        )
    )


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
    _rating_label: str,
    _risk_label: str,
    _action_label: str,
    context: Mapping[str, Any],
) -> list[str]:
    """只输出四项由 Python 计算的读者结论。"""
    summary = _deterministic_summary(context)
    profile = context.get("profile")
    is_spac = isinstance(profile, Mapping) and profile.get("security_profile") == "spac"
    valuation_label = "估值适用性" if is_spac else "相对自身历史估值"
    valuation_value = "不适用" if is_spac else summary["valuation"]
    expectations_label = "市场预期评估" if is_spac else "市场隐含预期"
    expectations_value = "不适用" if is_spac else summary["expectations"]
    return [
        f"- **经营质量：** {summary['quality']}",
        f"- **{valuation_label}：** {valuation_value}",
        f"- **{expectations_label}：** {expectations_value}",
        f"- **研究动作：** {summary['action']}",
    ]


def _ttm_metrics_markdown(context: Mapping[str, Any]) -> str:
    displayable_metric_ids = frozenset(
        {"revenue", "net_income", "operating_cash_flow", "free_cash_flow"}
    )
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
        if not metric_id or metric_id not in displayable_metric_ids or value is None:
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

    def markdown_cell(value: Any) -> str:
        text = _text(value) or "不可用"
        return (
            text.replace("\\", "\\\\")
            .replace("|", "\\|")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\n", "<br>")
        )

    def source_cell(references: Sequence[str]) -> str:
        if not references:
            return markdown_cell("未提供已验证来源")
        return "<br>".join(markdown_cell(reference) for reference in references)

    def impact_path(statement: str) -> tuple[str, str]:
        normalized = statement.casefold()
        for keywords, paths in _RISK_IMPACT_RULES:
            if any(keyword.casefold() in normalized for keyword in keywords):
                return paths
        return "需结合SEC风险更新判断财务影响", "SEC风险更新和对应财务指标"

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
        source = "、".join(references) if references else "未提供已验证来源"
        path, observation = impact_path(_text(claim["statement"]) or "")
        return (
            f"| {markdown_cell(claim['statement'])} "
            f"| {markdown_cell(f'影响路径：{path}')} "
            f"| {markdown_cell(f'监控指标：{observation}（观察项：{observation}）')} "
            f"| {source_cell([f'来源：{source}'])} |"
        )

    main = "\n".join(
        (
            "| 风险 | 影响路径 | 监控指标 | 来源 |",
            "|---|---|---|---|",
            *(render_claim(claim) for claim in displayable_claims[:3]),
        )
    )
    if len(displayable_claims) <= 3:
        return main
    remaining_claims = displayable_claims[3:]
    appendix = "\n".join(
        (
            "| 风险 | 影响路径 | 监控指标 | 来源 |",
            "|---|---|---|---|",
            *(render_claim(claim) for claim in remaining_claims),
        )
    )
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


def _caption_sources(context: Mapping[str, Any], key: str) -> str:
    metric_ids = {
        "financial_kpis": frozenset(
            {
                "revenue_growth",
                "operating_margin",
                "net_margin",
                "free_cash_flow_margin",
                "cash_conversion",
                "share_dilution",
            }
        ),
        "annual_financial_trend": _REPORT_TREND_METRIC_IDS,
        "historical_pe": frozenset(
            {"pe_ratio", "historical_pe_current", "historical_pe_median"}
        ),
    }[key]
    references: list[str] = []
    metrics = context.get("metrics", [])
    if isinstance(metrics, Sequence) and not isinstance(metrics, (str, bytes)):
        for metric in metrics:
            if not isinstance(metric, Mapping) or metric.get("metric_id") not in metric_ids:
                continue
            reference = _text(metric.get("source_reference"))
            if reference and reference not in references:
                references.append(reference)

    if key == "annual_financial_trend" and not references:
        annual = context.get("annual_financial_history", {})
        evidence_ids = {
            _text(evidence_id)
            for period in annual.get("periods", [])
            if isinstance(period, Mapping)
            for evidence_id in period.get("evidence_ids", [])
            if _text(evidence_id)
        } if isinstance(annual, Mapping) else set()
    elif key == "historical_pe" and not references:
        historical = context.get("historical_valuation", {})
        evidence_ids = {
            _text(evidence_id)
            for evidence_id in historical.get("input_evidence_ids", [])
            if _text(evidence_id)
        } if isinstance(historical, Mapping) else set()
    else:
        evidence_ids = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            if _text(value.get("evidence_id")) in evidence_ids:
                reference = _text(value.get("source_reference"))
                if reference and reference not in references:
                    references.append(reference)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for nested in value:
                visit(nested)

    if evidence_ids:
        visit(context.get("source_metadata", {}))
    return "、".join(references) or "数据不足"


def _financial_period_metadata(
    context: Mapping[str, Any],
    *,
    require_period_basis: bool = True,
) -> tuple[str, str, str] | None:
    metric_ids = frozenset(
        {
            "revenue_growth",
            "operating_margin",
            "net_margin",
            "free_cash_flow_margin",
            "cash_conversion",
            "share_dilution",
        }
    )
    signatures: list[tuple[str, str, str]] = []
    metrics = context.get("metrics", [])
    if not isinstance(metrics, Sequence) or isinstance(metrics, (str, bytes)):
        return None
    for metric in metrics:
        if not isinstance(metric, Mapping) or metric.get("metric_id") not in metric_ids:
            continue
        period_basis = _text(metric.get("period_basis")) or _text(metric.get("period"))
        period_end = _text(metric.get("period_end")) or ""
        as_of = _text(metric.get("as_of"))
        if (require_period_basis and period_basis is None) or as_of is None:
            return None
        signatures.append((period_basis or "", period_end, as_of))
    if not signatures or len(set(signatures)) != 1:
        return None
    return signatures[0]


def _financial_period_caption(context: Mapping[str, Any]) -> str:
    metadata = _financial_period_metadata(context)
    if metadata is None:
        return "数据不足"
    period_basis, period_end, as_of = metadata
    if not period_basis:
        return "数据不足"
    if period_end and period_end != as_of:
        point = f"period_end={period_end}；as_of={as_of}"
    elif period_end:
        point = period_end
    else:
        point = f"as_of={as_of}"
    return f"{period_basis}（{point}）"


def _caption_cutoff(context: Mapping[str, Any], key: str) -> str:
    if key == "annual_financial_trend":
        annual = context.get("annual_financial_history", {})
        periods = annual.get("periods", []) if isinstance(annual, Mapping) else []
        dates = [
            _text(period.get("filed_at")) or _text(period.get("period_end"))
            for period in periods
            if isinstance(period, Mapping)
        ]
        dates = [date for date in dates if date]
        return max(dates) if dates else "数据不足"
    if key == "historical_pe":
        historical = context.get("historical_valuation", {})
        if isinstance(historical, Mapping):
            current_date = _text(historical.get("current_date"))
            if current_date:
                return current_date
            dates = historical.get("selected_dates", [])
            if isinstance(dates, Sequence) and not isinstance(dates, (str, bytes)):
                dates = [_text(date) for date in dates if _text(date)]
                if dates:
                    return max(dates)
        return "数据不足"

    metadata = _financial_period_metadata(context, require_period_basis=False)
    if metadata is None:
        return "数据不足"
    _, period_end, as_of = metadata
    return as_of or period_end or "数据不足"


def _chart_caption_markdown(
    context: Mapping[str, Any], key: str, chart_context: Mapping[str, Any]
) -> str:
    chart = chart_context.get(key, {})
    observations = chart.get("observations") if isinstance(chart, Mapping) else None
    observation = (
        "；".join(str(item) for item in observations if _text(item))
        if isinstance(observations, Sequence) and not isinstance(observations, (str, bytes))
        else ""
    ) or "数据不足"
    captions = {
        "financial_kpis": (
            "图 1：最新经营质量",
            "最新经营质量是否由盈利能力与现金转换共同支持？",
            _financial_period_caption(context),
            "百分比（%）",
            "用于判断经营质量是否值得继续跟踪，不单独构成估值结论。",
            "三个面板分别展示增长与资本配置、盈利能力和现金流质量，并使用独立刻度。",
            "指标为已验证期间的口径比较，不能据此证明因果关系；缺少可比口径时以数据不足处理。",
        ),
        "annual_financial_trend": (
            "图 2：五年核心财务趋势指数",
            "五个完整财年的核心财务指标相对首年如何变化？",
            _annual_period_range(context) or "数据不足",
            "指数（首个财年=100；基期=100）",
            "用于比较跨财年的相对变化，绝对金额以五年财务表为准。",
            "近五年核心财务趋势（已验证完整财年），展示最近五个共同完整财年；绝对金额由五年财务表承担，三条序列共享指数纵轴。",
            "指数不显示绝对金额；首年值非正或无效时不生成，也不能替代五年财务表或证明因果关系。",
        ),
        "historical_pe": (
            "图 3：五年历史 P/E",
            "当前 P/E 相对过去五年历史区间处于何处？",
            "过去五年历史序列",
            "P/E（倍）",
            "用于判断估值相对自身历史位置，不作绝对价格判断。",
            "曲线展示 TTM P/E 的历史变化，参考线用于判断当前估值相对过去五年的位置。",
            "P/E 受盈利口径、样本日期和异常值影响，不代表未来收益；若历史摘要缺失则写数据不足。",
        ),
    }[key]
    title, question, period, unit, meaning, note, limitation = captions
    return "\n".join(
        (
            f"**{title}**",
            f"- 研究问题：{question}",
            f"- 期间：{period}",
            f"- 单位：{unit}",
            f"- 来源：{_caption_sources(context, key)}",
            f"- 截止：{_caption_cutoff(context, key)}",
            f"- 观察：{observation}",
            f"- 投资含义：{meaning}",
            f"- 限制与反证：{limitation}",
            f"- 图表说明：{note}",
        )
    )


def _reverse_dcf_markdown(
    payload: Mapping[str, Any], annual_summary: Mapping[str, Any] | None = None
) -> str:
    """把确定性反向 DCF 参数渲染成外行可读的表格。"""
    if not payload:
        return "反向 DCF：缺少已验证的 TTM 自由现金流或模型结果，未生成参数表。"
    forecast_years = _text(payload.get("forecast_years"))
    annual_summary = annual_summary if isinstance(annual_summary, Mapping) else {}
    historical_cagr = _percentage_points(annual_summary.get("free_cash_flow_cagr"))
    implied_growth = _percentage_points(payload.get("implied_growth"), ratio=True)
    gap = (
        implied_growth - historical_cagr
        if implied_growth is not None and historical_cagr is not None
        else None
    )
    rows = [
        "| 参数 | 数值 | 含义 |",
        "|---|---:|---|",
        f"| 基础自由现金流（TTM，模型起点） | {_currency_display(_normalized_amount(payload.get('base_fcf'), payload.get('base_fcf_unit') or payload.get('unit')))} | 最近十二个月自由现金流 |",
        f"| 基准折现率 | {_percent_display(payload.get('discount_rate')) or '不可用'} | 将未来现金流折算到今天 |",
        f"| 基准永续增长率 | {_percent_display(payload.get('terminal_growth')) or '不可用'} | 预测期后的稳定增长假设 |",
        f"| 历史 FY FCF CAGR | {f'{historical_cagr:.2f}%' if historical_cagr is not None else '不可用'} | 五个完整财年的历史复合增长 |",
        f"| 隐含增长率 | {_percent_display(payload.get('implied_growth')) or '不可用'} | {'未来 ' + forecast_years + ' 年' if forecast_years else ''}自由现金流年复合增长要求 |",
        f"| 差值（百分点） | {f'{gap:.2f} 个百分点' if gap is not None else '不可用'} | 隐含增长率减历史 FY FCF CAGR |",
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
    rows.extend(("", "方向性对照：历史 FY FCF CAGR 与反向 DCF 隐含增长率口径不同，仅作方向性对照，不是预测。"))
    return "\n".join(rows)


def _markdown_cell(value: Any, fallback: str = "不可用") -> str:
    text = _text(value)
    if not text:
        return fallback
    return " ".join(text.replace("|", "／").split()) or fallback


def _research_metadata_markdown(context: Mapping[str, Any]) -> str:
    company = context.get("company", {})
    company = company if isinstance(company, Mapping) else {}
    profile = context.get("profile", {})
    profile = profile if isinstance(profile, Mapping) else {}
    horizon = (
        _text(context.get("horizon"))
        or _text(company.get("horizon"))
        or _text(company.get("investment_horizon"))
    )
    profile_values = [
        _text(profile.get(key))
        for key in ("issuer_profile", "security_profile", "reporting_profile")
    ]
    profile_text = " / ".join(value for value in profile_values if value)
    rows = []
    for label, value in (
        ("公司名称", company.get("name") or company.get("company")),
        ("股票代码", company.get("ticker")),
        ("研究期限", horizon),
        ("研究 Profile", profile_text),
    ):
        if _text(value):
            rows.append((label, _markdown_cell(value)))
    if not rows:
        return "数据不足。"
    lines = ["| 字段 | 内容 |", "|---|---|"]
    lines.extend(f"| {label} | {value} |" for label, value in rows)
    return "\n".join(lines)


def _annual_financial_table_markdown(context: Mapping[str, Any]) -> str:
    annual = context.get("annual_financial_history", {})
    annual = annual if isinstance(annual, Mapping) else {}
    periods = annual.get("periods")
    if (
        annual.get("status") != "ok"
        or annual.get("validation_status") != "valid"
        or not isinstance(periods, Sequence)
        or isinstance(periods, (str, bytes))
    ):
        return "### 五年财务表（已验证完整财年）\n\n数据不足。"

    columns: list[tuple[str, Mapping[str, Any]]] = []
    for period in periods:
        if not isinstance(period, Mapping) or period.get("period_basis") != "FY":
            continue
        fiscal_year = _text(period.get("fiscal_year"))
        if not fiscal_year:
            continue
        fiscal_year = fiscal_year.upper()
        columns.append(
            (fiscal_year if fiscal_year.startswith("FY") else f"FY{fiscal_year}", period)
        )
    if not columns:
        return "### 五年财务表（已验证完整财年）\n\n数据不足。"

    metric_labels = (
        ("revenue", "营业收入"),
        ("net_income", "净利润"),
        ("operating_cash_flow", "经营现金流"),
        ("capex", "资本开支"),
        ("free_cash_flow", "自由现金流"),
    )
    available_metrics = [
        (metric_id, label)
        for metric_id, label in metric_labels
        if any(_text(period.get(metric_id)) for _, period in columns)
    ]
    lines = [
        "### 五年财务表（已验证完整财年）",
        "| 指标 | " + " | ".join(year for year, _ in columns) + " |",
        "|---|" + "---:|" * len(columns),
    ]
    for metric_id, label in available_metrics:
        values = [
            _currency_display(period.get(metric_id))
            if _text(period.get(metric_id))
            else "数据不足"
            for _, period in columns
        ]
        lines.append(f"| {label} | " + " | ".join(values) + " |")

    summary = context.get("annual_financial_summary", {})
    summary = summary if isinstance(summary, Mapping) else {}
    cagr_labels = (
        ("revenue_cagr", "收入 CAGR"),
        ("net_income_cagr", "净利润 CAGR"),
        ("free_cash_flow_cagr", "FCF CAGR"),
    )
    for key, label in cagr_labels:
        value = _percentage_points(summary.get(key))
        if value is not None:
            lines.append(f"- {label}：{value:.2f}%")
    return "\n".join(lines)


def _decision_basis_markdown(context: Mapping[str, Any]) -> str:
    summary = _deterministic_summary(context)
    lines = [
        "### 已验证事实",
        f"- 经营质量：{summary['quality']}",
        "",
        "### 确定性比较",
        f"- 相对自身历史估值：{summary['valuation']}",
        f"- 市场隐含预期：{summary['expectations']}",
        "",
        "### 确定性判断",
        f"- 研究动作：{summary['action']}",
        "",
        _reevaluation_conditions_markdown(context),
    ]
    return "\n".join(lines)


def _scope_markdown(context: Mapping[str, Any]) -> str:
    company = context.get("company", {})
    company = company if isinstance(company, Mapping) else {}
    horizon = (
        _text(context.get("horizon"))
        or _text(company.get("horizon"))
        or _text(company.get("investment_horizon"))
    )
    lines = [
        "### 研究范围",
    ]
    for label, value in (
        ("研究对象", company.get("name") or company.get("company")),
        ("股票代码", company.get("ticker")),
        ("请求研究期限", horizon),
    ):
        if _text(value):
            lines.append(f"- {label}：{_markdown_cell(value)}")
    if len(lines) == 1:
        lines.append("数据不足。")
    return "\n".join(lines)


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
    strict_lite = profile_kind in {None, "standard_operating"}
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
        if strict_lite:
            if field == "execution_summary":
                sections.extend(
                    (
                        "## 0. 封面与研究元数据",
                        "",
                        _research_metadata_markdown(context_payload),
                        "",
                        "## 1. 一页结论",
                        "",
                    )
                )
            elif field == "company_quality":
                sections.extend(
                    (
                        "## 2. 公司与研究范围",
                        "",
                        _scope_markdown(context_payload),
                        "",
                    )
                )
            elif field == "financial_trend":
                sections.extend(
                    (
                        "## 3. 历史经营与财务质量",
                        "",
                        _annual_financial_table_markdown(context_payload),
                        "",
                        "## 4. 最新经营状态",
                        "",
                    )
                )
            elif field == "current_valuation":
                sections.extend(("## 5. 估值", ""))
            elif field == "key_risks":
                sections.extend(("## 6. 主要风险与监控条件", ""))
            elif field == "sources_and_method":
                sections.extend(
                    (
                        "## 7. 综合判断与重新评估条件",
                        "",
                        _decision_basis_markdown(context_payload),
                        "",
                        "## 8. 数据来源、方法与技术附录",
                        "",
                    )
                )
        legacy_heading = (
            f"## {heading}"
            if not strict_lite or field == "non_investment_disclaimer"
            else f"<!-- ## {heading} -->"
        )
        sections.extend((legacy_heading, ""))
        if strict_lite and field == "non_investment_disclaimer":
            sections.extend(("<!-- ## 9. 非投资建议声明 -->", ""))
        if field == "execution_summary":
            sections.extend(
                (*_execution_summary_lines(
                    rating_label, risk_label, action_label, context_payload
                ), "")
            )
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
                        _chart_caption_markdown(
                            context_payload, "financial_kpis", chart_context
                        ),
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
            sections.extend(
                (
                    _metric_text_for_section(
                        metrics,
                        "financial",
                        _REPORT_TREND_METRIC_IDS - {"free_cash_flow"},
                    ),
                    "",
                )
            )
            if basis_note := _financial_basis_note(metrics):
                sections.extend((basis_note, ""))
            sections.extend((_annual_financial_trend_markdown(context_payload), ""))
            if ttm_markdown := _ttm_metrics_markdown(context_payload):
                sections.extend((ttm_markdown, ""))
                if context_payload.get("annual_financial_summary"):
                    sections.extend(
                        (
                            "TTM 数据与完整财年数据期间不同，不可直接视为同一期数据。",
                            "",
                        )
                    )
                    if period_range := _annual_period_range(context_payload):
                        sections.extend((f"完整财年范围：{period_range}。", ""))
            chart = _visual_markdown(
                visuals,
                "annual_financial_trend",
                "近五年核心财务趋势（已验证完整财年）",
            )
            if chart:
                sections.extend(
                    (
                        chart,
                        "",
                        _chart_caption_markdown(
                            context_payload, "annual_financial_trend", chart_context
                        ),
                        "",
                    )
                )
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
                    "以下数据用于相对自身历史估值比较，不作绝对价格判断。",
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
                        _chart_caption_markdown(
                            context_payload, "historical_pe", chart_context
                        ),
                        "",
                    )
                )
            sections.extend((_reevaluation_conditions_markdown(context_payload), ""))
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
                    _reverse_dcf_markdown(
                        context_payload.get("reverse_dcf", {}),
                        context_payload.get("annual_financial_summary", {}),
                    ),
                    "",
                )
            )
        elif field == "key_risks":
            sections.extend(
                (
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
                    _judgment_rules_markdown(profile_kind),
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
            sections.extend((_annual_financial_trend_markdown({}), ""))
        elif field == "current_valuation":
            sections.extend((getattr(report_draft, field), "", _claim_text(claims, "current_valuation"), "", f"确定性估值数据：{_json_text(valuation_payload)}", ""))
        elif field == "historical_valuation":
            sections.extend(
                (
                    "以下数据用于相对自身历史估值比较，不作绝对价格判断。",
                    "",
                    _claim_text(claims, "historical_valuation"),
                    "",
                    f"确定性历史估值数据：{_json_text(historical_payload)}",
                    "",
                    _reevaluation_conditions_markdown(
                        {"historical_valuation": historical_payload}
                    ),
                    "",
                )
            )
        elif field == "reverse_dcf":
            sections.extend((
                _claim_text(claims, "reverse_dcf"),
                "",
                f"确定性反向 DCF 数据：{_json_text(reverse_dcf_payload)}",
                "",
                _reverse_dcf_markdown(reverse_dcf_payload),
                "",
            ))
        elif field == "key_risks":
            sections.extend(
                (
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
                    _judgment_rules_markdown(),
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
