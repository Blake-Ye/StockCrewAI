from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import tempfile
import tomllib
import unittest
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

from tests.test_crew_configuration import (
    VALID_REPORT_DRAFT,
    RecordingCrew,
    _valid_analysis_outputs,
    _valid_pipeline_fakes,
)


REQUEST = "分析苹果公司未来 3 年投资价值"
FLOW_LABELS = {
    "analysis_ready",
    "analysis_blocked",
    "claims_ready",
    "claims_blocked",
}
_UNSET = object()

# CrewAI creates the SQLite persistence backend while importing a @persist()
# Flow. Keep this test-only storage outside the repository when a caller has
# not already provided the documented storage directory.
_TEST_FLOW_STORAGE = tempfile.TemporaryDirectory(prefix="stockcrewai-main-flow-")
os.environ.setdefault("CREWAI_STORAGE_DIR", _TEST_FLOW_STORAGE.name)


def _main_module():
    return importlib.import_module("stockcrewai.main")


def _flow_symbols():
    module = _main_module()
    flow = getattr(module, "ResearchFlow", None)
    state = getattr(module, "ResearchFlowState", None)
    if flow is None or state is None:
        missing = [
            name
            for name, value in (
                ("ResearchFlow", flow),
                ("ResearchFlowState", state),
            )
            if value is None
        ]
        raise AssertionError(
            "stockcrewai.main 尚未直接定义：" + ", ".join(missing)
        )
    return module, flow, state


def _flow_class():
    module = _main_module()
    flow = getattr(module, "ResearchFlow", None)
    if flow is None:
        raise AssertionError("stockcrewai.main 尚未提供 ResearchFlow")
    return module, flow


def _flow_dependencies():
    """复用现有完整流水线 fake，并注入离线市场价格工具。"""
    parser_result, dependencies = _valid_pipeline_fakes()
    from stockcrewai.tools.edgar_tool import EdgarRiskEligibility

    filing = dependencies["edgar_tool"].run.return_value.filings[0]
    filing.risk_eligibility = EdgarRiskEligibility(
        evidence_id=filing.evidence_id,
        eligibility="eligible",
        evidence_kind="item_1a",
        reason_code="eligible_item_1a",
        section_title="Item 1A. Risk Factors",
        filed_at=filing.filed_at,
        source_reference=filing.source_reference,
    )
    filing.risk_sections[0].section_title = "Item 1A. Risk Factors"
    market_price_tool = Mock()
    market_price_tool.run.return_value = dependencies.pop("market_price_data")
    dependencies["market_price_tool"] = market_price_tool
    return parser_result, dependencies


def _run_flow(flow):
    """运行离线 Flow，并收起 CrewAI 的 Rich 进度输出。"""
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return flow.kickoff(inputs={"request": REQUEST})


class SequencedAnalysisCrew:
    """按预设顺序返回 Analysis Task 输出的离线 Crew 替身。"""

    def __init__(self, task_raw_sequences: list[list[str]]) -> None:
        self._task_raw_sequences = task_raw_sequences
        self.inputs: list[dict[str, Any]] = []
        self.kickoff_calls = 0

    def kickoff(self, *, inputs: dict[str, Any]) -> SimpleNamespace:
        """记录每次 payload，并返回对应轮次的两个 Agent Task 输出。"""
        sequence_index = min(self.kickoff_calls, len(self._task_raw_sequences) - 1)
        task_raws = self._task_raw_sequences[sequence_index]
        self.inputs.append(inputs)
        self.kickoff_calls += 1
        return SimpleNamespace(
            raw=None,
            # The Flow adds deterministic valuation Claims as the third Gate
            # output after these two Agent tasks return.
            tasks_output=[SimpleNamespace(raw=raw) for raw in task_raws[:2]],
        )


@contextmanager
def _offline_flow_patches(parser_result: Any, verdict: Any = _UNSET):
    """只替换请求解析和可选 Verdict 边界，不触发外部服务。"""
    module = _main_module()
    with ExitStack() as stack:
        parser_factory = stack.enter_context(
            patch.object(module, "RequestParserCrew", create=True)
        )
        parser_factory.return_value.crew.return_value.kickoff.return_value = (
            parser_result
        )

        # Current pre-migration Flow calls main.kickoff; the target Flow calls
        # the renamed support helper. Patch both boundaries so this RED suite
        # remains offline while still rejecting kickoff recursion separately.
        stack.enter_context(
            patch.object(
                module,
                "run_request_parser",
                create=True,
                return_value=parser_result,
            )
        )
        support_module = None
        try:
            support_module = importlib.import_module("stockcrewai.pipeline_support")
        except ModuleNotFoundError as exc:
            if exc.name != "stockcrewai.pipeline_support":
                raise
        if support_module is not None:
            stack.enter_context(
                patch.object(
                    support_module,
                    "run_request_parser",
                    create=True,
                    return_value=parser_result,
                )
            )

        verdict_mocks = []
        if verdict is not _UNSET:
            verdict_mocks.append(
                stack.enter_context(
                    patch.object(
                        module,
                        "_deterministic_verdict",
                        create=True,
                        return_value=verdict,
                    )
                )
            )
            if support_module is not None:
                verdict_mocks.append(
                    stack.enter_context(
                        patch.object(
                            support_module,
                            "_deterministic_verdict",
                            create=True,
                            return_value=verdict,
                        )
                    )
                )
        yield parser_factory, verdict_mocks


class MainFlowDefinitionTests(unittest.TestCase):
    def test_ready_gate_summary_has_no_failure_diagnostics(self):
        _, flow_class, _ = _flow_symbols()
        flow = flow_class()
        flow.state.status = "running"
        flow.state.required_data = []
        flow.state.analysis_diagnostics = {}

        self.assertEqual(
            flow._gate_summary(),
            {
                "status": "READY",
                "domain": "none",
                "reason_code": "none",
                "required_data": "none",
            },
        )

    def test_state_and_flow_are_reexported_from_flow(self):
        _, flow, state = _flow_symbols()

        self.assertEqual(state.__module__, "stockcrewai.flow")
        self.assertEqual(flow.__module__, "stockcrewai.flow")
        expected_fields = {
            "request",
            "parsed_request",
            "input_requirements",
            "edgar",
            "facts",
            "filings",
            "calculations",
            "validation",
            "market_price_data",
            "valuation",
            "historical_valuation",
            "reverse_dcf",
            "ttm",
            "analysis",
            "analysis_diagnostics",
            "verdict",
            "report",
            "status",
            "stage",
            "required_data",
        }
        self.assertTrue(
            expected_fields.issubset(state.model_fields),
            sorted(expected_fields - set(state.model_fields)),
        )

        first = state(request=REQUEST)
        second = state(request=REQUEST)
        mutable_fields = (
            "parsed_request",
            "input_requirements",
            "edgar",
            "facts",
            "filings",
            "calculations",
            "validation",
            "market_price_data",
            "valuation",
            "historical_valuation",
            "reverse_dcf",
            "ttm",
            "analysis",
            "required_data",
            "analysis_diagnostics",
        )
        for field_name in mutable_fields:
            first_value = getattr(first, field_name)
            second_value = getattr(second, field_name)
            self.assertIsInstance(first_value, (dict, list), field_name)
            self.assertIsNot(first_value, second_value, field_name)

    def test_analysis_crew_is_reexported_from_canonical_module(self):
        main_module = _main_module()
        canonical_module = importlib.import_module(
            "stockcrewai.crews.analysis.crew"
        )

        self.assertIs(
            getattr(main_module, "AnalysisCrew", None),
            canonical_module.AnalysisCrew,
        )

    def test_flow_uses_one_start_real_listeners_routers_and_stable_labels(self):
        _, flow, _ = _flow_symbols()
        definitions = {
            name: getattr(member, "__flow_method_definition__", None)
            for name, member in flow.__dict__.items()
        }
        starts = [
            name
            for name, definition in definitions.items()
            if definition is not None and bool(definition.start)
        ]
        self.assertEqual(starts, ["parse_request"])

        expected_edges = {
            "prepare_evidence": ("ListenMethod", "parse_request"),
            "prepare_valuation": ("ListenMethod", "prepare_evidence"),
            "route_analysis": ("RouterMethod", "prepare_valuation"),
            "finalize_analysis_blocked": ("ListenMethod", "analysis_blocked"),
            "run_analysis": ("ListenMethod", "analysis_ready"),
            "route_claims": ("RouterMethod", "run_analysis"),
            "finalize_claims_blocked": ("ListenMethod", "claims_blocked"),
            "generate_report": ("ListenMethod", "claims_ready"),
        }
        for method_name, (method_kind, listened_to) in expected_edges.items():
            member = flow.__dict__.get(method_name)
            self.assertIsNotNone(member, method_name)
            self.assertEqual(type(member).__name__, method_kind, method_name)
            definition = getattr(member, "__flow_method_definition__", None)
            self.assertIsNotNone(definition, method_name)
            self.assertEqual(definition.listen, listened_to, method_name)
            self.assertEqual(bool(definition.router), method_kind == "RouterMethod")

        self.assertEqual(
            flow.route_analysis.__flow_method_definition__.emit,
            ["analysis_ready", "analysis_blocked"],
        )
        self.assertEqual(
            flow.route_claims.__flow_method_definition__.emit,
            ["claims_ready", "claims_blocked"],
        )

        listened_labels = {
            definition.listen
            for definition in definitions.values()
            if definition is not None and definition.listen in FLOW_LABELS
        }
        self.assertEqual(listened_labels, FLOW_LABELS)
        self.assertIsNotNone(getattr(flow, "__flow_persistence_config__", None))


class MainFlowExecutionTests(unittest.TestCase):
    def test_real_edgar_ttm_evidence_is_validated_before_builder(self):
        from datetime import date

        from tests.test_financial_tools import TTMEdgar
        from stockcrewai.tools.calculator_tool import FinancialCalculatorTool
        from stockcrewai.tools.edgar_tool import EdgarTool
        from stockcrewai.tools.ttm_tool import TTMBuilderTool
        from stockcrewai.tools.validation_tool import FinancialValidationTool

        parser_result, dependencies = _flow_dependencies()
        edgar_tool = EdgarTool(
            edgar_module=TTMEdgar(),
            as_of=date(2026, 8, 5),
        )
        initial_edgar_result = edgar_tool.run(ticker="AAPL")
        self.assertTrue(initial_edgar_result.ttm_inputs)
        self.assertTrue(
            all(
                fact.validation_status == "unvalidated"
                for by_role in initial_edgar_result.ttm_inputs.values()
                for fact in by_role.values()
            )
        )

        validation_tool = Mock(wraps=FinancialValidationTool())
        ttm_builder_tool = Mock(wraps=TTMBuilderTool())
        dependencies.update(
            {
                "edgar_tool": edgar_tool,
                "calculator_tool": FinancialCalculatorTool(),
                "validation_tool": validation_tool,
                "ttm_builder_tool": ttm_builder_tool,
            }
        )
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)
        _, flow_class = _flow_class()
        flow = flow_class(
            **dependencies,
            analysis_crew=analysis_crew,
            report_crew=report_crew,
        )

        with _offline_flow_patches(parser_result, verdict={"status": "ready"}):
            result = _run_flow(flow)

        ttm_validation_calls = [
            call
            for call in validation_tool.run.call_args_list
            if call.kwargs.get("calculations") == []
        ]
        self.assertEqual(len(ttm_validation_calls), 1)
        ttm_validation_call = ttm_validation_calls[0]
        self.assertEqual(
            set(ttm_validation_call.kwargs["facts"]),
            {
                f"{metric_id}:{role}"
                for metric_id in initial_edgar_result.ttm_inputs
                for role in ("latest_fy", "current_ytd", "prior_ytd")
            },
        )

        projected_inputs = ttm_builder_tool.run.call_args.kwargs["metric_inputs"]
        self.assertTrue(
            all(
                fact["validation_status"] == "valid"
                for by_role in projected_inputs.values()
                for fact in by_role.values()
            )
        )
        self.assertEqual(result["ttm"]["status"], "ok")
        self.assertTrue(
            all(
                metric["status"] == "available"
                and metric["validation_status"] == "valid"
                for metric in result["ttm"]["metrics"]
            )
        )
        self.assertEqual(result["ttm"]["evidence_validation"]["status"], "valid")

    def test_ttm_builder_result_is_saved_and_stage_summary_only_exposes_counts(self):
        from stockcrewai.tools.validation_tool import ValidationResult

        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)
        parser_result, dependencies = _flow_dependencies()
        validation_tool = dependencies["validation_tool"]
        ttm_builder_tool = dependencies["ttm_builder_tool"]
        expected_ttm_evidence_ids = {
            "ev_revenue_latest_fy",
            "ev_revenue_current_ytd",
            "ev_revenue_prior_ytd",
        }
        base_validation_result = validation_tool.run.return_value

        def validation_side_effect(**kwargs):
            if kwargs.get("calculations") == []:
                return ValidationResult(
                    status="valid",
                    validated=True,
                    company_name=kwargs["company_name"],
                    ticker=kwargs["ticker"],
                    validated_evidence_ids=sorted(expected_ttm_evidence_ids),
                )
            return base_validation_result

        validation_tool.run.side_effect = validation_side_effect
        ttm_builder_tool.run.return_value = {
            "status": "available",
            "company_name": "Apple Inc.",
            "ticker": "AAPL",
            "metrics": [
                {
                    "metric_id": "revenue",
                    "status": "available",
                    "validation_status": "valid",
                    "raw_result": "123.45",
                    "period_basis": "TTM",
                }
            ],
            "warnings": [],
        }
        _, flow_class = _flow_class()
        flow = flow_class(
            **dependencies,
            analysis_crew=analysis_crew,
            report_crew=report_crew,
        )
        events = []
        flow._progress_callback = events.append

        with _offline_flow_patches(parser_result, verdict={"status": "ready"}):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "ok")
        builder_output = ttm_builder_tool.run.return_value
        for key in ("status", "metrics", "warnings"):
            self.assertEqual(flow.state.ttm[key], builder_output[key])
            self.assertEqual(result["ttm"][key], builder_output[key])

        self.assertIn("evidence_validation", flow.state.ttm)
        evidence_validation = flow.state.ttm["evidence_validation"]
        self.assertEqual(evidence_validation["status"], "valid")
        self.assertTrue(evidence_validation["validated"])
        self.assertEqual(
            set(evidence_validation["validated_evidence_ids"]),
            expected_ttm_evidence_ids,
        )
        ttm_validation_call = next(
            call
            for call in validation_tool.run.call_args_list
            if call.kwargs.get("calculations") == []
        )
        self.assertEqual(
            set(ttm_validation_call.kwargs["facts"]),
            {
                "revenue:latest_fy",
                "revenue:current_ytd",
                "revenue:prior_ytd",
            },
        )

        ttm_builder_tool.run.assert_called_once()
        builder_call = ttm_builder_tool.run.call_args
        self.assertEqual(builder_call.kwargs["company_name"], "Apple Inc.")
        self.assertEqual(builder_call.kwargs["ticker"], "AAPL")
        projected_inputs = builder_call.kwargs["metric_inputs"]
        self.assertEqual(set(projected_inputs), {"revenue"})
        self.assertEqual(
            set(projected_inputs["revenue"]),
            {"latest_fy", "current_ytd", "prior_ytd"},
        )
        projected_facts = projected_inputs["revenue"].values()
        self.assertTrue(all(isinstance(fact, dict) for fact in projected_facts))
        self.assertEqual(
            {fact["evidence_id"] for fact in projected_inputs["revenue"].values()},
            expected_ttm_evidence_ids,
        )
        self.assertTrue(
            all(
                fact["validation_status"] == "valid"
                for fact in projected_inputs["revenue"].values()
            )
        )
        evidence_event = next(event for event in events if event.step == 2)
        self.assertIn("ttm=1/1", evidence_event.output_summary)
        self.assertNotIn("123.45", evidence_event.output_summary)
        self.assertNotIn("raw_result", evidence_event.output_summary)
        self.assertNotIn("ttm_builder_tool", flow.state.model_dump(mode="json"))
        self.assertNotIn("ttm_builder_tool", result)
        valuation_facts = dependencies["valuation_tool"].run.call_args.kwargs["facts"]
        self.assertNotIn("ttm", valuation_facts)

    def test_ttm_builder_failure_becomes_unavailable_without_changing_gates(self):
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)
        parser_result, dependencies = _flow_dependencies()
        dependencies["ttm_builder_tool"].run.side_effect = RuntimeError(
            "ttm implementation detail must stay hidden"
        )
        _, flow_class = _flow_class()
        flow = flow_class(
            **dependencies,
            analysis_crew=analysis_crew,
            report_crew=report_crew,
        )

        with _offline_flow_patches(parser_result, verdict={"status": "ready"}):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stage"], "report")
        self.assertEqual(result["validation"]["status"], "valid")
        self.assertEqual(result["verdict"], {"status": "ready"})
        self.assertTrue(result["report"])
        self.assertEqual(result["ttm"]["status"], "unavailable")
        self.assertEqual(result["ttm"]["reason_code"], "ttm_builder_error")
        self.assertNotIn("implementation detail", json.dumps(result))
        valuation_facts = dependencies["valuation_tool"].run.call_args.kwargs["facts"]
        self.assertNotIn("ttm", valuation_facts)

    def test_historical_valuation_excludes_prices_after_market_timestamp(self):
        from calendar import monthrange
        from datetime import date

        from stockcrewai.tools.historical_valuation_tool import HistoricalValuationTool

        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)
        parser_result, dependencies = _flow_dependencies()
        historical_prices = []
        financial_snapshots = []
        year, month = 2021, 8
        for index in range(61):
            point_date = date(year, month, monthrange(year, month)[1]).isoformat()
            historical_prices.append(
                {
                    "date": point_date,
                    "price": "100",
                    "evidence_id": f"ev_history_{index}",
                }
            )
            financial_snapshots.append(
                {
                    "filed_at": point_date,
                    "period_end": point_date,
                    "period_basis": "TTM",
                    "ttm_eps": "1",
                    "financial_evidence_ids": [
                        f"ev_eps_fy_{index}",
                        f"ev_eps_current_{index}",
                        f"ev_eps_prior_{index}",
                    ],
                }
            )
            month += 1
            if month == 13:
                year += 1
                month = 1

        market_price_data = dependencies["market_price_tool"].run.return_value
        market_price_data["price_timestamp"] = "2026-08-07T15:30:00Z"
        market_price_data["historical_prices"] = historical_prices
        dependencies["edgar_tool"].run.return_value.historical_financial_snapshots = (
            financial_snapshots
        )
        historical_valuation_tool = Mock(wraps=HistoricalValuationTool())
        dependencies["historical_valuation_tool"] = historical_valuation_tool
        _, flow_class = _flow_class()
        flow = flow_class(
            **dependencies,
            analysis_crew=analysis_crew,
            report_crew=report_crew,
        )

        with _offline_flow_patches(parser_result, verdict={"status": "ready"}):
            result = _run_flow(flow)

        self.assertEqual(
            historical_valuation_tool.run.call_args.kwargs.get("as_of"),
            "2026-08-07",
        )
        historical_call = historical_valuation_tool.run.call_args.kwargs
        self.assertEqual(historical_call["current_pe_ratio"], "25.00x")
        self.assertEqual(historical_call["current_price"], "100")
        self.assertEqual(
            historical_call["current_price_date"], "2026-08-07T15:30:00Z"
        )
        self.assertEqual(
            historical_call["current_price_evidence_id"], "ev_market_price"
        )
        self.assertEqual(historical_call["current_financial_evidence_ids"], ["ev_revenue"])
        self.assertEqual(result["historical_valuation"]["current_date"], "2026-08-07")
        self.assertEqual(result["historical_valuation"]["current_value"], "25")
        self.assertNotIn(
            "2026-08-31",
            result["historical_valuation"]["selected_dates"],
        )

    def test_generate_report_passes_verified_ttm_state_to_report_context(self):
        from stockcrewai.tools.validation_tool import ValidationResult

        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)
        parser_result, flow, dependencies = self._make_flow(
            analysis_crew, report_crew
        )
        ttm_evidence_ids = {
            "ev_revenue_latest_fy",
            "ev_revenue_current_ytd",
            "ev_revenue_prior_ytd",
        }
        dependencies["ttm_builder_tool"].run.return_value = {
            "status": "available",
            "company_name": "Apple Inc.",
            "ticker": "AAPL",
            "metrics": [
                {
                    "metric_id": "revenue",
                    "calculation_id": "calc_revenue_ttm",
                    "formula_id": "ttm_revenue",
                    "input_evidence_ids": sorted(ttm_evidence_ids),
                    "raw_result": "105",
                    "unit": "USD",
                    "period_basis": "TTM",
                    "status": "available",
                    "validation_status": "valid",
                }
            ],
            "warnings": [],
        }
        base_validation_result = dependencies["validation_tool"].run.return_value

        def validation_side_effect(**kwargs):
            if kwargs.get("calculations") == []:
                return ValidationResult(
                    status="valid",
                    validated=True,
                    company_name=kwargs["company_name"],
                    ticker=kwargs["ticker"],
                    validated_evidence_ids=sorted(ttm_evidence_ids),
                )
            return base_validation_result

        dependencies["validation_tool"].run.side_effect = validation_side_effect
        captured_context_kwargs: dict[str, Any] = {}
        flow_module = importlib.import_module("stockcrewai.flow")
        original_build_report_context = flow_module.build_report_context

        def capture_report_context(**kwargs):
            captured_context_kwargs.update(kwargs)
            return original_build_report_context(**kwargs)

        with (
            _offline_flow_patches(parser_result, verdict={"status": "ready"}),
            patch.object(
                flow_module,
                "build_report_context",
                side_effect=capture_report_context,
            ),
        ):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "ok")
        forwarded_ttm = captured_context_kwargs.get("ttm")
        self.assertEqual(forwarded_ttm, flow.state.ttm)
        self.assertEqual(forwarded_ttm["status"], "available")
        self.assertEqual(
            forwarded_ttm["metrics"][0]["raw_result"],
            "105",
        )
        self.assertEqual(
            forwarded_ttm["metrics"][0]["validation_status"],
            "valid",
        )
        self.assertEqual(
            set(forwarded_ttm["evidence_validation"]["validated_evidence_ids"]),
            ttm_evidence_ids,
        )
        json.dumps(forwarded_ttm, ensure_ascii=False, allow_nan=False)

    def test_invalid_parser_output_keeps_request_stage_and_skips_downstream_calls(self):
        _, flow_class = _flow_class()
        parser_result = Mock(json_dict=None, raw="not JSON")
        downstream_tools = {
            name: Mock()
            for name in (
                "edgar_tool",
                "calculator_tool",
                "validation_tool",
                "valuation_tool",
                "market_price_tool",
                "historical_valuation_tool",
                "reverse_dcf_tool",
            )
        }
        analysis_crew = RecordingCrew("must not run")
        report_crew = RecordingCrew("must not run")
        flow = flow_class(
            **downstream_tools,
            analysis_crew=analysis_crew,
            report_crew=report_crew,
        )

        with _offline_flow_patches(parser_result):
            result = _run_flow(flow)

        self.assertEqual(flow.state.stage, "request")
        self.assertEqual(result["stage"], "request")
        self.assertEqual(result["required_data"], ["invalid_parser_output"])
        for tool in downstream_tools.values():
            tool.run.assert_not_called()
        self.assertEqual(analysis_crew.kickoff_calls, 0)
        self.assertEqual(report_crew.kickoff_calls, 0)

    def test_run_research_success_keeps_legacy_public_json_keys(self):
        module = _main_module()
        parser_result, dependencies = _flow_dependencies()
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)

        with _offline_flow_patches(
            parser_result, verdict={"status": "ready"}
        ):
            result = module.run_research(
                REQUEST,
                **dependencies,
                analysis_crew=analysis_crew,
                report_crew=report_crew,
            )

        self.assertEqual(
            set(result),
            {
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
                "profile",
                "policy_context",
                "status",
                "stage",
                "analysis",
                "verdict",
                "report",
            },
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stage"], "report")
        self.assertNotIn("required_data", result)
        self.assertNotIn("request", result)
        self.assertNotIn("facts", result)
        self.assertNotIn("filings", result)

    def test_run_research_success_preserves_report_stage_from_flow_result(self):
        module = _main_module()
        flow = Mock()
        flow.kickoff.return_value = {
            "parsed_request": {},
            "input_requirements": {},
            "edgar": {},
            "calculations": {},
            "validation": {},
            "market_price_data": {},
            "valuation": {},
            "historical_valuation": {},
            "reverse_dcf": {},
            "analysis": [],
            "status": "ok",
            "stage": "report",
            "required_data": [],
            "verdict": {"status": "ready"},
            "report": "已生成",
        }

        with patch.object(module, "ResearchFlow", return_value=flow):
            result = module.run_research(REQUEST)

        self.assertEqual(result["stage"], "report")

    def test_request_stage_counts_requested_focus(self):
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)
        events = []
        flow._progress_callback = events.append

        with _offline_flow_patches(parser_result, verdict={"status": "ready"}):
            _run_flow(flow)

        request_event = next(event for event in events if event.step == 1)
        self.assertIn("focus=3", request_event.output_summary)

    def test_summary_claim_counts_treats_reverse_dcf_as_valuation(self):
        module = _main_module()
        counts = module._summary_claim_counts(
            [
                {"category": "financial_quality"},
                {"category": "risk"},
                {"category": "valuation"},
                {"category": "reverse_dcf"},
            ]
        )

        self.assertEqual(
            counts,
            {"total": 4, "financial": 1, "risk": 1, "valuation": 2},
        )

    def test_analysis_retries_once_after_empty_claim_gate_result(self):
        first_outputs = _valid_analysis_outputs()
        first_outputs[1] = json.dumps({"claims": []}, ensure_ascii=False)
        second_outputs = _valid_analysis_outputs()
        analysis_crew = SequencedAnalysisCrew([first_outputs, second_outputs])
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)
        events = []
        flow._progress_callback = events.append

        with _offline_flow_patches(parser_result, verdict={"status": "ready"}):
            result = _run_flow(flow)

        self.assertEqual(analysis_crew.kickoff_calls, 2)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(getattr(flow.state, "analysis_attempts", 0), 2)
        self.assertEqual(report_crew.kickoff_calls, 1)
        analysis_events = [event for event in events if event.step == 5]
        self.assertEqual(len(analysis_events), 1)
        self.assertIn("attempts=2", analysis_events[0].output_summary)

        role_keys = {
            "financial_analysis_input",
            "risk_analysis_input",
        }
        first_inputs, retry_inputs = analysis_crew.inputs
        self.assertEqual(set(first_inputs), role_keys)
        self.assertEqual(set(retry_inputs), role_keys)
        for key in role_keys:
            self.assertNotIn("retry_notice", first_inputs[key])
            self.assertIn("retry_notice", retry_inputs[key])
            self.assertEqual(
                {
                    field: value
                    for field, value in retry_inputs[key].items()
                    if field != "retry_notice"
                },
                first_inputs[key],
            )
        self.assertIn("不得编造", retry_inputs["risk_analysis_input"]["retry_notice"])

    def test_empty_risk_claim_gate_result_retries_once_then_blocks(self):
        empty_outputs = _valid_analysis_outputs()
        empty_outputs[1] = json.dumps({"claims": []}, ensure_ascii=False)
        analysis_crew = SequencedAnalysisCrew([empty_outputs, empty_outputs])
        report_crew = RecordingCrew("must not run")
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)
        events = []
        flow._progress_callback = events.append

        with _offline_flow_patches(parser_result) as (_, verdict_mocks):
            result = _run_flow(flow)

        self.assertEqual(analysis_crew.kickoff_calls, 2)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stage"], "analysis")
        self.assertEqual(result["required_data"], ["risk_analysis_claims_required"])
        self.assertEqual(report_crew.kickoff_calls, 0)
        self.assertEqual(getattr(flow.state, "analysis_attempts", 0), 2)
        self.assertEqual(sum(mock.call_count for mock in verdict_mocks), 0)
        analysis_events = [event for event in events if event.step == 5]
        self.assertEqual(len(analysis_events), 1)
        self.assertIn("attempts=2", analysis_events[0].output_summary)

    def test_only_rejected_shell_blocks_before_analysis_kickoff(self):
        from stockcrewai.tools.edgar_tool import EdgarRiskEligibility

        analysis_crew = RecordingCrew("must not run")
        report_crew = RecordingCrew("must not run")
        parser_result, flow, dependencies = self._make_flow(
            analysis_crew, report_crew
        )
        filing = dependencies["edgar_tool"].run.return_value.filings[0]
        filing.risk_eligibility = EdgarRiskEligibility(
            evidence_id=filing.evidence_id,
            eligibility="rejected",
            reason_code="attachment_shell",
            source_reference=filing.source_reference,
            filed_at=filing.filed_at,
        )

        with _offline_flow_patches(parser_result):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stage"], "analysis")
        self.assertEqual(result["required_data"], ["risk_evidence_missing"])
        self.assertEqual(analysis_crew.kickoff_calls, 0)
        self.assertEqual(report_crew.kickoff_calls, 0)

    def test_builder_is_not_called_for_invalid_or_nonempty_risk_output(self):
        invalid_risk_outputs = (
            "not JSON",
            json.dumps(
                {
                    "claims": [
                        {
                            "claim_id": "claim_bad_evidence",
                            "category": "risk",
                            "statement": "申报文本披露了事件。",
                            "evidence_ids": ["ev_not_allowlisted"],
                            "calculation_ids": [],
                            "confidence": 0.9,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "claims": [
                        {
                            "claim_id": "claim_wrong_category",
                            "category": "financial_quality",
                            "statement": "申报文本披露了事件。",
                            "evidence_ids": ["ev_filing"],
                            "calculation_ids": [],
                            "confidence": 0.9,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        )

        for invalid_risk_output in invalid_risk_outputs:
            with self.subTest(invalid_risk_output=invalid_risk_output):
                analysis_outputs = _valid_analysis_outputs()
                analysis_outputs[1] = invalid_risk_output
                analysis_crew = SequencedAnalysisCrew([analysis_outputs])
                report_crew = RecordingCrew("must not run")
                parser_result, flow, _ = self._make_flow(
                    analysis_crew, report_crew
                )

                with _offline_flow_patches(parser_result):
                    result = _run_flow(flow)

                self.assertEqual(result["status"], "blocked")
                self.assertEqual(analysis_crew.kickoff_calls, 1)

    def test_run_research_parser_failure_keeps_legacy_error_contract(self):
        module = _main_module()
        parser_result = Mock(json_dict=None, raw="not JSON")
        analysis_crew = RecordingCrew("must not run")
        report_crew = RecordingCrew("must not run")

        with _offline_flow_patches(parser_result):
            result = module.run_research(
                REQUEST,
                analysis_crew=analysis_crew,
                report_crew=report_crew,
            )

        self.assertEqual(set(result), {"parsed_request", "edgar"})
        self.assertEqual(result["parsed_request"], {"raw": "not JSON"})
        self.assertEqual(result["edgar"]["status"], "error")
        self.assertEqual(
            result["edgar"]["errors"][0]["code"], "invalid_parser_output"
        )
        self.assertEqual(analysis_crew.kickoff_calls, 0)
        self.assertEqual(report_crew.kickoff_calls, 0)

    def test_run_research_analysis_block_uses_legacy_blocked_shape(self):
        module = _main_module()
        parser_result, dependencies = _flow_dependencies()
        dependencies["edgar_tool"].run.return_value.filings[0].risk_sections = []
        analysis_crew = RecordingCrew("must not run")
        report_crew = RecordingCrew("must not run")

        with _offline_flow_patches(parser_result):
            result = module.run_research(
                REQUEST,
                **dependencies,
                analysis_crew=analysis_crew,
                report_crew=report_crew,
            )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stage"], "analysis")
        self.assertIsNone(result["analysis"])
        self.assertIsNone(result["report"])
        self.assertNotIn("verdict", result)
        self.assertNotIn("facts", result)
        self.assertNotIn("filings", result)
        self.assertIn("required_data", result)
        self.assertIn("next_action", result)

    def _make_flow(self, analysis_crew, report_crew):
        _, flow_class = _flow_class()
        parser_result, dependencies = _flow_dependencies()
        flow = flow_class(
            **dependencies,
            analysis_crew=analysis_crew,
            report_crew=report_crew,
        )
        return parser_result, flow, dependencies

    def test_success_path_completes_full_flow_with_deterministic_verdict(self):
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)
        events = []
        flow._progress_callback = events.append

        with _offline_flow_patches(
            parser_result, verdict={"status": "ready"}
        ) as (_, verdict_mocks):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["analysis"][0]["category"], "financial_quality")
        self.assertEqual(result["verdict"], {"status": "ready"})
        self.assertIn("## 1. 一页结论", result["report"])
        self.assertIn("财务质量稳定。", result["report"])
        self.assertEqual(analysis_crew.kickoff_calls, 1)
        self.assertEqual(report_crew.kickoff_calls, 1)
        self.assertEqual(getattr(flow.state, "analysis_attempts", 0), 1)
        self.assertEqual(sum(mock.call_count for mock in verdict_mocks), 1)
        report_event = next(
            event
            for event in events
            if event.step == 7 and event.status == "completed"
        )
        self.assertIn("draft_source=agent", report_event.output_summary)
        self.assertNotIn("fallback_reason=", report_event.output_summary)
        json.dumps(result, ensure_ascii=False, allow_nan=False)
        for private_dependency in (
            "edgar_tool",
            "calculator_tool",
            "validation_tool",
            "analysis_crew",
            "report_crew",
        ):
            self.assertNotIn(private_dependency, result)

    def test_report_kickoff_failure_blocks_without_leaking_exception_details(self):
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        secret = "raw model output claim_forged=secret must not leak"

        class FailingReportCrew:
            kickoff_calls = 0

            def kickoff(self, *, inputs):
                self.kickoff_calls += 1
                cause = ValueError(secret)
                failure = RuntimeError(secret)
                failure.__cause__ = cause
                cause.__cause__ = failure
                raise failure

        report_crew = FailingReportCrew()
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)
        events = []
        flow._progress_callback = events.append

        with _offline_flow_patches(parser_result, verdict={"status": "ready"}):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stage"], "report")
        self.assertIsNone(result["report"])
        self.assertEqual(result["required_data"], ["report_provider_error"])
        self.assertEqual(result["analysis_diagnostics"]["domain"], "report")
        self.assertEqual(
            result["analysis_diagnostics"]["reason_code"],
            "report_provider_error",
        )
        report_event = next(
            event
            for event in events
            if event.step == 7 and event.status == "blocked"
        )
        self.assertIn("report_kickoff:RuntimeError", report_event.output_summary)
        self.assertIn("report_kickoff:RuntimeError", report_event.reason)
        serialized = json.dumps(
            {"result": result, "event": report_event.__dict__},
            ensure_ascii=False,
        )
        self.assertNotIn(secret, serialized)
        self.assertEqual(report_crew.kickoff_calls, 1)

    def test_report_guardrail_exhaustion_uses_deterministic_safe_draft(self):
        for last_error in ("report_draft_not_json", "report_draft_forbidden_number"):
            with self.subTest(last_error=last_error):
                class GuardrailExhaustedReportCrew:
                    kickoff_calls = 0

                    def kickoff(self, *, inputs):
                        self.kickoff_calls += 1
                        raise RuntimeError(
                            "Task failed guardrail validation after 2 retries. "
                            f"Last error: {last_error}"
                        )

                analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
                report_crew = GuardrailExhaustedReportCrew()
                parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)
                events = []
                flow._progress_callback = events.append

                with _offline_flow_patches(parser_result, verdict={"status": "ready"}):
                    result = _run_flow(flow)

                self.assertEqual(result["status"], "ok")
                self.assertIn(
                    "SEC 申报中的年度及季度数据、已验证计算和市场数据；季度数据可能未经审计。",
                    result["report"],
                )
                self.assertNotIn("经审计财务数据", result["report"])
                self.assertNotIn("报告由已验证研究结果生成。", result["report"])
                self.assertEqual(report_crew.kickoff_calls, 1)
                report_event = next(
                    event
                    for event in events
                    if event.step == 7 and event.status == "completed"
                )
                self.assertIn("draft_source=deterministic_safe_draft", report_event.output_summary)
                self.assertIn("report_guardrail_retries_exhausted", report_event.reason)

    def test_report_provider_connection_error_blocks_with_stable_code(self):
        secret = "provider raw secret"

        class ProviderFailureReportCrew:
            kickoff_calls = 0

            def kickoff(self, *, inputs):
                self.kickoff_calls += 1
                raise ConnectionError(secret)

        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = ProviderFailureReportCrew()
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)

        with _offline_flow_patches(parser_result, verdict={"status": "ready"}):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["required_data"], ["report_provider_error"])
        self.assertIsNone(result["report"])
        self.assertNotIn("deterministic_safe_draft", json.dumps(result))
        self.assertNotIn(secret, json.dumps(result))

    def test_report_renderer_failure_blocks_with_renderer_error(self):
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)
        events = []
        flow._progress_callback = events.append

        with (
            _offline_flow_patches(parser_result, verdict={"status": "ready"}),
            patch.object(
                importlib.import_module("stockcrewai.flow"),
                "render_validated_report",
                side_effect=RuntimeError("renderer implementation detail"),
            ),
        ):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stage"], "report")
        self.assertEqual(result["required_data"], ["report_renderer_error"])
        self.assertIsNone(result["report"])
        self.assertEqual(
            result["analysis_diagnostics"]["reason_code"], "report_renderer_error"
        )
        self.assertNotIn("renderer implementation detail", json.dumps(result))
        report_event = next(
            event
            for event in events
            if event.step == 7 and event.status == "blocked"
        )
        self.assertIn("renderer:RuntimeError", report_event.output_summary)
        self.assertIn("renderer:RuntimeError", report_event.reason)
        self.assertNotIn("renderer implementation detail", json.dumps(report_event.__dict__))
        self.assertEqual(report_crew.kickoff_calls, 1)

    def test_report_result_getter_failure_blocks_without_leaking_secret(self):
        secret = "raw-model-secret-should-not-leak"
        events = []

        class ExplodingReportResult:
            @property
            def json_dict(self):
                raise RuntimeError(secret)

            @property
            def pydantic(self):
                raise RuntimeError(secret)

            @property
            def raw(self):
                raise RuntimeError(secret)

        class GetterFailureReportCrew:
            kickoff_calls = 0

            def kickoff(self, *, inputs):
                self.kickoff_calls += 1
                return ExplodingReportResult()

        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = GetterFailureReportCrew()
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)
        flow._progress_callback = events.append

        with _offline_flow_patches(parser_result, verdict={"status": "ready"}):
            try:
                result = _run_flow(flow)
            except Exception as exc:
                self.fail(f"Report result getter escaped: {type(exc).__name__}")

        serialized = json.dumps(
            {
                "result": result,
                "events": [event.__dict__ for event in events],
            },
            ensure_ascii=False,
        )
        self.assertNotIn(secret, serialized)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stage"], "report")
        self.assertIsNone(result["report"])
        self.assertEqual(result["required_data"], ["report_provider_error"])
        report_event = next(
            event
            for event in events
            if event.step == 7 and event.status == "blocked"
        )
        self.assertIn("report_output:RuntimeError", report_event.output_summary)
        self.assertIn("report_output:RuntimeError", report_event.reason)
        self.assertEqual(report_crew.kickoff_calls, 1)

    def test_report_draft_parse_failure_blocks_before_renderer(self):
        invalid_payload = json.loads(VALID_REPORT_DRAFT)
        secret = "raw model output claim_forged=secret"
        invalid_payload["execution_summary"] = f"非法数字 42；{secret}。"
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(json.dumps(invalid_payload, ensure_ascii=False))
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)
        events = []
        flow._progress_callback = events.append

        with (
            _offline_flow_patches(parser_result, verdict={"status": "ready"}),
            patch.object(
                importlib.import_module("stockcrewai.flow"),
                "render_validated_report",
                side_effect=AssertionError("ReportDraft parse failure reached Renderer"),
            ) as renderer,
        ):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stage"], "report")
        self.assertEqual(result["required_data"], ["report_draft_forbidden_number"])
        self.assertIsNone(result["report"])
        renderer.assert_not_called()
        report_event = next(
            event
            for event in events
            if event.step == 7 and event.status == "blocked"
        )
        self.assertIn("report_parse:ReportDraftError", report_event.output_summary)
        self.assertIn("report_parse:ReportDraftError", report_event.reason)
        serialized = json.dumps(
            {"result": result, "events": [event.__dict__ for event in events]},
            ensure_ascii=False,
        )
        self.assertNotIn(secret, serialized)
        self.assertEqual(report_crew.kickoff_calls, 1)

    def test_report_crew_and_renderer_share_one_json_safe_report_context(self):
        flow_module = importlib.import_module("stockcrewai.flow")
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)
        rendered_context = {}
        original_renderer = flow_module.render_validated_report

        def capture_renderer(*args, **kwargs):
            rendered_context["value"] = (
                kwargs["report_context"] if "report_context" in kwargs else args[0]
            )
            return original_renderer(*args, **kwargs)

        with (
            _offline_flow_patches(parser_result, verdict={"status": "ready"}),
            patch.object(flow_module, "render_validated_report", side_effect=capture_renderer),
        ):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "ok")
        report_context = rendered_context["value"]
        self.assertNotIn("report_context", report_crew.inputs)
        self.assertIn("narrative_context", report_crew.inputs)
        json.dumps(report_context, ensure_ascii=False, allow_nan=False)

    def test_broken_pipe_progress_callback_is_disabled_without_interrupting_flow(self):
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)
        callback_calls = 0

        def broken_callback(_event):
            nonlocal callback_calls
            callback_calls += 1
            raise BrokenPipeError("terminal closed")

        flow._progress_callback = broken_callback

        with _offline_flow_patches(parser_result, verdict={"status": "ready"}):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "ok")
        self.assertGreaterEqual(callback_calls, 1)
        self.assertIsNone(flow._progress_callback)
        self.assertEqual(report_crew.kickoff_calls, 1)

    def test_flow_state_hides_raw_analysis_diagnostics_but_result_keeps_them(self):
        invalid_outputs = _valid_analysis_outputs()
        invalid_outputs[0] = "raw financial analysis output"
        analysis_crew = RecordingCrew(task_raws=invalid_outputs)
        report_crew = RecordingCrew("must not run")
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)

        with _offline_flow_patches(parser_result):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("raw_task_outputs", flow.state.analysis_diagnostics)
        self.assertNotIn(
            "raw_task_outputs",
            json.dumps(flow.state.model_dump(mode="json"), ensure_ascii=False),
        )
        self.assertEqual(
            result["analysis_diagnostics"]["raw_task_outputs"]["financial"],
            "raw financial analysis output",
        )
        self.assertEqual(
            flow.state.analysis_diagnostics["reason_code"], "raw_json_invalid"
        )

    def test_analysis_gate_block_stops_analysis_and_report_crews(self):
        analysis_crew = RecordingCrew("must not run")
        report_crew = RecordingCrew("must not run")
        parser_result, flow, dependencies = self._make_flow(
            analysis_crew, report_crew
        )
        dependencies["edgar_tool"].run.return_value.filings[0].risk_sections = []

        with _offline_flow_patches(parser_result):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stage"], "analysis")
        self.assertIn("risk_evidence_missing", result["required_data"])
        self.assertEqual(analysis_crew.kickoff_calls, 0)
        self.assertEqual(report_crew.kickoff_calls, 0)
        self.assertEqual(flow.state.status, "blocked")
        self.assertEqual(flow.state.stage, "analysis")

    def test_claim_gate_block_stops_verdict_and_report(self):
        invalid_outputs = _valid_analysis_outputs()
        invalid_outputs[0] = "not JSON"
        analysis_crew = RecordingCrew(task_raws=invalid_outputs)
        report_crew = RecordingCrew("must not run")
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)

        with _offline_flow_patches(
            parser_result, verdict={"status": "must not run"}
        ) as (_, verdict_mocks):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stage"], "analysis")
        self.assertEqual(result["required_data"], ["analysis_output_invalid"])
        self.assertEqual(analysis_crew.kickoff_calls, 1)
        self.assertEqual(getattr(flow.state, "analysis_attempts", 0), 1)
        self.assertEqual(report_crew.kickoff_calls, 0)
        self.assertEqual(sum(mock.call_count for mock in verdict_mocks), 0)


class MainEntrypointTests(unittest.TestCase):
    def test_request_resolution_requires_explicit_company_request(self):
        module = _main_module()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(module.sys, "argv", ["kickoff"]),
            self.assertRaisesRegex(ValueError, "STOCKCREWAI_REQUEST"),
        ):
            module._resolve_request()

    def test_request_resolution_uses_explicit_environment_request(self):
        module = _main_module()
        with patch.dict(
            os.environ,
            {"STOCKCREWAI_REQUEST": "请分析 Netflix（NFLX）"},
            clear=True,
        ):
            self.assertEqual(
                module._resolve_request(),
                "请分析 Netflix（NFLX）",
            )

    def test_kickoff_exports_validated_report_with_one_trailing_newline(self):
        module = _main_module()
        report = "# 正式报告\n\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "run-output.md"
            with patch.object(
                module,
                "run_research",
                return_value={"status": "ok", "stage": "report", "report": report},
            ):
                module.kickoff(REQUEST, output_path=output_path)

            exported = output_path.with_name("investment-report.md")
            exported_text = exported.read_text(encoding="utf-8")
            self.assertEqual(exported_text, "# 正式报告\n")
            exported_bytes = exported_text.encode("utf-8")
            result_path = output_path.with_name("run-result.json")
            persisted = json.loads(result_path.read_text(encoding="utf-8"))

        self.assertEqual(persisted["report"], exported_text)
        self.assertEqual(
            persisted["artifacts"]["report_sha256"],
            hashlib.sha256(exported_bytes).hexdigest(),
        )
        self.assertEqual(persisted["artifacts"]["report_bytes"], len(exported_bytes))

    def test_kickoff_removes_stale_report_when_run_has_no_formal_report(self):
        module = _main_module()
        results = (
            {"status": "blocked", "stage": "analysis", "report": "# 不应导出"},
            {"status": "ok", "stage": "report", "report": None},
        )
        for run_result in results:
            with self.subTest(run_result=run_result), tempfile.TemporaryDirectory() as temp_dir:
                output_path = Path(temp_dir) / "run-output.md"
                exported = output_path.with_name("investment-report.md")
                exported.write_text("旧正式报告\n", encoding="utf-8")
                with patch.object(module, "run_research", return_value=run_result):
                    module.kickoff(REQUEST, output_path=output_path)

                self.assertFalse(exported.exists())
                persisted = json.loads(
                    output_path.with_name("run-result.json").read_text(encoding="utf-8")
                )
                artifacts = persisted.get("artifacts", {})
                self.assertNotIn("report_path", artifacts)
                self.assertNotIn("report_sha256", artifacts)
                self.assertNotIn("report_bytes", artifacts)

    def test_kickoff_exports_report_next_to_custom_output_path(self):
        module = _main_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "nested" / "run-output.md"
            with patch.object(
                module,
                "run_research",
                return_value={"status": "ok", "stage": "report", "report": "正文"},
            ):
                module.kickoff(REQUEST, output_path=output_path)

            exported = output_path.with_name("investment-report.md")
            self.assertEqual(exported.read_text(encoding="utf-8"), "正文\n")
            result_path = output_path.with_name("run-result.json")
            persisted = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {output_path.parent, exported.parent, result_path.parent},
                {Path(temp_dir) / "nested"},
            )
            self.assertEqual(persisted["report"], exported.read_text(encoding="utf-8"))

    def test_kickoff_does_not_claim_report_when_atomic_export_fails(self):
        module = _main_module()
        run_output = importlib.import_module("stockcrewai.run_output")
        real_atomic_write = getattr(run_output, "_atomic_write_text", None)

        def fail_formal_report(path, text):
            if path.name == "investment-report.md":
                raise OSError("formal report replace failed")
            if real_atomic_write is not None:
                return real_atomic_write(path, text)
            return path.write_text(text, encoding="utf-8")

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "run-output.md"
            exported = output_path.with_name("investment-report.md")
            exported.write_text("旧正式报告\n", encoding="utf-8")
            with (
                patch.object(
                    module,
                    "run_research",
                    return_value={
                        "status": "ok",
                        "stage": "report",
                        "report": "新正式报告",
                    },
                ),
                patch.object(
                    run_output,
                    "_atomic_write_text",
                    side_effect=fail_formal_report,
                    create=True,
                ),
            ):
                module.kickoff(REQUEST, output_path=output_path)

            self.assertFalse(exported.exists())
            persisted = json.loads(
                output_path.with_name("run-result.json").read_text(encoding="utf-8")
            )
            artifacts = persisted.get("artifacts", {})
            self.assertNotIn("report_path", artifacts)
            self.assertNotIn("report_sha256", artifacts)
            self.assertNotIn("report_bytes", artifacts)

    def test_kickoff_swallows_error_reporter_failure_and_does_not_append_raw_error(self):
        module = _main_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "run-output.md"
            with (
                patch.object(
                    module,
                    "run_research",
                    side_effect=RuntimeError("raw exception details"),
                ),
                patch.object(
                    module.CompactRunReporter,
                    "emit",
                    side_effect=BrokenPipeError("terminal closed"),
                ),
                patch.object(
                    module,
                    "sanitize_text",
                    return_value="sanitized exception details",
                ) as sanitize_text,
            ):
                result = module.kickoff(REQUEST, output_path=output_path)

            self.assertEqual(result, 1)
            sanitize_text.assert_called_once_with("raw exception details")
            saved_text = output_path.read_text(encoding="utf-8")
            self.assertNotIn("异常摘要", saved_text)
            saved_result = json.loads(
                output_path.with_name("run-result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                saved_result["error"]["message"], "sanitized exception details"
            )

    def test_kickoff_runs_full_flow_entrypoint_and_preserves_run_output(self):
        module = _main_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            original_cwd = Path.cwd()
            os.chdir(temp_dir)
            try:
                with (
                    patch.dict(
                        os.environ,
                        {"STOCKCREWAI_REQUEST": "环境变量请求"},
                        clear=False,
                    ),
                    patch.object(module.sys, "argv", ["kickoff", "命令行请求"]),
                    patch.object(
                        module,
                        "run_research",
                        return_value={
                            "status": "ok",
                            "stage": "report",
                            "report": "offline",
                        },
                    ) as run_research,
                    patch.object(module, "RequestParserCrew", create=True) as parser,
                ):
                    result = module.kickoff(REQUEST)

                saved = Path("run-output.md")
                self.assertTrue(saved.is_file())
                saved_text = saved.read_text(encoding="utf-8")
                self.assertIn("- 业务状态（status）：`ok`", saved_text)
                self.assertIn("- 阶段（stage）：`report`", saved_text)
                self.assertNotIn('"status": "ok"', saved_text)
                self.assertNotIn('"report": "offline"', saved_text)
                self.assertNotIn("\x1b", saved_text)

                result_path = Path("run-result.json")
                self.assertTrue(result_path.is_file())
                with result_path.open(encoding="utf-8") as result_file:
                    saved_result = json.load(result_file)
                self.assertEqual(saved_result["status"], "ok")
                self.assertEqual(saved_result["report"], "offline\n")
            finally:
                os.chdir(original_cwd)

        self.assertIn(result, (None, 0))
        run_research.assert_called_once_with(REQUEST, progress_callback=ANY)
        parser.assert_not_called()

    def test_plot_delegates_to_named_flow_plot(self):
        module = _main_module()
        plot_entrypoint = getattr(module, "plot", None)
        self.assertTrue(callable(plot_entrypoint))

        with patch.object(module.ResearchFlow, "plot") as flow_plot:
            result = plot_entrypoint()

        self.assertIsNone(result)
        flow_plot.assert_called_once_with("stockcrewai_flow")

    def test_pyproject_exposes_kickoff_and_plot_scripts(self):
        project_root = Path(__file__).resolve().parents[1]
        with (project_root / "pyproject.toml").open("rb") as project_file:
            scripts = tomllib.load(project_file)["project"]["scripts"]

        self.assertEqual(scripts.get("kickoff"), "stockcrewai.main:kickoff")
        self.assertEqual(scripts.get("plot"), "stockcrewai.main:plot")


if __name__ == "__main__":
    unittest.main()
