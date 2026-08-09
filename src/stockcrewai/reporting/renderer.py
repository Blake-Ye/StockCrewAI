from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from .context import (
    _CLAIM_CATEGORY_TO_SECTION,
    _REPORT_NARRATIVE_CATEGORIES,
    _REPORT_NARRATIVE_CATEGORY_MAP,
    _REPORT_QUALITY_METRIC_IDS,
    _REPORT_SECTIONS,
    _REPORT_TREND_METRIC_IDS,
    _currency_display,
    _json_safe_context,
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
        sources_and_method="来源与方法部分由确定性 Renderer 注入已验证内容。",
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
    if metric_id in _REPORT_AMOUNT_METRIC_IDS or unit_lower in {"currency", "usd"}:
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
    return f"- {label}：{value}（截至 {metric['as_of']}；来源：{metric['source_reference']}）"


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
    except Exception as exc:
        raise ValueError("ReportContext 未通过本地来源和结构校验。") from exc
    context_payload = validated_context.model_dump(mode="json")
    claims = _validated_claims(context_payload["claims"])
    status = context_payload["verdict_status"]
    metrics = context_payload["metrics"]
    verdict = context_payload.get("verdict", {})
    if not isinstance(verdict, Mapping):
        verdict = {}
    rating_label, risk_label, rule_label, action_label = _verdict_display(verdict, status)
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
            profile = context_payload.get("profile", {})
            if isinstance(profile, Mapping):
                profile_values = {
                    key: _text(profile.get(key))
                    for key in ("issuer_profile", "security_profile", "reporting_profile")
                }
                profile_values = {key: value for key, value in profile_values.items() if value}
                if profile_values:
                    sections.append(
                        "Profile："
                        + "; ".join(
                            f"{key.removesuffix('_profile')}={value}"
                            for key, value in profile_values.items()
                        )
                    )
            if coverage_level := _text(context_payload.get("coverage_level")):
                sections.append(f"覆盖范围：{coverage_level}")
            if policy_version := _text(context_payload.get("policy_version")):
                sections.append(f"Policy version：{policy_version}")
            sections.append("")
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
                    _metric_text_for_section(metrics, "financial", _REPORT_TREND_METRIC_IDS),
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
                (
                    getattr(report_draft, field),
                    "",
                    _claim_text(claims, "current_valuation"),
                    "",
                    _metric_text_for_section(metrics, "current_valuation"),
                    "",
                )
            )
        elif field == "historical_valuation":
            sections.extend(
                (
                    getattr(report_draft, field),
                    "",
                    _claim_text(claims, "historical_valuation"),
                    "",
                    _metric_text_for_section(metrics, "historical_valuation"),
                    "",
                )
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
