from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError, is_dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

_RUN_OUTPUT_IMPORT_ERROR: ImportError | None = None
try:
    from stockcrewai.run_output import (
        CompactRunReporter,
        RunStageEvent,
        sanitize_text,
        strip_ansi,
        summarize_result,
    )
except ImportError as exc:
    _RUN_OUTPUT_IMPORT_ERROR = exc


def _blocked_claim_gate_result() -> dict[str, object]:
    """返回不访问网络的 Claim Gate 风险域阻断结果。"""
    return {
        "request": "分析苹果公司未来 3 年投资价值",
        "parsed_request": {
            "company_name_guess": "Apple Inc.",
            "ticker_guess": "AAPL",
            "investment_horizon": "3 年",
            "requested_focus": ["financial", "risk", "valuation"],
        },
        "facts": {
            "revenue": {
                "value": 100.0,
                "evidence_id": "evidence-001",
            }
        },
        "filings": [
            {
                "evidence_id": "evidence-002",
                "form": "10-K",
                "filed_at": "2025-11-01",
            }
        ],
        "calculations": {
            "items": [
                {
                    "calculation_id": "calculation-001",
                    "name": "fcf_yield",
                    "value": 0.025,
                }
            ]
        },
        "validation": {"status": "valid"},
        "market_price_data": {
            "price": 198.12,
            "timestamp": "2026-08-07T09:00:00+00:00",
            "currency": "USD",
        },
        "valuation": {
            "pe": 31.2,
            "fcf_yield": 0.025,
            "historical_percentile": 0.72,
            "reverse_dcf_implied_growth": 0.11,
        },
        "evidence": [
            {
                "evidence_id": "evidence-001",
                "source_reference": "sec://AAPL/10-K/revenue",
                "period": "FY2025",
            },
            {
                "evidence_id": "evidence-002",
                "source_reference": "sec://AAPL/10-K/risk-factors",
                "period": "FY2025",
            },
        ],
        "analysis": None,
        "verdict": None,
        "report": None,
        "status": "blocked",
        "stage": "analysis",
        "required_data": ["risk_claims"],
        "analysis_diagnostics": {
            "domain": "risk",
            "reason_code": "claims_empty",
            "reason": "风险分析未产生可验证 Claim，无法继续。",
            "required_data": ["risk_claims"],
            "completed": [
                "请求解析",
                "SEC 证据与财务验证",
                "市场价格与估值",
                "Analysis Gate",
                "Analysis Crew",
            ],
            "not_executed": ["Verdict", "Report"],
            "evidence_ids": ["evidence-001", "evidence-002"],
            "raw_task_outputs": {
                "financial": "\x1b[32mfinancial claims\x1b[0m",
                "risk": "\x1b[31m{\"claims\": []}\x1b[0m",
                "valuation": "valuation claims",
            },
        },
    }


def _current_run_research_result() -> dict[str, object]:
    """返回当前 run_research 的嵌套确定性结果结构。"""
    return {
        "status": "ok",
        "stage": "report",
        "parsed_request": {
            "company_name_guess": ["", "苹果公司"],
            "ticker_guess": [None, "AAPL"],
            "investment_horizon": "3 年",
            "requested_focus": ["financial", "risk", "valuation"],
        },
        "edgar": {
            "facts": {
                "revenue": {"evidence_id": "ev-current-revenue"},
                "net_income": {"evidence_id": "ev-current-net-income"},
            },
            "filings": [
                {
                    "evidence_id": "ev-current-filing",
                    "risk_sections": ["Item 1A", "Quarterly update"],
                }
            ],
        },
        "calculations": {
            "status": "ok",
            "calculations": [
                {"calculation_id": "calc-margin"},
                {"calculation_id": "calc-growth"},
                {"calculation_id": "calc-fcf"},
            ],
        },
        "validation": {
            "status": "valid",
            "validated_evidence_ids": [
                "ev-current-revenue",
                "ev-current-net-income",
            ],
            "validated_calculation_ids": [
                "calc-margin",
                "calc-growth",
                "calc-fcf",
            ],
        },
        "market_price_data": {
            "market_price": "198.12",
            "price_timestamp": "2026-08-07T09:00:00+00:00",
            "currency": "USD",
        },
        "valuation": {
            "calculations": [
                {
                    "formula_id": "pe_ratio",
                    "display_result": "31.20x",
                    "normalized_result": "3.12000E+1",
                    "raw_result": "31.2",
                },
                {
                    "formula_id": "fcf_yield",
                    "display_result": "2.50%",
                    "normalized_result": "2.50000E-2",
                    "raw_result": "0.025",
                },
            ]
        },
        "historical_valuation": {"current_percentile": "72.5"},
        "reverse_dcf": {"implied_growth": "0.11"},
        "analysis": [],
        "verdict": {"status": "ready"},
        "report": "已生成",
    }


def _stage_events() -> list[RunStageEvent]:
    """返回一次阻断运行的七个结构化逻辑阶段事件。"""
    return [
        RunStageEvent(
            step=1,
            title="请求解析",
            actor="Request Parser",
            status="completed",
            input_summary="苹果公司 / 3 年",
            output_summary="ticker=AAPL, focus=3",
            next_step="SEC 证据与财务验证",
        ),
        RunStageEvent(
            step=2,
            title="SEC 证据与财务验证",
            actor="SEC + Validator",
            status="completed",
            input_summary="ticker=AAPL",
            output_summary="事实 1 / filing 1 / 验证 valid",
            next_step="市场价格与估值",
        ),
        RunStageEvent(
            step=3,
            title="市场价格与估值",
            actor="Market + Valuation",
            status="completed",
            input_summary="AAPL / USD",
            output_summary="price=198.12, P/E=31.2",
            next_step="Analysis Gate",
        ),
        RunStageEvent(
            step=4,
            title="Analysis Gate",
            actor="Analysis Gate",
            status="completed",
            input_summary="已验证证据",
            output_summary="READY",
            decision="READY",
            next_step="Analysis Crew",
        ),
        RunStageEvent(
            step=5,
            title="Analysis Crew",
            actor="Analysis Crew",
            status="completed",
            input_summary="financial / risk / valuation",
            output_summary="3 个任务返回",
            next_step="Claim Gate",
        ),
        RunStageEvent(
            step=6,
            title="Claim Gate",
            actor="Claim Gate",
            status="blocked",
            input_summary="Analysis Crew 输出",
            output_summary="risk claims=0",
            decision="BLOCKED",
            reason="risk / claims_empty",
            next_step="最终阻断",
        ),
        RunStageEvent(
            step=7,
            title="最终阻断",
            actor="Claim Gate",
            status="blocked",
            output_summary="不生成 Verdict 与 Report",
            decision="BLOCKED",
            reason="required_data=risk_claims",
        ),
    ]


def _error_result() -> dict[str, object]:
    """返回包含敏感异常文本的运行错误结果。"""
    return {
        "status": "error",
        "stage": "runtime",
        "error": {
            "type": "RuntimeError",
            "message": (
                "\x1b[31mapi_key=sk-test-secret token=token-secret "
                "trace URL=https://amp.example/trace?access_code=trace-secret "
                "raw_task_outputs={\"token\":\"raw-secret\"}\x1b[0m"
            ),
        },
    }


class BrokenStream:
    """模拟终端已关闭的输出流。"""

    def isatty(self) -> bool:
        return False

    def write(self, text: str) -> int:
        raise BrokenPipeError("terminal closed")

    def flush(self) -> None:
        raise BrokenPipeError("terminal closed")


class CompactRunOutputTests(unittest.TestCase):
    def test_run_stage_event_is_frozen_and_reporter_emits_each_logical_stage(self):
        self.assertIsNone(
            _RUN_OUTPUT_IMPORT_ERROR,
            f"stockcrewai.run_output frozen API is unavailable: {_RUN_OUTPUT_IMPORT_ERROR}",
        )
        event = RunStageEvent(
            step=1,
            title="请求解析",
            actor="Request Parser",
            status="running",
        )
        self.assertTrue(is_dataclass(event))
        self.assertEqual(event.input_summary, "")
        with self.assertRaises(FrozenInstanceError):
            event.status = "completed"  # type: ignore[misc]

        terminal = StringIO()
        reporter = CompactRunReporter(terminal)
        for stage_event in _stage_events():
            reporter.emit(stage_event)

        rendered = terminal.getvalue()
        for title in (
            "请求解析",
            "SEC 证据与财务验证",
            "市场价格与估值",
            "Analysis Gate",
            "Analysis Crew",
            "Claim Gate",
            "最终阻断",
        ):
            self.assertIn(title, rendered)
        self.assertIn("risk", rendered)
        self.assertIn("claims_empty", rendered)
        self.assertLessEqual(len(rendered.splitlines()), 120)

    def test_strip_ansi_removes_terminal_control_sequences(self):
        self.assertIsNone(
            _RUN_OUTPUT_IMPORT_ERROR,
            f"stockcrewai.run_output frozen API is unavailable: {_RUN_OUTPUT_IMPORT_ERROR}",
        )
        self.assertEqual(
            strip_ansi("\x1b[1;31mClaim Gate\x1b[0m: risk"),
            "Claim Gate: risk",
        )

    def test_error_summary_redacts_sensitive_fields_and_renders_short_error(self):
        self.assertIsNone(
            _RUN_OUTPUT_IMPORT_ERROR,
            f"stockcrewai.run_output frozen API is unavailable: {_RUN_OUTPUT_IMPORT_ERROR}",
        )
        result = _error_result()
        summary = summarize_result(result)
        summary_text = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        self.assertEqual(summary["error"]["type"], "RuntimeError")
        self.assertEqual(summary["status"], "error")
        for secret in ("sk-test-secret", "token-secret", "trace-secret", "raw-secret"):
            self.assertNotIn(secret, summary_text)
        self.assertNotIn("\x1b", summary_text)
        self.assertIn("[已隐藏]", summary_text)

        terminal = StringIO()
        reporter = CompactRunReporter(terminal)
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "run-output.md"
            result_path = Path(temp_dir) / "run-result.json"
            reporter.finalize(
                result=result,
                output_path=output_path,
                result_path=result_path,
                started_at=datetime(2026, 8, 7, 9, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 8, 7, 9, 1, tzinfo=timezone.utc),
                exit_code=1,
            )
            markdown = output_path.read_text(encoding="utf-8")

        for rendered in (terminal.getvalue(), markdown):
            self.assertIn("ERROR", rendered)
            self.assertIn("RuntimeError", rendered)
            self.assertNotIn("sk-test-secret", rendered)
            self.assertNotIn("token-secret", rendered)
            self.assertNotIn("trace-secret", rendered)
            self.assertNotIn("raw-secret", rendered)
            self.assertNotIn("\x1b", rendered)

    def test_sanitize_text_redacts_sensitive_fields_and_applies_limit(self):
        self.assertIsNone(
            _RUN_OUTPUT_IMPORT_ERROR,
            f"stockcrewai.run_output frozen API is unavailable: {_RUN_OUTPUT_IMPORT_ERROR}",
        )
        safe = sanitize_text(
            "\x1b[31mapi_key=sk-secret token=tok-secret "
            "trace URL=https://amp.example/trace?access_code=access-secret "
            "raw_task_outputs=secret payload\x1b[0m",
            limit=80,
        )
        self.assertLessEqual(len(safe), 80)
        self.assertNotIn("\x1b", safe)
        for secret in ("sk-secret", "tok-secret", "access-secret", "secret payload"):
            self.assertNotIn(secret, safe)

    def test_summarize_result_exposes_blocked_claim_gate_without_raw_details(self):
        self.assertIsNone(
            _RUN_OUTPUT_IMPORT_ERROR,
            f"stockcrewai.run_output frozen API is unavailable: {_RUN_OUTPUT_IMPORT_ERROR}",
        )
        result = _blocked_claim_gate_result()

        summary = summarize_result(result)
        summary_text = json.dumps(summary, ensure_ascii=False, sort_keys=True)

        self.assertIn("blocked", summary_text)
        self.assertIn("Claim Gate", summary_text)
        self.assertIn("risk", summary_text)
        self.assertIn("claims_empty", summary_text)
        self.assertIn("risk_claims", summary_text)
        self.assertIn("Analysis Gate", summary_text)
        self.assertIn("Verdict", summary_text)
        self.assertNotIn("raw_task_outputs", summary_text)
        for evidence_id in ("evidence-001", "evidence-002"):
            self.assertNotIn(evidence_id, summary_text)
        self.assertEqual(summary["evidence"]["facts"], 1)
        self.assertEqual(summary["evidence"]["filings"], 1)
        self.assertEqual(summary["valuation"]["price"], 198.12)
        self.assertEqual(summary["valuation"]["pe"], 31.2)
        self.assertEqual(summary["valuation"]["fcf_yield"], 0.025)

    def test_summarize_result_reads_current_run_research_nested_contract(self):
        result = _current_run_research_result()

        summary = summarize_result(result)
        summary_text = json.dumps(summary, ensure_ascii=False, sort_keys=True)

        self.assertEqual(summary["request"]["company"], "苹果公司")
        self.assertEqual(summary["request"]["ticker"], "AAPL")
        self.assertEqual(
            summary["evidence"],
            {
                "facts": 2,
                "filings": 1,
                "risk_sections": 2,
                "calculations": 3,
                "validated_evidence": 2,
                "validated_calculations": 3,
                "validation_status": "valid",
            },
        )
        self.assertEqual(
            summary["valuation"],
            {
                "price": "198.12",
                "timestamp": "2026-08-07T09:00:00+00:00",
                "currency": "USD",
                "pe": "31.20x",
                "fcf_yield": "2.50%",
                "historical_percentile": "72.5",
                "reverse_dcf_implied_growth": "0.11",
            },
        )
        for item_id in (
            "ev-current-revenue",
            "ev-current-net-income",
            "ev-current-filing",
            "calc-margin",
            "calc-growth",
            "calc-fcf",
        ):
            self.assertNotIn(item_id, summary_text)

    def test_summarize_result_counts_reverse_dcf_as_valuation(self):
        result = _current_run_research_result()
        result["analysis"] = {
            "claims": [
                {"category": "financial_quality"},
                {"category": "risk"},
                {"category": "valuation"},
                {"category": "reverse_dcf"},
            ]
        }

        summary = summarize_result(result)

        self.assertEqual(
            summary["analysis"]["claims"],
            {"total": 4, "financial": 1, "risk": 1, "valuation": 2},
        )

    def test_blocked_summary_counts_agent_outputs_without_rendering_raw_json(self):
        result = _blocked_claim_gate_result()
        result["analysis"] = []
        diagnostics = result["analysis_diagnostics"]
        self.assertIsInstance(diagnostics, dict)
        diagnostics["raw_task_outputs"] = {
            "financial": json.dumps(
                {"claims": [{"statement": "raw-financial-1"}, {"statement": "raw-financial-2"}]}
            ),
            "risk": "\x1b[31m"
            + json.dumps({"claims": [{"statement": "raw-risk-1"}]})
            + "\x1b[0m",
            "valuation": json.dumps(
                {
                    "claims": [
                        {"statement": "raw-valuation-1"},
                        {"statement": "raw-valuation-2"},
                        {"statement": "raw-valuation-3"},
                    ]
                }
            ),
        }

        summary = summarize_result(result)
        summary_text = json.dumps(summary, ensure_ascii=False, sort_keys=True)

        self.assertEqual(summary["analysis"]["claims"]["total"], 0)
        self.assertEqual(
            summary["analysis"]["agent_output_claims"],
            {"financial": 2, "risk": 1, "valuation": 3},
        )
        self.assertNotIn("raw_task_outputs", summary_text)
        for raw_statement in (
            "raw-financial-1",
            "raw-risk-1",
            "raw-valuation-1",
        ):
            self.assertNotIn(raw_statement, summary_text)

        terminal = StringIO()
        reporter = CompactRunReporter(terminal)
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "run-output.md"
            result_path = Path(temp_dir) / "run-result.json"
            reporter.finalize(
                result=result,
                output_path=output_path,
                result_path=result_path,
                started_at=datetime(2026, 8, 7, 9, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 8, 7, 9, 1, tzinfo=timezone.utc),
                exit_code=0,
            )
            markdown = output_path.read_text(encoding="utf-8")

        for rendered in (terminal.getvalue(), markdown):
            self.assertIn("通过 Claim Gate Claims：0", rendered)
            self.assertIn("Agent 输出 Claims：财务 2 / 风险 1 / 估值 3", rendered)
            self.assertNotIn("raw_task_outputs", rendered)
            self.assertNotIn("raw-financial-1", rendered)
            self.assertNotIn("raw-risk-1", rendered)
            self.assertNotIn("raw-valuation-1", rendered)

    def test_finalize_separates_business_status_from_exit_code_and_preserves_json(self):
        self.assertIsNone(
            _RUN_OUTPUT_IMPORT_ERROR,
            f"stockcrewai.run_output frozen API is unavailable: {_RUN_OUTPUT_IMPORT_ERROR}",
        )
        result = _blocked_claim_gate_result()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "run-output.md"
            result_path = Path(temp_dir) / "run-result.json"
            terminal = StringIO()
            reporter = CompactRunReporter(terminal)
            for event in _stage_events():
                reporter.emit(event)

            reporter.finalize(
                result=result,
                output_path=output_path,
                result_path=result_path,
                started_at=datetime(2026, 8, 7, 9, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 8, 7, 9, 1, tzinfo=timezone.utc),
                exit_code=0,
            )

            markdown = output_path.read_text(encoding="utf-8")
            result_json_text = result_path.read_text(encoding="utf-8")
            saved_result = json.loads(result_json_text)

        self.assertLessEqual(len(markdown.splitlines()), 200)
        self.assertNotIn("\x1b", markdown)
        self.assertNotIn("raw_task_outputs", markdown)
        self.assertIn("Claim Gate", markdown)
        self.assertIn("risk", markdown)
        self.assertIn("claims_empty", markdown)
        self.assertIn("risk_claims", markdown)
        self.assertIn("风险分析未产生可验证 Claim", markdown)
        for evidence_id in ("evidence-001", "evidence-002"):
            self.assertNotIn(evidence_id, markdown)

        status_line = next(line for line in markdown.splitlines() if "业务状态" in line)
        exit_code_line = next(line for line in markdown.splitlines() if "退出码" in line)
        self.assertIn("blocked", status_line)
        self.assertIn("0", exit_code_line)
        self.assertNotIn("退出码", status_line)
        self.assertNotIn("业务状态", exit_code_line)

        self.assertEqual(
            saved_result["analysis_diagnostics"],
            result["analysis_diagnostics"],
        )
        self.assertEqual(saved_result["evidence"], result["evidence"])
        self.assertIn("analysis_diagnostics", result_json_text)
        self.assertIn("风险分析未产生可验证 Claim", result_json_text)
        self.assertLessEqual(len(terminal.getvalue().splitlines()), 120)
        self.assertIn("Claim Gate", terminal.getvalue())
        self.assertIn("risk", terminal.getvalue())
        self.assertIn("claims_empty", terminal.getvalue())

    def test_finalize_writes_both_artifacts_before_broken_stream_rendering(self):
        self.assertIsNone(
            _RUN_OUTPUT_IMPORT_ERROR,
            f"stockcrewai.run_output frozen API is unavailable: {_RUN_OUTPUT_IMPORT_ERROR}",
        )
        result = _error_result()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "run-output.md"
            result_path = Path(temp_dir) / "run-result.json"
            reporter = CompactRunReporter(BrokenStream())
            reporter.finalize(
                result=result,
                output_path=output_path,
                result_path=result_path,
                started_at=datetime(2026, 8, 7, 9, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 8, 7, 9, 1, tzinfo=timezone.utc),
                exit_code=1,
            )

            self.assertTrue(output_path.is_file())
            self.assertTrue(result_path.is_file())
            self.assertIn("ERROR", output_path.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), result)


if __name__ == "__main__":
    unittest.main()
