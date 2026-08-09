from decimal import Decimal
import unittest


class ValuationToolTests(unittest.TestCase):
    @staticmethod
    def _ttm(value, *, unit, evidence_id=None, evidence_ids=None, valid=True):
        payload = {
            "value": value,
            "unit": unit,
            "period_basis": "TTM",
            "validation_status": "valid" if valid else "unvalidated",
        }
        if evidence_id is not None:
            payload["evidence_id"] = evidence_id
        if evidence_ids is not None:
            payload["evidence_ids"] = evidence_ids
        return payload

    def test_market_price_provenance_is_stable_and_validates_calculations(self):
        from stockcrewai.tools.valuation_tool import ValuationTool

        inputs = {
            "ticker": "aapl",
            "market_price": "50.00",
            "price_timestamp": "2026-08-06T15:30:00Z",
            "currency": "USD",
            "source_reference": "https://finance.yahoo.com/quote/AAPL",
            "facts": {
                "common_shares_outstanding": {
                    "value": "10",
                    "evidence_id": "ev_shares",
                    "unit": "shares",
                },
                "diluted_eps": self._ttm("2", unit="USD/share", evidence_id="ev_eps"),
                "current_fcf": self._ttm("20", unit="USD", evidence_id="ev_fcf"),
            },
        }

        result = ValuationTool().run(**inputs)
        repeated = ValuationTool().run(**inputs)

        self.assertIsNotNone(result.market_price_evidence_id)
        self.assertTrue(result.market_price_evidence_id.startswith("ev_market_price_"))
        self.assertEqual(
            result.market_price_evidence_id,
            repeated.market_price_evidence_id,
        )
        for calculation in result.calculations:
            self.assertEqual(calculation.validation_status, "valid")
            self.assertEqual(calculation.warnings, [])
            self.assertIn(
                result.market_price_evidence_id,
                calculation.input_evidence_ids,
            )

    def test_missing_one_financial_evidence_id_stays_unvalidated(self):
        from stockcrewai.tools.valuation_tool import ValuationTool

        result = ValuationTool().run(
            ticker="AAPL",
            market_price="50",
            price_timestamp="2026-08-06T15:30:00Z",
            currency="USD",
            source_reference="https://finance.yahoo.com/quote/AAPL",
            facts={
                "common_shares_outstanding": {
                    "value": "10",
                    "unit": "shares",
                },
                "current_fcf": self._ttm(
                    "20",
                    unit="USD",
                    evidence_ids=["ev_fcf_ocf", "ev_fcf_capex"],
                ),
            },
        )

        fcf_yield = next(
            item for item in result.calculations if item.formula_id == "fcf_yield"
        )
        self.assertEqual(result.readiness, "not_ready")
        self.assertEqual(fcf_yield.status, "available")
        self.assertEqual(fcf_yield.validation_status, "unvalidated")
        self.assertIn(result.market_price_evidence_id, fcf_yield.input_evidence_ids)
        self.assertIn("ev_fcf_ocf", fcf_yield.input_evidence_ids)
        self.assertIn("ev_fcf_capex", fcf_yield.input_evidence_ids)
        self.assertIn("至少一个财务输入缺少 Evidence ID", fcf_yield.warnings)

    def test_calculates_supported_metrics_with_decimal_and_provenance(self):
        from stockcrewai.tools.valuation_tool import ValuationTool

        result = ValuationTool().run(
            company_name="Apple Inc.",
            ticker="aapl",
            market_price="50.00",
            price_timestamp="2026-08-06T15:30:00Z",
            currency="USD",
            source_reference="manual:test-price",
            facts={
                "common_shares_outstanding": {
                    "value": "10",
                    "evidence_id": "ev_shares",
                    "unit": "shares",
                },
                "earnings_per_share_diluted": self._ttm(
                    "2", unit="USD/share", evidence_id="ev_eps"
                ),
                "free_cash_flow": self._ttm("20", unit="USD", evidence_id="ev_fcf"),
            },
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.readiness, "ready")
        self.assertEqual(result.ticker, "AAPL")
        self.assertEqual(
            [calculation.formula_id for calculation in result.calculations],
            ["market_capitalization", "pe_ratio", "fcf_yield"],
        )

        by_formula = {item.formula_id: item for item in result.calculations}
        self.assertEqual(by_formula["market_capitalization"].raw_result, "500.00")
        self.assertEqual(by_formula["pe_ratio"].raw_result, "25.00")
        self.assertEqual(by_formula["fcf_yield"].raw_result, "0.04")
        market_price_evidence_id = result.market_price_evidence_id
        self.assertIsNotNone(market_price_evidence_id)
        self.assertEqual(
            by_formula["market_capitalization"].input_evidence_ids,
            [market_price_evidence_id, "ev_shares"],
        )
        self.assertEqual(
            by_formula["fcf_yield"].input_evidence_ids,
            [market_price_evidence_id, "ev_fcf", "ev_shares"],
        )
        for calculation in result.calculations:
            self.assertEqual(calculation.validation_status, "valid")
            self.assertEqual(calculation.price_timestamp, "2026-08-06T15:30:00Z")
            self.assertEqual(calculation.price_source_reference, "manual:test-price")
            self.assertEqual(calculation.price_currency, "USD")
            self.assertEqual(calculation.warnings, [])

        self.assertEqual(Decimal(by_formula["fcf_yield"].raw_result), Decimal("0.04"))

    def test_accepts_sec_eps_unit_and_derived_fcf_unit(self):
        from stockcrewai.tools.valuation_tool import ValuationTool

        result = ValuationTool().run(
            ticker="AAPL",
            market_price="50",
            price_timestamp="2026-08-06T15:30:00Z",
            currency="USD",
            source_reference="manual:test-price",
            facts={
                "common_shares_outstanding": {
                    "value": "10",
                    "evidence_id": "ev_shares",
                    "unit": "shares",
                },
                "earnings_per_share_diluted": self._ttm(
                    "2", unit="USD_per_share", evidence_id="ev_eps"
                ),
                "current_fcf": {
                    **self._ttm("20", unit="USD", evidence_ids=["ev_fcf"]),
                    "raw_result": "20",
                    "value": None,
                },
            },
        )

        self.assertEqual(result.readiness, "ready")
        self.assertTrue(all(item.status == "available" for item in result.calculations))

    def test_missing_price_is_not_ready_and_never_invents_results(self):
        from stockcrewai.tools.valuation_tool import ValuationTool

        result = ValuationTool().run(
            ticker="AAPL",
            facts={
                "common_shares_outstanding": {
                    "value": "10",
                    "evidence_id": "ev_shares",
                    "unit": "shares",
                },
                "earnings_per_share_diluted": self._ttm(
                    "2", unit="USD/share", evidence_id="ev_eps"
                ),
                "free_cash_flow": self._ttm("20", unit="USD", evidence_id="ev_fcf"),
            },
        )

        self.assertEqual(result.status, "not_ready")
        self.assertEqual(result.readiness, "not_ready")
        self.assertIn("market_price", result.readiness_reasons)
        self.assertIsNone(result.market_price)
        self.assertTrue(all(item.status == "unavailable" for item in result.calculations))
        self.assertTrue(all(item.raw_result is None for item in result.calculations))
        self.assertEqual(
            result.calculations[0].input_evidence_ids,
            ["ev_shares"],
        )
        self.assertEqual(
            result.calculations[1].input_evidence_ids,
            ["ev_eps"],
        )
        self.assertEqual(
            result.calculations[2].input_evidence_ids,
            ["ev_fcf", "ev_shares"],
        )

    def test_non_positive_diluted_eps_is_unavailable(self):
        from stockcrewai.tools.valuation_tool import ValuationTool

        result = ValuationTool().run(
            market_price="50",
            price_timestamp="2026-08-06T15:30:00Z",
            currency="USD",
            source_reference="manual:test-price",
            facts={
                "common_shares_outstanding": {
                    "value": "10",
                    "evidence_id": "ev_shares",
                    "unit": "shares",
                },
                "earnings_per_share_diluted": self._ttm(
                    "0", unit="USD/share", evidence_id="ev_eps"
                ),
            },
        )

        pe = next(item for item in result.calculations if item.formula_id == "pe_ratio")
        self.assertEqual(pe.status, "unavailable")
        self.assertIsNone(pe.raw_result)
        self.assertIn("正数", pe.warnings[0])

    def test_invalid_price_timestamp_is_not_ready(self):
        from stockcrewai.tools.valuation_tool import ValuationTool

        result = ValuationTool().run(
            market_price="50",
            price_timestamp="not-a-timestamp",
            currency="USD",
            source_reference="manual:test-price",
            facts={
                "common_shares_outstanding": {
                    "value": "10",
                    "evidence_id": "ev_shares",
                    "unit": "shares",
                },
                "earnings_per_share_diluted": self._ttm(
                    "2", unit="USD/share", evidence_id="ev_eps"
                ),
                "free_cash_flow": self._ttm("20", unit="USD", evidence_id="ev_fcf"),
            },
        )

        self.assertEqual(result.readiness, "not_ready")
        self.assertIn("price_timestamp", result.readiness_reasons)
        self.assertTrue(all(item.status == "unavailable" for item in result.calculations))

    def test_currency_mismatch_makes_affected_metrics_unavailable(self):
        from stockcrewai.tools.valuation_tool import ValuationTool

        result = ValuationTool().run(
            market_price="50",
            price_timestamp="2026-08-06T15:30:00Z",
            currency="USD",
            source_reference="manual:test-price",
            facts={
                "common_shares_outstanding": {
                    "value": "10",
                    "evidence_id": "ev_shares",
                    "unit": "shares",
                },
                "earnings_per_share_diluted": self._ttm(
                    "2", unit="EUR/share", evidence_id="ev_eps"
                ),
                "free_cash_flow": self._ttm("20", unit="EUR", evidence_id="ev_fcf"),
            },
        )

        by_formula = {item.formula_id: item for item in result.calculations}
        self.assertEqual(result.readiness, "not_ready")
        self.assertEqual(by_formula["market_capitalization"].status, "available")
        self.assertEqual(by_formula["pe_ratio"].status, "unavailable")
        self.assertEqual(by_formula["fcf_yield"].status, "unavailable")
        self.assertIsNone(by_formula["pe_ratio"].raw_result)
        self.assertIsNone(by_formula["fcf_yield"].raw_result)
        self.assertTrue(any("currency" in reason for reason in result.readiness_reasons))

    def test_unknown_source_units_make_affected_metrics_unavailable(self):
        from stockcrewai.tools.valuation_tool import ValuationTool

        result = ValuationTool().run(
            market_price="50",
            price_timestamp="2026-08-06T15:30:00Z",
            currency="USD",
            source_reference="manual:test-price",
            facts={
                "common_shares_outstanding": {
                    "value": "10",
                    "evidence_id": "ev_shares",
                    "unit": "unknown",
                },
                "earnings_per_share_diluted": self._ttm(
                    "2", unit="USD/share", evidence_id="ev_eps"
                ),
                "free_cash_flow": self._ttm("20", unit="USD", evidence_id="ev_fcf"),
            },
        )

        by_formula = {item.formula_id: item for item in result.calculations}
        self.assertEqual(result.readiness, "not_ready")
        self.assertEqual(by_formula["market_capitalization"].status, "unavailable")
        self.assertEqual(by_formula["pe_ratio"].status, "available")
        self.assertEqual(by_formula["fcf_yield"].status, "unavailable")
        self.assertIn("common_shares_outstanding_unit", result.readiness_reasons)

    def test_stale_brk_b_shares_block_current_valuation(self):
        from stockcrewai.tools.valuation_tool import ValuationTool

        result = ValuationTool().run(
            ticker="BRK.B",
            market_price="500",
            price_timestamp="2026-08-06T15:30:00Z",
            currency="USD",
            source_reference="manual:yahoo:BRK-B",
            facts={
                "common_shares_outstanding": {
                    "value": "1500000000",
                    "evidence_id": "ev_brk_shares",
                    "unit": "shares",
                    "period_end": "2024-03-01",
                    "share_class": "B",
                },
                "diluted_eps": self._ttm(
                    "30", unit="USD/share", evidence_id="ev_brk_eps"
                ),
                "current_fcf": self._ttm(
                    "30000000000", unit="USD", evidence_id="ev_brk_fcf"
                ),
            },
        )

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.readiness, "not_ready")
        self.assertIn("share_count_stale", result.readiness_reasons)
        self.assertTrue(
            any("share_count_stale" in warning for warning in result.warnings)
        )
        by_formula = {item.formula_id: item for item in result.calculations}
        self.assertEqual(by_formula["market_capitalization"].status, "unavailable")
        self.assertEqual(by_formula["fcf_yield"].status, "unavailable")

    def test_unconfirmed_brk_b_share_class_blocks_current_valuation(self):
        from stockcrewai.tools.valuation_tool import ValuationTool

        result = ValuationTool().run(
            ticker="BRK-B",
            market_price="500",
            price_timestamp="2026-08-06T15:30:00Z",
            currency="USD",
            source_reference="manual:yahoo:BRK-B",
            facts={
                "common_shares_outstanding": {
                    "value": "1500000000",
                    "evidence_id": "ev_brk_shares",
                    "unit": "shares",
                    "period_end": "2026-07-31",
                },
                "diluted_eps": self._ttm(
                    "30", unit="USD/share", evidence_id="ev_brk_eps"
                ),
                "current_fcf": self._ttm(
                    "30000000000", unit="USD", evidence_id="ev_brk_fcf"
                ),
            },
        )

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.readiness, "not_ready")
        self.assertIn("share_class_mismatch_risk", result.readiness_reasons)
        self.assertTrue(
            any("share_class_mismatch_risk" in warning for warning in result.warnings)
        )

    def test_implausible_brk_b_market_cap_is_not_ready(self):
        from stockcrewai.tools.valuation_tool import ValuationTool

        result = ValuationTool().run(
            ticker="BRK.B",
            market_price="500",
            price_timestamp="2026-08-06T15:30:00Z",
            currency="USD",
            source_reference="manual:yahoo:BRK-B",
            facts={
                "common_shares_outstanding": {
                    "value": "100000",
                    "evidence_id": "ev_brk_shares",
                    "unit": "shares",
                    "period_end": "2026-07-31",
                    "share_class": "B",
                },
                "diluted_eps": self._ttm(
                    "30", unit="USD/share", evidence_id="ev_brk_eps"
                ),
                "current_fcf": self._ttm(
                    "10000000", unit="USD", evidence_id="ev_brk_fcf"
                ),
            },
        )

        self.assertEqual(result.readiness, "not_ready")
        self.assertIn("market_cap_magnitude_check_failed", result.readiness_reasons)
        by_formula = {item.formula_id: item for item in result.calculations}
        self.assertEqual(by_formula["market_capitalization"].status, "unavailable")

    def test_implausible_brk_b_fcf_yield_is_not_ready(self):
        from stockcrewai.tools.valuation_tool import ValuationTool

        result = ValuationTool().run(
            ticker="BRK.B",
            market_price="500",
            price_timestamp="2026-08-06T15:30:00Z",
            currency="USD",
            source_reference="manual:yahoo:BRK-B",
            facts={
                "common_shares_outstanding": {
                    "value": "1500000000",
                    "evidence_id": "ev_brk_shares",
                    "unit": "shares",
                    "period_end": "2026-07-31",
                    "share_class": "B",
                },
                "diluted_eps": self._ttm(
                    "30", unit="USD/share", evidence_id="ev_brk_eps"
                ),
                "current_fcf": self._ttm(
                    "1000000000000", unit="USD", evidence_id="ev_brk_fcf"
                ),
            },
        )

        self.assertEqual(result.readiness, "not_ready")
        self.assertIn("fcf_yield_magnitude_check_failed", result.readiness_reasons)
        by_formula = {item.formula_id: item for item in result.calculations}
        self.assertEqual(by_formula["market_capitalization"].status, "available")
        self.assertEqual(by_formula["fcf_yield"].status, "unavailable")

    def test_normal_mega_cap_market_cap_and_fcf_yield_remain_ready(self):
        from stockcrewai.tools.valuation_tool import ValuationTool

        result = ValuationTool().run(
            ticker="AAPL",
            market_price="250",
            price_timestamp="2026-08-06T15:30:00Z",
            currency="USD",
            source_reference="manual:yahoo:AAPL",
            facts={
                "common_shares_outstanding": {
                    "value": "15000000000",
                    "evidence_id": "ev_aapl_shares",
                    "unit": "shares",
                    "period_end": "2026-07-31",
                },
                "diluted_eps": self._ttm(
                    "10", unit="USD/share", evidence_id="ev_aapl_eps"
                ),
                "current_fcf": self._ttm(
                    "100000000000", unit="USD", evidence_id="ev_aapl_fcf"
                ),
            },
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.readiness, "ready")
        self.assertEqual(
            {item.formula_id: item for item in result.calculations}["fcf_yield"].status,
            "available",
        )


if __name__ == "__main__":
    unittest.main()
