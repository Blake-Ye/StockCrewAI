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

    def test_foreign_evidence_only_never_produces_an_investment_rating(self):
        from stockcrewai.tools.verdict_tool import DeterministicVerdictTool

        result = DeterministicVerdictTool().run(
            validation_status="valid",
            valuation={
                "status": "not_applicable",
                "reason_code": "foreign_currency_fx_not_implemented",
            },
            historical_valuation={
                "status": "not_applicable",
                "reason_code": "foreign_currency_fx_not_implemented",
            },
            reverse_dcf={
                "status": "not_applicable",
                "reason_code": "foreign_currency_fx_not_implemented",
            },
            risk_input={"status": "available", "risk_level": "medium"},
            policy_context={"gate": {"status": "evidence_only"}},
        )

        self.assertEqual(result.status, "insufficient_data")
        self.assertFalse(result.policy_defined)
        self.assertFalse(result.is_investment_rating)
        self.assertEqual(result.overall_rating, "insufficient_data")
        self.assertEqual(result.summary_code, "FOREIGN_PROFILE_EVIDENCE_ONLY")
        self.assertEqual(result.reasons, ["foreign_profile_evidence_only"])

    def test_foreign_ifrs_holding_uses_evidence_only_before_holding_semantics(self):
        from stockcrewai.tools.verdict_tool import DeterministicVerdictTool

        result = DeterministicVerdictTool().run(
            validation_status="valid",
            valuation={
                "status": "not_applicable",
                "reason_code": "foreign_currency_fx_not_implemented",
            },
            historical_valuation={
                "status": "not_applicable",
                "reason_code": "foreign_currency_fx_not_implemented",
            },
            reverse_dcf={
                "status": "not_applicable",
                "reason_code": "foreign_currency_fx_not_implemented",
            },
            risk_input={"status": "available", "risk_level": "medium"},
            policy_context={
                "profile": {
                    "issuer_profile": "holding_company",
                    "reporting_profile": "foreign_private_issuer_ifrs",
                },
                "gate": {"status": "evidence_only"},
            },
        )

        self.assertEqual(result.summary_code, "FOREIGN_PROFILE_EVIDENCE_ONLY")
        self.assertEqual(result.reasons, ["foreign_profile_evidence_only"])

    def test_domestic_holding_evidence_only_keeps_holding_nav_semantics(self):
        from stockcrewai.tools.verdict_tool import DeterministicVerdictTool

        result = DeterministicVerdictTool().run(
            validation_status="valid",
            valuation={
                "status": "not_applicable",
                "reason_code": "holding_company_nav_primary_valuation",
            },
            historical_valuation={
                "status": "not_applicable",
                "reason_code": "holding_company_nav_primary_valuation",
            },
            reverse_dcf={
                "status": "not_applicable",
                "reason_code": "holding_company_nav_primary_valuation",
            },
            risk_input={"status": "available", "risk_level": "medium"},
            policy_context={
                "profile": {
                    "issuer_profile": "holding_company",
                    "reporting_profile": "domestic_us_gaap",
                },
                "gate": {"status": "evidence_only"},
            },
        )

        self.assertEqual(result.summary_code, "HOLDING_COMPANY_NAV_ONLY")
        self.assertEqual(result.reasons, ["holding_company_nav_primary_valuation"])


if __name__ == "__main__":
    unittest.main()
