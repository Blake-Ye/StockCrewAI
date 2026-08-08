"""提供紧凑、可审计且不泄露原始内容的研究运行输出。"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text as RichText
except ImportError:  # pragma: no cover - 仅在最小安装环境中使用纯文本回退
    Console = None  # type: ignore[assignment,misc]
    Panel = None  # type: ignore[assignment,misc]
    RichText = None  # type: ignore[assignment,misc]


_ANSI_RE = re.compile(
    r"(?:\x1b\][^\x07]*(?:\x07|\x1b\\))"
    r"|(?:\x1b[@-_][0-?]*[ -/]*[@-~])"
    r"|(?:\x9b[0-?]*[ -/]*[@-~])"
)
_TRACE_RE = re.compile(
    r"(?i)(?:https?://[^\s)\]]*(?:trace|access[_-]?code)[^\s)\]]*"
    r"|\b(?:trace[_ -]?url|access[_ -]?code)\b\s*[:=]\s*[^\s,;]+)"
)
_SECRET_RE = re.compile(
    r"(?i)\b(api[_ -]?key|secret|password|token)\b\s*[:=]\s*[^\s,;]+"
)
_RAW_OUTPUT_RE = re.compile(
    r"(?i)\b(?:analysis_)?raw(?:[_ -]?(?:task[_ -]?)?outputs?|"
    r"[_ -](?:response|result|text|content))?\b"
    r"[\"']?(?:\s*[:=]\s*[^;\n]*)?"
)
_EVIDENCE_LIST_RE = re.compile(
    r"(?i)\b(?:validated_)?evidence[_ -]?ids?\b\s*[:=]\s*"
    r"(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\})"
)

_STAGE_TITLES = (
    "请求解析",
    "SEC 证据与财务验证",
    "市场价格与估值",
    "Analysis Gate",
    "Analysis Crew",
    "Claim Gate",
    "Verdict 与 Report",
)


@dataclass(frozen=True)
class RunStageEvent:
    """表示一个已经压缩为摘要的逻辑运行阶段事件。"""

    step: int
    title: str
    actor: str
    status: str
    input_summary: str = ""
    output_summary: str = ""
    decision: str = ""
    reason: str = ""
    next_step: str = ""


def strip_ansi(text: str) -> str:
    """移除 ANSI 控制序列，返回可以安全写入 Markdown 的文本。"""

    return _ANSI_RE.sub("", text).replace("\x1b", "")


def _redact_output_text(text: str) -> str:
    """清理控制码、敏感值、原始任务输出名和证据 ID 列表。"""

    safe_text = strip_ansi(text)
    safe_text = _TRACE_RE.sub("[trace access code 已隐藏]", safe_text)
    safe_text = _SECRET_RE.sub(lambda match: f"{match.group(1)}=[已隐藏]", safe_text)
    safe_text = _RAW_OUTPUT_RE.sub("原始任务输出已隐藏", safe_text)
    return _EVIDENCE_LIST_RE.sub("Evidence ID 列表已隐藏", safe_text)


def sanitize_text(text: str, limit: int = 240) -> str:
    """清理 ANSI、敏感字段、trace URL 和原始字段并限制文本长度。"""

    if limit <= 0:
        return ""
    safe_text = _redact_output_text(str(text))
    safe_text = " ".join(safe_text.replace("\r", " ").replace("\n", " ").split())
    if len(safe_text) > limit:
        return f"{safe_text[: limit - 1]}…"
    return safe_text


def _safe_text(value: Any, limit: int = 160) -> str:
    """把单个展示值转为脱敏、单行且有限长度的文本。"""

    if value is None:
        return ""
    if isinstance(value, Mapping):
        return "[结构化内容已隐藏]"
    if isinstance(value, (list, tuple, set)):
        return f"[{len(value)} 项]"
    return sanitize_text(str(value), limit)


def _compact_text(value: Any, limit: int = 160) -> str:
    """生成面向终端和 Markdown 的非空单行摘要。"""

    return _safe_text(value, limit) or "-"


def _mapping_value(mapping: Mapping[str, Any], *keys: str) -> Any:
    """按固定优先级从映射中读取第一个非空字段。"""

    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _scalar_value(mapping: Mapping[str, Any], *keys: str) -> Any:
    """读取适合摘要展示的标量字段并过滤嵌套原始对象。"""

    for key in keys:
        value = mapping.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, (list, tuple)):
            value = next(
                (
                    item
                    for item in value
                    if isinstance(item, (str, int, float, bool, datetime))
                    and item not in (None, "")
                ),
                None,
            )
            if value is None:
                continue
        if isinstance(value, Mapping):
            value = _mapping_value(value, "value", "amount", "rate")
        if isinstance(value, (list, tuple, set, Mapping)):
            continue
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, (str, int, float, bool)):
            return sanitize_text(value) if isinstance(value, str) else value
        return _safe_text(value)
    return None


def _first_scalar(mappings: tuple[Any, ...], *keys: str) -> Any:
    """从多个候选映射中读取第一个可展示标量。"""

    for candidate in mappings:
        if isinstance(candidate, Mapping):
            value = _scalar_value(candidate, *keys)
            if value not in (None, ""):
                return value
    return None


def _as_string_list(value: Any) -> list[str]:
    """把 required_data 或阶段列表转换为脱敏字符串列表。"""

    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return [item for item in (_safe_text(item, 120) for item in values) if item]


def _count_items(value: Any) -> int:
    """计算映射或序列中的项目数量，不展开项目内容。"""

    if value is None:
        return 0
    if isinstance(value, (Mapping, list, tuple, set)):
        return len(value)
    return 1


def _count_risk_sections(filings: Any) -> int:
    """统计 filing 中的风险章节数量而不输出章节或证据 ID。"""

    if not isinstance(filings, (list, tuple)):
        return 0
    count = 0
    for filing in filings:
        if not isinstance(filing, Mapping):
            continue
        sections = filing.get("risk_sections")
        count += _count_items(sections)
    return count


def _calculation_display(value: Any, formula_id: str) -> Any:
    """按公式 ID 读取估值计算，优先返回面向展示的结果。"""

    if not isinstance(value, Mapping):
        return None
    calculations = value.get("calculations")
    if not isinstance(calculations, (list, tuple)):
        return None
    for calculation in calculations:
        if not isinstance(calculation, Mapping):
            continue
        if calculation.get("formula_id") == formula_id:
            return _first_scalar(
                (calculation,), "display_result", "normalized_result", "raw_result"
            )
    return None


def _claim_counts(analysis: Any) -> dict[str, int]:
    """统计全部 Claim 及财务、风险、估值三个域的数量。"""

    counts = {"total": 0, "financial": 0, "risk": 0, "valuation": 0}

    def add_claim(claim: Any, fallback_domain: str = "") -> None:
        """计入一个 Claim，并只保留其类别计数。"""

        if not isinstance(claim, Mapping):
            return
        domain = str(claim.get("category") or claim.get("domain") or fallback_domain)
        domain = domain.lower()
        counts["total"] += 1
        if "reverse_dcf" in domain:
            counts["valuation"] += 1
            return
        for name in ("financial", "risk", "valuation"):
            if name in domain:
                counts[name] += 1
                break

    if isinstance(analysis, (list, tuple)):
        for claim in analysis:
            add_claim(claim)
    elif isinstance(analysis, Mapping):
        claims = analysis.get("claims")
        if isinstance(claims, (list, tuple)):
            for claim in claims:
                add_claim(claim)
        else:
            for domain in ("financial", "risk", "valuation"):
                domain_claims = analysis.get(domain)
                if isinstance(domain_claims, (list, tuple)):
                    for claim in domain_claims:
                        add_claim(claim, domain)
    return counts


def _agent_output_claim_counts(diagnostics: Mapping[str, Any]) -> dict[str, int]:
    """安全解析三个 Agent 原始 JSON，仅返回 claims 数组数量。"""

    raw_outputs = diagnostics.get("raw_task_outputs")
    if not isinstance(raw_outputs, Mapping) or not raw_outputs:
        return {}
    counts: dict[str, int] = {}
    for domain in ("financial", "risk", "valuation"):
        raw_output = raw_outputs.get(domain)
        try:
            payload = json.loads(strip_ansi(raw_output)) if isinstance(raw_output, str) else None
        except json.JSONDecodeError:
            payload = None
        claims = payload.get("claims") if isinstance(payload, Mapping) else None
        counts[domain] = len(claims) if isinstance(claims, list) else 0
    return counts


def _request_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    """提取公司、ticker、期限和 focus 数量等请求摘要。"""

    parsed = result.get("parsed_request")
    parsed_mapping = parsed if isinstance(parsed, Mapping) else {}
    focus = _mapping_value(
        parsed_mapping,
        "focus",
        "focus_areas",
        "focus_items",
        "requested_focus",
    )
    focus_count = _first_scalar(
        (parsed_mapping, result), "focus_count", "focus_number"
    )
    if focus_count is None and isinstance(focus, (list, tuple, set)):
        focus_count = len(focus)
    summary = {
        "company": _first_scalar(
            (parsed_mapping, result),
            "company_name",
            "company_name_guess",
            "company",
            "name",
        ),
        "ticker": _first_scalar(
            (parsed_mapping, result), "ticker", "ticker_guess", "symbol"
        ),
        "period": _first_scalar(
            (parsed_mapping, result),
            "period",
            "period_years",
            "horizon_years",
            "time_horizon",
            "investment_horizon",
            "term",
            "years",
        ),
        "focus_count": focus_count,
    }
    return {key: value for key, value in summary.items() if value not in (None, "")}


def _progress_summary(
    result: Mapping[str, Any],
    status: str,
    stage: str,
    diagnostics: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    """根据结果中的显式字段或固定阶段推导已完成和未执行阶段。"""

    completed_value = result.get("completed", result.get("completed_stages"))
    skipped_value = result.get("skipped", result.get("skipped_stages"))
    if completed_value is not None or skipped_value is not None:
        return _as_string_list(completed_value), _as_string_list(skipped_value)

    diagnostic_completed = diagnostics.get(
        "completed", diagnostics.get("completed_stages")
    )
    diagnostic_skipped = diagnostics.get(
        "not_executed", diagnostics.get("skipped", diagnostics.get("skipped_stages"))
    )
    if diagnostic_completed is not None or diagnostic_skipped is not None:
        completed = _as_string_list(diagnostic_completed)
        skipped = _as_string_list(diagnostic_skipped)
        if diagnostics.get("domain") or diagnostics.get("reason_code"):
            if not any("Claim Gate" in item for item in completed):
                completed.append("Claim Gate（阻断）")
            if not skipped:
                skipped.append("Verdict 与 Report")
        return completed, skipped

    normalized_status = status.lower()
    normalized_stage = stage.lower().replace("_", " ")
    if normalized_status in {"ok", "ready", "success", "completed"}:
        return list(_STAGE_TITLES), []

    if normalized_status != "blocked":
        return [], []

    if "request" in normalized_stage or "parser" in normalized_stage:
        index = 0
    elif "analysis" in normalized_stage:
        index = 5 if diagnostics else 3
    elif "claim" in normalized_stage:
        index = 5
    elif "report" in normalized_stage or "verdict" in normalized_stage:
        index = 6
    elif "valuation" in normalized_stage or "market" in normalized_stage:
        index = 2
    elif "evidence" in normalized_stage or "sec" in normalized_stage:
        index = 1
    else:
        index = -1
    if index < 0:
        return [], list(_STAGE_TITLES)
    return list(_STAGE_TITLES[: index + 1]), list(_STAGE_TITLES[index + 1 :])


def summarize_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """将完整研究结果压缩为不含原始输出和 ID 列表的人类摘要。"""

    status = _safe_text(result.get("status"), 40) or "unknown"
    stage = _safe_text(result.get("stage"), 80) or "unknown"
    diagnostics_value = result.get("analysis_diagnostics")
    diagnostics = diagnostics_value if isinstance(diagnostics_value, Mapping) else {}
    required_data = _as_string_list(
        result.get("required_data") or diagnostics.get("required_data")
    )
    completed, skipped = _progress_summary(result, status, stage, diagnostics)

    edgar_value = result.get("edgar")
    edgar = edgar_value if isinstance(edgar_value, Mapping) else {}
    filings = result.get("filings")
    if filings is None:
        filings = edgar.get("filings")
    facts = result.get("facts")
    if facts is None:
        facts = edgar.get("facts")
    calculations_value = result.get("calculations")
    calculations_mapping = (
        calculations_value if isinstance(calculations_value, Mapping) else {}
    )
    calculations = calculations_mapping.get("calculations", calculations_value)
    validation = result.get("validation")
    validation_mapping = validation if isinstance(validation, Mapping) else {}
    validated_evidence_ids = result.get("validated_evidence_ids")
    if validated_evidence_ids is None:
        validated_evidence_ids = validation_mapping.get("validated_evidence_ids")
    validated_calculation_ids = result.get("validated_calculation_ids")
    if validated_calculation_ids is None:
        validated_calculation_ids = validation_mapping.get(
            "validated_calculation_ids"
        )
    evidence_summary = {
        "facts": _count_items(facts),
        "filings": _count_items(filings),
        "risk_sections": _count_risk_sections(filings),
        "calculations": _count_items(calculations),
        "validated_evidence": _count_items(validated_evidence_ids),
        "validated_calculations": _count_items(validated_calculation_ids),
        "validation_status": _first_scalar(
            (validation_mapping, result), "status", "validation_status"
        ),
    }

    market = result.get("market_price_data")
    valuation = result.get("valuation")
    historical = result.get("historical_valuation")
    reverse_dcf = result.get("reverse_dcf")
    pe = _calculation_display(valuation, "pe_ratio")
    if pe is None:
        pe = _first_scalar((valuation,), "pe", "pe_ratio", "price_earnings")
    fcf_yield = _calculation_display(valuation, "fcf_yield")
    if fcf_yield is None:
        fcf_yield = _first_scalar(
            (valuation,), "fcf_yield", "free_cash_flow_yield"
        )
    valuation_summary = {
        "price": _first_scalar(
            (market, valuation),
            "market_price",
            "price",
            "current_price",
            "close",
            "value",
        ),
        "timestamp": _first_scalar(
            (market, valuation), "price_timestamp", "timestamp", "as_of"
        ),
        "currency": _first_scalar((market, valuation), "currency", "currency_code"),
        "pe": pe,
        "fcf_yield": fcf_yield,
        "historical_percentile": _first_scalar(
            (historical, valuation),
            "current_percentile",
            "percentile",
            "historical_percentile",
        ),
        "reverse_dcf_implied_growth": _first_scalar(
            (reverse_dcf, valuation),
            "implied_growth",
            "implied_growth_rate",
            "reverse_dcf_implied_growth",
            "growth_rate",
        ),
    }
    valuation_summary = {
        key: value for key, value in valuation_summary.items() if value not in (None, "")
    }

    claims = _claim_counts(result.get("analysis"))
    analysis_summary: dict[str, Any] = {"claims": claims}
    if status.lower() == "blocked" and claims["total"] == 0:
        agent_output_claims = _agent_output_claim_counts(diagnostics)
        if agent_output_claims:
            analysis_summary["agent_output_claims"] = agent_output_claims
    report = result.get("report")
    report_summary = {
        "generated": report not in (None, "", {}, []),
        "verdict_status": _first_scalar(
            (result.get("verdict"),), "status", "decision"
        ),
    }

    domain = _safe_text(diagnostics.get("domain") or result.get("domain"), 80)
    reason_code = _safe_text(
        diagnostics.get("reason_code") or result.get("reason_code"), 100
    )
    reason = _safe_text(diagnostics.get("reason") or result.get("reason"), 180)
    gate_status = "BLOCKED" if status.lower() == "blocked" else "READY"
    summary: dict[str, Any] = {
        "status": status,
        "stage": stage,
        "request": _request_summary(result),
        "evidence": evidence_summary,
        "valuation": valuation_summary,
        "analysis": analysis_summary,
        "gate": {
            "status": gate_status,
            "required_data": required_data,
            "domain": domain,
            "reason_code": reason_code,
        },
        "report": report_summary,
        "completed": completed,
        "skipped": skipped,
        "next_action": _safe_text(
            result.get("next_action") or diagnostics.get("next_action"), 180
        ),
    }
    if status.lower() == "error":
        error_value = result.get("error")
        error_mapping = error_value if isinstance(error_value, Mapping) else {}
        error_type = _mapping_value(
            error_mapping, "type", "error_type"
        ) or result.get("error_type")
        error_message = _mapping_value(
            error_mapping, "message", "error_message"
        )
        if error_message in (None, "") and not isinstance(error_value, Mapping):
            error_message = error_value
        summary["error"] = {
            "type": sanitize_text(str(error_type or "UnknownError"), 80),
            "message": sanitize_text(str(error_message or "未提供错误消息")),
        }
    if status.lower() == "blocked":
        summary.update(
            {
                "domain": domain,
                "reason_code": reason_code,
                "reason": reason,
                "required_data": required_data,
            }
        )
        if not summary["next_action"]:
            summary["next_action"] = "补齐 required_data 后重新运行"
    return summary


def _json_ready(value: Any) -> Any:
    """递归转换结果为禁止 NaN 且可由 json.load 读取的值。"""

    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return _safe_text(value, 1000)
    return value


class CompactRunReporter:
    """实时渲染阶段单框，并把事件和最终摘要分别保存为运行产物。"""

    def __init__(self, terminal_stream: TextIO) -> None:
        """绑定真实终端流并初始化不含原始输出的事件列表。"""

        self.terminal_stream = terminal_stream
        self.events: list[RunStageEvent] = []
        if Console is not None and Panel is not None and RichText is not None:
            self._console = Console(
                file=terminal_stream,
                force_terminal=False,
                color_system=None,
                width=100,
                soft_wrap=True,
            )
        else:
            self._console = None

    def _render_box(self, title: str, lines: list[str], status: str) -> None:
        """将已脱敏的多行内容渲染为一个 Rich 或纯文本边框。"""

        safe_title = _compact_text(title, 80)
        safe_lines = [_compact_text(line, 180) for line in lines]
        if self._console is not None:
            body = "\n".join(safe_lines)
            style = "red" if status.lower() == "blocked" else "green"
            panel = Panel(
                RichText(body),
                title=RichText(safe_title),
                border_style=style,
                expand=False,
                padding=(0, 1),
            )
            self._console.print(panel)
        else:
            width = min(
                100,
                max([len(safe_title), *(len(line) for line in safe_lines), 20]) + 4,
            )
            border = "─" * max(1, width - 2)
            self.terminal_stream.write(f"┌{border}┐\n")
            self.terminal_stream.write(f"│ {_safe_text(safe_title, width - 4):<{width - 4}} │\n")
            self.terminal_stream.write(f"├{border}┤\n")
            for line in safe_lines:
                self.terminal_stream.write(
                    f"│ {_safe_text(line, width - 4):<{width - 4}} │\n"
                )
            self.terminal_stream.write(f"└{border}┘\n")
        self.terminal_stream.flush()

    def emit(self, event: RunStageEvent) -> None:
        """保存一个结构化事件并立即输出一个紧凑单框。"""

        self.events.append(event)
        lines = [
            f"执行者：{_compact_text(event.actor)}",
            f"输入摘要：{_compact_text(event.input_summary)}",
            f"输出摘要：{_compact_text(event.output_summary)}",
            f"状态：{_compact_text(event.status, 40)}",
            f"决策：{_compact_text(event.decision)}",
            f"原因：{_compact_text(event.reason)}",
            f"下一节点：{_compact_text(event.next_step)}",
        ]
        self._render_box(
            f"{event.step}. {_compact_text(event.title, 80)}", lines, event.status
        )

    def _render_final(self, summary: Mapping[str, Any]) -> None:
        """输出只含最终状态和阻断关键信息的收尾单框。"""

        status = _safe_text(summary.get("status"), 40) or "unknown"
        if status.lower() == "error":
            error = summary.get("error", {})
            error_type = error.get("type") if isinstance(error, Mapping) else ""
            error_message = error.get("message") if isinstance(error, Mapping) else ""
            lines = [
                "状态（status）：ERROR",
                f"阶段（stage）：{_compact_text(summary.get('stage'), 80)}",
                f"错误摘要（error）：{_compact_text(error_type, 80)}：{_compact_text(error_message, 240)}",
            ]
        elif status.lower() == "blocked":
            lines = [
                f"状态（status）：{_compact_text(status, 40)}",
                f"阶段（stage）：{_compact_text(summary.get('stage'), 80)}",
                f"域（domain）：{_compact_text(summary.get('domain'))}",
                f"原因（reason_code）：{_compact_text(summary.get('reason_code'))}",
                f"required_data：{_compact_text(', '.join(summary.get('required_data', [])))}",
                f"已完成（completed）：{_compact_text(', '.join(summary.get('completed', [])))}",
                f"未执行（skipped）：{_compact_text(', '.join(summary.get('skipped', [])))}",
                f"下一步（next_action）：{_compact_text(summary.get('next_action'))}",
            ]
            analysis = summary.get("analysis", {})
            claims = analysis.get("claims", {}) if isinstance(analysis, Mapping) else {}
            agent_claims = (
                analysis.get("agent_output_claims", {})
                if isinstance(analysis, Mapping)
                else {}
            )
            if isinstance(agent_claims, Mapping) and agent_claims:
                lines.extend(
                    [
                        f"通过 Claim Gate Claims：{_compact_text(claims.get('total'), 40)}",
                        "Agent 输出 Claims："
                        f"财务 {_compact_text(agent_claims.get('financial'), 40)} / "
                        f"风险 {_compact_text(agent_claims.get('risk'), 40)} / "
                        f"估值 {_compact_text(agent_claims.get('valuation'), 40)}",
                    ]
                )
        else:
            report = summary.get("report", {})
            generated = report.get("generated") if isinstance(report, Mapping) else False
            lines = [
                f"状态（status）：{_compact_text(status, 40)}",
                f"阶段（stage）：{_compact_text(summary.get('stage'), 80)}",
                f"已完成（completed）：{_compact_text(', '.join(summary.get('completed', [])))}",
                f"报告（report）：{'已生成' if generated else '未生成'}",
            ]
        self._render_box("最终运行结果", lines, status)

    def _markdown(self, summary: Mapping[str, Any], result_name: str, started_at: datetime, finished_at: datetime, exit_code: int) -> str:
        """把事件和摘要编排成无 ANSI 的短 Markdown 文档。"""

        def md(value: Any, limit: int = 180) -> str:
            """转义 Markdown 行内代码中的展示值。"""

            return _compact_text(value, limit).replace("`", "'")

        status = md(summary.get("status"), 40)
        lines = [
            "# StockCrewAI 运行输出",
            "",
            "## 最终结果",
            "",
            f"- 业务状态（status）：`{status}`",
            f"- 退出码（exit_code）：`{exit_code}`（退出码与状态独立）",
            f"- 阶段（stage）：`{md(summary.get('stage'), 80)}`",
        ]
        if status.lower() == "error":
            error = summary.get("error", {})
            error_type = error.get("type") if isinstance(error, Mapping) else "UnknownError"
            error_message = error.get("message") if isinstance(error, Mapping) else "未提供错误消息"
            lines.append(
                f"- ERROR 摘要：`{md(error_type, 80)}`：{md(error_message, 240)}"
            )
        elif status.lower() == "blocked":
            lines.extend(
                [
                    f"- 域（domain）：`{md(summary.get('domain'))}`",
                    f"- 原因（reason_code）：`{md(summary.get('reason_code'), 100)}`",
                    f"- 直接原因：{md(summary.get('reason'))}",
                    f"- required_data：`{md(', '.join(summary.get('required_data', [])))}`",
                    f"- 已完成（completed）：{md(', '.join(summary.get('completed', [])))}",
                    f"- 未执行（skipped）：{md(', '.join(summary.get('skipped', [])))}",
                    f"- 下一步（next_action）：{md(summary.get('next_action'))}",
                ]
            )
        lines.extend(
            [
                f"- 完整结果：`{md(result_name, 180)}`",
                "",
                "## 请求",
                "",
            ]
        )
        request = summary.get("request", {})
        if isinstance(request, Mapping) and request:
            for key, label in (
                ("company", "公司"),
                ("ticker", "Ticker"),
                ("period", "期限"),
                ("focus_count", "Focus 数量"),
            ):
                if request.get(key) not in (None, ""):
                    lines.append(f"- {label}：`{md(request[key])}`")
        else:
            lines.append("- 未提供结构化请求摘要")

        evidence = summary.get("evidence", {})
        if isinstance(evidence, Mapping):
            lines.extend(
                [
                    "",
                    "## 证据与验证摘要",
                    "",
                    "- "
                    + "；".join(
                        f"{label}：{md(evidence.get(key), 80)}"
                        for key, label in (
                            ("facts", "事实数"),
                            ("filings", "Filing 数"),
                            ("risk_sections", "风险章节数"),
                            ("calculations", "计算数"),
                            ("validated_evidence", "已验证证据数"),
                            ("validated_calculations", "已验证计算数"),
                            ("validation_status", "验证状态"),
                        )
                    ),
                ]
            )

        valuation = summary.get("valuation", {})
        if isinstance(valuation, Mapping):
            lines.extend(
                [
                    "",
                    "## 估值摘要",
                    "",
                    "- "
                    + "；".join(
                        f"{label}：{md(valuation.get(key), 100)}"
                        for key, label in (
                            ("price", "价格"),
                            ("timestamp", "时间戳"),
                            ("currency", "币种"),
                            ("pe", "P/E"),
                            ("fcf_yield", "FCF Yield"),
                            ("historical_percentile", "历史百分位"),
                            ("reverse_dcf_implied_growth", "Reverse DCF 隐含增长"),
                        )
                        if valuation.get(key) not in (None, "")
                    ),
                ]
            )

        analysis = summary.get("analysis", {})
        claims = analysis.get("claims", {}) if isinstance(analysis, Mapping) else {}
        agent_claims = (
            analysis.get("agent_output_claims", {})
            if isinstance(analysis, Mapping)
            else {}
        )
        if isinstance(claims, Mapping):
            lines.extend(
                [
                    "",
                    "## Analysis 与报告",
                    "",
                    f"- 通过 Claim Gate Claims：{md(claims.get('total'), 40)}（财务 {md(claims.get('financial'), 40)}；风险 {md(claims.get('risk'), 40)}；估值 {md(claims.get('valuation'), 40)}）",
                    f"- 报告：{'已生成' if summary.get('report', {}).get('generated') else '未生成'}",
                ]
            )
            if isinstance(agent_claims, Mapping) and agent_claims:
                lines.append(
                    "- Agent 输出 Claims："
                    f"财务 {md(agent_claims.get('financial'), 40)} / "
                    f"风险 {md(agent_claims.get('risk'), 40)} / "
                    f"估值 {md(agent_claims.get('valuation'), 40)}"
                )

        lines.extend(["", "## 时间线", ""])
        if not self.events:
            lines.append("- 无阶段事件")
        else:
            for event in self.events:
                lines.extend(
                    [
                        f"### {event.step}. {md(event.title, 80)} · {md(event.status, 40)}",
                        f"- 执行者：{md(event.actor)}；输入：{md(event.input_summary)}；输出：{md(event.output_summary)}",
                        f"- 决策：{md(event.decision)}；原因：{md(event.reason)}；下一节点：{md(event.next_step)}",
                    ]
                )
        lines.extend(
            [
                "",
                "## 运行时间",
                "",
                f"- 开始：`{md(started_at.isoformat(), 80)}`",
                f"- 结束：`{md(finished_at.isoformat(), 80)}`",
            ]
        )
        return strip_ansi("\n".join(lines).rstrip() + "\n")

    def finalize(
        self,
        *,
        result: Mapping[str, Any],
        output_path: Path,
        result_path: Path,
        started_at: datetime,
        finished_at: datetime,
        exit_code: int,
    ) -> None:
        """输出最终终端框并写入摘要 Markdown 与完整 JSON 结果。"""

        summary = summarize_result(result)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            self._markdown(summary, result_path.name, started_at, finished_at, exit_code),
            encoding="utf-8",
        )
        result_path.write_text(
            json.dumps(_json_ready(result), ensure_ascii=False, indent=2, allow_nan=False)
            + "\n",
            encoding="utf-8",
        )
        try:
            self._render_final(summary)
        except (Exception, SystemExit):
            return
