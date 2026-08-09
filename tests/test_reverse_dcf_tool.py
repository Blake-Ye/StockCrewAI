from decimal import Decimal
import unittest


class ReverseDCFToolTests(unittest.TestCase):
    def test_fixed_scenarios_solve_implied_growth_with_decimal_bisection(self):
        from stockcrewai.tools.reverse_dcf_tool import ReverseDCFTool

        result = ReverseDCFTool().run(
            ticker="AAPL",
            market_price={"value": "100", "evidence_id": "ev_price"},
            fcf={
                "value": "20",
                "evidence_id": "ev_fcf",
                "period_basis": "TTM",
                "validation_status": "valid",
            },
            shares_outstanding={"value": "10", "evidence_id": "ev_shares"},
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.calculation_id, "calc_reverse_dcf_growth")
        self.assertEqual(result.base_fcf, "20")
        self.assertEqual(result.equity_value, "1000")
        self.assertEqual(result.forecast_years, 10)
        self.assertEqual(result.discount_rate, "0.09")
        self.assertEqual(result.terminal_growth, "0.025")
        self.assertEqual(
            result.input_evidence_ids,
            ["ev_price", "ev_fcf", "ev_shares"],
        )
        self.assertEqual(len(result.scenario_matrix), 3)
        self.assertTrue(
            all(item.convergence_status == "converged" for item in result.scenario_matrix)
        )
        self.assertGreater(result.iteration_count, 0)
        self.assertLess(abs(Decimal(result.residual)), Decimal("1E-12"))

    def test_missing_price_fcf_or_shares_evidence_is_unavailable(self):
        from stockcrewai.tools.reverse_dcf_tool import ReverseDCFTool

        result = ReverseDCFTool().run(
            ticker="AAPL",
            market_price={"value": "100"},
            fcf={
                "value": "20",
                "evidence_id": "ev_fcf",
                "period_basis": "TTM",
                "validation_status": "valid",
            },
            shares_outstanding={"value": "10", "evidence_id": "ev_shares"},
        )

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.calculation_id, "calc_reverse_dcf_growth")
        self.assertIsNone(result.equity_value)
        self.assertIn("missing_price_evidence_id", result.reasons)
        self.assertTrue(result.warnings)


if __name__ == "__main__":
    unittest.main()
