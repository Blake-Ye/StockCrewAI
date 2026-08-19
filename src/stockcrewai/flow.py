"""StockCrewAI 的独立研究 Flow 与 JSON-safe 状态定义。

本模块从旧入口复制研究事件链、摘要辅助函数和结构化状态，保持
CrewAI 的官方 Flow 装饰器、稳定路由标签以及 PrivateAttr 运行时依赖语义。
它不依赖旧入口模块，因此可被入口或其他集成层独立导入。
"""

# 必须先准备 CrewAI 的存储目录，再导入会初始化 SQLite 的 CrewAI 模块。
# 因此本文件有意保留导入顺序，E402 在此处是已知且受控的例外。
# ruff: noqa: E402

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar


def _configure_default_crewai_storage() -> None:
    """在 CrewAI 导入前准备可写的默认 SQLite 存储目录。"""
    configured_storage = os.getenv("CREWAI_STORAGE_DIR", "").strip()
    if configured_storage:
        return
    default_storage = (Path.cwd() / ".crewai").resolve()
    default_storage.mkdir(parents=True, exist_ok=True)
    os.environ["CREWAI_STORAGE_DIR"] = str(default_storage)


_configure_default_crewai_storage()

from crewai.flow.flow import Flow, listen, router, start
from crewai.flow.persistence import persist
from pydantic import BaseModel, Field, PrivateAttr, ValidationError

import stockcrewai.pipeline_support as pipeline_support
from stockcrewai.crews.analysis.crew import AnalysisCrew
from stockcrewai.crews.report.crew import ReportCrew
from stockcrewai.reporting.context import build_report_context
from stockcrewai.reporting.renderer import (
    build_deterministic_report_draft,
    build_narrative_context,
    render_validated_report,
)
from stockcrewai.reporting.validator import (
    ReportDraft,
    parse_report_draft,
    validate_rendered_report,
)
from stockcrewai.pipeline_support import (
    _analysis_gate,
    _calculation_facts,
    _crew_instance,
    _crew_output,
    _edgar_error,
    _filter_analysis_claims_with_diagnostics,
    _financial_analysis_input,
    _first_value,
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
    sync_validation_status,
)
from stockcrewai.run_output import RunStageEvent, sanitize_text
from stockcrewai.models.evidence import EvidenceRecord, MarketPriceRecord
from stockcrewai.models.policy import PolicyDecision
from stockcrewai.models.profile import ProfileResult
from stockcrewai.services.evidence_store import EvidenceStore
from stockcrewai.services.runtime_metrics import RuntimeMetricsCollector
from stockcrewai.tools.calculator_tool import FinancialCalculatorTool
from stockcrewai.tools.edgar_tool import EdgarTool
from stockcrewai.tools.historical_valuation_tool import HistoricalValuationTool
from stockcrewai.tools.market_price_tool import MarketPriceTool
from stockcrewai.tools.reverse_dcf_tool import ReverseDCFTool
from stockcrewai.tools.validation_tool import FinancialValidationTool
from stockcrewai.tools.valuation_tool import ValuationTool, _market_price_evidence_id


_RUNTIME_METRICS_LOGGER = logging.getLogger(__name__)
_RUNTIME_METRICS_DISABLED_VALUES = frozenset({"", "0", "false", "off", "no"})
_RUNTIME_METRICS_OUTPUT_ENV = "STOCKCREWAI_RUNTIME_METRICS_OUTPUT"
_RUNTIME_METRICS_ENABLED_ENV = "STOCKCREWAI_RUNTIME_METRICS"
_RUNTIME_METRICS_DEFAULT_OUTPUT = Path("run-artifacts/runtime-metrics.json")
_RUNTIME_METRICS_RESERVED_OUTPUTS = frozenset({"run-result.json", "run-output.md"})
_RUNTIME_METRICS_STAGE_LABELS: dict[int, tuple[str, str, str]] = {
    1: ("research_flow", "request_parser", "parse_request"),
    2: ("research_flow", "deterministic_tools", "prepare_evidence"),
    3: ("research_flow", "valuation_tools", "prepare_valuation"),
    4: ("research_flow", "analysis_gate", "route_analysis"),
    5: ("research_flow", "analysis_crew", "run_analysis"),
    6: ("research_flow", "claim_gate", "route_claims"),
    7: ("research_flow", "report", "generate_report"),
}
def _is_unsupported_security_profile(profile: Any) -> bool:
    if not isinstance(profile, Mapping):
        return False
    return any(
        str(getattr(profile.get(field), "value", profile.get(field)))
        .strip()
        .casefold()
        == expected
        for field, expected in (
            ("coverage_level", "unsupported_security"),
            ("security_profile", "unsupported_fund_security"),
            ("reporting_profile", "investment_company_reporting"),
        )
    )


def _is_unsupported_sic_category_profile(profile: Any) -> bool:
    """判断 Profile 是否由 SIC 识别为普通公司主线之外的类别。"""
    if not isinstance(profile, Mapping):
        return False
    issuer_profile = profile.get("issuer_profile", profile.get("issuer_type"))
    issuer_profile = getattr(issuer_profile, "value", issuer_profile)
    reason_codes = profile.get("reason_codes", [])
    return (
        str(issuer_profile).strip().casefold() != "standard_operating"
        and isinstance(reason_codes, Sequence)
        and not isinstance(reason_codes, (str, bytes, bytearray))
        and "profile_classified_from_sic" in reason_codes
    )


def _unsupported_scope_reason(profile: Any) -> str | None:
    """仅为明确识别出的不支持范围返回稳定原因码。"""
    if _is_unsupported_sic_category_profile(profile):
        return "unsupported_category_sic"
    if _is_unsupported_security_profile(profile):
        return "unsupported_security"
    return None


def _unsupported_scope_required_data(
    profile: Any,
    edgar: Any,
    reason_code: str,
) -> list[str]:
    """把范围门禁转换成可读且不含 ``missing`` 的摘要字段。"""
    if reason_code == "unsupported_security":
        security_profile = (
            profile.get("security_profile", profile.get("security_type"))
            if isinstance(profile, Mapping)
            else None
        )
        security_profile = getattr(security_profile, "value", security_profile) or "unknown"
        coverage_level = (
            profile.get("coverage_level") if isinstance(profile, Mapping) else None
        )
        coverage_level = getattr(coverage_level, "value", coverage_level) or "unknown"
        return [
            f"unsupported_security:security_profile={security_profile}",
            f"unsupported_security:coverage_level={coverage_level}",
        ]

    issuer_profile = (
        profile.get("issuer_profile", profile.get("issuer_type"))
        if isinstance(profile, Mapping)
        else None
    )
    issuer_profile = getattr(issuer_profile, "value", issuer_profile) or "unknown"
    sic = edgar.get("sic") if isinstance(edgar, Mapping) else None
    sic = sic if sic not in (None, "") else "unknown"
    if reason_code not in {"unsupported_category_sic", "unsupported_security"}:
        return [reason_code]
    return [
        f"unsupported_category_sic:sic={sic}",
        f"unsupported_category_sic:issuer_profile={issuer_profile}",
    ]


def _runtime_metrics_enabled() -> bool:
    value = os.getenv(_RUNTIME_METRICS_ENABLED_ENV, "")
    return value.strip().casefold() not in _RUNTIME_METRICS_DISABLED_VALUES


def _runtime_metrics_output_path() -> Path:
    value = os.getenv(_RUNTIME_METRICS_OUTPUT_ENV, "").strip()
    return Path(value).expanduser() if value else _RUNTIME_METRICS_DEFAULT_OUTPUT


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
    profile: dict[str, Any] = Field(default_factory=dict)
    policy_context: dict[str, Any] = Field(default_factory=dict)
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
    annual_financial_history: dict[str, Any] = Field(default_factory=dict)
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
        "evidence_store",
        "report_crew",
        "market_price_data",
        "progress_callback",
        "profile_input",
        "profile_evidence_records",
        "profile_market_price_records",
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
    _evidence_store: Any = PrivateAttr(default=None)
    _report_crew: Any = PrivateAttr(default=None)
    _market_price_data: Any = PrivateAttr(default=None)
    _profile_input: Mapping[str, Any] | None = PrivateAttr(default=None)
    _profile_evidence_records: tuple[EvidenceRecord, ...] = PrivateAttr(
        default_factory=tuple
    )
    _profile_market_price_records: tuple[MarketPriceRecord, ...] = PrivateAttr(
        default_factory=tuple
    )

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
    _runtime_metrics_collector: RuntimeMetricsCollector | None = PrivateAttr(
        default=None
    )
    _runtime_metrics_enabled: bool = PrivateAttr(default=False)
    _runtime_metrics_output: Path = PrivateAttr(default=_RUNTIME_METRICS_DEFAULT_OUTPUT)
    _runtime_metrics_stage_started_at: float | None = PrivateAttr(default=None)
    _runtime_metrics_finalized: bool = PrivateAttr(default=False)

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
        profile_input = dependencies["profile_input"]
        dependencies["profile_input"] = (
            dict(profile_input) if isinstance(profile_input, Mapping) else None
        )
        dependencies["profile_evidence_records"] = self._validated_records(
            dependencies["profile_evidence_records"], EvidenceRecord
        )
        dependencies["profile_market_price_records"] = self._validated_records(
            dependencies["profile_market_price_records"], MarketPriceRecord
        )
        super().__init__(**data)
        for name, dependency in dependencies.items():
            setattr(self, f"_{name}", dependency)
        self._runtime_metrics_enabled = _runtime_metrics_enabled()
        self._runtime_metrics_output = _runtime_metrics_output_path()
        if self._runtime_metrics_enabled:
            self._runtime_metrics_collector = RuntimeMetricsCollector(
                run_id=str(self.state.id)
            )
            self._runtime_metrics_stage_started_at = time.monotonic()

    @staticmethod
    def _validated_records(value: Any, record_type: Any) -> tuple[Any, ...]:
        if not isinstance(value, Sequence) or isinstance(
            value, (str, bytes, bytearray)
        ):
            return ()
        records: list[Any] = []
        for item in value:
            try:
                records.append(record_type.model_validate(item))
            except (TypeError, ValueError, ValidationError):
                continue
        return tuple(records)

    def _build_analysis_evidence_store(self) -> EvidenceStore:
        """为当前 Flow run 构造不进入 state 的只读 EvidenceStore。"""
        state_payload = _json_safe(self.state.model_dump(mode="json"))
        if isinstance(self._pipeline_state, Mapping):
            state_payload.update(_json_safe(self._pipeline_state))

        validation = state_payload.get("validation", {})
        if not isinstance(validation, Mapping):
            validation = {}

        def records_for(key: str, default: Any) -> Any:
            value = state_payload.get(key, default)
            if key == "calculations" and isinstance(value, Mapping):
                value = value.get("calculations", [])
            if key == "filings" and isinstance(value, Mapping):
                value = value.get("filings", [])
            if key == "facts" and not isinstance(value, Mapping):
                return {}
            if key != "facts" and not isinstance(value, list):
                return []
            return _json_safe(value)

        def text_value(value: Any) -> str | None:
            if not isinstance(value, str):
                return None
            value = value.strip()
            return value or None

        def validated_ids(key: str) -> list[str]:
            for source in (validation, state_payload):
                value = source.get(key)
                if isinstance(value, str):
                    value = [value]
                if isinstance(value, (list, tuple, set, frozenset)):
                    return [item for item in value if isinstance(item, str) and item]
            return []

        facts = records_for("facts", {})
        calculations = records_for("calculations", [])
        facts_by_evidence_id = {
            evidence_id: record
            for key, record in facts.items()
            if isinstance(record, Mapping)
            for evidence_id in [
                text_value(record.get("evidence_id")) or text_value(key)
            ]
            if evidence_id
        }
        time_keys = (
            "as_of",
            "filed_at",
            "period",
            "period_start",
            "period_end",
            "date",
            "price_timestamp",
        )
        normalized_calculations: list[dict[str, Any]] = []
        for raw_calculation in calculations:
            if not isinstance(raw_calculation, Mapping):
                continue
            calculation = dict(raw_calculation)
            calculation_id = text_value(calculation.get("calculation_id"))
            formula_id = text_value(calculation.get("formula_id"))
            if (
                not calculation_id
                or not formula_id
                or not text_value(calculation.get("validation_status"))
            ):
                continue
            calculation["calculation_id"] = calculation_id
            if not text_value(calculation.get("source_reference")):
                calculation["source_reference"] = f"derived:{formula_id}"

            input_evidence_ids = calculation.get("input_evidence_ids", [])
            if isinstance(input_evidence_ids, str):
                input_evidence_ids = [input_evidence_ids]
            if not isinstance(input_evidence_ids, Sequence):
                input_evidence_ids = []
            for evidence_id in input_evidence_ids:
                evidence_record = facts_by_evidence_id.get(
                    text_value(evidence_id) or ""
                )
                if evidence_record is None:
                    continue
                for key in time_keys:
                    if not text_value(calculation.get(key)) and text_value(
                        evidence_record.get(key)
                    ):
                        calculation[key] = evidence_record[key]
            if not any(text_value(calculation.get(key)) for key in time_keys):
                continue
            normalized_calculations.append(calculation)

        normalized_calculation_ids = {
            calculation["calculation_id"]
            for calculation in normalized_calculations
        }
        validated_calculation_ids = [
            calculation_id
            for calculation_id in validated_ids("validated_calculation_ids")
            if calculation_id in normalized_calculation_ids
        ]
        return EvidenceStore(
            {
                "evidence": facts,
                "calculations": normalized_calculations,
                "filings": records_for("filings", []),
            },
            run_id=str(self.state.id),
            allowlist={
                "validated_evidence_ids": validated_ids("validated_evidence_ids"),
                "validated_calculation_ids": validated_calculation_ids,
                "validated_filing_ids": validated_ids("validated_filing_ids"),
            },
        )

    def _emit_stage(self, event: RunStageEvent) -> None:
        """向可选进度回调发送一个不可变的阶段摘要事件。"""
        self._record_runtime_metrics(event)
        if callable(self._progress_callback):
            try:
                self._progress_callback(event)
            except Exception:
                self._progress_callback = None

    def _record_runtime_metrics(self, event: RunStageEvent) -> None:
        """把阶段事件压缩为白名单运行观测字段。"""
        collector = self._runtime_metrics_collector
        if not self._runtime_metrics_enabled or collector is None:
            return

        status = str(getattr(event, "status", "") or "").strip().casefold()
        if status == "blocked":
            event_type = "stage_failed"
            failure_category = "gate"
        elif status in {"error", "failed", "failure", "exception"}:
            event_type = "stage_failed"
            failure_category = "runtime"
        elif status in {"completed", "success", "succeeded", "ok"}:
            event_type = "stage_completed"
            failure_category = None
        elif status in {"started", "start", "running"}:
            event_type = "stage_started"
            failure_category = None
        else:
            event_type = "stage_observed"
            failure_category = None

        now = time.monotonic()
        started_at = self._runtime_metrics_stage_started_at
        self._runtime_metrics_stage_started_at = now
        elapsed = now - started_at if started_at is not None else None
        labels = _RUNTIME_METRICS_STAGE_LABELS.get(
            int(getattr(event, "step", 0) or 0),
            ("research_flow", "flow", "stage"),
        )
        metric_event: dict[str, Any] = {
            "event_type": event_type,
            "run_id": str(self.state.id),
            "crew": labels[0],
            "agent": labels[1],
            "task": labels[2],
            "status": status,
        }
        if elapsed is not None and elapsed >= 0:
            metric_event["elapsed_seconds"] = elapsed
        if failure_category is not None:
            metric_event["failure_category"] = failure_category
        try:
            collector.record(metric_event)
        except Exception as exc:  # pragma: no cover - defensive boundary
            _RUNTIME_METRICS_LOGGER.warning(
                "runtime metrics event rejected path=%s error_type=%s",
                self._runtime_metrics_output,
                type(exc).__name__,
            )

    def _write_runtime_metrics(self) -> None:
        """一次性写出独立、JSON-safe 的运行观测 artifact。"""
        if (
            not self._runtime_metrics_enabled
            or self._runtime_metrics_finalized
            or self._runtime_metrics_collector is None
        ):
            return
        self._runtime_metrics_finalized = True
        output = self._runtime_metrics_output
        if output.name in _RUNTIME_METRICS_RESERVED_OUTPUTS:
            _RUNTIME_METRICS_LOGGER.warning(
                "runtime metrics artifact skipped for reserved output path=%s",
                output,
            )
            return
        try:
            report = self._runtime_metrics_collector.report()
            payload = report.to_dict()
            payload["stable_hash"] = report.stable_hash
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
                + "\n",
                encoding="utf-8",
            )
        except Exception as exc:  # pragma: no cover - defensive boundary
            _RUNTIME_METRICS_LOGGER.warning(
                "runtime metrics artifact write failed path=%s error_type=%s",
                output,
                type(exc).__name__,
            )

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
        policy_gate = (
            self.state.policy_context.get("gate")
            if isinstance(self.state.policy_context, Mapping)
            else None
        )
        policy_reason_codes = (
            policy_gate.get("reason_codes", [])
            if isinstance(policy_gate, Mapping)
            else []
        )
        if reason_code == "unavailable" and isinstance(policy_reason_codes, Sequence):
            for scope_reason in ("unsupported_category_sic", "unsupported_security"):
                if scope_reason in policy_reason_codes:
                    reason_code = scope_reason
                    break
        if domain == "unavailable" and required_data:
            if any(
                item.startswith(
                    (
                        "unsupported_category_sic:",
                        "unsupported_security:",
                        "ordinary_scope_",
                    )
                )
                for item in required_data
            ):
                domain = "scope"
            else:
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
        if self.state.status != "blocked" and not required_data:
            domain = "none" if domain == "unavailable" else domain
            reason_code = "none" if reason_code == "unavailable" else reason_code
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
        requested_focus = parsed_request.get("requested_focus")
        if requested_focus is None:
            requested_focus = parsed_request.get("focus")
        self._emit_stage(
            RunStageEvent(
                step=1,
                title="请求解析",
                actor="Crew/Agent：Request Parser Crew",
                status="completed",
                input_summary="request=provided",
                output_summary=f"{snapshot['identity']}; focus={_summary_count(requested_focus)}",
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
        corporate_action_scan_status = getattr(
            edgar_result, "corporate_action_scan_status", "unavailable"
        )
        corporate_actions = getattr(edgar_result, "corporate_actions", [])
        if self._calculator_tool is None:
            self._calculator_tool = FinancialCalculatorTool()
        calculation_result = self._calculator_tool.run(
            company_name=edgar_result.company_name or company_name,
            ticker=edgar_result.ticker or ticker,
            facts=calculation_facts,
            corporate_action_scan_status=corporate_action_scan_status,
            corporate_actions=corporate_actions,
        )

        if self._validation_tool is None:
            self._validation_tool = FinancialValidationTool()
        validation_result = self._validation_tool.run(
            company_name=edgar_result.company_name or company_name,
            ticker=edgar_result.ticker or ticker,
            facts=calculation_facts,
            calculations=calculation_result.calculations,
            corporate_action_scan_status=corporate_action_scan_status,
            corporate_actions=corporate_actions,
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
        if isinstance(edgar_output, dict):
            edgar_output.setdefault(
                "corporate_action_scan_status",
                _json_safe(corporate_action_scan_status),
            )
            edgar_output.setdefault("corporate_actions", _json_safe(corporate_actions))
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
        # 估值节点只从这里读取已验证 TTM 指标；不允许再从原始
        # diluted EPS 或九个月自由现金流字段猜测口径。
        pipeline_state["ttm"] = ttm_output
        annual_financial_history = _json_safe(
            getattr(edgar_result, "annual_financial_history", {})
        )
        self.state.annual_financial_history = (
            dict(annual_financial_history)
            if isinstance(annual_financial_history, Mapping)
            else {}
        )
        if isinstance(annual_financial_history, Mapping) and annual_financial_history:
            pipeline_state["annual_financial_history"] = dict(annual_financial_history)

        validation_output = (
            validation_result.model_dump(mode="json")
            if hasattr(validation_result, "model_dump")
            else validation_result
        )
        self._edgar_result = edgar_result
        self._calculation_result = calculation_result
        self._validation_result = validation_result
        self._pipeline_state = _json_safe(pipeline_state)
        profile_payload = _json_safe(self.state.profile)
        state_profile = (
            profile_payload
            if isinstance(profile_payload, Mapping) and profile_payload
            else None
        )
        explicit_profile = (
            self._profile_input
            if isinstance(self._profile_input, Mapping)
            else state_profile
        )
        profile_evidence_records = self._profile_evidence_records
        profile_market_price_records = self._profile_market_price_records
        profile_metadata = pipeline_support.profile_metadata_from_edgar(edgar_result)
        policy_context = pipeline_support.build_profile_policy_context(
            profile=explicit_profile,
            source_metadata=profile_metadata,
            facts=self._pipeline_state.get("facts", {}),
            calculations=self._pipeline_state.get("calculations", []),
            evidence_records=profile_evidence_records,
            market_price_records=profile_market_price_records,
        )
        policy_context = _json_safe(policy_context)
        if not isinstance(policy_context, dict):
            raise TypeError("profile policy context 必须是 JSON-safe 映射")
        policy_profile = policy_context.get("profile")
        policy_reporting_profile = (
            policy_profile.get("reporting_profile")
            if isinstance(policy_profile, Mapping)
            else None
        )
        policy_reporting_profile = getattr(
            policy_reporting_profile,
            "value",
            policy_reporting_profile,
        )
        is_foreign_ifrs = (
            str(policy_reporting_profile).strip().casefold()
            == "foreign_private_issuer_ifrs"
        )
        is_unsupported_security = _is_unsupported_security_profile(policy_profile)
        policy_context["policy_activation"] = (
            "explicit_profile"
            if explicit_profile is not None
            else "sec_metadata"
            if policy_context.get("policies")
            or is_foreign_ifrs
            or is_unsupported_security
            else "legacy_analysis_gate"
        )
        self.state.profile = _json_safe(policy_context.get("profile", {}))
        self.state.policy_context = policy_context
        self._pipeline_state["profile"] = self.state.profile
        self._pipeline_state["policy_context"] = policy_context
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
        profile_context = pipeline_state.get("policy_context")
        profile_candidates = [
            profile_context.get("profile")
            if isinstance(profile_context, Mapping)
            else None,
            pipeline_state.get("profile"),
            self.state.profile,
            self._profile_input,
        ]
        scope_profile = None
        unsupported_scope_reason = None
        for candidate in profile_candidates:
            candidate_reason = _unsupported_scope_reason(candidate)
            if candidate_reason:
                scope_profile = candidate
                unsupported_scope_reason = candidate_reason
                break
        if unsupported_scope_reason:
            unavailable_valuation = {
                "status": "not_applicable",
                "readiness": "not_applicable",
                "validation_status": "unvalidated",
                "reason_code": unsupported_scope_reason,
                "calculations": [],
            }
            self._market_price_data = _json_safe(self._market_price_data)
            self._trusted_valuation_evidence_ids = set()
            self._historical_financial_snapshots = []
            self.state.market_price_data = _json_safe(self._market_price_data)
            self.state.valuation = dict(unavailable_valuation)
            self.state.historical_valuation = dict(unavailable_valuation)
            self.state.reverse_dcf = dict(unavailable_valuation)
            self.state.stage = "analysis"
            self.state.required_data = _unsupported_scope_required_data(
                scope_profile, self.state.edgar, unsupported_scope_reason
            )
            snapshot = self._stage_snapshot()
            self._emit_stage(
                RunStageEvent(
                    step=3,
                    title="市场价格与估值",
                    actor="Python：SEC Scope/Profile Gate",
                    status="blocked",
                    input_summary=snapshot["identity"],
                    output_summary=(
                        "unsupported scope; "
                        f"reason_code={unsupported_scope_reason}; "
                        "valuation=not_applicable"
                    ),
                    decision="SKIPPED",
                    reason=f"reason_code={unsupported_scope_reason}",
                    next_step="Analysis Gate",
                )
            )
            return dict(unavailable_valuation)

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
            str(evidence_id)
            for snapshot in self._historical_financial_snapshots
            if isinstance(snapshot, Mapping)
            for evidence_id in snapshot.get("financial_evidence_ids", [])
            if evidence_id
        )
        # 反向 DCF 和当前 FCF Yield 使用的是 TTM Builder 的输入 Evidence。
        # 这些 Evidence 不一定属于基础 Calculator 的 validated_evidence_ids，
        # 但 TTM 验证器已经单独确认过；必须把同一份白名单传给辅助估值，
        # 否则 DCF 会被误标记为 unvalidated 并在 Gate 被阻断。
        ttm_payload = self._pipeline_state.get("ttm", {})
        ttm_validation = (
            ttm_payload.get("evidence_validation", {})
            if isinstance(ttm_payload, Mapping)
            else {}
        )
        if (
            isinstance(ttm_validation, Mapping)
            and ttm_validation.get("status") == "valid"
        ):
            auxiliary_allowed_ids.update(
                str(evidence_id)
                for evidence_id in ttm_validation.get(
                    "validated_evidence_ids", []
                )
                if evidence_id
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

        current_valuation_kwargs: dict[str, Any] = {}
        valuation_calculations = valuation.get("calculations", [])
        if isinstance(valuation_calculations, list | tuple):
            pe_calculation = next(
                (
                    item
                    for item in valuation_calculations
                    if isinstance(item, Mapping)
                    and item.get("formula_id") == "pe_ratio"
                    and item.get("status") == "available"
                    and item.get("validation_status") == "valid"
                ),
                None,
            )
            if pe_calculation is not None:
                current_pe_ratio = pe_calculation.get("raw_result")
                if current_pe_ratio in (None, ""):
                    current_pe_ratio = pe_calculation.get("display_result")
                current_price_evidence_id = (
                    pe_calculation.get("market_price_evidence_id")
                    or valuation.get("market_price_evidence_id")
                    or market_price_payload.get("market_price_evidence_id")
                    or derived_market_price_evidence_id
                )
                current_input_ids = pe_calculation.get("input_evidence_ids", [])
                if isinstance(current_input_ids, str):
                    current_input_ids = [current_input_ids]
                if not isinstance(current_input_ids, Sequence) or isinstance(
                    current_input_ids, (str, bytes, bytearray)
                ):
                    current_input_ids = []
                current_financial_evidence_ids = [
                    evidence_id
                    for evidence_id in current_input_ids
                    if evidence_id != current_price_evidence_id
                ]
                raw_inputs = pe_calculation.get("raw_inputs", {})
                current_ttm_eps = (
                    raw_inputs.get("diluted_eps")
                    if isinstance(raw_inputs, Mapping)
                    else None
                )
                current_valuation_kwargs = {
                    "current_pe_ratio": current_pe_ratio,
                    "current_price": market_price_kwargs.get("market_price")
                    or pe_calculation.get("market_price")
                    or valuation.get("market_price"),
                    "current_price_date": market_price_kwargs.get("price_timestamp")
                    or pe_calculation.get("price_timestamp")
                    or valuation.get("price_timestamp"),
                    "current_price_evidence_id": current_price_evidence_id,
                    "current_ttm_eps": current_ttm_eps,
                    "current_financial_evidence_ids": current_financial_evidence_ids,
                }

        if self._historical_valuation_tool is None:
            self._historical_valuation_tool = HistoricalValuationTool()
        historical_kwargs = {
            "company_name": pipeline_state.get("company_name"),
            "ticker": pipeline_state.get("ticker"),
            "historical_prices": historical_prices,
            "financial_snapshots": self._historical_financial_snapshots,
        }
        try:
            historical_kwargs["as_of"] = datetime.fromisoformat(
                str(market_price_kwargs["price_timestamp"]).replace("Z", "+00:00")
            ).date().isoformat()
        except (KeyError, TypeError, ValueError):
            pass
        if len(historical_prices) >= 60 and self._historical_financial_snapshots:
            historical_kwargs.update(current_valuation_kwargs)
        historical_result = self._historical_valuation_tool.run(**historical_kwargs)
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
            gate_state = dict(self._pipeline_state)
            profile = _json_safe(self.state.profile)
            if isinstance(profile, Mapping):
                gate_state["profile"] = profile
                for key, value in profile.items():
                    gate_state.setdefault(key, value)
            policy_context = gate_state.get("policy_context")
            policy_activation = (
                policy_context.get("policy_activation")
                if isinstance(policy_context, Mapping)
                else None
            )
            active_policy_context = (
                policy_context if isinstance(policy_context, Mapping) else {}
            )
            use_profile_policy = policy_activation in {
                "explicit_profile",
                "sec_metadata",
            }
            if (
                policy_activation is None
                and isinstance(policy_context, Mapping)
                and isinstance(policy_context.get("profile"), Mapping)
                and isinstance(policy_context.get("policy_decisions"), list)
                and isinstance(policy_context.get("policy_version"), str)
            ):
                # 保留直接注入完整 policy context 的旧集成调用方式。
                use_profile_policy = True
            if use_profile_policy:
                try:
                    profile_result = ProfileResult.model_validate(
                        active_policy_context.get("profile")
                    )
                    decisions_payload = active_policy_context.get("policy_decisions")
                    if not isinstance(decisions_payload, list):
                        raise ValueError("policy_decisions must be a list")
                    decisions = tuple(
                        PolicyDecision.model_validate(decision)
                        for decision in decisions_payload
                    )
                    profile_gate = pipeline_support._profile_policy_gate(
                        profile_result,
                        decisions,
                    )
                    gate = profile_gate.model_dump(mode="json")
                    normalized_context = dict(active_policy_context)
                    normalized_context["profile"] = profile_result.model_dump(
                        mode="json"
                    )
                    normalized_context["gate"] = gate
                    self.state.profile = normalized_context["profile"]
                    self.state.policy_context = _json_safe(normalized_context)
                    self._pipeline_state["profile"] = self.state.profile
                    self._pipeline_state["policy_context"] = self.state.policy_context
                except (TypeError, ValueError, ValidationError):
                    gate = {
                        "status": "blocked",
                        "required_data": ["invalid_profile_policy_context"],
                    }
            else:
                gate = _analysis_gate(
                    self._validation_result,
                    gate_state,
                    self._risk_input,
                    dict(valuation or self.state.valuation),
                    self.state.historical_valuation,
                    self.state.reverse_dcf,
                )
            scope_reason = _unsupported_scope_reason(self.state.profile)
            if scope_reason:
                gate = dict(gate)
                gate["status"] = "unsupported"
                gate["blocking_decisions"] = []
                gate["reason_codes"] = [scope_reason]
                gate["required_data"] = _unsupported_scope_required_data(
                    self.state.profile,
                    self.state.edgar,
                    scope_reason,
                )
            elif gate.get("status") in {"blocked", "unsupported"} and (
                "blocking_decisions" in gate
            ):
                if gate.get("status") in {"blocked", "unsupported"}:
                    required_data = [
                        f"{decision.get('metric_id')}:{decision.get('reason_code')}"
                        for decision in gate.get("blocking_decisions", [])
                        if isinstance(decision, Mapping)
                        and decision.get("metric_id")
                        and decision.get("reason_code")
                    ]
                    gate["required_data"] = required_data or [
                        f"profile_policy_gate_{gate.get('status', 'blocked')}"
                    ]
        if gate.get("status") in {"blocked", "unsupported"}:
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
        """把两个 LLM 输入和确定性 Claims 交给 Claim Gate。

        财务和风险输入分别由 ``pipeline_support`` 构造并交给 Analysis
        Crew；估值输入同样构造，但只由 Python 的确定性构建器生成第三项
        Claims。三个输出按 financial/risk/valuation 顺序组合后，仍由
        Claim Gate 解析和校验，不直接写入 SQLite state。只有财务或风险
        Claims 为空时才对两个 LLM 输入做一次带 ``retry_notice`` 的重试；
        若重试后仍为空或结构无效，则交给 Claim Gate 阻断，不由 Python
        替 Agent 生成财务或风险 Claim。估值输入的 Evidence allowlist
        来自 ``prepare_valuation`` 保存的 trusted set，Calculation allowlist
        来自固定注册表与基础已验证计算集合。
        """
        financial_input = _financial_analysis_input(self._pipeline_state)
        profile = _json_safe(self.state.profile)
        if not isinstance(profile, Mapping):
            profile = {}
        financial_input["profile"] = profile
        risk_input = _json_safe(self._risk_input)
        if not isinstance(risk_input, dict):
            risk_input = {}
        risk_input["profile"] = profile
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
            "risk_analysis_input": risk_input,
        }
        self._valuation_analysis_input = valuation_input
        if self._analysis_crew is None:
            if self._evidence_store is None:
                self._evidence_store = self._build_analysis_evidence_store()
            analysis_crew = _crew_instance(
                AnalysisCrew.from_evidence_store(self._evidence_store),
                AnalysisCrew,
            )
        else:
            analysis_crew = _crew_instance(self._analysis_crew, AnalysisCrew)
        self.state.analysis_attempts = 1
        raw_analysis_result = analysis_crew.kickoff(inputs=self._analysis_inputs)
        agent_task_count = _summary_count(
            getattr(raw_analysis_result, "tasks_output", None)
        )

        def with_deterministic_claims(
            result: Any,
        ) -> Any:
            """保留两个 Agent task，并追加确定性估值 task。"""
            task_outputs = getattr(result, "tasks_output", None)
            if not isinstance(task_outputs, (list, tuple)):
                task_outputs = ()
            task_outputs = list(task_outputs)
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

        self._analysis_result = with_deterministic_claims(
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
            self._analysis_result = with_deterministic_claims(raw_analysis_result)
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
        financial_evidence_ids = list(
            financial_input.get("validated_evidence_ids", [])
        )
        claim_calculation_ids = list(
            valuation_input.get("validated_calculation_ids", [])
        )
        claims, required_data, diagnostics = _filter_analysis_claims_with_diagnostics(
            analysis_result,
            financial_evidence_ids,
            list(self._risk_input.get("validated_filing_ids", [])),
            list(valuation_input.get("validated_evidence_ids", [])),
            claim_calculation_ids,
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
        解析失败，Python 直接阻断报告阶段，不把失败伪装成成功报告；Renderer
        和最终 Markdown 安全检查失败时同样 fail closed。本节点只由
        ``claims_ready`` 触发，因此任一阻断分支
        都不会调用下游依赖。
        """
        verdict_risk_input = _verdict_risk_input(self.state.analysis)
        verdict = pipeline_support._deterministic_verdict(
            validation_status=getattr(self._validation_result, "status", "unavailable"),
            valuation=self.state.valuation,
            historical_valuation=self.state.historical_valuation,
            reverse_dcf=self.state.reverse_dcf,
            risk_input=verdict_risk_input,
            policy_context=self.state.policy_context,
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
                        "fiscal_period",
                        "period_type",
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
                        "financial_evidence_ids",
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
            "ttm_evidence": [],
            "ttm_metrics": self.state.ttm.get("metrics", [])
            if isinstance(self.state.ttm, Mapping)
            else [],
        }
        if self.state.annual_financial_history:
            source_metadata["annual_financial_history"] = _json_safe(
                self.state.annual_financial_history
            )
        raw_ttm_inputs = getattr(self._edgar_result, "ttm_inputs", {})
        if isinstance(raw_ttm_inputs, Mapping):
            for by_role in raw_ttm_inputs.values():
                if not isinstance(by_role, Mapping):
                    continue
                for raw_fact in by_role.values():
                    fact_payload = _json_safe(raw_fact)
                    if isinstance(fact_payload, Mapping):
                        source_metadata["ttm_evidence"].append(dict(fact_payload))
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
            "horizon": self.state.parsed_request.get("investment_horizon"),
            "profile": _json_safe(self.state.profile),
            },
            validated_claims=self.state.analysis,
            deterministic_verdict=verdict,
            calculations=self._pipeline_state.get("calculations", []),
            valuation=self.state.valuation,
            historical_valuation=self.state.historical_valuation,
            reverse_dcf=self.state.reverse_dcf,
            ttm=self.state.ttm,
            annual_financial_history=self.state.annual_financial_history,
            source_metadata=source_metadata,
            policy_context=self.state.policy_context,
        )
        report_inputs = {"narrative_context": build_narrative_context(report_context)}
        self.state.verdict = _json_safe(verdict)
        draft_source = "agent"
        fallback_reason = None

        def _failure_summary(phase: str, exc: BaseException) -> str:
            """只保留异常阶段和类型，不把模型文本写入运行输出。"""
            exception_types: list[str] = []
            current: BaseException | None = exc
            seen: set[int] = set()
            while (
                isinstance(current, BaseException)
                and id(current) not in seen
                and len(exception_types) < 4
            ):
                seen.add(id(current))
                exception_types.append(type(current).__name__)
                current = current.__cause__ or current.__context__
            return sanitize_text(f"{phase}:{'->'.join(exception_types)}", 120)

        def _block_report_output_invalid(code: str, reason: Any) -> dict[str, Any]:
            """报告 Draft、Renderer 或最终安全检查失败时 fail closed。"""
            safe_reason = sanitize_text(str(reason), 240) or "unknown"
            self.state.report = None
            self.state.status = "blocked"
            self.state.stage = "report"
            self.state.required_data = [code]
            self.state.analysis_diagnostics = {
                "domain": "report",
                "reason_code": code,
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
                        f"BLOCKED; reason_code={code}; "
                        f"draft_source={draft_source}; reason={safe_reason}"
                    ),
                    decision="BLOCKED",
                    reason=safe_reason,
                    next_step="修正报告输出或 Renderer 输入后重新运行",
                )
            )
            return self._flow_result()

        def _guardrail_exhausted(exc: BaseException) -> bool:
            current: BaseException | None = exc
            seen: set[int] = set()
            while isinstance(current, BaseException) and id(current) not in seen:
                seen.add(id(current))
                text = str(current).lower()
                if "report_guardrail_retries_exhausted" in text or (
                    "guardrail" in text and "after" in text and "retries" in text
                ):
                    return True
                current = current.__cause__ or current.__context__
            return False

        report_draft: ReportDraft | None = None
        try:
            report_crew = _crew_instance(self._report_crew, ReportCrew)
            report_result = report_crew.kickoff(inputs=report_inputs)
        except Exception as exc:
            if _guardrail_exhausted(exc) and str(verdict.get("status")) == "ready":
                report_draft = build_deterministic_report_draft()
                draft_source = "deterministic_safe_draft"
                fallback_reason = "report_guardrail_retries_exhausted"
            else:
                code = "report_guardrail_retries_exhausted" if _guardrail_exhausted(exc) else "report_provider_error"
                return _block_report_output_invalid(code, _failure_summary("report_kickoff", exc))

        if report_draft is None:
            try:
                report_output = _crew_output(report_result)
            except Exception as exc:
                return _block_report_output_invalid("report_provider_error", _failure_summary("report_output", exc))

            try:
                report_draft = parse_report_draft(report_output)
            except Exception as exc:
                return _block_report_output_invalid(getattr(exc, "code", "report_draft_schema_invalid"), _failure_summary("report_parse", exc))

        try:
            report = render_validated_report(
                report_context=report_context,
                report_draft=report_draft,
            )
        except Exception as exc:
            return _block_report_output_invalid("report_renderer_error", _failure_summary("renderer", exc))

        try:
            report_passed, report_message = validate_rendered_report(
                report, str(verdict.get("status"))
            )
        except Exception as exc:
            return _block_report_output_invalid("report_final_validation_error", _failure_summary("final_safety", exc))
        if not report_passed:
            return _block_report_output_invalid("report_final_validation_error", sanitize_text(str(report_message), 240))

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
                    f"PE={snapshot['pe']}; FCF Yield={snapshot['fcf_yield']}; "
                    f"historical percentile={snapshot['historical_percentile']}; "
                    f"reverse DCF growth={snapshot['reverse_dcf_growth']}"
                ),
                decision="READY",
                reason=("Claim Gate 已通过" if fallback_reason is None else f"Claim Gate 已通过; reason_code={fallback_reason}"),
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
        self._write_runtime_metrics()
        return result
