from decimal import Decimal
from calendar import monthrange
from datetime import date
import unittest


class HistoricalValuationToolTests(unittest.TestCase):
    def _prices(self):
        prices = []
        year, month = 2021, 9
        for index in range(60):
            last_day = monthrange(year, month)[1]
            prices.append(
                {
                    "date": date(year, month, last_day).isoformat(),
                    "price": str(index + 1),
                    "evidence_id": f"ev_price_{index + 1}",
                }
            )
            if index == 29:
                prices.append(
                    {
                        "date": date(year, month, 15).isoformat(),
                        "price": "999",
                        "evidence_id": "ev_price_mid_month",
                    }
                )
            month += 1
            if month == 13:
                year += 1
                month = 1
        return prices

    def _legacy_snapshots(self):
        return [
            {
                "as_of": point["date"],
                "eps": "1",
                "evidence_id": f"ev_eps_{point['evidence_id'][9:]}",
            }
            for point in self._prices()
        ]

    def _ttm_snapshots(self, eps="1"):
        return [
            {
                "filed_at": point["date"],
                "period_end": point["date"],
                "period_basis": "TTM",
                "ttm_eps": eps,
                "financial_evidence_ids": [
                    f"ev_fy_{point['evidence_id']}",
                    f"ev_current_{point['evidence_id']}",
                    f"ev_prior_{point['evidence_id']}",
                ],
            }
            for point in self._prices()
        ]

    def test_requires_ttm_snapshot_instead_of_legacy_quarter_eps(self):
        from stockcrewai.tools.historical_valuation_tool import HistoricalValuationTool

        result = HistoricalValuationTool().run(
            ticker="AAPL",
            historical_prices=self._prices(),
            financial_snapshots=self._legacy_snapshots(),
        )

        self.assertEqual(result.status, "unavailable")
        self.assertIn("historical_ttm_eps_required", result.reasons)

    def test_requires_filed_at_for_ttm_snapshot(self):
        from stockcrewai.tools.historical_valuation_tool import HistoricalValuationTool

        snapshots = self._ttm_snapshots()
        snapshots[0].pop("filed_at")
        result = HistoricalValuationTool().run(
            ticker="AAPL",
            historical_prices=self._prices(),
            financial_snapshots=snapshots,
        )

        self.assertEqual(result.status, "unavailable")
        self.assertIn("historical_ttm_eps_required", result.reasons)

    def test_history_uses_ttm_eps_and_exposes_recomputable_series(self):
        from stockcrewai.tools.historical_valuation_tool import HistoricalValuationTool

        result = HistoricalValuationTool().run(
            ticker="AAPL",
            as_of="2026-08-31",
            historical_prices=self._prices(),
            financial_snapshots=self._ttm_snapshots(eps="2"),
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.current_value, "30")
        self.assertEqual(result.series[-1]["ttm_eps"], "2")
        self.assertEqual(
            result.series[-1]["financial_evidence_ids"],
            ["ev_fy_ev_price_60", "ev_current_ev_price_60", "ev_prior_ev_price_60"],
        )

    def test_complete_point_in_time_history_returns_decimal_statistics(self):
        from stockcrewai.tools.historical_valuation_tool import HistoricalValuationTool

        result = HistoricalValuationTool().run(
            ticker="AAPL",
            as_of="2026-08-31",
            historical_prices=self._prices(),
            financial_snapshots=self._ttm_snapshots(),
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.calculation_id, "calc_historical_pe")
        self.assertEqual(result.current_value, "60")
        self.assertEqual(result.five_year_median, "30.5")
        self.assertEqual(result.percentile_25, "15.75")
        self.assertEqual(result.percentile_75, "45.25")
        self.assertEqual(result.current_percentile, "100")
        self.assertEqual(result.history_count, 60)
        self.assertIn("2024-02-29", result.selected_dates)
        self.assertNotIn("2024-02-15", result.selected_dates)
        self.assertEqual(
            Decimal(result.current_value) / Decimal(result.five_year_median),
            Decimal("1.967213114754098360655737705"),
        )

    def test_success_result_exposes_auditable_series_and_current_date(self):
        from stockcrewai.tools.historical_valuation_tool import HistoricalValuationTool

        result = HistoricalValuationTool().run(
            ticker="AAPL",
            as_of="2026-08-31",
            historical_prices=self._prices(),
            financial_snapshots=self._ttm_snapshots(),
        )

        self.assertEqual(len(result.series), 60)
        self.assertTrue(
            all(
                set(point)
                == {"date", "ttm_eps", "pe_ratio", "financial_evidence_ids"}
                for point in result.series
            )
        )
        self.assertTrue(all(point["ttm_eps"] == "1" for point in result.series))
        self.assertTrue(
            all(len(point["financial_evidence_ids"]) == 3 for point in result.series)
        )
        self.assertEqual(
            [point["date"] for point in result.series],
            sorted(point["date"] for point in result.series),
        )
        self.assertEqual(result.current_value, result.series[-1]["pe_ratio"])
        self.assertEqual(result.current_date, result.series[-1]["date"])

    def test_unavailable_result_does_not_fabricate_series(self):
        from stockcrewai.tools.historical_valuation_tool import HistoricalValuationTool

        result = HistoricalValuationTool().run(
            ticker="AAPL",
            historical_prices=self._prices()[:-1],
            financial_snapshots=self._ttm_snapshots(),
        )

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.series, [])
        self.assertIsNone(result.current_date)

    def test_look_ahead_snapshot_makes_history_unavailable(self):
        from stockcrewai.tools.historical_valuation_tool import HistoricalValuationTool

        snapshots = self._ttm_snapshots()
        snapshots[0]["filed_at"] = "2023-01-15"
        result = HistoricalValuationTool().run(
            ticker="AAPL",
            as_of="2026-08-31",
            historical_prices=self._prices(),
            financial_snapshots=snapshots,
        )

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.calculation_id, "calc_historical_pe")
        self.assertIsNone(result.current_value)
        self.assertIn("look_ahead", result.reasons)
        self.assertEqual(result.series, [])

    def test_invalid_evidence_or_date_is_typed_unavailable(self):
        from stockcrewai.tools.historical_valuation_tool import HistoricalValuationTool

        result = HistoricalValuationTool().run(
            ticker="AAPL",
            historical_prices=[
                {"date": "not-a-date", "price": "10", "evidence_id": "bad"}
            ],
            financial_snapshots=[
                {
                    "filed_at": "2022-12-30",
                    "period_end": "2022-12-30",
                    "period_basis": "TTM",
                    "ttm_eps": "1",
                    "financial_evidence_ids": [
                        "ev_fy_invalid",
                        "ev_current_invalid",
                        "ev_prior_invalid",
                    ],
                }
            ],
        )

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.calculation_id, "calc_historical_pe")
        self.assertIn("invalid_price_date", result.reasons)
        self.assertIn("invalid_price_evidence_id", result.reasons)


if __name__ == "__main__":
    unittest.main()
