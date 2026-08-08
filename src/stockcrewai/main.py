"""StockCrewAI 的主入口与唯一原生研究 Flow。

本模块只负责展示和编排研究事件：请求解析、SEC 取证、确定性计算、验证、
估值、Analysis Gate、Claim Gate、Verdict 和报告分别调用现有工具、Crew
以及 ``pipeline_support`` 中的边界适配器。所有可持久化状态都是 JSON-safe
数据，工具、Crew、原始模型输出和其他不可序列化依赖只保存在 Flow 的
``PrivateAttr`` 中，不让 SQLite state 承载运行时对象。
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

from crewai.flow.flow import Flow, listen, router, start
from crewai.flow.persistence import persist
from pydantic import BaseModel, Field, PrivateAttr

import stockcrewai.pipeline_support as pipeline_support
from stockcrewai.crews.analysis.crew import AnalysisCrew
from stockcrewai.crews.report.crew import (
    ReportCrew,
    ReportDraft,
    build_deterministic_report_draft,
    build_report_context,
    parse_report_draft,
    render_validated_report,
    validate_rendered_report,
)
from stockcrewai.crews.request_parser.crew import RequestParserCrew
from stockcrewai.pipeline_support import (
    DEFAULT_REQUEST,
    _NoopTaskOutputStorageHandler,
    _analysis_gate,
    _blocked_analysis_result,
    _calculation_facts,
    _configure_crewai_runtime,
    _crew_instance,
    _crew_output,
    _deterministic_verdict,
    _edgar_error,
    _first_value,
    _filter_analysis_claims,
    _filter_analysis_claims_with_diagnostics,
    _financial_analysis_input,
    _historical_financial_snapshots,
    _historical_prices,
    _input_requirements,
    _json_safe,
    _market_price_kwargs,
    _parser_payload,
    _risk_analysis_input,
    _reverse_dcf_inputs,
    _synchronized_outputs,
    _valuation_analysis_input,
    _valuation_facts,
    _validated_state,
    _verdict_risk_input,
    _with_validation_status,
    build_deterministic_valuation_claims,
    run_request_parser,
    sync_validation_status,
)
from stockcrewai.run_output import CompactRunReporter, RunStageEvent, sanitize_text
from stockcrewai.tools.calculator_tool import FinancialCalculatorTool
from stockcrewai.tools.edgar_tool import EdgarError, EdgarResult, EdgarTool
from stockcrewai.tools.historical_valuation_tool import HistoricalValuationTool
from stockcrewai.tools.market_price_tool import MarketPriceTool
from stockcrewai.tools.reverse_dcf_tool import ReverseDCFTool
from stockcrewai.tools.validation_tool import FinancialValidationTool
from stockcrewai.tools.valuation_tool import (
    ValuationTool,
    _market_price_evidence_id,
)


def _summary_value(
    *mappings: Any,
    keys: tuple[str, ...],
    default: str = "unavailable",
) -> str:
    """从多个 JSON-safe 映射中读取一个有限长度的展示值。"""
    for candidate in mappings:
        if not isinstance(candidate, Mapping):
            continue
        for key in keys:
            value = candidate.get(key)
            if value in (None, "") or isinstance(value, (Mapping, list, tuple, set)):
                continue
            text = " ".join(str(value).split())
            if text:
                return text[:120]
    return default


def _summary_count(value: Any, nested_key: str | None = None) -> int:
    """统计摘要所需的容器数量，不展开其中的证据或原始内容。"""
    if nested_key and isinstance(value, Mapping):
        value = value.get(nested_key)
    if isinstance(value, Mapping | list | tuple | set):
        return len(value)
    return 0


def _summary_risk_sections(filings: Any) -> int:
    """统计 filing 风险章节数量而不暴露章节文本和 Evidence ID。"""
    if not isinstance(filings, list | tuple):
        return 0
    return sum(
        _summary_count(filing.get("risk_sections"))
        for filing in filings
        if isinstance(filing, Mapping)
    )


def _summary_claim_counts(analysis: Any) -> dict[str, int]:
    """按财务、风险和估值域统计 Claim 数量。"""
    counts = {"total": 0, "financial": 0, "risk": 0, "valuation": 0}
    claims: Any = analysis
    if isinstance(analysis, Mapping):
        claims = analysis.get("claims", [])
    if not isinstance(claims, list | tuple):
        return counts
    for claim in claims:
        if not isinstance(claim, Mapping):
            continue
        domain = str(claim.get("category") or claim.get("domain") or "").lower()
        counts["total"] += 1
        if "reverse_dcf" in domain:
            counts["valuation"] += 1
            continue
        for name in ("financial", "risk", "valuation"):
            if name in domain:
                counts[name] += 1
                break
    return counts


def _summary_metric(valuation: Any, formula_id: str) -> str:
    """从估值计算结果中提取一个指标值，缺失时统一返回 unavailable。"""
    if not isinstance(valuation, Mapping):
        return "unavailable"
    direct = _summary_value(
        valuation,
        keys=(formula_id, formula_id.removesuffix("_ratio")),
    )
    if direct != "unavailable":
        return direct
    calculations = valuation.get("calculations")
    if not isinstance(calculations, list | tuple):
        return "unavailable"
    for calculation in calculations:
        if not isinstance(calculation, Mapping):
            continue
        if calculation.get("formula_id") != formula_id:
            continue
        return _summary_value(
            calculation,
            keys=("display_result", "normalized_result", "raw_result"),
        )
    return "unavailable"


class ResearchFlowState(BaseModel):
    """定义一次研究请求在 Flow 和 SQLite 中保存的 JSON-safe 状态。

    状态字段覆盖请求解析、SEC 证据、确定性计算、验证、市场价格、三类
    估值、Analysis Claims、Verdict、报告以及两个 Gate 的控制信息。所有
    字典和列表都使用 ``Field(default_factory=...)``，确保不同 Flow 实例
    之间不共享可变默认值。工具实例、Crew 实例、原始 Pydantic 对象、LLM
    配置和凭据不属于本模型；它们由 ``ResearchFlow`` 的私有属性保存，
    因而不会被持久化或出现在最终 JSON 中。
    """

    request: str = ""
    parsed_request: dict[str, Any] = Field(default_factory=dict)
    input_requirements: dict[str, Any] = Field(default_factory=dict)
    edgar: dict[str, Any] = Field(default_factory=dict)
    facts: dict[str, Any] = Field(default_factory=dict)
    filings: list[dict[str, Any]] = Field(default_factory=list)
    calculations: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    market_price_data: dict[str, Any] | None = Field(default_factory=dict)
    valuation: dict[str, Any] = Field(default_factory=dict)
    historical_valuation: dict[str, Any] = Field(default_factory=dict)
    reverse_dcf: dict[str, Any] = Field(default_factory=dict)
    ttm: dict[str, Any] = Field(default_factory=dict)
    analysis: list[dict[str, Any]] = Field(default_factory=list)
    analysis_attempts: int = 0
    analysis_diagnostics: dict[str, Any] = Field(default_factory=dict)
    verdict: dict[str, Any] | None = None
    report: Any = None
    status: str = "pending"
    stage: str = "request"
    required_data: list[str] = Field(default_factory=list)


@persist()
class ResearchFlow(Flow[ResearchFlowState]):
    """按固定事件链编排一次可审计的股票研究请求。

    唯一无条件入口是 ``parse_request``。请求解析之后，``@listen`` 依次
    连接证据准备和估值准备；两个 ``@router`` 只返回稳定标签，分别控制
    Analysis Gate 和 Claim Gate 的成功、阻断分支。Python 工具和
    ``pipeline_support`` 负责来源选择、计算、验证、路由与 Verdict，
    Analysis Crew 只解释已验证输入，Report Crew 只在 Claims 通过后运行。

    构造函数支持注入工具、Crew 和离线市场价格，所有注入值都会在交给
    ``Flow`` 父类前移出输入数据并保存到 ``PrivateAttr``。因此这些对象
    不会进入 ``ResearchFlowState``、SQLite persistence 或 Flow 返回结果。
    """

    suppress_flow_events: bool = True

    _DEPENDENCY_NAMES: ClassVar[tuple[str, ...]] = (
        "edgar_tool",
        "calculator_tool",
        "validation_tool",
        "valuation_tool",
        "market_price_tool",
        "historical_valuation_tool",
        "reverse_dcf_tool",
        "ttm_builder_tool",
        "analysis_crew",
        "report_crew",
        "market_price_data",
        "progress_callback",
    )

    _edgar_tool: Any = PrivateAttr(default=None)
    _calculator_tool: Any = PrivateAttr(default=None)
    _validation_tool: Any = PrivateAttr(default=None)
    _valuation_tool: Any = PrivateAttr(default=None)
    _market_price_tool: Any = PrivateAttr(default=None)
    _historical_valuation_tool: Any = PrivateAttr(default=None)
    _reverse_dcf_tool: Any = PrivateAttr(default=None)
    _ttm_builder_tool: Any = PrivateAttr(default=None)
    _analysis_crew: Any = PrivateAttr(default=None)
    _report_crew: Any = PrivateAttr(default=None)
    _market_price_data: Any = PrivateAttr(default=None)

    _parser_result: Any = PrivateAttr(default=None)
    _parser_failed: bool = PrivateAttr(default=False)
    _edgar_result: Any = PrivateAttr(default=None)
    _calculation_result: Any = PrivateAttr(default=None)
    _validation_result: Any = PrivateAttr(default=None)
    _pipeline_state: dict[str, Any] = PrivateAttr(default_factory=dict)
    _risk_input: dict[str, Any] = PrivateAttr(default_factory=dict)
    _analysis_inputs: dict[str, Any] = PrivateAttr(default_factory=dict)
    _trusted_valuation_evidence_ids: set[str] = PrivateAttr(default_factory=set)
    _valuation_analysis_input: dict[str, Any] = PrivateAttr(default_factory=dict)
    _analysis_result: Any = PrivateAttr(default=None)
    _analysis_diagnostics: dict[str, Any] = PrivateAttr(default_factory=dict)
    _historical_financial_snapshots: list[dict[str, Any]] = PrivateAttr(
        default_factory=list
    )
    _progress_callback: Any = PrivateAttr(default=None)

    def __init__(self, **data: Any) -> None:
        """提取运行时依赖并初始化不含私有对象的 Flow state。

        ``data`` 可以包含 CrewAI Flow 自身支持的配置，也可以包含工具、
        Crew 或预先获取的 ``market_price_data``。本方法只负责把这些依赖
        移到私有属性，不执行研究节点、不调用外部服务，也不改变业务输入。
        剩余数据交给 ``Flow``/Pydantic 父类处理，保证 SQLite 只看到
        ``ResearchFlowState`` 的 JSON-safe 字段。
        """
        dependencies = {
            name: data.pop(name, None) for name in self._DEPENDENCY_NAMES
        }
        super().__init__(**data)
        for name, dependency in dependencies.items():
            setattr(self, f"_{name}", dependency)

    def _emit_stage(self, event: RunStageEvent) -> None:
        """向可选进度回调发送一个不可变的阶段摘要事件。"""
        if callable(self._progress_callback):
            try:
                self._progress_callback(event)
            except Exception:
                self._progress_callback = None

    def _stage_snapshot(self) -> dict[str, Any]:
        """从当前 state 生成不含原始对象和 ID 列表的阶段摘要字段。"""
        parsed = self.state.parsed_request
        market = self.state.market_price_data
        valuation = self.state.valuation
        historical = self.state.historical_valuation
        reverse_dcf = self.state.reverse_dcf
        validation = _summary_value(
            self.state.validation,
            keys=("status", "validation_status"),
        )
        validation_mapping = self.state.validation
        validated_evidence = _summary_count(
            validation_mapping.get("validated_evidence_ids")
            if isinstance(validation_mapping, Mapping)
            else None
        )
        validated_calculations = _summary_count(
            validation_mapping.get("validated_calculation_ids")
            if isinstance(validation_mapping, Mapping)
            else None
        )
        ttm_metrics = (
            self.state.ttm.get("metrics", [])
            if isinstance(self.state.ttm, Mapping)
            else []
        )
        if isinstance(ttm_metrics, Mapping):
            ttm_metrics = list(ttm_metrics.values())
        ttm_metrics = ttm_metrics if isinstance(ttm_metrics, list | tuple) else []
        ttm_available = sum(
            1
            for metric in ttm_metrics
            if isinstance(metric, Mapping)
            and metric.get("status") == "available"
            and metric.get("validation_status") == "valid"
        )
        return {
            "identity": (
                f"company={_summary_value(parsed, keys=('company_name_guess', 'company_name', 'company'))}; "
                f"ticker={_summary_value(parsed, keys=('ticker_guess', 'ticker', 'symbol'))}; "
                f"period={_summary_value(parsed, keys=('investment_horizon', 'period', 'horizon_years', 'term'))}"
            ),
            "facts": _summary_count(self.state.facts),
            "filings": _summary_count(self.state.filings),
            "risk_sections": _summary_risk_sections(self.state.filings),
            "calculations": _summary_count(self.state.calculations, "calculations"),
            "validation": validation,
            "validated_evidence": validated_evidence,
            "validated_calculations": validated_calculations,
            "ttm_available": ttm_available,
            "ttm_total": len(ttm_metrics),
            "price": _summary_value(
                market,
                valuation,
                keys=("market_price", "price", "current_price", "close"),
            ),
            "timestamp": _summary_value(
                market,
                valuation,
                keys=("price_timestamp", "timestamp", "as_of"),
            ),
            "currency": _summary_value(
                market,
                valuation,
                keys=("currency", "currency_code"),
            ),
            "pe": _summary_metric(valuation, "pe_ratio"),
            "fcf_yield": _summary_metric(valuation, "fcf_yield"),
            "historical_percentile": _summary_value(
                historical,
                keys=("current_percentile", "percentile", "historical_percentile"),
            ),
            "reverse_dcf_growth": _summary_value(
                reverse_dcf,
                keys=("implied_growth", "implied_growth_rate", "growth_rate"),
            ),
            "claims": _summary_claim_counts(self.state.analysis),
        }

    def _gate_summary(self) -> dict[str, str]:
        """生成 Gate 的 READY/BLOCKED、域、原因和缺失数据摘要。"""
        diagnostics = self.state.analysis_diagnostics
        required_data = list(self.state.required_data)
        domain = (
            _summary_value(diagnostics, keys=("domain",))
            if isinstance(diagnostics, Mapping)
            else "unavailable"
        )
        reason_code = (
            _summary_value(diagnostics, keys=("reason_code",))
            if isinstance(diagnostics, Mapping)
            else "unavailable"
        )
        if domain == "unavailable" and required_data:
            domain = next(
                (
                    name
                    for name in ("financial", "risk", "valuation")
                    if any(name in item for item in required_data)
                ),
                "general",
            )
        if reason_code == "unavailable" and required_data:
            reason_code = required_data[0]
        return {
            "status": "BLOCKED" if self.state.status == "blocked" else "READY",
            "domain": domain,
            "reason_code": reason_code,
            "required_data": ", ".join(required_data) or "none",
        }

    @start()
    def parse_request(self) -> dict[str, Any]:
        """解析用户请求并写入请求阶段的结构化状态。

        节点只调用 ``pipeline_support.run_request_parser`` 获取原始 Crew
        输出，再用确定性的 ``_parser_payload`` 和 ``_input_requirements``
        转换为普通字典。解析成功时返回请求字典并把流程推进到 evidence
        阶段；解析失败时只写入稳定的 ``invalid_parser_output`` 阻断信息，
        不猜测公司身份，也不触发后续 SEC、市场或 Analysis 调用。
        原始 Crew 输出始终留在 ``PrivateAttr``，不会写入 state。
        """
        self.state.request = str(self.state.request or "")
        self._parser_result = None
        self._parser_failed = False
        self._analysis_diagnostics = {}
        try:
            self._parser_result = pipeline_support.run_request_parser(self.state.request)
            parsed_request = _parser_payload(self._parser_result)
        except (TypeError, ValueError) as exc:
            self._parser_failed = True
            self.state.parsed_request = {
                "raw": getattr(self._parser_result, "raw", ""),
            }
            self.state.input_requirements = {
                "status": "blocked",
                "missing": ["valid_parser_output"],
                "message": f"请求解析结果无法转换为 JSON：{exc}",
            }
            self.state.status = "blocked"
            self.state.stage = "request"
            self.state.required_data = ["invalid_parser_output"]
            self._emit_stage(
                RunStageEvent(
                    step=1,
                    title="请求解析",
                    actor="Crew/Agent：Request Parser Crew",
                    status="blocked",
                    input_summary="request=provided",
                    output_summary="公司/ticker/期限=unavailable",
                    decision="BLOCKED",
                    reason="reason_code=invalid_parser_output",
                    next_step="最终阻断",
                )
            )
            return {"status": "blocked", "required_data": list(self.state.required_data)}

        self.state.parsed_request = _json_safe(parsed_request)
        self.state.input_requirements = _json_safe(
            _input_requirements(parsed_request)
        )
        self.state.status = "running"
        self.state.stage = "evidence"
        self.state.required_data = []
        snapshot = self._stage_snapshot()
        self._emit_stage(
            RunStageEvent(
                step=1,
                title="请求解析",
                actor="Crew/Agent：Request Parser Crew",
                status="completed",
                input_summary="request=provided",
                output_summary=(
                    f"{snapshot['identity']}; focus={_summary_count(
                        parsed_request.get('requested_focus')
                        if parsed_request.get('requested_focus') is not None
                        else parsed_request.get('focus')
                    )}"
                ),
                next_step="SEC 证据与财务验证",
            )
        )
        return parsed_request

    @listen(parse_request)
    def prepare_evidence(self, parsed_request: Mapping[str, Any]) -> dict[str, Any]:
        """运行 SEC、计算和验证工具，并保存同步后的可审计证据状态。

        公司身份候选由 Python 的 ``_first_value`` 规则读取，SEC 查询参数
        不由 LLM 自由决定。节点调用注入或默认的 Edgar、Calculator、Validation
        工具，再使用 ``pipeline_support`` 的事实别名、验证状态同步和
        Evidence/Calculation 白名单构造内部状态。原始工具对象保存在私有
        属性，公开 state 只接收 JSON-safe 的 EDGAR、事实、filing、计算和
        validation 输出。请求解析已经阻断时，本节点只传播阻断，不调用工具。
        """
        if self._parser_failed:
            self.state.stage = "request"
            self._emit_stage(
                RunStageEvent(
                    step=2,
                    title="SEC 证据与财务验证",
                    actor="确定性工具：Edgar + Calculator + Validation",
                    status="blocked",
                    input_summary="公司/ticker/期限=unavailable",
                    output_summary="facts/filings/risk_sections/calculations/validation=unavailable",
                    decision="SKIPPED",
                    reason="上游 parse_request 已阻断",
                    next_step="最终阻断",
                )
            )
            return {}

        parsed_request = dict(parsed_request)
        company_name = _first_value(parsed_request.get("company_name_guess"))
        ticker = _first_value(parsed_request.get("ticker_guess"))
        if not company_name and not ticker:
            edgar_result = _edgar_error(
                "missing_company_identity",
                "解析结果缺少公司名称和 ticker，停止 EDGAR 查询。",
            )
        else:
            if self._edgar_tool is None:
                self._edgar_tool = EdgarTool()
            edgar_result = self._edgar_tool.run(
                company_name=company_name,
                ticker=ticker,
                include_filing_text=True,
            )

        calculation_facts = _calculation_facts(edgar_result)
        if self._calculator_tool is None:
            self._calculator_tool = FinancialCalculatorTool()
        calculation_result = self._calculator_tool.run(
            company_name=edgar_result.company_name or company_name,
            ticker=edgar_result.ticker or ticker,
            facts=calculation_facts,
        )

        if self._validation_tool is None:
            self._validation_tool = FinancialValidationTool()
        validation_result = self._validation_tool.run(
            company_name=edgar_result.company_name or company_name,
            ticker=edgar_result.ticker or ticker,
            facts=calculation_facts,
            calculations=calculation_result.calculations,
        )

        ttm_inputs = getattr(edgar_result, "ttm_inputs", {})
        ttm_inputs, ttm_evidence_validation = pipeline_support.validate_ttm_evidence(
            ttm_inputs,
            company_name=edgar_result.company_name or company_name,
            ticker=edgar_result.ticker or ticker,
            validation_tool=self._validation_tool,
        )
        if self._ttm_builder_tool is None:
            try:
                from stockcrewai.tools.ttm_tool import TTMBuilderTool
            except ModuleNotFoundError:
                ttm_result = pipeline_support._ttm_unavailable(
                    edgar_result.company_name or company_name,
                    edgar_result.ticker or ticker,
                    "ttm_builder_unavailable",
                )
            else:
                self._ttm_builder_tool = TTMBuilderTool()
        if self._ttm_builder_tool is not None:
            try:
                ttm_result = self._ttm_builder_tool.run(
                    company_name=edgar_result.company_name or company_name,
                    ticker=edgar_result.ticker or ticker,
                    metric_inputs=ttm_inputs,
                )
            except Exception:
                ttm_result = pipeline_support._ttm_unavailable(
                    edgar_result.company_name or company_name,
                    edgar_result.ticker or ticker,
                    "ttm_builder_error",
                )
        ttm_output = _json_safe(ttm_result)
        if not isinstance(ttm_output, dict):
            ttm_output = pipeline_support._ttm_unavailable(
                edgar_result.company_name or company_name,
                edgar_result.ticker or ticker,
                "ttm_output_invalid",
            )
        ttm_output["evidence_validation"] = _json_safe(ttm_evidence_validation)
        self.state.ttm = ttm_output

        edgar_output, calculation_output = _synchronized_outputs(
            edgar_result, calculation_result, validation_result
        )
        if getattr(validation_result, "status", None) == "valid":
            pipeline_state = _validated_state(
                edgar_result, calculation_result, validation_result
            )
        else:
            pipeline_state = {
                "company_name": edgar_result.company_name or company_name,
                "ticker": edgar_result.ticker or ticker,
                "validated_evidence_ids": [],
                "validated_calculation_ids": [],
                "validated_filing_ids": [],
                "facts": {},
                "calculations": [],
                "filings": [],
            }
        state_synced = sync_validation_status(
            pipeline_state.get("facts", {}),
            pipeline_state.get("calculations", []),
            validation_result,
        )
        pipeline_state["facts"] = state_synced["facts"]
        pipeline_state["calculations"] = state_synced["calculations"]

        validation_output = (
            validation_result.model_dump(mode="json")
            if hasattr(validation_result, "model_dump")
            else validation_result
        )
        self._edgar_result = edgar_result
        self._calculation_result = calculation_result
        self._validation_result = validation_result
        self._pipeline_state = _json_safe(pipeline_state)
        self._risk_input = _json_safe(
            _risk_analysis_input(edgar_result, self._pipeline_state)
        )

        self.state.edgar = _json_safe(edgar_output)
        self.state.facts = _json_safe(self._pipeline_state.get("facts", {}))
        self.state.filings = _json_safe(self._pipeline_state.get("filings", []))
        self.state.calculations = _json_safe(calculation_output)
        self.state.validation = _json_safe(validation_output)
        snapshot = self._stage_snapshot()
        self._emit_stage(
            RunStageEvent(
                step=2,
                title="SEC 证据与财务验证",
                actor="确定性工具：Edgar + Calculator + Validation",
                status="completed",
                input_summary=snapshot["identity"],
                output_summary=(
                    f"facts={snapshot['facts']}; filings={snapshot['filings']}; "
                    f"risk_sections={snapshot['risk_sections']}; calculations={snapshot['calculations']}; "
                    f"ttm={snapshot['ttm_available']}/{snapshot['ttm_total']}; "
                    f"validation={snapshot['validation']} "
                    f"({snapshot['validated_evidence']} evidence/{snapshot['validated_calculations']} calculations)"
                ),
                next_step="市场价格与估值",
            )
        )
        return self._pipeline_state

    @listen(prepare_evidence)
    def prepare_valuation(self, pipeline_state: Mapping[str, Any]) -> dict[str, Any]:
        """获取市场价格并运行当前、历史和反向 DCF 估值工具。

        估值事实来自 evidence 节点产生的已验证状态，并通过
        ``_valuation_facts``、``_market_price_kwargs`` 等支持函数适配为
        工具输入。市场价格可以由注入的 JSON-safe 数据提供；未注入时才
        创建 MarketPriceTool 并按 ticker 获取历史数据。当前估值、历史估值
        和反向 DCF 的 readiness 与 validation_status 仍由现有工具和确定性
        规则决定，不在 Flow 中补造假设。由基础验证、filing、历史快照、
        历史价格和市场价格来源组成的 auxiliary allowed Evidence ID 集合
        会保存为私有 trusted set，供后续估值输入构造使用。请求解析阻断时
        本节点不创建工具。
        """
        if self._parser_failed:
            self.state.stage = "request"
            self._emit_stage(
                RunStageEvent(
                    step=3,
                    title="市场价格与估值",
                    actor="确定性工具：Market Price + Valuation",
                    status="blocked",
                    input_summary="公司/ticker/期限=unavailable",
                    output_summary="price/timestamp/currency/PE/FCF Yield=unavailable",
                    decision="SKIPPED",
                    reason="上游 parse_request 已阻断",
                    next_step="最终阻断",
                )
            )
            return {}

        pipeline_state = dict(pipeline_state)
        market_price_data = self._market_price_data
        if market_price_data is None and pipeline_state.get("ticker"):
            if self._market_price_tool is None:
                self._market_price_tool = MarketPriceTool(include_history=True)
            fetched_market_price = self._market_price_tool.run(
                ticker=pipeline_state["ticker"]
            )
            market_price_data = (
                fetched_market_price.model_dump(mode="json")
                if hasattr(fetched_market_price, "model_dump")
                else fetched_market_price
            )
        self._market_price_data = _json_safe(market_price_data)

        if self._valuation_tool is None:
            self._valuation_tool = ValuationTool()
        valuation_result = self._valuation_tool.run(
            company_name=pipeline_state.get("company_name"),
            ticker=pipeline_state.get("ticker"),
            facts=_valuation_facts(pipeline_state),
            **_market_price_kwargs(market_price_data),
        )
        valuation = _json_safe(valuation_result)
        if not isinstance(valuation, dict):
            valuation = {"status": "not_ready", "readiness": "not_ready"}
        valuation["validation_status"] = (
            "valid"
            if valuation.get("readiness") == "ready"
            and bool(valuation.get("calculations"))
            and all(
                item.get("status") == "available"
                and item.get("validation_status") == "valid"
                for item in valuation.get("calculations", [])
                if isinstance(item, Mapping)
            )
            else "unvalidated"
        )

        historical_prices = _historical_prices(market_price_data)
        self._historical_financial_snapshots = _historical_financial_snapshots(
            self._edgar_result
        )
        auxiliary_allowed_ids = set(
            self._pipeline_state.get("validated_evidence_ids", [])
        )
        auxiliary_allowed_ids.update(
            self._pipeline_state.get("validated_filing_ids", [])
        )
        auxiliary_allowed_ids.update(
            str(snapshot["evidence_id"])
            for snapshot in self._historical_financial_snapshots
            if snapshot.get("evidence_id")
        )
        auxiliary_allowed_ids.update(
            str(price["evidence_id"])
            for price in historical_prices
            if price.get("evidence_id")
        )
        market_price_payload = (
            self._market_price_data
            if isinstance(self._market_price_data, Mapping)
            else {}
        )
        market_price_kwargs = _market_price_kwargs(market_price_payload)
        normalized_ticker = str(pipeline_state.get("ticker") or "").strip().upper()
        derived_market_price_evidence_id = _market_price_evidence_id(
            normalized_ticker or None,
            market_price_kwargs.get("market_price"),
            market_price_kwargs.get("price_timestamp"),
            market_price_kwargs.get("currency"),
            market_price_kwargs.get("source_reference"),
        )
        for market_price_evidence_id in (
            market_price_payload.get("market_price_evidence_id"),
            derived_market_price_evidence_id,
        ):
            if isinstance(market_price_evidence_id, str) and market_price_evidence_id:
                auxiliary_allowed_ids.add(market_price_evidence_id)
        self._trusted_valuation_evidence_ids = {
            item
            for item in _json_safe(auxiliary_allowed_ids)
            if isinstance(item, str) and item
        }

        if self._historical_valuation_tool is None:
            self._historical_valuation_tool = HistoricalValuationTool()
        historical_result = self._historical_valuation_tool.run(
            company_name=pipeline_state.get("company_name"),
            ticker=pipeline_state.get("ticker"),
            historical_prices=historical_prices,
            financial_snapshots=self._historical_financial_snapshots,
        )
        historical_valuation = _with_validation_status(
            historical_result,
            allowed_evidence_ids=auxiliary_allowed_ids,
            base_valid=getattr(self._validation_result, "status", None) == "valid",
        )

        if self._reverse_dcf_tool is None:
            self._reverse_dcf_tool = ReverseDCFTool()
        reverse_result = self._reverse_dcf_tool.run(
            company_name=pipeline_state.get("company_name"),
            ticker=pipeline_state.get("ticker"),
            **_reverse_dcf_inputs(pipeline_state, valuation),
        )
        reverse_dcf = _with_validation_status(
            reverse_result,
            allowed_evidence_ids=auxiliary_allowed_ids,
            base_valid=getattr(self._validation_result, "status", None) == "valid",
        )

        self.state.market_price_data = _json_safe(self._market_price_data)
        self.state.valuation = valuation
        self.state.historical_valuation = historical_valuation
        self.state.reverse_dcf = reverse_dcf
        self.state.stage = "analysis"
        snapshot = self._stage_snapshot()
        self._emit_stage(
            RunStageEvent(
                step=3,
                title="市场价格与估值",
                actor="确定性工具：Market Price + Valuation",
                status="completed",
                input_summary=snapshot["identity"],
                output_summary=(
                    f"price={snapshot['price']}; timestamp={snapshot['timestamp']}; "
                    f"currency={snapshot['currency']}; PE={snapshot['pe']}; "
                    f"FCF Yield={snapshot['fcf_yield']}; "
                    f"historical percentile={snapshot['historical_percentile']}; "
                    f"reverse DCF growth={snapshot['reverse_dcf_growth']}"
                ),
                next_step="Analysis Gate",
            )
        )
        return valuation

    @router(
        prepare_valuation,
        emit=["analysis_ready", "analysis_blocked"],
    )
    def route_analysis(self, valuation: Mapping[str, Any] | None = None) -> str:
        """执行确定性 Analysis Gate 并返回稳定的分析路由标签。

        Gate 同时检查已验证财务事实和计算、可用风险章节、当前估值、历史
        估值及反向 DCF。任一项缺失就写入 ``status='blocked'``、
        ``stage='analysis'`` 和机器可读的 ``required_data``，并返回
        ``analysis_blocked``；全部满足时清空旧阻断信息并返回
        ``analysis_ready``。本节点只负责调用支持模块、更新 state 和路由，
        不调用 Analysis、Verdict 或 Report Crew。
        """
        if self._parser_failed:
            gate = {
                "status": "blocked",
                "required_data": list(self.state.required_data)
                or ["invalid_parser_output"],
            }
        else:
            gate = _analysis_gate(
                self._validation_result,
                self._pipeline_state,
                self._risk_input,
                dict(valuation or self.state.valuation),
                self.state.historical_valuation,
                self.state.reverse_dcf,
            )
        if gate.get("status") == "blocked":
            self.state.status = "blocked"
            self.state.stage = "request" if self._parser_failed else "analysis"
            self.state.required_data = list(gate.get("required_data", []))
            self.state.analysis = []
            self.state.verdict = None
            self.state.report = None
            route = "analysis_blocked"
        else:
            self.state.status = "running"
            self.state.stage = "analysis"
            self.state.required_data = []
            route = "analysis_ready"
        snapshot = self._stage_snapshot()
        gate_summary = self._gate_summary()
        self._emit_stage(
            RunStageEvent(
                step=4,
                title="Analysis Gate",
                actor="Python Gate：Analysis Gate",
                status="blocked" if route == "analysis_blocked" else "completed",
                input_summary=(
                    f"{snapshot['identity']}; facts={snapshot['facts']}; "
                    f"filings={snapshot['filings']}; calculations={snapshot['calculations']}"
                ),
                output_summary=(
                    f"{gate_summary['status']}; domain={gate_summary['domain']}; "
                    f"reason_code={gate_summary['reason_code']}; "
                    f"required_data={gate_summary['required_data']}"
                ),
                decision=gate_summary["status"],
                reason=(
                    f"domain={gate_summary['domain']}; "
                    f"reason_code={gate_summary['reason_code']}"
                ),
                next_step=(
                    "最终阻断"
                    if route == "analysis_blocked"
                    else "Analysis Crew"
                ),
            )
        )
        return route

    @listen("analysis_blocked")
    def finalize_analysis_blocked(self) -> dict[str, Any]:
        """输出 Analysis Gate 阻断分支的确定性中间结果。

        该节点只读取已经写入 state 的 JSON-safe 数据，保留 SEC、验证和
        估值结果，明确保持 ``status='blocked'``、``stage='analysis'``、
        ``required_data`` 以及空的 Analysis/Report。由于它只由稳定标签
        触发，不会调用 Analysis Crew、Verdict 工具或 Report Crew，也不会
        为缺失数据创建补充文本。
        """
        gate_summary = self._gate_summary()
        self._emit_stage(
            RunStageEvent(
                step=7,
                title="最终阻断",
                actor="Python Gate：Analysis Gate",
                status="blocked",
                input_summary=f"domain={gate_summary['domain']}",
                output_summary=(
                    f"BLOCKED; reason_code={gate_summary['reason_code']}; "
                    f"required_data={gate_summary['required_data']}"
                ),
                decision="BLOCKED",
                reason="Analysis Gate 未通过",
                next_step="补齐 required_data 后重新运行",
            )
        )
        return self._flow_result()

    @listen("analysis_ready")
    def run_analysis(self) -> Any:
        """把两个 LLM 输入和一项确定性估值 Claims 交给 Claim Gate。

        财务和风险输入分别由 ``pipeline_support`` 构造并交给 Analysis
        Crew；估值输入同样构造，但只由 Python 的确定性构建器生成第三项
        Claims。三个输出按 financial/risk/valuation 顺序组合后，仍由
        Claim Gate 解析和校验，不直接写入 SQLite state。只有财务或风险
        Claims 为空时才对两个 LLM 输入做一次带 ``retry_notice`` 的重试；
        确定性估值 Claims 为空时直接 fail closed，不重跑 LLM。估值输入的
        Evidence allowlist 来自 ``prepare_valuation`` 保存的 trusted set，
        Calculation allowlist 来自固定注册表与基础已验证计算集合。
        """
        financial_input = _financial_analysis_input(self._pipeline_state)
        valuation_input = _valuation_analysis_input(
            self._pipeline_state,
            self.state.valuation,
            self.state.historical_valuation,
            self.state.reverse_dcf,
            trusted_evidence_ids=self._trusted_valuation_evidence_ids,
        )
        valuation_claims = build_deterministic_valuation_claims(valuation_input)
        self._analysis_inputs = {
            "financial_analysis_input": financial_input,
            "risk_analysis_input": self._risk_input,
        }
        self._valuation_analysis_input = valuation_input
        analysis_crew = _crew_instance(self._analysis_crew, AnalysisCrew)
        self.state.analysis_attempts = 1
        raw_analysis_result = analysis_crew.kickoff(inputs=self._analysis_inputs)
        agent_task_count = _summary_count(
            getattr(raw_analysis_result, "tasks_output", None)
        )

        def with_deterministic_valuation_claims(result: Any) -> Any:
            """把 Python 估值 Claims 作为 Claim Gate 的第三项任务输出。"""
            task_outputs = getattr(result, "tasks_output", None)
            if not isinstance(task_outputs, (list, tuple)):
                task_outputs = ()
            valuation_task_output = SimpleNamespace(
                raw=json.dumps(
                    {"claims": valuation_claims},
                    ensure_ascii=False,
                )
            )
            return SimpleNamespace(
                raw=getattr(result, "raw", None),
                json_dict=getattr(result, "json_dict", None),
                pydantic=getattr(result, "pydantic", None),
                tasks_output=[*task_outputs, valuation_task_output],
            )

        self._analysis_result = with_deterministic_valuation_claims(
            raw_analysis_result
        )
        _, required_data, diagnostics = _filter_analysis_claims_with_diagnostics(
            self._analysis_result,
            list(financial_input.get("validated_evidence_ids", [])),
            list(self._risk_input.get("validated_filing_ids", [])),
            list(valuation_input.get("validated_evidence_ids", [])),
            list(valuation_input.get("validated_calculation_ids", [])),
        )
        retryable_claim_empty = (
            diagnostics is not None
            and diagnostics.get("reason_code") == "claims_empty"
            and bool(required_data)
            and all(
                code in {
                    "financial_analysis_claims_required",
                    "risk_analysis_claims_required",
                }
                for code in required_data
            )
        )
        if retryable_claim_empty:
            retry_notice = (
                "这是对同一份已验证输入的唯一一次重试：请重新检查已有输入，"
                "不要沿用空 claims 结果；不得编造任何事实、风险、Evidence ID 或 Calculation ID。"
            )
            retry_inputs = {
                key: {**payload, "retry_notice": retry_notice}
                for key, payload in self._analysis_inputs.items()
            }
            self.state.analysis_attempts = 2
            raw_analysis_result = analysis_crew.kickoff(inputs=retry_inputs)
            agent_task_count = _summary_count(
                getattr(raw_analysis_result, "tasks_output", None)
            )
            self._analysis_result = with_deterministic_valuation_claims(
                raw_analysis_result
            )
        snapshot = self._stage_snapshot()
        self._emit_stage(
            RunStageEvent(
                step=5,
                title="Analysis Crew",
                actor="Crew/Agent：Analysis Crew",
                status="completed",
                input_summary=(
                    f"{snapshot['identity']}; financial/risk=validated input; "
                    "valuation=deterministic builder"
                ),
                output_summary=(
                    f"agent_tasks={agent_task_count}; "
                    f"deterministic_valuation_claims={len(valuation_claims)}; "
                    f"attempts={self.state.analysis_attempts}; "
                    "Claims=awaiting Claim Gate; "
                    f"facts={snapshot['facts']}; calculations={snapshot['calculations']}"
                ),
                next_step="Claim Gate",
            )
        )
        return self._analysis_result

    @router(
        run_analysis,
        emit=["claims_ready", "claims_blocked"],
    )
    def route_claims(self, analysis_result: Any) -> str:
        """通过确定性 Claim Gate 并返回报告路由标签。

        本节点按固定的财务、风险、估值顺序解析两个 Agent Task 和一个
        确定性估值输出，使用
        上游已验证 Evidence/Calculation ID 白名单检查 Claim schema、类别、
        引用和必需覆盖范围。通过时把过滤后的 Claims 写入 state 并返回
        ``claims_ready``；任何失败都写入脱敏诊断、``analysis_output_invalid``
        或其他稳定缺失码，清空 Analysis/Report 并返回 ``claims_blocked``。
        节点不调用 Verdict 或 Report Crew，不让 Agent 自己决定路由。
        """
        financial_input = self._analysis_inputs.get("financial_analysis_input", {})
        valuation_input = self._valuation_analysis_input
        claims, required_data, diagnostics = _filter_analysis_claims_with_diagnostics(
            analysis_result,
            list(financial_input.get("validated_evidence_ids", [])),
            list(self._risk_input.get("validated_filing_ids", [])),
            list(valuation_input.get("validated_evidence_ids", [])),
            list(valuation_input.get("validated_calculation_ids", [])),
        )
        self.state.required_data = list(required_data)
        if diagnostics is None:
            self._analysis_diagnostics = {}
            self.state.analysis_diagnostics = {}
        else:
            full_diagnostics = _json_safe(diagnostics)
            self._analysis_diagnostics = dict(full_diagnostics)
            self.state.analysis_diagnostics = {
                key: value
                for key, value in full_diagnostics.items()
                if key != "raw_task_outputs"
            }
        if required_data:
            self.state.status = "blocked"
            self.state.stage = "analysis"
            self.state.analysis = []
            self.state.verdict = None
            self.state.report = None
            route = "claims_blocked"
        else:
            self.state.analysis = _json_safe(claims)
            self.state.status = "running"
            self.state.stage = "report"
            route = "claims_ready"
        snapshot = self._stage_snapshot()
        gate_summary = self._gate_summary()
        claim_counts = snapshot["claims"]
        self._emit_stage(
            RunStageEvent(
                step=6,
                title="Claim Gate",
                actor="Python Gate：Claim Gate",
                status="blocked" if route == "claims_blocked" else "completed",
                input_summary="Analysis Crew 原始结果（仅内部传递）",
                output_summary=(
                    f"{gate_summary['status']}; financial_claims={claim_counts['financial']}; "
                    f"risk_claims={claim_counts['risk']}; valuation_claims={claim_counts['valuation']}; "
                    f"domain={gate_summary['domain']}; reason_code={gate_summary['reason_code']}; "
                    f"required_data={gate_summary['required_data']}"
                ),
                decision=gate_summary["status"],
                reason=(
                    f"domain={gate_summary['domain']}; "
                    f"reason_code={gate_summary['reason_code']}"
                ),
                next_step=(
                    "最终阻断" if route == "claims_blocked" else "Verdict 与 Report"
                ),
            )
        )
        return route

    @listen("claims_blocked")
    def finalize_claims_blocked(self) -> dict[str, Any]:
        """输出 Claim Gate 阻断分支并保留脱敏诊断。

        节点只把已写入的 state 序列化为最终 JSON-safe 结果，保留
        ``stage='analysis'``、稳定的 ``required_data`` 和可选
        ``analysis_diagnostics``，同时确保 Analysis/Report 不被伪造。它
        不调用确定性 Verdict 工具或 Report Crew，只有 ``claims_blocked``
        路由标签可以触发它。
        """
        gate_summary = self._gate_summary()
        claim_counts = self._stage_snapshot()["claims"]
        self._emit_stage(
            RunStageEvent(
                step=7,
                title="最终阻断",
                actor="Python Gate：Claim Gate",
                status="blocked",
                input_summary=(
                    f"financial_claims={claim_counts['financial']}; "
                    f"risk_claims={claim_counts['risk']}; valuation_claims={claim_counts['valuation']}"
                ),
                output_summary=(
                    f"BLOCKED; domain={gate_summary['domain']}; "
                    f"reason_code={gate_summary['reason_code']}; "
                    f"required_data={gate_summary['required_data']}"
                ),
                decision="BLOCKED",
                reason="Claim Gate 未通过；不执行 Verdict 与 Report",
                next_step="补齐 required_data 后重新运行",
            )
        )
        return self._flow_result()

    @listen("claims_ready")
    def generate_report(self) -> dict[str, Any]:
        """在 Claims 通过后调用确定性 Verdict 和 Report Crew。

        先由 ``pipeline_support._deterministic_verdict`` 根据已验证估值、
        历史估值、反向 DCF 和风险输入生成决策，再把已通过 Claim Gate 的
        Claims、确定性结果、计算结果、估值和来源元数据交给 Report Crew。
        Verdict 和报告都会先转为 JSON-safe 值后写入 state；Report Crew
        不能改变 Verdict、补造引用或绕过 Gate。若 Crew kickoff 或 Draft
        解析失败，Python 使用无动态事实的 deterministic fallback，随后仍
        经过同一个 Renderer 和最终 Markdown 安全检查；这些检查失败时
        fail closed。本节点只由 ``claims_ready`` 触发，因此任一阻断分支
        都不会调用下游依赖。
        """
        verdict_risk_input = _verdict_risk_input(self.state.analysis)
        verdict = pipeline_support._deterministic_verdict(
            validation_status=getattr(self._validation_result, "status", "unavailable"),
            valuation=self.state.valuation,
            historical_valuation=self.state.historical_valuation,
            reverse_dcf=self.state.reverse_dcf,
            risk_input=verdict_risk_input,
        )
        source_metadata = {
            "facts": {
                fact_id: {
                    key: fact.get(key)
                    for key in (
                        "evidence_id",
                        "source_reference",
                        "period",
                        "period_start",
                        "period_end",
                        "form",
                        "accession_number",
                    )
                    if (fact := raw_fact).get(key) is not None
                }
                for fact_id, raw_fact in self._pipeline_state.get("facts", {}).items()
            },
            "risk_filings": [
                {
                    key: filing.get(key)
                    for key in (
                        "evidence_id",
                        "form",
                        "filed_at",
                        "period_end",
                        "accession_number",
                        "source_reference",
                        "text_source_reference",
                    )
                    if filing.get(key) is not None
                }
                for filing in self._risk_input.get("filings", [])
            ],
            "historical_financial_snapshots": [
                {
                    key: snapshot.get(key)
                    for key in (
                        "evidence_id",
                        "as_of",
                        "filed_at",
                        "period_end",
                        "form",
                        "accession_number",
                        "source_reference",
                    )
                    if snapshot.get(key) is not None
                }
                for snapshot in self._historical_financial_snapshots
            ],
        }
        market_price_payload = (
            self._market_price_data
            if isinstance(self._market_price_data, Mapping)
            else {}
        )
        market_price_evidence_id = (
            self.state.valuation.get("market_price_evidence_id")
            or market_price_payload.get("market_price_evidence_id")
        )
        market_price_metadata = {
            key: value
            for key, value in {
                "evidence_id": market_price_evidence_id,
                "price_timestamp": self.state.valuation.get("price_timestamp")
                or market_price_payload.get("price_timestamp"),
                "currency": self.state.valuation.get("currency")
                or market_price_payload.get("currency"),
                "source_reference": self.state.valuation.get("source_reference")
                or market_price_payload.get("source_reference"),
            }.items()
            if value is not None
        }
        source_metadata["market_price"] = market_price_metadata
        source_metadata["historical_prices"] = [
            {
                key: value
                for key, value in {
                    **price,
                    "as_of": price.get("as_of", price.get("date")),
                    "source_reference": price.get("source_reference")
                    or market_price_metadata.get("source_reference"),
                }.items()
                if value is not None
            }
            for price in market_price_payload.get("historical_prices", [])
            if isinstance(price, Mapping)
        ]
        source_metadata = _json_safe(source_metadata)
        report_context = build_report_context(
            company={
                "name": self._pipeline_state.get("company_name"),
                "ticker": self._pipeline_state.get("ticker"),
            },
            validated_claims=self.state.analysis,
            deterministic_verdict=verdict,
            calculations=self._pipeline_state.get("calculations", []),
            valuation=self.state.valuation,
            historical_valuation=self.state.historical_valuation,
            reverse_dcf=self.state.reverse_dcf,
            source_metadata=source_metadata,
        )
        report_inputs = {"report_context": report_context}
        self.state.verdict = _json_safe(verdict)
        draft_source = "agent"
        fallback_reason = ""

        def _failure_summary(phase: str, exc: BaseException) -> str:
            """只保留异常阶段和类型，不把模型文本写入运行输出。"""
            return sanitize_text(f"{phase}:{type(exc).__name__}", 120)

        def _block_report_output_invalid(reason: Any) -> dict[str, Any]:
            """报告 fallback、Renderer 或最终安全检查失败时 fail closed。"""
            safe_reason = sanitize_text(str(reason), 240) or "unknown"
            self.state.report = None
            self.state.status = "blocked"
            self.state.stage = "report"
            self.state.required_data = ["report_output_invalid"]
            self.state.analysis_diagnostics = {
                "domain": "report",
                "reason_code": "report_output_invalid",
                "reason": safe_reason,
                "message": "ReportDraft、Renderer 或最终 Markdown 安全检查失败；原始 Agent 输出未写入结果。",
            }
            self._emit_stage(
                RunStageEvent(
                    step=7,
                    title="Report Output Safety Gate",
                    actor="Python Gate：Report Draft + Renderer",
                    status="blocked",
                    input_summary=(
                        "report_context（仅内部传递）；"
                        f"draft_source={draft_source}"
                    ),
                    output_summary=(
                        "BLOCKED; reason_code=report_output_invalid; "
                        f"draft_source={draft_source}; reason={safe_reason}"
                    ),
                    decision="BLOCKED",
                    reason=safe_reason,
                    next_step="修正报告输出或 Renderer 输入后重新运行",
                )
            )
            return self._flow_result()

        def _fallback_draft(exc: BaseException) -> ReportDraft | None:
            """为预期的 Crew/草稿失败构造安全 fallback；失败则阻断。"""
            nonlocal draft_source, fallback_reason
            draft_source = "deterministic_fallback"
            fallback_reason = _failure_summary("fallback", exc)
            try:
                draft = build_deterministic_report_draft()
                if not isinstance(draft, ReportDraft):
                    raise TypeError("deterministic_fallback:invalid_type")
                return parse_report_draft(draft.model_dump(mode="json"))
            except Exception as fallback_exc:
                _block_report_output_invalid(
                    _failure_summary("deterministic_fallback", fallback_exc)
                )
                return None

        report_draft: ReportDraft | None = None
        try:
            report_crew = _crew_instance(self._report_crew, ReportCrew)
            report_result = report_crew.kickoff(inputs=report_inputs)
        except Exception as exc:
            report_draft = _fallback_draft(exc)
        else:
            try:
                report_output = _crew_output(report_result)
            except Exception as exc:
                report_draft = _fallback_draft(exc)
            else:
                try:
                    report_draft = parse_report_draft(report_output)
                except (TypeError, ValueError) as exc:
                    report_draft = _fallback_draft(exc)

        if report_draft is None:
            return self._flow_result()

        try:
            report = render_validated_report(
                report_context=report_context,
                report_draft=report_draft,
            )
        except Exception as exc:
            return _block_report_output_invalid(_failure_summary("renderer", exc))

        try:
            report_passed, report_message = validate_rendered_report(
                report, str(verdict.get("status"))
            )
        except Exception as exc:
            return _block_report_output_invalid(
                _failure_summary("final_safety", exc)
            )
        if not report_passed:
            return _block_report_output_invalid(
                sanitize_text(str(report_message), 240)
            )

        self.state.report = report
        self.state.status = "ok"
        self.state.stage = "report"
        self.state.required_data = []
        self.state.analysis_diagnostics = {}
        snapshot = self._stage_snapshot()
        verdict_status = _summary_value(
            self.state.verdict,
            keys=("status", "decision", "verdict"),
        )
        report_status = "generated" if self.state.report not in (None, "", {}, []) else "unavailable"
        fallback_summary = (
            f"fallback_reason={fallback_reason}; " if fallback_reason else ""
        )
        claim_counts = snapshot["claims"]
        self._emit_stage(
            RunStageEvent(
                step=7,
                title="Verdict 与 Report",
                actor="Python：Deterministic Verdict + Crew/Agent：Report Crew",
                status="completed",
                input_summary=(
                    f"financial_claims={claim_counts['financial']}; "
                    f"risk_claims={claim_counts['risk']}; valuation_claims={claim_counts['valuation']}"
                ),
                output_summary=(
                    f"Verdict={verdict_status}; Report={report_status}; "
                    f"draft_source={draft_source}; "
                    f"{fallback_summary}"
                    f"PE={snapshot['pe']}; FCF Yield={snapshot['fcf_yield']}; "
                    f"historical percentile={snapshot['historical_percentile']}; "
                    f"reverse DCF growth={snapshot['reverse_dcf_growth']}"
                ),
                decision="READY",
                reason="Claim Gate 已通过",
                next_step="结束",
            )
        )
        return self._flow_result()

    def _flow_result(self) -> dict[str, Any]:
        """把当前公开 state 转换为不含私有依赖的 JSON-safe 结果。

        该辅助方法不改变状态、不调用工具或 Crew，只递归序列化当前
        ``ResearchFlowState``。它同时服务于 Analysis Gate 和 Claim Gate
        阻断收尾以及成功报告收尾，返回值包含公开 state 字段；完整的
        Analysis diagnostics 只从私有属性恢复到返回值，其他私有工具、Crew、
        原始 Crew 输出、LLM 配置或密钥不会进入返回值。
        """
        result = _json_safe(self.state.model_dump(mode="json"))
        if self._analysis_diagnostics and isinstance(result, dict):
            result["analysis_diagnostics"] = _json_safe(self._analysis_diagnostics)
        return result


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
):
    """以完整的 CrewAI 原生 Flow 保持旧 ``run_research`` 调用契约。

    参数签名和依赖注入边界保持不变；本函数只构造唯一的
    ``ResearchFlow``，再调用 ``flow.kickoff(inputs={"request": request})``。
    请求解析、SEC、计算、验证、估值、两个 Gate、Verdict 和报告都由 Flow
    事件链决定，不在这里手写第二套流程。最终结果沿用旧兼容入口的 JSON
    安全和输出归一化：移除 Flow 内部使用的 request/facts/filings 字段，
    阻断时把 Analysis/Report 统一为 ``None``，不改变确定性业务结果。可选
    ``progress_callback`` 只接收 Flow 生成的 ``RunStageEvent`` 摘要。
    """
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
    flow = ResearchFlow(
        **flow_kwargs,
    )
    result = _json_safe(flow.kickoff(inputs={"request": request}))
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
    deterministic_outputs = {
        key: result.get(key) for key in deterministic_keys
    }

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
        if result.get("required_data") == ["report_output_invalid"]:
            output = {
                **deterministic_outputs,
                "status": "blocked",
                "stage": "report",
                "analysis": result.get("analysis"),
                "verdict": result.get("verdict"),
                "report": None,
                "required_data": ["report_output_invalid"],
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
    """解析请求来源，打印完整 JSON，并保持旧入口的 None 返回契约。

    请求优先级保持为显式参数、``STOCKCREWAI_REQUEST`` 环境变量、命令行
    参数和 ``DEFAULT_REQUEST``。本函数只承担终端层输入输出，真正的研究
    运行统一经过 ``run_research``；它不会再把 kickoff 解释为 Request
    Parser helper，也不会绕过 Flow 的事件链。
    """
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
    """运行完整 ResearchFlow，并只输出紧凑摘要及独立完整 JSON 文件。

    这是项目级官方 Flow 入口，不再代表 Request Parser。请求参数为空时
    由 ``main`` 按环境变量、命令行和默认值解析；显式 ``output_path`` 只
    改变记录文件位置，默认仍为当前目录的 ``run-output.md``。Flow 的普通
    原始 CrewAI stdout/stderr 只进入内存捕获，不写入终端或 Markdown；阶段
    Reporter 始终绑定捕获前的真实终端流，异常只显示短错误摘要。
    """
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
            result = {"status": "error", "error": {"type": "InvalidResult", "message": "结果不是 JSON 对象"}}
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
    """按项目约定生成命名为 ``stockcrewai_flow`` 的 Flow 图。

    该入口只在用户显式调用时执行 ``ResearchFlow().plot``，普通研究运行
    不自动生成 HTML，避免工作区出现无关产物。返回值沿用 CrewAI plot
    接口的副作用语义并固定为 ``None``。
    """
    ResearchFlow().plot("stockcrewai_flow")


def cli(output_path: Path | None = None) -> int | None:
    """保留旧 CLI 函数名，并薄包装到新的完整 ``kickoff`` 入口。

    旧调用方仍可传入输出路径；请求解析、Flow 执行、双写、异常处理和
    退出码全部由 ``kickoff`` 统一处理，避免兼容入口与官方脚本分叉。
    """
    return kickoff(output_path=output_path)


if __name__ == "__main__":
    raise SystemExit(cli())
