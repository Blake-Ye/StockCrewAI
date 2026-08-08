from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_crew_configuration import VALID_REPORT_DRAFT, _valid_pipeline_fakes


_TEST_FLOW_STORAGE = tempfile.TemporaryDirectory(prefix="stockcrewai-valuation-claims-")
os.environ.setdefault("CREWAI_STORAGE_DIR", _TEST_FLOW_STORAGE.name)


def _valuation_payload() -> dict[str, object]:
    return {
        "company_name": "Example Holdings",
        "ticker": "ZZZ",
        "valuation_result": {
            "readiness": "ready",
            "validation_status": "valid",
            "calculations": [
                {
                    "calculation_id": "calc_pe_ratio",
                    "status": "available",
                    "validation_status": "valid",
                    "input_evidence_ids": ["ev_price", "ev_eps"],
                },
                {
                    "calculation_id": "calc_fcf_yield",
                    "status": "available",
                    "validation_status": "valid",
                    "input_evidence_ids": ["ev_price", "ev_fcf"],
                },
            ],
        },
        "historical_valuation_result": {
            "status": "ok",
            "validation_status": "valid",
            "calculation_id": "calc_historical",
            "input_evidence_ids": ["ev_history"],
        },
        "reverse_dcf_result": {
            "status": "ok",
            "validation_status": "valid",
            "calculation_id": "calc_reverse_dcf",
            "input_evidence_ids": ["ev_price", "ev_fcf"],
        },
        "validated_evidence_ids": [
            "ev_price",
            "ev_eps",
            "ev_fcf",
            "ev_history",
        ],
        "validated_calculation_ids": [
            "calc_pe_ratio",
            "calc_fcf_yield",
            "calc_historical",
            "calc_reverse_dcf",
        ],
    }


def _claim_output(claims: list[dict[str, object]]) -> str:
    return json.dumps({"claims": claims}, ensure_ascii=False)


def _financial_output(*, empty: bool = False) -> str:
    claims = []
    if not empty:
        claims = [
            {
                "claim_id": "claim_financial_quality",
                "category": "financial_quality",
                "statement": "财务质量可由已验证输入解释。",
                "evidence_ids": ["ev_revenue"],
                "calculation_ids": ["calc_margin"],
                "confidence": 0.9,
            },
            {
                "claim_id": "claim_financial_trend",
                "category": "financial_trend",
                "statement": "财务趋势可由已验证输入解释。",
                "evidence_ids": ["ev_revenue"],
                "calculation_ids": ["calc_margin"],
                "confidence": 0.9,
            },
        ]
    return _claim_output(claims)


def _risk_output(*, empty: bool = False) -> str:
    claims = []
    if not empty:
        claims = [
            {
                "claim_id": "claim_risk",
                "category": "risk",
                "statement": "申报文本包含可审计风险。",
                "evidence_ids": ["ev_filing"],
                "calculation_ids": [],
                "confidence": 0.9,
            }
        ]
    return _claim_output(claims)


class _SequenceAnalysisCrew:
    def __init__(self, sequences: list[list[str]]) -> None:
        self.sequences = sequences
        self.inputs: list[dict[str, object]] = []
        self.kickoff_calls = 0

    def kickoff(self, *, inputs: dict[str, object]) -> SimpleNamespace:
        sequence_index = min(self.kickoff_calls, len(self.sequences) - 1)
        self.inputs.append(inputs)
        self.kickoff_calls += 1
        return SimpleNamespace(
            raw=None,
            tasks_output=[
                SimpleNamespace(raw=raw) for raw in self.sequences[sequence_index]
            ],
        )


class _ReportCrew:
    def __init__(self) -> None:
        self.kickoff_calls = 0

    def kickoff(self, *, inputs: dict[str, object]) -> SimpleNamespace:
        self.kickoff_calls += 1
        return SimpleNamespace(raw=VALID_REPORT_DRAFT)


class DeterministicValuationClaimTests(unittest.TestCase):
    def test_valid_dynamic_payload_builds_three_allowlisted_claims(self):
        from stockcrewai.pipeline_support import build_deterministic_valuation_claims

        payload = _valuation_payload()
        claims = build_deterministic_valuation_claims(payload)

        self.assertEqual(
            [claim["category"] for claim in claims],
            ["current_valuation", "historical_valuation", "reverse_dcf"],
        )
        evidence_allowlist = set(payload["validated_evidence_ids"])
        calculation_allowlist = set(payload["validated_calculation_ids"])
        for claim in claims:
            self.assertTrue(claim["evidence_ids"])
            self.assertTrue(claim["calculation_ids"])
            self.assertTrue(set(claim["evidence_ids"]) <= evidence_allowlist)
            self.assertTrue(set(claim["calculation_ids"]) <= calculation_allowlist)
            self.assertEqual(claim["confidence"], 1.0)

        serialized = json.dumps(claims, ensure_ascii=False)
        self.assertNotIn("Apple", serialized)
        self.assertNotIn("AAPL", serialized)
        self.assertNotIn("100", serialized)
        self.assertNotIn("25", serialized)

    def test_invalid_status_or_missing_allowlisted_id_fails_closed(self):
        from stockcrewai.pipeline_support import build_deterministic_valuation_claims

        cases = []
        invalid_current = _valuation_payload()
        invalid_current["valuation_result"]["readiness"] = "not_ready"
        cases.append(("current status", invalid_current))

        invalid_historical = _valuation_payload()
        invalid_historical["historical_valuation_result"]["status"] = "unavailable"
        cases.append(("historical status", invalid_historical))

        invalid_reverse = _valuation_payload()
        invalid_reverse["reverse_dcf_result"]["validation_status"] = "unvalidated"
        cases.append(("reverse validation", invalid_reverse))

        missing_calculation = _valuation_payload()
        missing_calculation["validated_calculation_ids"].remove("calc_historical")
        cases.append(("calculation allowlist", missing_calculation))

        missing_evidence = _valuation_payload()
        missing_evidence["validated_evidence_ids"].remove("ev_history")
        cases.append(("evidence allowlist", missing_evidence))

        for name, payload in cases:
            with self.subTest(case=name):
                self.assertEqual(build_deterministic_valuation_claims(payload), [])

    def test_current_calculation_requires_explicit_status_fields(self):
        from stockcrewai.pipeline_support import build_deterministic_valuation_claims

        for field in ("status", "validation_status"):
            with self.subTest(field=field):
                payload = _valuation_payload()
                payload["valuation_result"]["calculations"][0].pop(field)
                self.assertEqual(build_deterministic_valuation_claims(payload), [])

    def test_valuation_input_does_not_self_authorize_injected_ids(self):
        from stockcrewai.pipeline_support import (
            _valuation_analysis_input,
            build_deterministic_valuation_claims,
        )

        state = {
            "company_name": "Example Holdings",
            "ticker": "ZZZ",
            "facts": {},
            "calculations": [],
            "validated_evidence_ids": ["ev_state"],
            "validated_calculation_ids": ["calc_state"],
        }
        current = {
            "status": "ok",
            "readiness": "ready",
            "validation_status": "valid",
            "calculations": [
                {
                    "calculation_id": "calc_injected",
                    "status": "available",
                    "validation_status": "valid",
                    "input_evidence_ids": ["ev_injected"],
                }
            ],
        }
        historical = {
            "status": "ok",
            "validation_status": "valid",
            "calculation_id": "calc_historical_pe",
            "input_evidence_ids": ["ev_history"],
        }
        reverse_dcf = {
            "status": "ok",
            "validation_status": "valid",
            "calculation_id": "calc_reverse_dcf_growth",
            "input_evidence_ids": ["ev_state"],
        }

        payload = _valuation_analysis_input(
            state,
            current,
            historical,
            reverse_dcf,
            trusted_evidence_ids={"ev_state", "ev_history"},
        )

        self.assertNotIn("ev_injected", payload["validated_evidence_ids"])
        self.assertNotIn("calc_injected", payload["validated_calculation_ids"])
        self.assertIn("calc_state", payload["validated_calculation_ids"])
        self.assertEqual(build_deterministic_valuation_claims(payload), [])

    def test_missing_trusted_evidence_set_only_keeps_original_state_ids(self):
        from stockcrewai.pipeline_support import (
            _valuation_analysis_input,
            build_deterministic_valuation_claims,
        )

        state = {
            "company_name": "Example Holdings",
            "ticker": "ZZZ",
            "facts": {},
            "calculations": [],
            "validated_evidence_ids": ["ev_state"],
            "validated_calculation_ids": ["calc_state"],
        }
        current = {
            "status": "ok",
            "readiness": "ready",
            "validation_status": "valid",
            "calculations": [
                {
                    "calculation_id": "calc_injected",
                    "status": "available",
                    "validation_status": "valid",
                    "input_evidence_ids": ["ev_injected"],
                }
            ],
        }
        historical = {
            "status": "ok",
            "validation_status": "valid",
            "calculation_id": "calc_historical_pe",
            "input_evidence_ids": ["ev_history"],
        }
        reverse_dcf = {
            "status": "ok",
            "validation_status": "valid",
            "calculation_id": "calc_reverse_dcf_growth",
            "input_evidence_ids": ["ev_state"],
        }

        payload = _valuation_analysis_input(
            state,
            current,
            historical,
            reverse_dcf,
        )

        self.assertNotIn("ev_injected", payload["validated_evidence_ids"])
        self.assertNotIn("calc_injected", payload["validated_calculation_ids"])
        self.assertEqual(build_deterministic_valuation_claims(payload), [])


class AnalysisCrewStabilityTests(unittest.TestCase):
    def test_analysis_crew_has_only_financial_and_risk_agents_and_tasks(self):
        from stockcrewai.crews.analysis.crew import AnalysisCrew

        analysis_crew = AnalysisCrew()
        crew = analysis_crew.crew()

        self.assertEqual(len(crew.agents), 2)
        self.assertEqual(len(crew.tasks), 2)
        self.assertTrue(crew.agents[0].role.startswith("FinancialQualityAgent"))
        self.assertTrue(crew.agents[1].role.startswith("RiskAnalysisAgent"))
        self.assertNotIn("valuation_analysis_agent", analysis_crew.agents_config)
        self.assertNotIn("valuation_analysis_task", analysis_crew.tasks_config)
        self.assertFalse(hasattr(AnalysisCrew, "valuation_analysis_agent"))
        self.assertFalse(hasattr(AnalysisCrew, "valuation_analysis_task"))


class AnalysisFlowStabilityTests(unittest.TestCase):
    @staticmethod
    def _run_flow(analysis_crew, report_crew, *, progress_callback=None):
        from stockcrewai.main import run_research

        parser_result, dependencies = _valid_pipeline_fakes()
        market_price_data = dependencies.pop("market_price_data")
        with (
            patch(
                "stockcrewai.pipeline_support.run_request_parser",
                return_value=parser_result,
            ),
            patch(
                "stockcrewai.pipeline_support._deterministic_verdict",
                return_value={"status": "ready"},
            ) as verdict,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            result = run_research(
                "分析示例公司未来 3 年投资价值",
                market_price_data=market_price_data,
                analysis_crew=analysis_crew,
                report_crew=report_crew,
                progress_callback=progress_callback,
                **dependencies,
            )
        return result, verdict

    def test_two_llm_tasks_plus_deterministic_valuation_claims_pass_gate(self):
        events = []
        analysis_crew = _SequenceAnalysisCrew(
            [[_financial_output(), _risk_output()]]
        )
        report_crew = _ReportCrew()

        result, _ = self._run_flow(
            analysis_crew,
            report_crew,
            progress_callback=events.append,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(analysis_crew.kickoff_calls, 1)
        self.assertEqual(report_crew.kickoff_calls, 1)
        self.assertEqual(
            [claim["category"] for claim in result["analysis"][-3:]],
            ["current_valuation", "historical_valuation", "reverse_dcf"],
        )
        self.assertEqual(set(analysis_crew.inputs[0]), {
            "financial_analysis_input",
            "risk_analysis_input",
        })
        analysis_event = next(event for event in events if event.step == 5)
        self.assertIn("agent_tasks=2", analysis_event.output_summary)
        self.assertIn("deterministic_valuation_claims=3", analysis_event.output_summary)
        self.assertIn("attempts=1", analysis_event.output_summary)
        self.assertNotIn("calc_", analysis_event.output_summary)
        self.assertNotIn("ev_", analysis_event.output_summary)

    def test_empty_deterministic_valuation_claims_blocks_without_retry_or_report(self):
        parser_result, dependencies = _valid_pipeline_fakes()
        dependencies["valuation_tool"].run.return_value["calculations"][0].pop(
            "input_evidence_ids"
        )
        analysis_crew = _SequenceAnalysisCrew(
            [[_financial_output(), _risk_output()]]
        )
        report_crew = _ReportCrew()
        from stockcrewai.main import run_research

        market_price_data = dependencies.pop("market_price_data")
        with (
            patch(
                "stockcrewai.pipeline_support.run_request_parser",
                return_value=parser_result,
            ),
            patch(
                "stockcrewai.pipeline_support._deterministic_verdict",
                return_value={"status": "must not run"},
            ) as verdict,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            result = run_research(
                "分析示例公司未来 3 年投资价值",
                market_price_data=market_price_data,
                analysis_crew=analysis_crew,
                report_crew=report_crew,
                **dependencies,
            )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["required_data"], ["valuation_analysis_claims_required"]
        )
        self.assertEqual(analysis_crew.kickoff_calls, 1)
        self.assertEqual(report_crew.kickoff_calls, 0)
        verdict.assert_not_called()

    def test_injected_current_ids_block_before_verdict_and_report(self):
        parser_result, dependencies = _valid_pipeline_fakes()
        valuation = dependencies["valuation_tool"].run.return_value
        valuation["status"] = "ok"
        valuation["validation_status"] = "valid"
        valuation["calculations"][0]["calculation_id"] = "calc_injected"
        valuation["calculations"][0]["input_evidence_ids"] = ["ev_injected"]
        analysis_crew = _SequenceAnalysisCrew(
            [[_financial_output(), _risk_output()]]
        )
        report_crew = _ReportCrew()
        from stockcrewai.main import run_research

        market_price_data = dependencies.pop("market_price_data")
        with (
            patch(
                "stockcrewai.pipeline_support.run_request_parser",
                return_value=parser_result,
            ),
            patch(
                "stockcrewai.pipeline_support._deterministic_verdict",
                return_value={"status": "must not run"},
            ) as verdict,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            result = run_research(
                "分析示例公司未来 3 年投资价值",
                market_price_data=market_price_data,
                analysis_crew=analysis_crew,
                report_crew=report_crew,
                **dependencies,
            )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["required_data"], ["valuation_analysis_claims_required"]
        )
        self.assertEqual(analysis_crew.kickoff_calls, 1)
        self.assertEqual(report_crew.kickoff_calls, 0)
        verdict.assert_not_called()

    def test_market_evidence_id_cannot_be_self_reported_by_valuation(self):
        parser_result, dependencies = _valid_pipeline_fakes()
        valuation = dependencies["valuation_tool"].run.return_value
        valuation["status"] = "ok"
        valuation["validation_status"] = "valid"
        valuation["market_price_evidence_id"] = "ev_injected_market"
        valuation["calculations"][0]["calculation_id"] = "calc_pe_ratio"
        valuation["calculations"][0]["input_evidence_ids"] = [
            "ev_injected_market"
        ]
        dependencies["reverse_dcf_tool"].run.return_value[
            "input_evidence_ids"
        ] = ["ev_injected_market"]
        analysis_crew = _SequenceAnalysisCrew(
            [[_financial_output(), _risk_output()]]
        )
        report_crew = _ReportCrew()
        from stockcrewai.main import run_research

        market_price_data = dependencies.pop("market_price_data")
        with (
            patch(
                "stockcrewai.pipeline_support.run_request_parser",
                return_value=parser_result,
            ),
            patch(
                "stockcrewai.pipeline_support._deterministic_verdict",
                return_value={"status": "must not run"},
            ) as verdict,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            result = run_research(
                "分析示例公司未来 3 年投资价值",
                market_price_data=market_price_data,
                analysis_crew=analysis_crew,
                report_crew=report_crew,
                **dependencies,
            )

        self.assertEqual(result["status"], "blocked")
        self.assertNotEqual(result["status"], "ok")
        self.assertEqual(report_crew.kickoff_calls, 0)
        verdict.assert_not_called()

    def test_financial_or_risk_empty_claims_retry_once_and_second_success_passes(self):
        analysis_crew = _SequenceAnalysisCrew(
            [
                [_financial_output(empty=True), _risk_output()],
                [_financial_output(), _risk_output()],
            ]
        )
        report_crew = _ReportCrew()

        result, _ = self._run_flow(analysis_crew, report_crew)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(analysis_crew.kickoff_calls, 2)
        self.assertEqual(report_crew.kickoff_calls, 1)
        self.assertEqual(set(analysis_crew.inputs[0]), {
            "financial_analysis_input",
            "risk_analysis_input",
        })
        self.assertIn("retry_notice", analysis_crew.inputs[1]["financial_analysis_input"])
        self.assertIn("retry_notice", analysis_crew.inputs[1]["risk_analysis_input"])

    def test_financial_or_risk_empty_claims_retry_once_then_blocks(self):
        analysis_crew = _SequenceAnalysisCrew(
            [
                [_financial_output(), _risk_output(empty=True)],
                [_financial_output(), _risk_output(empty=True)],
            ]
        )
        report_crew = _ReportCrew()

        result, verdict = self._run_flow(analysis_crew, report_crew)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["required_data"], ["risk_analysis_claims_required"])
        self.assertEqual(analysis_crew.kickoff_calls, 2)
        self.assertEqual(report_crew.kickoff_calls, 0)
        verdict.assert_not_called()
        self.assertIn(
            '"claims"', result["analysis_diagnostics"]["raw_task_outputs"]["valuation"]
        )

    def test_success_claim_counts_and_blocked_diagnostics_remain_domain_correct(self):
        analysis_crew = _SequenceAnalysisCrew(
            [[_financial_output(), _risk_output()]]
        )
        report_crew = _ReportCrew()
        result, _ = self._run_flow(analysis_crew, report_crew)

        from stockcrewai.run_output import summarize_result

        summary = summarize_result(result)
        self.assertEqual(
            summary["analysis"]["claims"],
            {"total": 6, "financial": 2, "risk": 1, "valuation": 3},
        )

        blocked_crew = _SequenceAnalysisCrew([["not JSON", _risk_output()]])
        blocked_report = _ReportCrew()
        blocked, _ = self._run_flow(blocked_crew, blocked_report)
        self.assertEqual(blocked["required_data"], ["analysis_output_invalid"])
        self.assertEqual(
            set(blocked["analysis_diagnostics"]["raw_task_outputs"]),
            {"financial", "risk", "valuation"},
        )


if __name__ == "__main__":
    unittest.main()
