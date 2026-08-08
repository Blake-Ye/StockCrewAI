from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


class RunAndSaveOutputTests(unittest.TestCase):
    def test_successful_run_output_names_report_without_including_body(self):
        from stockcrewai.main import cli

        report = "# 机密正式报告正文"
        run_result = {"status": "ok", "stage": "report", "report": report}
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "run-output.md"
            terminal = io.StringIO()
            with (
                patch.dict(os.environ, {"STOCKCREWAI_REQUEST": "测试请求"}, clear=True),
                patch("stockcrewai.main.sys.argv", ["kickoff"]),
                patch("stockcrewai.main.run_research", return_value=run_result),
                redirect_stdout(terminal),
            ):
                cli(output_path)

            saved = output_path.read_text(encoding="utf-8")

        self.assertIn("正式报告：investment-report.md", saved)
        self.assertNotIn(report, saved)
        self.assertIn("正式报告：investment-report.md", terminal.getvalue())
        self.assertNotIn(report, terminal.getvalue())

    def test_cli_saves_crewai_run_output_without_extra_command(self):
        from stockcrewai.main import cli

        run_result = {
            "status": "blocked",
            "analysis_diagnostics": {
                "domain": "financial",
                "reason_code": "raw_json_invalid",
                "reason": "财务分析任务输出不是有效 JSON。",
                "raw_task_outputs": {
                    "financial": "not JSON",
                    "risk": "{}",
                    "valuation": "{}",
                },
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "run.md"
            terminal = io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    {"STOCKCREWAI_REQUEST": "分析苹果公司未来 3 年投资价值"},
                    clear=True,
                ),
                patch("stockcrewai.main.sys.argv", ["kickoff"]),
                patch(
                    "stockcrewai.main.run_research",
                    return_value=run_result,
                ),
                redirect_stdout(terminal),
            ):
                result = cli(output_path)
            saved = output_path.read_text(encoding="utf-8")
            result_path = output_path.with_name("run-result.json")
            with result_path.open(encoding="utf-8") as result_file:
                saved_result = json.load(result_file)

        self.assertIsNone(result)
        self.assertIn("# StockCrewAI 运行输出", saved)
        self.assertLessEqual(len(saved.splitlines()), 200)
        self.assertNotIn("\x1b", saved)
        self.assertNotIn("analysis_diagnostics", saved)
        self.assertNotIn("raw_task_outputs", saved)
        self.assertEqual(
            saved_result["analysis_diagnostics"],
            run_result["analysis_diagnostics"],
        )
        self.assertIn("analysis_diagnostics", json.dumps(saved_result))
        self.assertIn("raw_task_outputs", json.dumps(saved_result))
        self.assertIn("- 退出码（exit_code）：`0`", saved)


if __name__ == "__main__":
    unittest.main()
