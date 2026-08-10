"""StockCrewAI 的 CLI/入口兼容层。

研究流程和 JSON-safe 状态由 stockcrewai.flow 唯一实现；本模块只保留
命令行入口、输出封装、run_research 依赖注入契约以及历史兼容导出。
"""

# This module intentionally re-exports the legacy public surface.
# ruff: noqa: F401

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any

import stockcrewai.pipeline_support as pipeline_support
from stockcrewai.crews.analysis.crew import AnalysisCrew  # noqa: F401
from stockcrewai.crews.request_parser.crew import RequestParserCrew  # noqa: F401
from stockcrewai.flow import (
    ResearchFlow,
    ResearchFlowState,
    _summary_claim_counts,
    _summary_count,
    _summary_metric,
    _summary_risk_sections,
    _summary_value,
)
from stockcrewai.models.policy import PolicyDecision
from stockcrewai.models.profile import ProfileResult
from stockcrewai.pipeline_support import (
    DEFAULT_REQUEST,
    ANALYSIS_DOMAIN_RULES,  # noqa: F401
    AnalysisClaim,  # noqa: F401
    AnalysisTaskOutput,  # noqa: F401
    Claim,  # noqa: F401
    ClaimSchema,  # noqa: F401
    _NoopTaskOutputStorageHandler,  # noqa: F401
    _analysis_diagnostic,  # noqa: F401
    _analysis_gate,
    _blocked_analysis_result,
    _calculation_facts,  # noqa: F401
    _configure_crewai_runtime,
    _crew_instance,  # noqa: F401
    _crew_output,  # noqa: F401
    _deterministic_verdict,
    _edgar_error,
    _filter_analysis_claims,
    _filter_analysis_claims_with_diagnostics,
    _financial_analysis_input,  # noqa: F401
    _first_value,  # noqa: F401
    _historical_financial_snapshots,  # noqa: F401
    _historical_prices,  # noqa: F401
    _input_requirements,
    _json_safe,
    _market_price_kwargs,  # noqa: F401
    _parser_payload,  # noqa: F401
    _risk_analysis_input,  # noqa: F401
    _reverse_dcf_inputs,  # noqa: F401
    _sensitive_environment_values,  # noqa: F401
    _synchronized_outputs,  # noqa: F401
    _valuation_analysis_input,
    _valuation_facts,  # noqa: F401
    _validated_state,
    _verdict_risk_input,  # noqa: F401
    _with_validation_status,  # noqa: F401
    build_deterministic_valuation_claims,  # noqa: F401
    build_profile_policy_context,  # noqa: F401
    profile_metadata_from_edgar,  # noqa: F401
    run_request_parser,
    sync_validation_status,  # noqa: F401
    validate_claim,  # noqa: F401
)
from stockcrewai.reporting.context import (
    ReportContext,  # noqa: F401
    ReportMetric,  # noqa: F401
    build_report_context,  # noqa: F401
)
from stockcrewai.reporting.renderer import (
    build_deterministic_report_draft,  # noqa: F401
    build_narrative_context,  # noqa: F401
    render_validated_report,  # noqa: F401
)
from stockcrewai.reporting.validator import (
    REPORT_DRAFT_FIELDS,  # noqa: F401
    REPORT_ERROR_CODES,  # noqa: F401
    ReportDraft,  # noqa: F401
    ReportDraftError,  # noqa: F401
    parse_report_draft,  # noqa: F401
    validate_rendered_report,  # noqa: F401
    validate_report_draft,  # noqa: F401
    validate_report_output,  # noqa: F401
)
from stockcrewai.crews.report.crew import ReportCrew  # noqa: F401
from stockcrewai.run_output import CompactRunReporter, RunStageEvent, sanitize_text
from stockcrewai.tools.calculator_tool import FinancialCalculatorTool  # noqa: F401
from stockcrewai.tools.edgar_tool import EdgarError, EdgarResult, EdgarTool  # noqa: F401
from stockcrewai.tools.historical_valuation_tool import HistoricalValuationTool  # noqa: F401
from stockcrewai.tools.market_price_tool import MarketPriceTool  # noqa: F401
from stockcrewai.tools.reverse_dcf_tool import ReverseDCFTool  # noqa: F401
from stockcrewai.tools.validation_tool import FinancialValidationTool  # noqa: F401
from stockcrewai.tools.valuation_tool import (  # noqa: F401
    ValuationTool,
    _market_price_evidence_id,
)


def run_research(
    request: str = DEFAULT_REQUEST,
    edgar_tool: EdgarTool | None = None,
    calculator_tool: FinancialCalculatorTool | None = None,
    validation_tool: FinancialValidationTool | None = None,
    valuation_tool: ValuationTool | None = None,
    market_price_data: Mapping[str, Any] | Any | None = None,
    market_price_tool: MarketPriceTool | Any | None = None,
    analysis_crew: Any | None = None,
    report_crew: Any | None = None,
    historical_valuation_tool: HistoricalValuationTool | Any | None = None,
    reverse_dcf_tool: ReverseDCFTool | Any | None = None,
    ttm_builder_tool: Any | None = None,
    progress_callback: Any | None = None,
    profile: Mapping[str, Any] | None = None,
):
    """以完整的 CrewAI 原生 Flow 保持旧 run_research 调用契约。"""
    flow_kwargs = {
        "edgar_tool": edgar_tool,
        "calculator_tool": calculator_tool,
        "validation_tool": validation_tool,
        "valuation_tool": valuation_tool,
        "market_price_data": market_price_data,
        "market_price_tool": market_price_tool,
        "analysis_crew": analysis_crew,
        "report_crew": report_crew,
        "historical_valuation_tool": historical_valuation_tool,
        "reverse_dcf_tool": reverse_dcf_tool,
        "ttm_builder_tool": ttm_builder_tool,
    }
    if progress_callback is not None:
        flow_kwargs["progress_callback"] = progress_callback
    profile_payload = _json_safe(profile) if profile is not None else {}
    if not isinstance(profile_payload, dict):
        raise TypeError("profile 必须是 JSON-safe 映射")
    flow_inputs: dict[str, Any] = {"request": request}
    if profile is not None:
        flow_kwargs["profile"] = profile_payload
        flow_inputs["profile"] = profile_payload
    flow = ResearchFlow(**flow_kwargs)
    result = _json_safe(flow.kickoff(inputs=flow_inputs))
    if not isinstance(result, dict):
        return result

    flow_state_fields = {
        "parsed_request",
        "input_requirements",
        "edgar",
        "calculations",
        "validation",
        "ttm",
        "valuation",
        "analysis",
        "status",
        "stage",
        "required_data",
    }
    if not flow_state_fields.issubset(result):
        return result

    deterministic_keys = (
        "parsed_request",
        "input_requirements",
        "edgar",
        "calculations",
        "validation",
        "market_price_data",
        "valuation",
        "historical_valuation",
        "reverse_dcf",
        "ttm",
    )
    deterministic_outputs = {key: result.get(key) for key in deterministic_keys}
    if result.get("profile"):
        deterministic_outputs["profile"] = result["profile"]
    if result.get("policy_context"):
        deterministic_outputs["policy_context"] = result["policy_context"]

    if result.get("required_data") == ["invalid_parser_output"]:
        parsed_request = result.get("parsed_request")
        raw = parsed_request.get("raw", "") if isinstance(parsed_request, dict) else ""
        input_requirements = result.get("input_requirements")
        message = (
            input_requirements.get("message", "请求解析结果无法转换为 JSON")
            if isinstance(input_requirements, dict)
            else "请求解析结果无法转换为 JSON"
        )
        edgar_result = _edgar_error("invalid_parser_output", message)
        return {
            "parsed_request": {"raw": raw},
            "edgar": _json_safe(edgar_result.model_dump(mode="json")),
        }

    if result.get("status") == "blocked":
        report_errors = result.get("required_data")
        if (
            isinstance(report_errors, list)
            and len(report_errors) == 1
            and str(report_errors[0]).startswith("report_")
        ):
            output = {
                **deterministic_outputs,
                "status": "blocked",
                "stage": "report",
                "analysis": result.get("analysis"),
                "verdict": result.get("verdict"),
                "report": None,
                "required_data": report_errors,
                "next_action": "修正报告输出后重新运行",
            }
            if result.get("analysis_diagnostics"):
                output["analysis_diagnostics"] = result["analysis_diagnostics"]
            return output
        return _blocked_analysis_result(
            deterministic_outputs,
            list(result.get("required_data", [])),
            result.get("analysis_diagnostics") or None,
        )

    output = {
        **deterministic_outputs,
        "status": result.get("status"),
        "stage": result.get("stage"),
        "analysis": result.get("analysis"),
        "verdict": result.get("verdict"),
        "report": result.get("report"),
    }
    if result.get("analysis_diagnostics"):
        output["analysis_diagnostics"] = result["analysis_diagnostics"]
    return output


def main(request: str | None = None) -> None:
    """解析请求来源，打印完整 JSON，并保持旧入口的 None 返回契约。"""
    if request is None:
        request = os.getenv("STOCKCREWAI_REQUEST", "").strip()
        if not request:
            request = " ".join(sys.argv[1:]).strip() or DEFAULT_REQUEST
    result = run_research(request)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def kickoff(
    request: str | None = None,
    output_path: Path | None = None,
) -> int | None:
    """运行完整 ResearchFlow，并输出紧凑摘要及独立完整 JSON 文件。"""
    output_path = output_path or Path("run-output.md")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result_path = output_path.with_name("run-result.json")
    started_at = datetime.now().astimezone()
    return_code = 0

    reporter = CompactRunReporter(sys.stdout)
    captured = StringIO()
    result: Mapping[str, Any]
    error_message = ""
    error_type = ""
    resolved_request = request
    if resolved_request is None:
        resolved_request = os.getenv("STOCKCREWAI_REQUEST", "").strip()
        if not resolved_request:
            resolved_request = " ".join(sys.argv[1:]).strip() or DEFAULT_REQUEST
    try:
        with redirect_stdout(captured), redirect_stderr(captured):
            result = run_research(
                resolved_request,
                progress_callback=reporter.emit,
            )
        if not isinstance(result, Mapping):
            result = {
                "status": "error",
                "error": {
                    "type": "InvalidResult",
                    "message": "结果不是 JSON 对象",
                },
            }
            return_code = 1
    except Exception as exc:
        error_type = type(exc).__name__
        error_message = sanitize_text(str(exc)) or "未提供错误消息"
        result = {
            "status": "error",
            "stage": "runtime",
            "error": {"type": error_type, "message": error_message},
        }
        return_code = 1
        try:
            reporter.emit(
                RunStageEvent(
                    step=7,
                    title="ERROR",
                    actor="Python Runtime",
                    status="error",
                    output_summary=f"{error_type}: {error_message}",
                    decision="ERROR",
                    reason=error_message,
                    next_step="结束",
                )
            )
        except Exception:
            pass
    finished_at = datetime.now().astimezone()
    reporter.finalize(
        result=_json_safe(result),
        output_path=output_path,
        result_path=result_path,
        started_at=started_at,
        finished_at=finished_at,
        exit_code=return_code,
    )
    return return_code or None


def plot() -> None:
    """按项目约定生成命名为 stockcrewai_flow 的 Flow 图。"""
    plot_path = ResearchFlow().plot("stockcrewai_flow")
    if isinstance(plot_path, (str, Path)):
        source_path = Path(plot_path)
        target_path = Path("stockcrewai_flow.html")
        if source_path.resolve() != target_path.resolve():
            shutil.copyfile(source_path, target_path)
        for suffix, target_name in (
            ("_style.css", "stockcrewai_flow_style.css"),
            ("_script.js", "stockcrewai_flow_script.js"),
        ):
            companion_path = source_path.with_name(source_path.stem + suffix)
            companion_target = Path(target_name)
            if (
                companion_path.is_file()
                and companion_path.resolve() != companion_target.resolve()
            ):
                shutil.copyfile(companion_path, companion_target)


def cli(output_path: Path | None = None) -> int | None:
    """保留旧 CLI 函数名，并薄包装到完整 kickoff 入口。"""
    return kickoff(output_path=output_path)


if __name__ == "__main__":
    raise SystemExit(cli())
