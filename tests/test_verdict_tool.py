import unittest


class DeterministicVerdictToolTests(unittest.TestCase):
    def test_missing_components_use_a_defined_insufficient_data_policy(self):
        from stockcrewai.tools.verdict_tool import DeterministicVerdictTool

        result = DeterministicVerdictTool().run(
            validation_status="valid",
            valuation={"readiness": "ready"},
            historical_valuation={"status": "unavailable"},
            reverse_dcf={"status": "unavailable"},
            risk_input={"status": "available"},
        )

        self.assertEqual(result.status, "insufficient_data")
        self.assertTrue(result.policy_defined)
        self.assertFalse(result.is_investment_rating)
        self.assertEqual(result.overall_rating, "insufficient_data")
        self.assertEqual(result.summary_code, "INSUFFICIENT_DATA")
        self.assertEqual(result.rules_version, "v1")
        self.assertTrue(result.triggered_rules)

    def test_complete_inputs_are_evaluated_by_the_explicit_policy(self):
        from stockcrewai.tools.verdict_tool import DeterministicVerdictTool

        result = DeterministicVerdictTool().run(
            validation_status="valid",
            valuation={"readiness": "ready", "validation_status": "valid"},
            historical_valuation={"status": "ok", "validation_status": "valid"},
            reverse_dcf={"status": "ok", "validation_status": "valid"},
            risk_input={"status": "available", "risk_level": "low"},
        )

        self.assertEqual(result.status, "ready")
        self.assertTrue(result.policy_defined)
        self.assertEqual(result.overall_rating, "reasonable")
        self.assertTrue(result.is_investment_rating)
        self.assertEqual(result.summary_code, "POLICY_EVALUATED")


if __name__ == "__main__":
    unittest.main()
