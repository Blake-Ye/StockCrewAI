from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


_TEST_FLOW_STORAGE = tempfile.TemporaryDirectory(prefix="stockcrewai-flow-module-")
os.environ.setdefault("CREWAI_STORAGE_DIR", _TEST_FLOW_STORAGE.name)

_FLOW_LABELS = {
    "analysis_ready",
    "analysis_blocked",
    "claims_ready",
    "claims_blocked",
}
_FLOW_EDGES = {
    "prepare_evidence": ("ListenMethod", "parse_request"),
    "prepare_valuation": ("ListenMethod", "prepare_evidence"),
    "route_analysis": ("RouterMethod", "prepare_valuation"),
    "finalize_analysis_blocked": ("ListenMethod", "analysis_blocked"),
    "run_analysis": ("ListenMethod", "analysis_ready"),
    "route_claims": ("RouterMethod", "run_analysis"),
    "finalize_claims_blocked": ("ListenMethod", "claims_blocked"),
    "generate_report": ("ListenMethod", "claims_ready"),
}


def _flow_module():
    return importlib.import_module("stockcrewai.flow")


def _definitions(flow_class: type[Any]) -> dict[str, Any]:
    return {
        name: getattr(member, "__flow_method_definition__", None)
        for name, member in flow_class.__dict__.items()
    }


def test_plot_copies_returned_html_without_network_or_llm(tmp_path, monkeypatch):
    main_module = importlib.import_module("stockcrewai.main")
    monkeypatch.chdir(tmp_path)
    source_dir = tmp_path / "crewai-temp"
    source_dir.mkdir()
    source_contents = {
        "flow.html": "<html><body>offline flow</body></html>",
        "flow_style.css": "body { background: #123456; }",
        "flow_script.js": "window.flowReady = true;",
    }
    for filename, content in source_contents.items():
        (source_dir / filename).write_text(content, encoding="utf-8")

    with patch(
        "stockcrewai.main.ResearchFlow.plot",
        return_value=source_dir / "flow.html",
    ) as flow_plot:
        result = main_module.plot()

    flow_plot.assert_called_once_with("stockcrewai_flow")
    expected_targets = {
        "stockcrewai_flow.html": source_contents["flow.html"],
        "stockcrewai_flow_style.css": source_contents["flow_style.css"],
        "stockcrewai_flow_script.js": source_contents["flow_script.js"],
    }
    for filename, content in expected_targets.items():
        assert (tmp_path / filename).read_text(encoding="utf-8") == content
    assert result is None


class FlowModuleDefinitionTests(unittest.TestCase):
    def test_flow_module_imports_without_importing_main(self):
        module = _flow_module()

        self.assertEqual(module.__name__, "stockcrewai.flow")
        self.assertTrue(hasattr(module, "ResearchFlow"))
        self.assertTrue(hasattr(module, "ResearchFlowState"))
        source = Path(module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from stockcrewai.main", source)
        self.assertNotIn("import stockcrewai.main", source)

    def test_flow_state_is_json_safe_and_keeps_runtime_dependencies_private(self):
        module = _flow_module()
        marker = object()
        state = module.ResearchFlowState(request="离线测试")
        flow = module.ResearchFlow(edgar_tool=marker)
        flow.state.request = "离线测试"

        state_payload = state.model_dump(mode="json")
        flow_payload = flow.state.model_dump(mode="json")
        json.dumps(state_payload, ensure_ascii=False)
        json.dumps(flow_payload, ensure_ascii=False)

        self.assertEqual(state_payload["request"], "离线测试")
        self.assertEqual(flow_payload["request"], "离线测试")
        self.assertIs(flow._edgar_tool, marker)
        self.assertNotIn("edgar_tool", flow_payload)

    def test_new_flow_has_the_complete_decorated_graph(self):
        module = _flow_module()
        definitions = _definitions(module.ResearchFlow)
        starts = [
            name
            for name, definition in definitions.items()
            if definition is not None and bool(definition.start)
        ]

        self.assertEqual(starts, ["parse_request"])
        for method_name, (method_kind, listened_to) in _FLOW_EDGES.items():
            member = module.ResearchFlow.__dict__.get(method_name)
            self.assertIsNotNone(member, method_name)
            self.assertEqual(type(member).__name__, method_kind, method_name)
            definition = getattr(member, "__flow_method_definition__", None)
            self.assertIsNotNone(definition, method_name)
            self.assertEqual(definition.listen, listened_to, method_name)
            self.assertEqual(bool(definition.router), method_kind == "RouterMethod")

        self.assertEqual(
            module.ResearchFlow.route_analysis.__flow_method_definition__.emit,
            ["analysis_ready", "analysis_blocked"],
        )
        self.assertEqual(
            module.ResearchFlow.route_claims.__flow_method_definition__.emit,
            ["claims_ready", "claims_blocked"],
        )
        self.assertIsNotNone(
            getattr(module.ResearchFlow, "__flow_persistence_config__", None)
        )

    def test_route_labels_match_the_previous_main_flow(self):
        old_module = importlib.import_module("stockcrewai.main")
        new_module = _flow_module()
        old_definitions = _definitions(old_module.ResearchFlow)
        new_definitions = _definitions(new_module.ResearchFlow)

        for method_name in ("route_analysis", "route_claims"):
            self.assertEqual(
                new_definitions[method_name].emit,
                old_definitions[method_name].emit,
            )

        old_labels = {
            definition.listen
            for definition in old_definitions.values()
            if definition is not None and definition.listen in _FLOW_LABELS
        }
        new_labels = {
            definition.listen
            for definition in new_definitions.values()
            if definition is not None and definition.listen in _FLOW_LABELS
        }
        self.assertEqual(new_labels, old_labels)
        self.assertEqual(new_labels, _FLOW_LABELS)

    def test_main_reexports_the_canonical_flow_and_reporting_symbols(self):
        old_module = importlib.import_module("stockcrewai.main")
        new_module = _flow_module()
        report_crew = importlib.import_module("stockcrewai.crews.report.crew")
        context = importlib.import_module("stockcrewai.reporting.context")
        renderer = importlib.import_module("stockcrewai.reporting.renderer")
        validator = importlib.import_module("stockcrewai.reporting.validator")
        flow_source = Path(new_module.__file__).read_text(encoding="utf-8")

        self.assertIs(old_module.ResearchFlow, new_module.ResearchFlow)
        self.assertIs(old_module.ResearchFlowState, new_module.ResearchFlowState)
        self.assertEqual(old_module.ResearchFlow.__module__, "stockcrewai.flow")
        self.assertIs(report_crew.ReportContext, context.ReportContext)
        self.assertIs(report_crew.ReportMetric, context.ReportMetric)
        self.assertIs(report_crew.build_report_context, context.build_report_context)
        self.assertIs(report_crew.build_narrative_context, renderer.build_narrative_context)
        self.assertIs(report_crew.render_validated_report, renderer.render_validated_report)
        self.assertIs(report_crew.ReportDraft, validator.ReportDraft)
        self.assertIs(report_crew.parse_report_draft, validator.parse_report_draft)
        self.assertIs(report_crew.validate_rendered_report, validator.validate_rendered_report)
        self.assertNotIn(
            "from stockcrewai.crews.report.crew import (\n    ReportCrew,\n    ReportDraft",
            flow_source,
        )


if __name__ == "__main__":
    unittest.main()
