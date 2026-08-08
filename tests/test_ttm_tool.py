import unittest

from stockcrewai.tools.edgar_tool import EdgarFact
from stockcrewai.tools.ttm_tool import TTMBuilderTool


PERIODS = {
    "latest_fy": {
        "period": "2024-FY",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "fiscal_year": 2024,
        "fiscal_period": "FY",
        "form": "10-K",
    },
    "current_ytd": {
        "period": "2025-Q3",
        "period_start": "2025-01-01",
        "period_end": "2025-09-30",
        "fiscal_year": 2025,
        "fiscal_period": "Q3",
        "form": "10-Q",
    },
    "prior_ytd": {
        "period": "2024-Q3",
        "period_start": "2024-01-01",
        "period_end": "2024-09-30",
        "fiscal_year": 2024,
        "fiscal_period": "Q3",
        "form": "10-Q",
    },
}


def fact(
    metric_id: str,
    role: str,
    value: str,
    *,
    unit: str = "USD",
    validation_status: str = "valid",
    **overrides: object,
) -> EdgarFact:
    metadata = {**PERIODS[role], **overrides}
    return EdgarFact(
        metric_id=metric_id,
        evidence_id=f"ev_{metric_id}_{role}",
        value=value,
        unit=unit,
        period_type="duration",
        period=metadata["period"],
        period_start=metadata["period_start"],
        period_end=metadata["period_end"],
        fiscal_year=metadata["fiscal_year"],
        fiscal_period=metadata["fiscal_period"],
        filed_at="2026-01-31",
        form=metadata["form"],
        accession_number=f"acc-{metric_id}-{role}",
        taxonomy="us-gaap",
        xbrl_tag=f"us-gaap:{metric_id}",
        source_reference=f"sec:test:{metric_id}:{role}",
        validation_status=validation_status,
    )


def complete_inputs() -> dict[str, dict[str, EdgarFact]]:
    values = {
        "revenue": ("100", "30", "25"),
        "operating_income": ("40", "12", "10"),
        "net_income": ("20", "6", "5"),
        "operating_cash_flow": ("30", "9", "7"),
        "capex": ("10", "3", "2"),
    }
    return {
        metric_id: {
            role: fact(metric_id, role, value)
            for role, value in zip(
                ("latest_fy", "current_ytd", "prior_ytd"), metric_values
            )
        }
        for metric_id, metric_values in values.items()
    }


class TTMBuilderToolTests(unittest.TestCase):
    def test_builds_all_supported_ttm_metrics_and_derived_fcf(self):
        result = TTMBuilderTool().run(
            company_name="Apple Inc.",
            ticker="aapl",
            metric_inputs=complete_inputs(),
        )

        self.assertEqual(result.status, "ok")
        by_metric = {metric.metric_id: metric for metric in result.metrics}
        self.assertEqual(
            {metric_id: by_metric[metric_id].raw_result for metric_id in (
                "revenue",
                "operating_income",
                "net_income",
                "operating_cash_flow",
                "capex",
            )},
            {
                "revenue": "105",
                "operating_income": "42",
                "net_income": "21",
                "operating_cash_flow": "32",
                "capex": "11",
            },
        )
        free_cash_flow = by_metric["free_cash_flow"]
        self.assertEqual(free_cash_flow.calculation_id, "calc_free_cash_flow_ttm")
        self.assertEqual(free_cash_flow.formula_id, "ttm_free_cash_flow")
        self.assertEqual(free_cash_flow.raw_inputs, {
            "operating_cash_flow": "32",
            "capex": "11",
        })
        self.assertEqual(free_cash_flow.raw_result, "21")
        self.assertEqual(free_cash_flow.unit, "USD")
        self.assertEqual(free_cash_flow.validation_status, "valid")
        self.assertEqual(by_metric["revenue"].period_start, "2024-10-01")
        self.assertEqual(by_metric["revenue"].period_end, "2025-09-30")
        self.assertEqual(
            by_metric["revenue"].input_evidence_ids,
            [
                "ev_revenue_latest_fy",
                "ev_revenue_current_ytd",
                "ev_revenue_prior_ytd",
            ],
        )

    def test_negative_capex_input_invalidates_capex_and_free_cash_flow(self):
        for negative_role in ("latest_fy", "current_ytd", "prior_ytd"):
            with self.subTest(negative_role=negative_role):
                inputs = complete_inputs()
                inputs["capex"][negative_role] = fact(
                    "capex", negative_role, "-1"
                )

                result = TTMBuilderTool().run(
                    company_name="Apple Inc.",
                    ticker="AAPL",
                    metric_inputs={
                        "operating_cash_flow": inputs["operating_cash_flow"],
                        "capex": inputs["capex"],
                    },
                )

                by_metric = {metric.metric_id: metric for metric in result.metrics}
                capex = by_metric["capex"]
                self.assertEqual(capex.status, "unavailable")
                self.assertEqual(capex.validation_status, "unvalidated")
                self.assertIn("capex_sign", capex.reasons)
                self.assertEqual(by_metric["free_cash_flow"].status, "unavailable")

    def test_missing_role_is_unavailable_without_zero_filling(self):
        inputs = complete_inputs()["revenue"]
        inputs.pop("prior_ytd")

        result = TTMBuilderTool().run(
            company_name="Apple Inc.",
            ticker="AAPL",
            metric_inputs={"revenue": inputs},
        )

        metric = result.metrics[0]
        self.assertEqual(result.status, "partial")
        self.assertEqual(metric.status, "unavailable")
        self.assertEqual(metric.validation_status, "unvalidated")
        self.assertIsNone(metric.raw_result)
        self.assertIn("missing_input", metric.reasons)

    def test_invalid_evidence_is_unvalidated(self):
        inputs = complete_inputs()["revenue"]
        inputs["current_ytd"] = fact(
            "revenue", "current_ytd", "30", validation_status="invalid"
        )

        result = TTMBuilderTool().run(
            company_name="Apple Inc.",
            ticker="AAPL",
            metric_inputs={"revenue": inputs},
        )

        metric = result.metrics[0]
        self.assertEqual(metric.status, "unavailable")
        self.assertEqual(metric.validation_status, "unvalidated")
        self.assertIn("invalid_evidence", metric.reasons)

    def test_unit_mismatch_is_unavailable(self):
        inputs = complete_inputs()["revenue"]
        inputs["prior_ytd"] = fact("revenue", "prior_ytd", "25", unit="shares")

        result = TTMBuilderTool().run(
            company_name="Apple Inc.",
            ticker="AAPL",
            metric_inputs={"revenue": inputs},
        )

        metric = result.metrics[0]
        self.assertEqual(metric.status, "unavailable")
        self.assertIn("unit_mismatch", metric.reasons)

    def test_period_mismatch_is_unavailable(self):
        inputs = complete_inputs()["revenue"]
        inputs["current_ytd"] = fact(
            "revenue",
            "current_ytd",
            "30",
            period="2025-Q2",
            period_end="2025-06-30",
            fiscal_period="Q2",
        )

        result = TTMBuilderTool().run(
            company_name="Apple Inc.",
            ticker="AAPL",
            metric_inputs={"revenue": inputs},
        )

        metric = result.metrics[0]
        self.assertEqual(metric.status, "unavailable")
        self.assertIn("period_mismatch", metric.reasons)

    def test_week_based_fiscal_periods_are_available(self):
        inputs = complete_inputs()["revenue"]
        inputs["latest_fy"] = fact(
            "revenue",
            "latest_fy",
            "100",
            period="2025-FY",
            period_start="2024-09-29",
            period_end="2025-09-27",
            fiscal_year=2025,
        )
        inputs["current_ytd"] = fact(
            "revenue",
            "current_ytd",
            "30",
            period="2026-Q3",
            period_start="2025-09-28",
            period_end="2026-06-27",
            fiscal_year=2026,
        )
        inputs["prior_ytd"] = fact(
            "revenue",
            "prior_ytd",
            "25",
            period="2025-Q3",
            period_start="2024-09-29",
            period_end="2025-06-28",
            fiscal_year=2025,
        )

        result = TTMBuilderTool().run(
            company_name="Apple Inc.",
            ticker="AAPL",
            metric_inputs={"revenue": inputs},
        )

        metric = result.metrics[0]
        self.assertEqual(metric.status, "available")
        self.assertEqual(metric.validation_status, "valid")
        self.assertEqual(metric.raw_result, "105")

    def test_missing_metadata_is_unavailable(self):
        inputs = complete_inputs()["revenue"]
        inputs["current_ytd"] = fact(
            "revenue", "current_ytd", "30", period_start=None
        )

        result = TTMBuilderTool().run(
            company_name="Apple Inc.",
            ticker="AAPL",
            metric_inputs={"revenue": inputs},
        )

        metric = result.metrics[0]
        self.assertEqual(metric.status, "unavailable")
        self.assertIn("missing_metadata", metric.reasons)

    def test_eps_and_shares_are_not_additive_ttm_inputs(self):
        result = TTMBuilderTool().run(
            company_name="Apple Inc.",
            ticker="AAPL",
            metric_inputs={
                metric_id: {
                    role: fact(metric_id, role, "10")
                    for role in ("latest_fy", "current_ytd", "prior_ytd")
                }
                for metric_id in (
                    "earnings_per_share_diluted",
                    "common_shares_outstanding",
                )
            },
        )

        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.metrics), 2)
        for metric in result.metrics:
            self.assertEqual(metric.status, "unavailable")
            self.assertIn("unsupported_metric", metric.reasons)


if __name__ == "__main__":
    unittest.main()
