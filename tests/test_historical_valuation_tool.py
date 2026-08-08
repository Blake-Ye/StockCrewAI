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

    def _snapshots(self):
        return [
            {
                "as_of": point["date"],
                "eps": "1",
                "evidence_id": f"ev_eps_{point['evidence_id'][9:]}",
            }
            for point in self._prices()
        ]

    def test_complete_point_in_time_history_returns_decimal_statistics(self):
        from stockcrewai.tools.historical_valuation_tool import HistoricalValuationTool

        result = HistoricalValuationTool().run(
            ticker="AAPL",
            as_of="2026-08-31",
            historical_prices=self._prices(),
            financial_snapshots=self._snapshots(),
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

    def test_look_ahead_snapshot_makes_history_unavailable(self):
        from stockcrewai.tools.historical_valuation_tool import HistoricalValuationTool

        snapshots = self._snapshots()
        snapshots[0]["as_of"] = "2023-01-15"
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
        self.assertTrue(any("look-ahead" in warning for warning in result.warnings))

    def test_invalid_evidence_or_date_is_typed_unavailable(self):
        from stockcrewai.tools.historical_valuation_tool import HistoricalValuationTool

        result = HistoricalValuationTool().run(
            ticker="AAPL",
            historical_prices=[
                {"date": "not-a-date", "price": "10", "evidence_id": "bad"}
            ],
            financial_snapshots=[
                {"as_of": "2022-12-30", "eps": "1", "evidence_id": "ev_eps"}
            ],
        )

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.calculation_id, "calc_historical_pe")
        self.assertIn("invalid_price_date", result.reasons)
        self.assertIn("invalid_price_evidence_id", result.reasons)


if __name__ == "__main__":
    unittest.main()
