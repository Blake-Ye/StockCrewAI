import unittest

from stockcrewai.tools.calculator_tool import FinancialCalculatorTool


class CalculatorPresentationTests(unittest.TestCase):
    def test_current_ratio_is_displayed_as_multiple_without_changing_result(self):
        result = FinancialCalculatorTool().run(
            ticker="AAPL",
            facts={
                "total_current_assets": {
                    "value": "1003294",
                    "evidence_id": "ev_current_assets",
                },
                "total_current_liabilities": {
                    "value": "1000000",
                    "evidence_id": "ev_current_liabilities",
                },
            },
            formulas=["current_ratio"],
        )

        calculation = result.calculations[0]
        self.assertEqual(result.status, "ok")
        self.assertEqual(calculation.calculation_id, "calc_current_ratio")
        self.assertEqual(calculation.formula_id, "current_ratio")
        self.assertEqual(calculation.input_evidence_ids, [
            "ev_current_assets",
            "ev_current_liabilities",
        ])
        self.assertEqual(calculation.raw_result, "1.003294")
        self.assertEqual(calculation.normalized_result, "1.00329E+0")
        self.assertEqual(calculation.display_result, "1.00x")
        self.assertEqual(calculation.unit, "ratio")
        self.assertEqual(calculation.validation_status, "unvalidated")
        self.assertEqual(calculation.warnings, [])


if __name__ == "__main__":
    unittest.main()
