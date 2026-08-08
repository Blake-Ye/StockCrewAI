from __future__ import annotations

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
    VALID_REPORT,
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
    def test_state_and_flow_are_defined_directly_in_main(self):
        _, flow, state = _flow_symbols()

        self.assertEqual(state.__module__, "stockcrewai.main")
        self.assertEqual(flow.__module__, "stockcrewai.main")
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
            "analysis",
            "required_data",
            "analysis_diagnostics",
        )
        for field_name in mutable_fields:
            first_value = getattr(first, field_name)
            second_value = getattr(second, field_name)
            self.assertIsInstance(first_value, (dict, list), field_name)
            self.assertIsNot(first_value, second_value, field_name)

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

    def test_empty_claim_gate_result_retries_once_then_remains_blocked(self):
        empty_outputs = _valid_analysis_outputs()
        empty_outputs[1] = json.dumps({"claims": []}, ensure_ascii=False)
        analysis_crew = SequencedAnalysisCrew([empty_outputs, empty_outputs])
        report_crew = RecordingCrew("must not run")
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)
        events = []
        flow._progress_callback = events.append

        with _offline_flow_patches(
            parser_result, verdict={"status": "must not run"}
        ) as (_, verdict_mocks):
            result = _run_flow(flow)

        self.assertEqual(analysis_crew.kickoff_calls, 2)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stage"], "analysis")
        self.assertEqual(result["required_data"], ["risk_analysis_claims_required"])
        self.assertIsNone(result["verdict"])
        self.assertIsNone(result["report"])
        self.assertEqual(getattr(flow.state, "analysis_attempts", 0), 2)
        self.assertEqual(report_crew.kickoff_calls, 0)
        self.assertEqual(sum(mock.call_count for mock in verdict_mocks), 0)
        analysis_events = [event for event in events if event.step == 5]
        self.assertEqual(len(analysis_events), 1)
        self.assertIn("attempts=2", analysis_events[0].output_summary)

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
        self.assertIn("## 执行摘要", result["report"])
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

    def test_report_kickoff_failure_uses_deterministic_fallback(self):
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())

        class FailingReportCrew:
            kickoff_calls = 0

            def kickoff(self, *, inputs):
                self.kickoff_calls += 1
                raise RuntimeError(
                    "BadRequestError: raw model output claim_forged=secret must not leak"
                )

        report_crew = FailingReportCrew()
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)
        events = []
        flow._progress_callback = events.append

        with _offline_flow_patches(parser_result, verdict={"status": "ready"}):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stage"], "report")
        self.assertTrue(result["report"])
        self.assertEqual(result["required_data"], [])
        self.assertEqual(result["analysis_diagnostics"], {})
        self.assertNotIn("raw model output", result["report"])
        self.assertNotIn("claim_forged", result["report"])
        report_event = next(
            event
            for event in events
            if event.step == 7 and event.status == "completed"
        )
        self.assertIn("draft_source=deterministic_fallback", report_event.output_summary)
        self.assertIn("fallback_reason=fallback:RuntimeError", report_event.output_summary)
        self.assertNotIn("claim_forged", report_event.output_summary)
        self.assertNotIn("raw model output", report_event.output_summary)
        self.assertEqual(report_crew.kickoff_calls, 1)

    def test_report_fallback_renderer_failure_remains_report_output_invalid(self):
        module = _main_module()
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())

        class FailingReportCrew:
            kickoff_calls = 0

            def kickoff(self, *, inputs):
                self.kickoff_calls += 1
                raise RuntimeError("guardrail model output must stay hidden")

        report_crew = FailingReportCrew()
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)

        with (
            _offline_flow_patches(parser_result, verdict={"status": "ready"}),
            patch.object(
                module,
                "render_validated_report",
                side_effect=RuntimeError("renderer implementation detail"),
            ),
        ):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stage"], "report")
        self.assertEqual(result["required_data"], ["report_output_invalid"])
        self.assertIsNone(result["report"])
        self.assertEqual(
            result["analysis_diagnostics"]["reason_code"], "report_output_invalid"
        )
        self.assertNotIn("renderer implementation detail", json.dumps(result))
        self.assertNotIn("guardrail model output", json.dumps(result))
        self.assertEqual(report_crew.kickoff_calls, 1)

    def test_report_deterministic_fallback_failure_remains_report_output_invalid(self):
        module = _main_module()
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())

        class FailingReportCrew:
            kickoff_calls = 0

            def kickoff(self, *, inputs):
                self.kickoff_calls += 1
                raise RuntimeError("model output must stay hidden")

        report_crew = FailingReportCrew()
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)

        with (
            _offline_flow_patches(parser_result, verdict={"status": "ready"}),
            patch.object(
                module,
                "build_deterministic_report_draft",
                side_effect=RuntimeError("fallback implementation detail"),
            ),
        ):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stage"], "report")
        self.assertEqual(result["required_data"], ["report_output_invalid"])
        self.assertIsNone(result["report"])
        self.assertEqual(
            result["analysis_diagnostics"]["reason_code"], "report_output_invalid"
        )
        self.assertNotIn("fallback implementation detail", json.dumps(result))
        self.assertEqual(report_crew.kickoff_calls, 1)

    def test_report_result_getter_failure_falls_back_without_leaking_secret(self):
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
        self.assertIn(result["status"], {"ok", "blocked"})
        if result["status"] == "ok":
            self.assertTrue(result["report"])
        else:
            self.assertEqual(result["required_data"], ["report_output_invalid"])
        self.assertEqual(report_crew.kickoff_calls, 1)

    def test_invalid_constructed_fallback_is_rejected_before_renderer(self):
        from stockcrewai.crews.report.crew import ReportDraft, parse_report_draft

        valid_draft = parse_report_draft(VALID_REPORT_DRAFT)
        invalid_payload = valid_draft.model_dump()
        invalid_payload["execution_summary"] = "非法 fallback 数字 42。"
        invalid_fallback = ReportDraft.model_construct(**invalid_payload)
        module = _main_module()
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())

        class FailingReportCrew:
            kickoff_calls = 0

            def kickoff(self, *, inputs):
                self.kickoff_calls += 1
                raise RuntimeError("guardrail failure")

        report_crew = FailingReportCrew()
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)

        with (
            _offline_flow_patches(parser_result, verdict={"status": "ready"}),
            patch.object(
                module,
                "build_deterministic_report_draft",
                return_value=invalid_fallback,
            ),
            patch.object(
                module,
                "render_validated_report",
                side_effect=AssertionError("invalid fallback reached Renderer"),
            ) as renderer,
        ):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stage"], "report")
        self.assertEqual(result["required_data"], ["report_output_invalid"])
        self.assertIsNone(result["report"])
        renderer.assert_not_called()
        self.assertEqual(report_crew.kickoff_calls, 1)

    def test_report_crew_and_renderer_share_one_json_safe_report_context(self):
        module = _main_module()
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)
        parser_result, flow, _ = self._make_flow(analysis_crew, report_crew)
        rendered_context = {}
        original_renderer = module.render_validated_report

        def capture_renderer(*args, **kwargs):
            rendered_context["value"] = (
                kwargs["report_context"] if "report_context" in kwargs else args[0]
            )
            return original_renderer(*args, **kwargs)

        with (
            _offline_flow_patches(parser_result, verdict={"status": "ready"}),
            patch.object(module, "render_validated_report", side_effect=capture_renderer),
        ):
            result = _run_flow(flow)

        self.assertEqual(result["status"], "ok")
        report_context = report_crew.inputs["report_context"]
        self.assertIs(report_context, rendered_context["value"])
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
        self.assertIn("risk_sections_required", result["required_data"])
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
                self.assertEqual(saved_result["report"], "offline")
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
