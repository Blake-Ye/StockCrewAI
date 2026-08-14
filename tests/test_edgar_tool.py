from datetime import date
from types import SimpleNamespace
import os
import unittest
import warnings
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


class DirectFYFacts:
    records = [
        {
            "concept_name": "diluted_eps",
            "tag_used": "us-gaap:EarningsPerShareDiluted",
            "value": "12.00",
            "unit": "USD/share",
            "period": "2025-FY",
            "period_type": "duration",
            "period_start": date(2025, 1, 1),
            "period_end": date(2025, 12, 31),
            "filing_date": date(2026, 2, 1),
            "form_type": "10-K",
            "accession": "acc-eps-fy-2025",
            "fiscal_year": 2025,
            "fiscal_period": "FY",
        },
        {
            "concept_name": "operating_cash_flow",
            "tag_used": "us-gaap:NetCashProvidedByUsedInOperatingActivities",
            "value": "30",
            "unit": "USD",
            "period": "2025-FY",
            "period_type": "duration",
            "period_start": date(2025, 1, 1),
            "period_end": date(2025, 12, 31),
            "filing_date": date(2026, 2, 1),
            "form_type": "10-K",
            "accession": "acc-ocf-fy-2025",
            "fiscal_year": 2025,
            "fiscal_period": "FY",
        },
        {
            "concept_name": "capex",
            "tag_used": "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
            "value": "10",
            "unit": "USD",
            "period": "2025-FY",
            "period_type": "duration",
            "period_start": date(2025, 1, 1),
            "period_end": date(2025, 12, 31),
            "filing_date": date(2026, 2, 1),
            "form_type": "10-K",
            "accession": "acc-capex-fy-2025",
            "fiscal_year": 2025,
            "fiscal_period": "FY",
        },
    ]

    def get_concept(self, concept, period=None, return_metadata=False):
        selected_period = period or "2025-FY"
        for record in self.records:
            if record["concept_name"] == concept and record["period"] == selected_period:
                return dict(record) if return_metadata else record["value"]
        return None

    def get_fact(self, tag, period=None):
        selected_period = period or "2025-FY"
        for record in self.records:
            if record["tag_used"] == tag and record["period"] == selected_period:
                return SimpleNamespace(**record)
        return None

    def get_all_facts(self):
        return []


class Direct20FFacts(DirectFYFacts):
    records = [
        {**record, "form_type": "20-F"}
        for record in DirectFYFacts.records
    ]


def _annual_metadata(
    concept_name,
    tag,
    fiscal_year,
    value,
    filed_at,
    accession,
    *,
    currency="USD",
    period_start=None,
    period_end=None,
    form="10-K",
):
    return {
        "concept_name": concept_name,
        "tag_used": tag,
        "value": value,
        "unit": currency,
        "currency": currency,
        "period": f"{fiscal_year}-FY",
        "period_type": "duration",
        "period_start": period_start or date(fiscal_year, 1, 1),
        "period_end": period_end or date(fiscal_year, 12, 31),
        "filing_date": filed_at,
        "form_type": form,
        "accession": accession,
        "fiscal_year": fiscal_year,
        "fiscal_period": "FY",
        "source_reference": SOURCE,
    }


class AnnualFacts:
    tags = {
        "revenue": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "net_income": "us-gaap:NetIncomeLoss",
        "operating_cash_flow": "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        "capex": "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
    }

    def __init__(self, *, missing_capex_year=None, currency_conflict=False):
        self.records = []
        for fiscal_year in range(2021, 2026):
            filed_at = date(fiscal_year + 1, 2, 1)
            period_start = date(fiscal_year, 1, 3) if fiscal_year == 2021 else date(fiscal_year, 1, 1)
            period_end = date(fiscal_year + 1, 1, 1) if fiscal_year == 2025 else date(fiscal_year, 12, 31)
            value_index = fiscal_year - 2020
            values = {
                "revenue": str(value_index * 10_000_000_000),
                "net_income": str(value_index * 1_000_000_000),
                "operating_cash_flow": str(value_index * 2_000_000_000),
                "capex": str(-(value_index * 500_000_000)),
            }
            for concept_name, tag in self.tags.items():
                if concept_name == "capex" and fiscal_year == missing_capex_year:
                    continue
                currency = "EUR" if currency_conflict and fiscal_year == 2025 else "USD"
                self.records.append(
                    _annual_metadata(
                        concept_name,
                        tag,
                        fiscal_year,
                        values[concept_name],
                        filed_at,
                        f"acc-{concept_name}-{fiscal_year}",
                        currency=currency,
                        period_start=period_start,
                        period_end=period_end,
                    )
                )

        self.records.append(
            _annual_metadata(
                "revenue",
                self.tags["revenue"],
                2024,
                "999999999999",
                date(2025, 3, 1),
                "acc-revenue-2024-amendment",
                form=" 10-k/a ",
            )
        )
        self.records.append(
            _annual_metadata(
                "revenue",
                self.tags["revenue"],
                2024,
                "777777777777",
                date(2025, 4, 1),
                "acc-revenue-2024-quarterly",
                form=" 10-q ",
            )
        )
        self.records.append(
            _annual_metadata(
                "revenue",
                self.tags["revenue"],
                2025,
                "888888888888",
                date(2026, 9, 1),
                "acc-revenue-2025-future",
            )
        )

    def get_concept(self, concept, period=None, return_metadata=False):
        candidates = [
            record
            for record in self.records
            if record["concept_name"] == concept
            and (period is None or record["period"] == period)
        ]
        if not candidates:
            return None
        selected = max(candidates, key=lambda record: record["filing_date"])
        return dict(selected) if return_metadata else selected["value"]

    def get_fact(self, tag, period=None):
        normalized_tag = str(tag).split(":", 1)[-1]
        for record in self.records:
            if (
                record["tag_used"].split(":", 1)[-1] == normalized_tag
                and (period is None or record["period"] == period)
            ):
                return SimpleNamespace(**record)
        return None

    def get_all_facts(self):
        return [SimpleNamespace(**record) for record in self.records]


class NamespacedAnnualFacts(AnnualFacts):
    def get_all_facts(self):
        facts = []
        for record in self.records:
            namespaced = dict(record)
            namespaced["concept"] = namespaced.pop("tag_used")
            namespaced["concept_name"] = None
            facts.append(SimpleNamespace(**namespaced))
        return facts


class OlderUSDRecentEURFacts(AnnualFacts):
    def __init__(self):
        self.records = []
        tags = AnnualFacts.tags
        for fiscal_year in range(2015, 2025):
            currency = "USD" if fiscal_year <= 2019 else "EUR"
            value_index = fiscal_year - 2014
            values = {
                "revenue": str(value_index * 10_000_000_000),
                "net_income": str(value_index * 1_000_000_000),
                "operating_cash_flow": str(value_index * 2_000_000_000),
                "capex": str(-(value_index * 500_000_000)),
            }
            for concept_name, tag in tags.items():
                self.records.append(
                    _annual_metadata(
                        concept_name,
                        tag,
                        fiscal_year,
                        values[concept_name],
                        date(fiscal_year + 1, 2, 1),
                        f"acc-{concept_name}-{fiscal_year}",
                        currency=currency,
                    )
                )

    def get_all_facts(self):
        return [SimpleNamespace(**record) for record in self.records]


class OptionalADRFacts(DirectFYFacts):
    records = [
        *DirectFYFacts.records,
        {
            "concept_name": "ordinary_shares_per_adr",
            "tag_used": "custom:OrdinarySharesPerADR",
            "value": "2",
            "unit": "ratio",
            "period": "2025-FY",
            "period_type": "instant",
            "period_start": date(2025, 12, 31),
            "period_end": date(2025, 12, 31),
            "filing_date": date(2026, 2, 1),
            "form_type": "20-F",
            "accession": "acc-adr-ratio-2025",
            "fiscal_year": 2025,
            "fiscal_period": "FY",
        },
        {
            "concept_name": "ordinary_shares_outstanding",
            "tag_used": "custom:OrdinarySharesOutstanding",
            "value": "1000",
            "unit": "shares",
            "period": "2025-FY",
            "period_type": "instant",
            "period_start": date(2025, 12, 31),
            "period_end": date(2025, 12, 31),
            "filing_date": date(2026, 2, 1),
            "form_type": "20-F",
            "accession": "acc-ordinary-shares-2025",
            "fiscal_year": 2025,
            "fiscal_period": "FY",
        },
    ]


class SecConceptOnlyFacts(OfflineEPSFacts):
    def get_concept(self, concept, period=None, return_metadata=False):
        if concept == "diluted_eps":
            return None
        return super().get_concept(concept, period, return_metadata)


def _bank_metadata(
    concept_name,
    tag,
    period,
    value,
    period_start,
    period_end,
    accession,
    period_type,
):
    return {
        "concept_name": concept_name,
        "tag_used": tag,
        "value": value,
        "unit": "USD",
        "currency": "USD",
        "period": period,
        "period_type": period_type,
        "period_start": period_start,
        "period_end": period_end,
        "filing_date": date(2026, 2, 1),
        "form_type": "10-K",
        "accession": accession,
        "fiscal_year": int(period[:4]),
        "fiscal_period": period[5:],
    }


class BankFacts:
    def __init__(self):
        self.records = [
            _bank_metadata(
                "net_income",
                "us-gaap:NetIncomeLoss",
                "2025-FY",
                "120",
                date(2025, 1, 1),
                date(2025, 12, 31),
                "acc-net-income-2025",
                "duration",
            ),
            _bank_metadata(
                "InterestIncomeExpenseNet",
                "us-gaap:InterestIncomeExpenseNet",
                "2025-FY",
                "360",
                date(2025, 1, 1),
                date(2025, 12, 31),
                "acc-nii-2025",
                "duration",
            ),
            _bank_metadata(
                "NoninterestIncome",
                "us-gaap:NoninterestIncome",
                "2025-FY",
                "140",
                date(2025, 1, 1),
                date(2025, 12, 31),
                "acc-noninterest-income-2025",
                "duration",
            ),
            _bank_metadata(
                "NoninterestExpense",
                "us-gaap:NoninterestExpense",
                "2025-FY",
                "200",
                date(2025, 1, 1),
                date(2025, 12, 31),
                "acc-noninterest-expense-2025",
                "duration",
            ),
            _bank_metadata(
                "Assets",
                "us-gaap:Assets",
                "2024-FY",
                "10000",
                None,
                date(2024, 12, 31),
                "acc-assets-2024",
                "instant",
            ),
            _bank_metadata(
                "Assets",
                "us-gaap:Assets",
                "2025-FY",
                "12000",
                None,
                date(2025, 12, 31),
                "acc-assets-2025",
                "instant",
            ),
            _bank_metadata(
                "StockholdersEquity",
                "us-gaap:StockholdersEquity",
                "2024-FY",
                "1000",
                None,
                date(2024, 12, 31),
                "acc-equity-2024",
                "instant",
            ),
            _bank_metadata(
                "StockholdersEquity",
                "us-gaap:StockholdersEquity",
                "2025-FY",
                "1400",
                None,
                date(2025, 12, 31),
                "acc-equity-2025",
                "instant",
            ),
        ]
        self.fact_calls = []

    def get_concept(self, concept, period=None, return_metadata=False):
        if concept != "net_income":
            return None
        selected_period = period or "2025-FY"
        for record in self.records:
            if record["period"] == selected_period and record["concept_name"] == concept:
                return dict(record) if return_metadata else record["value"]
        return None

    def get_fact(self, tag, period=None):
        self.fact_calls.append((tag, period))
        selected_period = period or "2025-FY"
        for record in self.records:
            accepted_tags = {record["tag_used"].split(":", 1)[-1]}
            if record["concept_name"] == "net_income":
                accepted_tags.add(record["tag_used"])
            if record["period"] == selected_period and tag in accepted_tags:
                return SimpleNamespace(**record)
        warnings.warn(f"missing raw fact: {tag}", UserWarning)
        return None

    def get_all_facts(self):
        return [SimpleNamespace(**record) for record in self.records]


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


class MetadataCompany(OfflineCompany):
    def __init__(self, facts, sic):
        super().__init__(facts)
        self.sic = sic


class MetadataEdgar(OfflineEdgar):
    def __init__(self, facts, sic):
        super().__init__(facts)
        self.sic = sic
        self.company_calls = 0

    def Company(self, identifier):
        self.company_calls += 1
        return MetadataCompany(self.facts, self.sic)


class EdgarToolTTMTests(unittest.TestCase):
    def test_preserves_sec_sic_metadata_without_extra_company_lookup(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        for raw_sic, expected_sic in ((6020, "6020"), ("4911", "4911"), (None, None)):
            with self.subTest(raw_sic=raw_sic):
                module = MetadataEdgar(OfflineEPSFacts(), raw_sic)
                with patch.dict(os.environ, {"EDGAR_IDENTITY": "offline test"}):
                    result = EdgarTool(
                        edgar_module=module,
                        as_of=date(2026, 8, 8),
                    ).run(ticker="AAPL")

                self.assertEqual(result.sic, expected_sic)
                self.assertIsNone(result.sec_registrant_profile)
                self.assertIsNone(result.sec_security_profile)
                self.assertIsNone(result.sec_reporting_profile)
                self.assertEqual(module.company_calls, 1)

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

    def test_collects_direct_fy_ttm_inputs_when_no_ytd_pair_exists(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        with patch.dict(os.environ, {"EDGAR_IDENTITY": "offline test"}):
            result = EdgarTool(
                edgar_module=OfflineEdgar(DirectFYFacts()),
                as_of=date(2026, 8, 8),
            ).run(ticker="AAPL")

        self.assertEqual(
            set(result.ttm_inputs),
            {"diluted_eps", "operating_cash_flow", "capex"},
        )
        for metric_id, expected_value, expected_unit in (
            ("diluted_eps", "12.00", "USD/share"),
            ("operating_cash_flow", "30", "USD"),
            ("capex", "10", "USD"),
        ):
            with self.subTest(metric_id=metric_id):
                inputs = result.ttm_inputs[metric_id]
                self.assertEqual(set(inputs), {"direct_ttm"})
                direct = inputs["direct_ttm"]
                self.assertEqual(direct.value, expected_value)
                self.assertEqual(direct.unit, expected_unit)
                self.assertEqual(direct.period, "2025-FY")
                self.assertEqual(direct.period_type, "duration")
                self.assertEqual(direct.period_basis, "TTM")
                self.assertEqual(direct.period_start, "2025-01-01")
                self.assertEqual(direct.period_end, "2025-12-31")
                self.assertEqual(direct.fiscal_year, 2025)
                self.assertEqual(direct.fiscal_period, "FY")
                self.assertEqual(direct.form, "10-K")
                self.assertTrue(direct.evidence_id.startswith("ev_"))
                self.assertIn("direct_ttm", direct.evidence_id)
                self.assertEqual(direct.source_reference, SOURCE)

    def test_collects_five_common_complete_fiscal_years_with_provenance(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        with patch.dict(os.environ, {"EDGAR_IDENTITY": "offline test"}):
            result = EdgarTool(
                edgar_module=OfflineEdgar(AnnualFacts()),
                as_of=date(2026, 8, 8),
            ).run(ticker="AAPL")

        history = result.annual_financial_history
        self.assertEqual(history["status"], "ok")
        self.assertEqual(history["validation_status"], "valid")
        self.assertEqual(history["currency"], "USD")
        self.assertEqual(
            [period["fiscal_year"] for period in history["periods"]],
            [2021, 2022, 2023, 2024, 2025],
        )
        latest = history["periods"][-1]
        self.assertEqual(latest["period_basis"], "FY")
        self.assertEqual(latest["period_end"], "2026-01-01")
        self.assertEqual(latest["filed_at"], "2026-02-01")
        self.assertEqual(latest["capex"], "2500000000")
        self.assertEqual(latest["free_cash_flow"], "7500000000")
        self.assertEqual(len(latest["evidence_ids"]), 4)
        self.assertEqual(
            latest["calculation_id"], "calc_annual_fcf_2025"
        )
        self.assertEqual(
            latest["calculation_provenance"]["formula"],
            "free_cash_flow = operating_cash_flow - positive_capex",
        )
        self.assertEqual(
            history["periods"][-2]["revenue"], "999999999999"
        )
        self.assertNotEqual(latest["revenue"], "888888888888")
        self.assertTrue(
            all(period["filed_at"] <= "2026-08-08" for period in history["periods"])
        )

    def test_collects_namespaced_financial_fact_concepts_without_concept_name(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        with patch.dict(os.environ, {"EDGAR_IDENTITY": "offline test"}):
            result = EdgarTool(
                edgar_module=OfflineEdgar(NamespacedAnnualFacts()),
                as_of=date(2026, 8, 8),
            ).run(ticker="AAPL")

        history = result.annual_financial_history
        self.assertEqual(history["status"], "ok")
        self.assertEqual(history["currency"], "USD")
        self.assertEqual(
            [period["fiscal_year"] for period in history["periods"]],
            [2021, 2022, 2023, 2024, 2025],
        )

    def test_incomplete_or_conflicting_annual_history_is_not_applicable(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        for facts, reason_code in (
            (AnnualFacts(missing_capex_year=2023), "insufficient_complete_fiscal_years"),
            (AnnualFacts(currency_conflict=True), "unsupported_currency"),
        ):
            with self.subTest(facts=type(facts).__name__):
                with patch.dict(os.environ, {"EDGAR_IDENTITY": "offline test"}):
                    result = EdgarTool(
                        edgar_module=OfflineEdgar(facts),
                        as_of=date(2026, 8, 8),
                    ).run(ticker="AAPL")

                history = result.annual_financial_history
                self.assertEqual(history["status"], "not_applicable")
                self.assertEqual(history["reason_code"], reason_code)
                self.assertEqual(history["validation_status"], "unvalidated")
                self.assertEqual(history["periods"], [])

    def test_recent_foreign_currency_window_does_not_fallback_to_older_usd(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        with patch.dict(os.environ, {"EDGAR_IDENTITY": "offline test"}):
            result = EdgarTool(
                edgar_module=OfflineEdgar(OlderUSDRecentEURFacts()),
                as_of=date(2026, 8, 8),
            ).run(ticker="AAPL")

        history = result.annual_financial_history
        self.assertEqual(history["status"], "not_applicable")
        self.assertEqual(history["reason_code"], "unsupported_currency")
        self.assertEqual(history["periods"], [])

    def test_annual_history_excludes_10q_and_accepts_normalized_10k_amendment(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        with patch.dict(os.environ, {"EDGAR_IDENTITY": "offline test"}):
            result = EdgarTool(
                edgar_module=OfflineEdgar(AnnualFacts()),
                as_of=date(2026, 8, 8),
            ).run(ticker="AAPL")

        history = result.annual_financial_history
        amended_2024 = next(
            period for period in history["periods"] if period["fiscal_year"] == 2024
        )
        self.assertEqual(amended_2024["revenue"], "999999999999")

    def test_annual_history_rejects_short_or_mismatched_period_windows(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        short_duration = AnnualFacts()
        for record in short_duration.records:
            if record["fiscal_year"] == 2023:
                record["period_end"] = date(2023, 3, 31)

        mismatched_window = AnnualFacts()
        for record in mismatched_window.records:
            if record["fiscal_year"] == 2023 and record["concept_name"] == "capex":
                record["period_start"] = date(2023, 1, 2)

        for facts in (short_duration, mismatched_window):
            with self.subTest(facts=facts):
                with patch.dict(os.environ, {"EDGAR_IDENTITY": "offline test"}):
                    result = EdgarTool(
                        edgar_module=OfflineEdgar(facts),
                        as_of=date(2026, 8, 8),
                    ).run(ticker="AAPL")

                history = result.annual_financial_history
                self.assertEqual(history["status"], "not_applicable")
                self.assertEqual(
                    history["reason_code"], "insufficient_complete_fiscal_years"
                )

    def test_collects_direct_20f_ttm_inputs_with_full_provenance(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        inputs = EdgarTool(as_of=date(2026, 8, 8))._collect_ttm_inputs(
            Direct20FFacts(), "0000320193", "FPI"
        )

        self.assertEqual(inputs["diluted_eps"]["direct_ttm"].form, "20-F")
        self.assertEqual(
            inputs["diluted_eps"]["direct_ttm"].accession_number,
            "acc-eps-fy-2025",
        )
        self.assertEqual(
            inputs["diluted_eps"]["direct_ttm"].period,
            "2025-FY",
        )

    def test_keeps_only_explicit_optional_adr_facts_and_no_missing_warning(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        tool = EdgarTool(as_of=date(2026, 8, 8))
        facts, warnings, _, _ = tool._collect_facts(
            OfflineCompany(OptionalADRFacts()), "0000320193", "FPI"
        )

        self.assertEqual(facts["ordinary_shares_per_adr"].value, "2")
        self.assertEqual(
            facts["ordinary_shares_per_adr"].xbrl_tag,
            "custom:OrdinarySharesPerADR",
        )
        self.assertEqual(
            facts["ordinary_shares_per_adr"].accession_number,
            "acc-adr-ratio-2025",
        )
        self.assertEqual(facts["ordinary_shares_per_adr"].period, "2025-FY")
        self.assertEqual(facts["ordinary_shares_per_adr"].source_reference, SOURCE)
        self.assertEqual(facts["ordinary_shares_outstanding"].value, "1000")
        self.assertFalse(any("ordinary_shares" in warning for warning in warnings))

    def test_missing_optional_adr_facts_do_not_change_default_fact_warnings(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        facts, warnings, _, _ = EdgarTool(as_of=date(2026, 8, 8))._collect_facts(
            OfflineCompany(DirectFYFacts()), "0000320193", "AAPL"
        )

        self.assertNotIn("ordinary_shares_per_adr", facts)
        self.assertNotIn("ordinary_shares_outstanding", facts)
        self.assertIn("缺少 Company Fact：revenue", warnings)
        self.assertFalse(any("ordinary_shares" in warning for warning in warnings))

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

    def test_collects_fixed_bank_concepts_with_sec_provenance(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        bank_facts = BankFacts()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            facts, fact_warnings, _, _ = EdgarTool(as_of=date(2026, 8, 8))._collect_facts(
                MetadataCompany(bank_facts, 6020), "0001234567", "JPM"
            )

        expected_keys = {
            "net_interest_income",
            "noninterest_income",
            "noninterest_expense",
            "total_assets_beginning",
            "total_assets_ending",
            "stockholders_equity_beginning",
            "stockholders_equity_ending",
        }
        self.assertTrue(expected_keys <= set(facts))
        expected_source = (
            "https://data.sec.gov/api/xbrl/companyfacts/CIK0001234567.json"
        )
        for metric_id in expected_keys:
            with self.subTest(metric_id=metric_id):
                fact = facts[metric_id]
                self.assertEqual(fact.source_reference, expected_source)
                expected_period = "2024-FY" if metric_id.endswith("_beginning") else "2025-FY"
                self.assertEqual(fact.period, expected_period)
                self.assertTrue(fact.accession_number.startswith("acc-"))
                self.assertTrue(fact.xbrl_tag.startswith("us-gaap:"))
                expected_start = (
                    "2024-12-31"
                    if metric_id.endswith("_beginning")
                    else "2025-01-01"
                    if metric_id in {
                        "net_interest_income",
                        "noninterest_income",
                        "noninterest_expense",
                    }
                    else "2025-12-31"
                )
                expected_end = (
                    "2025-12-31"
                    if metric_id in {
                        "net_interest_income",
                        "noninterest_income",
                        "noninterest_expense",
                    }
                    else expected_start
                )
                self.assertEqual(fact.period_start, expected_start)
                self.assertEqual(fact.period_end, expected_end)
                self.assertEqual(fact.filed_at, "2026-02-01")
                self.assertEqual(fact.form, "10-K")

        self.assertEqual(
            facts["net_interest_income"].xbrl_tag,
            "us-gaap:InterestIncomeExpenseNet",
        )
        self.assertIn(("us-gaap:InterestIncomeExpenseNet", "2025-FY"), bank_facts.fact_calls)
        self.assertIn(("InterestIncomeExpenseNet", "2025-FY"), bank_facts.fact_calls)
        self.assertNotIn("缺少银行 Company Fact：net_interest_income", fact_warnings)
        self.assertNotIn("缺少银行 Company Fact：noninterest_income", fact_warnings)
        self.assertNotIn("缺少银行 Company Fact：noninterest_expense", fact_warnings)
        self.assertNotIn("缺少银行 Company Fact：total_assets_beginning", fact_warnings)
        self.assertNotIn("缺少银行 Company Fact：total_assets_ending", fact_warnings)
        self.assertIn("缺少银行 Company Fact：interest_earning_assets_beginning", fact_warnings)
        self.assertIn("缺少银行 Company Fact：interest_earning_assets_ending", fact_warnings)
        self.assertEqual([item for item in caught if item.category is UserWarning], [])

    def test_collects_fixed_foreign_forms_without_changing_existing_form_order(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        class FilingCompany:
            def __init__(self):
                self.requests = []

            def get_filings(self, **kwargs):
                self.requests.append(kwargs)
                form = kwargs["form"]
                return [
                    SimpleNamespace(
                        form=form,
                        filing_date=date(2026, 3, 1),
                        period_of_report=date(2025, 12, 31),
                        accession_number=f"acc-{form}",
                        url=f"sec:test/{form}",
                        items=[],
                    )
                ]

        company = FilingCompany()
        filings, errors = EdgarTool(as_of=date(2026, 8, 8))._collect_filings(
            company, "0001234567", False, 12000
        )

        self.assertEqual(errors, [])
        self.assertEqual(
            [request["form"] for request in company.requests],
            ["10-K", "10-Q", "8-K", "20-F", "6-K"],
        )
        self.assertEqual(
            [filing.form for filing in filings],
            ["10-K", "10-Q", "8-K", "20-F", "6-K"],
        )
        foreign_filings = [filing for filing in filings if filing.form in {"20-F", "6-K"}]
        self.assertEqual(
            [(filing.accession_number, filing.source_reference, filing.filed_at)
             for filing in foreign_filings],
            [("acc-20-F", "sec:test/20-F", "2026-03-01"),
             ("acc-6-K", "sec:test/6-K", "2026-03-01")],
        )


if __name__ == "__main__":
    unittest.main()
