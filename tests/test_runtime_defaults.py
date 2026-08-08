from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class RuntimeDefaultsTests(unittest.TestCase):
    def test_no_input_uses_three_year_default_request(self):
        from stockcrewai.main import main

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("stockcrewai.main.sys.argv", ["kickoff"]),
            patch("stockcrewai.main.run_research", return_value={}) as run_research,
            patch("builtins.print"),
        ):
            main()

        run_research.assert_called_once_with("分析苹果公司未来 3 年投资价值")

    def test_unparseable_analysis_outputs_return_one_gate_code(self):
        from stockcrewai.main import _filter_analysis_claims

        output = SimpleNamespace(
            tasks_output=[
                SimpleNamespace(raw="not JSON"),
                SimpleNamespace(raw="not JSON"),
                SimpleNamespace(raw="not JSON"),
            ]
        )

        claims, required_data = _filter_analysis_claims(output, [], [], [], [])

        self.assertEqual(claims, [])
        self.assertEqual(required_data, ["analysis_output_invalid"])

    def test_main_top_level_classes_functions_and_methods_have_chinese_docstrings(self):
        project_root = Path(__file__).resolve().parents[1]
        main_path = project_root / "src" / "stockcrewai" / "main.py"
        tree = ast.parse(main_path.read_text(encoding="utf-8"))

        def assert_chinese_docstring(node):
            docstring = ast.get_docstring(node)
            self.assertIsNotNone(docstring, node.name)
            self.assertTrue(
                any("\u4e00" <= character <= "\u9fff" for character in docstring),
                node.name,
            )

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assert_chinese_docstring(node)
            if isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        assert_chinese_docstring(member)


if __name__ == "__main__":
    unittest.main()
