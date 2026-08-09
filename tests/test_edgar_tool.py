from datetime import date
from types import SimpleNamespace
import os
import unittest
from unittest.mock import patch


SOURCE = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"


def _eps_metadata(period, value, period_start, period_end, filed_at, form, accession):
    return {
        "concept_name": "diluted_eps",
        "tag_used": "us-gaap:EarningsPerShareDiluted",
        "value": value,
        "unit": "USD/share",
        "period": period,
        "period_type": "duration",
        "period_start": period_start,
        "period_end": period_end,
        "filing_date": filed_at,
        "form_type": form,
        "accession": accession,
        "fiscal_year": int(period[:4]),
        "fiscal_period": period[5:],
        "source_reference": SOURCE,
    }


class OfflineEPSFacts:
    records = [
        _eps_metadata(
            "2024-FY",
            "10.00",
            date(2024, 1, 1),
            date(2024, 12, 31),
            date(2025, 2, 1),
            "10-K",
            "acc-fy-2024",
        ),
        _eps_metadata(
            "2025-Q3",
            "7.00",
            date(2025, 1, 1),
            date(2025, 9, 30),
            date(2025, 11, 1),
            "10-Q",
            "acc-q3-2025",
        ),
        _eps_metadata(
            "2024-Q3",
            "6.00",
            date(2024, 1, 1),
            date(2024, 9, 30),
            date(2025, 11, 1),
            "10-Q",
            "acc-q3-2025",
        ),
    ]

    def get_concept(self, concept, period=None, return_metadata=False):
        if concept not in {"diluted_eps", "earnings_per_share_diluted"}:
            return None
        selected_period = period or "2025-Q3"
        for record in self.records:
            if record["period"] == selected_period:
                return dict(record) if return_metadata else record["value"]
        return None

    def get_fact(self, tag, period=None):
        selected_period = period or "2025-Q3"
        for record in self.records:
            if record["period"] == selected_period:
                return SimpleNamespace(**record)
        return None

    def get_all_facts(self):
        return [SimpleNamespace(**record) for record in self.records]


class MissingPriorYTDFacts(OfflineEPSFacts):
    records = [record for record in OfflineEPSFacts.records if record["period"] != "2024-Q3"]


class SecConceptOnlyFacts(OfflineEPSFacts):
    def get_concept(self, concept, period=None, return_metadata=False):
        if concept == "diluted_eps":
            return None
        return super().get_concept(concept, period, return_metadata)


class OfflineCompany:
    cik = 320193
    name = "Apple Inc."
    tickers = ["AAPL"]

    def __init__(self, facts):
        self.facts = facts

    def get_ticker(self):
        return "AAPL"

    def get_exchanges(self):
        return ["NASDAQ"]

    def get_facts(self):
        return self.facts

    def get_filings(self, **kwargs):
        return []


class OfflineEdgar:
    def __init__(self, facts):
        self.facts = facts

    def set_identity(self, identity):
        self.identity = identity

    def Company(self, identifier):
        return OfflineCompany(self.facts)


class EdgarToolTTMTests(unittest.TestCase):
    def test_diluted_eps_falls_back_to_sec_concept_name(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        with patch.dict(os.environ, {"EDGAR_IDENTITY": "offline test"}):
            result = EdgarTool(
                edgar_module=OfflineEdgar(SecConceptOnlyFacts()),
                as_of=date(2026, 8, 8),
            ).run(ticker="AAPL")

        self.assertIn("diluted_eps", result.ttm_inputs)
        self.assertEqual(
            result.ttm_inputs["diluted_eps"]["latest_fy"].value,
            "10.00",
        )

    def test_collects_diluted_eps_ttm_inputs_with_sec_provenance(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        with patch.dict(os.environ, {"EDGAR_IDENTITY": "offline test"}):
            result = EdgarTool(
                edgar_module=OfflineEdgar(OfflineEPSFacts()),
                as_of=date(2026, 8, 8),
            ).run(ticker="AAPL")

        self.assertIn("diluted_eps", result.ttm_inputs)
        inputs = result.ttm_inputs["diluted_eps"]
        self.assertEqual(set(inputs), {"latest_fy", "current_ytd", "prior_ytd"})
        self.assertEqual(
            {role: fact.value for role, fact in inputs.items()},
            {"latest_fy": "10.00", "current_ytd": "7.00", "prior_ytd": "6.00"},
        )
        self.assertEqual(inputs["latest_fy"].form, "10-K")
        self.assertEqual(inputs["current_ytd"].form, "10-Q")
        self.assertEqual(inputs["prior_ytd"].form, "10-Q")
        self.assertEqual(inputs["latest_fy"].period_start, "2024-01-01")
        self.assertEqual(inputs["current_ytd"].period_end, "2025-09-30")
        self.assertEqual(inputs["prior_ytd"].filed_at, "2025-11-01")
        self.assertEqual(inputs["current_ytd"].accession_number, "acc-q3-2025")
        self.assertTrue(all(fact.source_reference == SOURCE for fact in inputs.values()))
        self.assertEqual(len({fact.evidence_id for fact in inputs.values()}), 3)
        self.assertTrue(all(fact.evidence_id.startswith("ev_") for fact in inputs.values()))

    def test_builds_only_complete_point_in_time_ttm_eps_snapshot(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        tool = EdgarTool(as_of=date(2026, 8, 8))
        snapshots = tool._collect_historical_financial_snapshots(
            OfflineEPSFacts(), "0000320193", "AAPL"
        )

        self.assertEqual(len(snapshots), 1)
        snapshot = snapshots[0]
        self.assertEqual(snapshot["ttm_eps"], "11")
        self.assertEqual(snapshot["period_basis"], "TTM")
        self.assertEqual(snapshot["filed_at"], "2025-11-01")
        self.assertEqual(snapshot["period_end"], "2025-09-30")
        self.assertEqual(len(snapshot["financial_evidence_ids"]), 3)
        self.assertTrue(all(item.startswith("ev_") for item in snapshot["financial_evidence_ids"]))
        self.assertNotEqual(snapshot["filed_at"], snapshot["period_end"])
        self.assertNotIn("eps", snapshot)
        self.assertNotIn("evidence_id", snapshot)

    def test_missing_ttm_input_does_not_emit_a_legacy_eps_snapshot(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        snapshots = EdgarTool(as_of=date(2026, 8, 8))._collect_historical_financial_snapshots(
            MissingPriorYTDFacts(), "0000320193", "AAPL"
        )

        self.assertEqual(snapshots, [])


if __name__ == "__main__":
    unittest.main()
