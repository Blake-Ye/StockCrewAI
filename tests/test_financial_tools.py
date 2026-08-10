import os
from datetime import date, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class FakeFiling:
    def __init__(self, form: str, index: int, filed_at: date):
        self.cik = 320193
        self.company = "Apple Inc."
        self.form = form
        self.filing_date = filed_at
        self.period_of_report = date(filed_at.year - 1, 12, 31)
        self.accession_number = f"0000320193-{filed_at.year % 100:02d}-{index:06d}"
        self.items = ["1A"] if form == "8-K" else []
        self.url = f"https://www.sec.gov/Archives/edgar/data/320193/{self.accession_number}"
        self.text_url = f"{self.url}.txt"
        self.text_calls = 0
        self.text_content = f"{self.form} filing text"

    def text(self):
        self.text_calls += 1
        return self.text_content


class FakeFilings:
    def __init__(self, filings):
        self.filings = filings

    def head(self, count):
        return self.filings[:count]

    def __iter__(self):
        return iter(self.filings)


class FakeFacts:
    values = {
        "revenue": "100",
        "net_income": "20",
        "operating_income": "25",
        "operating_cash_flow": "30",
        "capex": "10",
        "cash_and_equivalents": "40",
        "short_term_investments": "5",
        "short_term_debt": "3",
        "long_term_debt": "15",
        "stockholders_equity": "60",
        "total_current_assets": "80",
        "total_current_liabilities": "40",
        "common_shares_outstanding": "10",
        "earnings_per_share_diluted": "2",
    }

    prior_values = {
        "revenue": "90",
        "common_shares_outstanding": "11",
    }

    def get_concept(self, concept, period=None, return_metadata=False):
        value = (
            self.prior_values.get(concept)
            if period == "2023-FY"
            else self.values.get(concept)
        )
        if value is None:
            return None
        if not return_metadata:
            return value
        return {
            "value": value,
            "tag_used": f"us-gaap:{concept}",
            "period": period or "2024-FY",
            "period_start": date(2023 if period else 2024, 1, 1),
            "period_end": date(2023 if period else 2024, 12, 31),
            "filing_date": date(2025, 2, 1),
            "unit": "USD",
            "accession": "0000320193-25-000001",
            "form_type": "10-K",
            "period_type": "duration",
            "fiscal_year": 2024,
            "fiscal_period": "FY",
        }


class TTMFacts(FakeFacts):
    observations = {
        "revenue": {
            "2024-FY": "100",
            "2025-Q3": "30",
            "2024-Q3": "25",
            "2025-Q4": "99",
            "2024-Q4": "88",
        },
        "operating_income": {
            "2024-FY": "40",
            "2025-Q3": "12",
            "2024-Q3": "10",
            "2025-Q4": "39",
            "2024-Q4": "38",
        },
        "net_income": {
            "2024-FY": "20",
            "2025-Q3": "6",
            "2024-Q3": "5",
            "2025-Q4": "19",
            "2024-Q4": "18",
        },
        "operating_cash_flow": {
            "2024-FY": "30",
            "2025-Q3": "9",
            "2024-Q3": "7",
            "2025-Q4": "29",
            "2024-Q4": "28",
        },
        "capex": {
            "2024-FY": "10",
            "2025-Q3": "3",
            "2024-Q3": "2",
            "2025-Q4": "9",
            "2024-Q4": "8",
        },
    }

    def __init__(self):
        self.fact_calls = []

    def get_concept(self, concept, period=None, return_metadata=False):
        if concept not in self.observations:
            return super().get_concept(
                concept,
                period=period,
                return_metadata=return_metadata,
            )
        resolved_period = period or "2024-FY"
        value = self.observations[concept].get(resolved_period)
        if value is None:
            return None
        if not return_metadata:
            return value
        fiscal_year = int(resolved_period[:4])
        fiscal_period = resolved_period[5:]
        if fiscal_period == "FY":
            period_end = date(fiscal_year, 12, 31)
        elif fiscal_period in {"Q3", "Q4"}:
            period_end = date(
                fiscal_year,
                9 if fiscal_period == "Q3" else 12,
                30 if fiscal_period == "Q3" else 31,
            )
        else:
            raise AssertionError(f"unexpected test period: {resolved_period}")
        return {
            "concept_name": concept,
            "filing_date": date(2026, 1, 31),
            "period": resolved_period,
            "period_end": period_end,
            "synonyms_tried": [],
            "tag_used": f"us-gaap:{concept}",
            "unit": "USD",
            "value": value,
        }

    def get_fact(self, tag_used, period=None):
        self.fact_calls.append((tag_used, period))
        concept = str(tag_used).split(":", 1)[-1]
        resolved_period = period or "2024-FY"
        value = self.observations.get(concept, {}).get(resolved_period)
        if value is None:
            return None
        fiscal_year = int(resolved_period[:4])
        fiscal_period = resolved_period[5:]
        if fiscal_period == "FY":
            period_start, period_end, form = date(fiscal_year, 1, 1), date(
                fiscal_year, 12, 31
            ), "10-K"
        elif fiscal_period in {"Q3", "Q4"}:
            period_start = date(fiscal_year, 1, 1)
            period_end = date(
                fiscal_year,
                9 if fiscal_period == "Q3" else 12,
                30 if fiscal_period == "Q3" else 31,
            )
            form = "10-Q" if fiscal_period == "Q3" else "10-K"
        else:
            raise AssertionError(f"unexpected fact period: {resolved_period}")
        return SimpleNamespace(
            concept=tag_used,
            taxonomy="us-gaap",
            period_start=period_start,
            period_end=period_end,
            period_type="duration",
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            filing_date=date(2026, 1, 31),
            form_type=form,
            accession=f"acc-{concept}-{resolved_period}",
        )


class FactsWithObjectMetadata(FakeFacts):
    def get_concept(self, concept, period=None, return_metadata=False):
        metadata = super().get_concept(
            concept,
            period=period,
            return_metadata=return_metadata,
        )
        if not metadata or not return_metadata:
            return metadata
        return {
            key: metadata[key]
            for key in ("value", "tag_used", "period", "period_end", "filing_date", "unit")
        }

    def get_fact(self, concept, period=None):
        resolved_period = period or "2024-FY"
        fiscal_year = int(resolved_period[:4])
        return SimpleNamespace(
            concept=concept,
            taxonomy="us-gaap",
            period_start=date(fiscal_year, 1, 1),
            period_end=date(fiscal_year, 12, 31),
            period_type="duration",
            fiscal_year=fiscal_year,
            fiscal_period="FY",
            filing_date=date(2025, 2, 1),
            form_type="10-K",
            accession="0000320193-25-000001",
        )


class HistoricalFacts(FakeFacts):
    def get_all_facts(self):
        return [
            SimpleNamespace(
                concept="us-gaap:EarningsPerShareDiluted",
                label="Diluted EPS",
                value="3.25",
                numeric_value=3.25,
                period_end=date(2023, 12, 31),
                filing_date=date(2024, 2, 2),
                form_type="10-K",
                accession="0000320193-24-000001",
            ),
            SimpleNamespace(
                concept="us-gaap:EarningsPerShareDiluted",
                label="Diluted EPS",
                value="4.50",
                numeric_value=4.5,
                period_end=date(2024, 12, 31),
                filing_date=date(2025, 2, 1),
                form_type="10-K",
                accession="0000320193-25-000001",
            ),
            SimpleNamespace(
                concept="us-gaap:EarningsPerShareBasic",
                label="Basic EPS",
                value="5",
                numeric_value=5,
                period_end=date(2024, 12, 31),
                filing_date=date(2025, 2, 1),
                form_type="10-K",
                accession="0000320193-25-000001",
            ),
            SimpleNamespace(
                concept="us-gaap:EarningsPerShareDiluted",
                label="Diluted EPS",
                value="-1",
                numeric_value=-1,
                period_end=date(2022, 12, 31),
                filing_date=date(2023, 2, 2),
                form_type="10-K",
                accession="0000320193-23-000001",
            ),
            SimpleNamespace(
                concept="us-gaap:EarningsPerShareDiluted",
                label="Diluted EPS",
                value="6",
                numeric_value=6,
                period_end=date(2025, 12, 31),
                filed_at=date(2026, 2, 2),
                form_type="10-K",
                accession="0000320193-26-000001",
            ),
            SimpleNamespace(
                concept="us-gaap:EarningsPerShareDiluted",
                label="Diluted EPS",
                value="7",
                numeric_value=7,
                period_end=date(2025, 12, 31),
                filing_date=date(2026, 8, 6),
                form_type="10-Q",
                accession="0000320193-26-000002",
            ),
        ]


class BrokenHistoricalFacts(FakeFacts):
    def get_all_facts(self):
        raise ValueError("historical facts malformed")


class FakeCompany:
    def __init__(self, identifier):
        self.identifier = identifier
        self.name = "Apple Inc."
        self.cik = 320193
        self.tickers = ["AAPL"]
        self.calls = []
        self.filings = []

    def get_ticker(self):
        return "AAPL"

    def get_exchanges(self):
        return ["NASDAQ"]

    def get_filings(self, **kwargs):
        self.calls.append(kwargs)
        form = kwargs["form"]
        if form == "8-K":
            start = date(2026, 8, 5)
            filings = [
                FakeFiling(form, index, start - timedelta(days=index))
                for index in range(25)
            ]
        else:
            filings = [
                FakeFiling(form, index, date(2026 - index, 1, 31))
                for index in range(6)
            ]
        self.filings.extend(filings)
        return FakeFilings(filings)

    def get_facts(self):
        return FakeFacts()


class MetadataCompany(FakeCompany):
    def get_facts(self):
        return FactsWithObjectMetadata()


class LongTextCompany(FakeCompany):
    def get_filings(self, **kwargs):
        filings = super().get_filings(**kwargs)
        for filing in filings.filings:
            filing.text_content = f"{filing.form} filing text " + ("x" * 2500)
        return filings


class NoTextCompany(FakeCompany):
    def get_filings(self, **kwargs):
        filings = super().get_filings(**kwargs)
        for filing in filings.filings:
            filing.text = None
        return filings


class FakeEdgar:
    company_class = FakeCompany

    def __init__(self):
        self.companies = []
        self.identity = None

    def set_identity(self, identity):
        self.identity = identity

    def find_company(self, company_name, top_n=10):
        self.search = (company_name, top_n)
        return type("SearchResult", (), {"ciks": [320193]})()

    def Company(self, identifier):
        company = self.company_class(identifier)
        self.companies.append(company)
        return company


class TTMCompany(FakeCompany):
    def get_facts(self):
        self.facts = TTMFacts()
        return self.facts


class TTMEdgar(FakeEdgar):
    company_class = TTMCompany


class HistoricalCompany(FakeCompany):
    def get_facts(self):
        return HistoricalFacts()


class HistoricalEdgar(FakeEdgar):
    company_class = HistoricalCompany


class BrokenHistoricalCompany(FakeCompany):
    def get_facts(self):
        return BrokenHistoricalFacts()


class BrokenHistoricalEdgar(FakeEdgar):
    company_class = BrokenHistoricalCompany


class MetadataEdgar(FakeEdgar):
    company_class = MetadataCompany


class LongTextEdgar(FakeEdgar):
    company_class = LongTextCompany


class NoTextEdgar(FakeEdgar):
    company_class = NoTextCompany


class FactsFailingCompany(FakeCompany):
    def get_facts(self):
        raise TimeoutError("Company Facts timed out")


class FactsFailingEdgar(FakeEdgar):
    def Company(self, identifier):
        company = FactsFailingCompany(identifier)
        self.companies.append(company)
        return company


class PartiallyFailingFilingsCompany(FakeCompany):
    def get_filings(self, **kwargs):
        if kwargs["form"] == "8-K":
            raise TimeoutError("8-K filings timed out")
        return super().get_filings(**kwargs)


class PartiallyFailingFilingsEdgar(FakeEdgar):
    def Company(self, identifier):
        company = PartiallyFailingFilingsCompany(identifier)
        self.companies.append(company)
        return company


class EdgarToolTests(unittest.TestCase):
    def test_company_name_is_resolved_and_fixed_sec_scope_is_normalized(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        fake_edgar = FakeEdgar()
        with patch.dict(os.environ, {"EDGAR_IDENTITY": "Test User test@example.com"}):
            result = EdgarTool(
                edgar_module=fake_edgar,
                as_of=date(2026, 8, 5),
            ).run(company_name="苹果公司", include_filing_text=True)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.company_name, "Apple Inc.")
        self.assertEqual(result.ticker, "AAPL")
        self.assertEqual(result.cik, "0000320193")
        self.assertEqual(len(result.filings), 27)
        self.assertEqual(sum(item.form == "10-K" for item in result.filings), 3)
        self.assertEqual(sum(item.form == "10-Q" for item in result.filings), 4)
        self.assertEqual(sum(item.form == "8-K" for item in result.filings), 20)
        self.assertEqual(result.facts["revenue"].value, "100")
        self.assertEqual(result.facts["revenue_prior"].value, "90")
        self.assertEqual(result.facts["revenue_prior"].period, "2023-FY")
        self.assertEqual(result.facts["shares_prior"].value, "11")
        self.assertEqual(result.facts["shares_prior"].period, "2023-FY")
        self.assertTrue(result.facts["revenue"].evidence_id.startswith("ev_"))
        self.assertEqual(result.filings[0].text, "10-K filing text")
        self.assertEqual(result.historical_financial_snapshots, [])
        self.assertEqual(fake_edgar.identity, "Test User test@example.com")

    def test_entity_facts_does_not_emit_legacy_single_period_eps(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        with patch.dict(os.environ, {"EDGAR_IDENTITY": "Test User test@example.com"}):
            result = EdgarTool(
                edgar_module=HistoricalEdgar(),
                as_of=date(2026, 8, 5),
            ).run(ticker="AAPL")

        self.assertEqual(result.status, "ok")
        # 只有 FY/YTD/上年同期 YTD 三项完整期间才能形成 TTM 快照；
        # 该旧 fixture 只有单期 EPS，因此必须 fail-closed。
        self.assertEqual(result.historical_financial_snapshots, [])

    def test_historical_fact_parse_failure_does_not_change_primary_result(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        with patch.dict(os.environ, {"EDGAR_IDENTITY": "Test User test@example.com"}):
            result = EdgarTool(
                edgar_module=BrokenHistoricalEdgar(),
                as_of=date(2026, 8, 5),
            ).run(ticker="AAPL")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.historical_financial_snapshots, [])
        self.assertEqual(result.facts["earnings_per_share_diluted"].value, "2")
        self.assertEqual(len(result.filings), 27)

    def test_default_run_does_not_load_filing_text(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        fake_edgar = FakeEdgar()
        with patch.dict(os.environ, {"EDGAR_IDENTITY": "Test User test@example.com"}):
            result = EdgarTool(
                edgar_module=fake_edgar,
                as_of=date(2026, 8, 5),
            ).run(ticker="AAPL")

        self.assertEqual(result.status, "ok")
        self.assertTrue(fake_edgar.companies)
        self.assertTrue(
            all(filing.text_calls == 0 for filing in fake_edgar.companies[0].filings)
        )

    def test_fact_provenance_uses_metadata_exposed_by_edgartools_fact(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        with patch.dict(os.environ, {"EDGAR_IDENTITY": "Test User test@example.com"}):
            result = EdgarTool(
                edgar_module=MetadataEdgar(),
                as_of=date(2026, 8, 5),
            ).run(ticker="AAPL")

        revenue = result.facts["revenue"]
        self.assertEqual(revenue.period, "2024-FY")
        self.assertEqual(revenue.period_start, "2024-01-01")
        self.assertEqual(revenue.period_type, "duration")
        self.assertEqual(revenue.form, "10-K")
        self.assertEqual(revenue.accession_number, "0000320193-25-000001")
        self.assertEqual(result.facts["revenue_prior"].period_start, "2023-01-01")

    def test_sparse_ttm_metadata_is_enriched_from_get_fact(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        fake_edgar = TTMEdgar()
        with patch.dict(os.environ, {"EDGAR_IDENTITY": "Test User test@example.com"}):
            result = EdgarTool(
                edgar_module=fake_edgar,
                as_of=date(2026, 8, 5),
            ).run(ticker="AAPL")

        self.assertEqual(
            set(result.ttm_inputs),
            {
                "revenue",
                "operating_income",
                "net_income",
                "operating_cash_flow",
                "capex",
            },
        )
        calls = set(fake_edgar.companies[0].facts.fact_calls)
        self.assertIn(("us-gaap:revenue", "2024-FY"), calls)
        self.assertIn(("us-gaap:revenue", "2025-Q3"), calls)
        self.assertIn(("us-gaap:revenue", "2024-Q3"), calls)
        revenue = result.ttm_inputs["revenue"]
        self.assertEqual(revenue["latest_fy"].period_start, "2024-01-01")
        self.assertEqual(revenue["current_ytd"].period_start, "2025-01-01")
        self.assertEqual(revenue["current_ytd"].period_end, "2025-09-30")
        self.assertEqual(revenue["current_ytd"].period_type, "duration")
        self.assertEqual(revenue["current_ytd"].fiscal_year, 2025)
        self.assertEqual(revenue["current_ytd"].fiscal_period, "Q3")
        self.assertEqual(revenue["current_ytd"].filed_at, "2026-01-31")
        self.assertEqual(revenue["current_ytd"].form, "10-Q")
        self.assertEqual(
            revenue["current_ytd"].accession_number,
            "acc-revenue-2025-Q3",
        )

    def test_extracts_non_colliding_ttm_evidence_roles_without_calculating(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        with patch.dict(os.environ, {"EDGAR_IDENTITY": "Test User test@example.com"}):
            result = EdgarTool(
                edgar_module=TTMEdgar(),
                as_of=date(2026, 8, 5),
            ).run(ticker="AAPL")

        self.assertEqual(
            set(result.ttm_inputs),
            {
                "revenue",
                "operating_income",
                "net_income",
                "operating_cash_flow",
                "capex",
            },
        )
        for metric_id, by_role in result.ttm_inputs.items():
            self.assertEqual(
                set(by_role), {"latest_fy", "current_ytd", "prior_ytd"}
            )
            evidence_ids = [fact.evidence_id for fact in by_role.values()]
            self.assertEqual(len(evidence_ids), len(set(evidence_ids)))
            for role, metric_fact in by_role.items():
                self.assertIn(metric_id, metric_fact.evidence_id)
                self.assertIn(role, metric_fact.evidence_id)
                self.assertEqual(
                    metric_fact.source_reference,
                    "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
                )
                self.assertTrue(
                    metric_fact.period in {"2024-FY", "2025-Q3", "2024-Q3"}
                )
        self.assertEqual(result.ttm_inputs["revenue"]["current_ytd"].period, "2025-Q3")

    def test_excludes_q4_from_current_ytd_candidate(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        with patch.dict(os.environ, {"EDGAR_IDENTITY": "Test User test@example.com"}):
            result = EdgarTool(
                edgar_module=TTMEdgar(),
                as_of=date(2026, 8, 5),
            ).run(ticker="AAPL")

        self.assertEqual(result.ttm_inputs["revenue"]["current_ytd"].period, "2025-Q3")

    def test_requested_filing_text_is_bounded_and_traceable(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        fake_edgar = LongTextEdgar()
        with patch.dict(os.environ, {"EDGAR_IDENTITY": "Test User test@example.com"}):
            result = EdgarTool(
                edgar_module=fake_edgar,
                as_of=date(2026, 8, 5),
            ).run(ticker="AAPL", include_filing_text=True, max_text_chars=1000)

        filing = result.filings[0]
        self.assertEqual(len(filing.text), 1000)
        self.assertTrue(filing.text_truncated)
        self.assertEqual(filing.text_retrieval_status, "available")
        self.assertEqual(filing.text_source_reference, f"{filing.source_reference}.txt")
        self.assertGreater(fake_edgar.companies[0].filings[0].text_calls, 0)

    def test_requested_missing_filing_text_remains_explicit(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        fake_edgar = NoTextEdgar()
        with patch.dict(os.environ, {"EDGAR_IDENTITY": "Test User test@example.com"}):
            result = EdgarTool(
                edgar_module=fake_edgar,
                as_of=date(2026, 8, 5),
            ).run(ticker="AAPL", include_filing_text=True)

        filing = result.filings[0]
        self.assertEqual(result.status, "partial")
        self.assertIsNone(filing.text)
        self.assertEqual(filing.text_retrieval_status, "unavailable")
        self.assertTrue(filing.warnings)
        self.assertTrue(any("申报文本" in warning for warning in result.warnings))

    def test_company_facts_failure_keeps_filings_as_partial_result(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        with patch.dict(os.environ, {"EDGAR_IDENTITY": "Test User test@example.com"}):
            result = EdgarTool(
                edgar_module=FactsFailingEdgar(),
                as_of=date(2026, 8, 5),
            ).run(ticker="AAPL")

        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.filings), 27)
        self.assertEqual(result.facts, {})
        self.assertEqual(result.errors[0].code, "facts_fetch_failed")
        self.assertIn("Company Facts timed out", result.errors[0].message)

    def test_real_edgar_http_cache_is_disabled_before_network_calls(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        get_http_mgr = Mock(return_value="disabled")
        fake_httpclient = type(
            "FakeHttpClient",
            (),
            {"HTTP_MGR": "cached", "get_http_mgr": staticmethod(get_http_mgr)},
        )()
        fake_module = type("FakeEdgarModule", (), {"__name__": "edgar"})()

        with patch(
            "stockcrewai.tools.edgar_tool.importlib.import_module",
            return_value=fake_httpclient,
        ):
            EdgarTool._configure_http(fake_module)

        get_http_mgr.assert_called_once_with(cache_enabled=False)
        self.assertEqual(fake_httpclient.HTTP_MGR, "disabled")

    def test_one_filing_form_failure_keeps_other_forms(self):
        from stockcrewai.tools.edgar_tool import EdgarTool

        with patch.dict(os.environ, {"EDGAR_IDENTITY": "Test User test@example.com"}):
            result = EdgarTool(
                edgar_module=PartiallyFailingFilingsEdgar(),
                as_of=date(2026, 8, 5),
            ).run(ticker="AAPL")

        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.filings), 7)
        self.assertEqual(result.errors[0].code, "filings_8_k_fetch_failed")
        self.assertIn("8-K filings timed out", result.errors[0].message)


class CalculatorToolTests(unittest.TestCase):
    def test_calculator_uses_decimal_and_returns_traceable_results(self):
        from stockcrewai.tools.calculator_tool import FinancialCalculatorTool

        facts = {
            "revenue_current": {"value": "120", "evidence_id": "ev_revenue_current"},
            "revenue_prior": {"value": "100", "evidence_id": "ev_revenue_prior"},
            "operating_cash_flow": {"value": "30", "evidence_id": "ev_ocf"},
            "capex": {"value": "10", "evidence_id": "ev_capex"},
            "net_income": {"value": "20", "evidence_id": "ev_net_income"},
        }

        result = FinancialCalculatorTool().run(
            company_name="Apple Inc.",
            ticker="AAPL",
            facts=facts,
            formulas=[
                "revenue_growth",
                "free_cash_flow",
                "free_cash_flow_margin",
                "cash_conversion",
            ],
        )

        self.assertEqual(result.status, "ok")
        by_id = {item.formula_id: item for item in result.calculations}
        self.assertEqual(by_id["revenue_growth"].raw_result, "0.2")
        self.assertEqual(by_id["revenue_growth"].normalized_result, "2.00000E-1")
        self.assertEqual(by_id["free_cash_flow"].raw_result, "20")
        self.assertEqual(
            by_id["free_cash_flow"].input_evidence_ids,
            ["ev_ocf", "ev_capex"],
        )

    def test_missing_input_is_unavailable_and_not_zero(self):
        from stockcrewai.tools.calculator_tool import FinancialCalculatorTool

        result = FinancialCalculatorTool().run(
            facts={"operating_cash_flow": {"value": "30"}},
            formulas=["free_cash_flow"],
        )

        calculation = result.calculations[0]
        self.assertEqual(result.status, "partial")
        self.assertEqual(calculation.status, "unavailable")
        self.assertIsNone(calculation.raw_result)


class ValidationToolTests(unittest.TestCase):
    def test_validator_rejects_fact_without_evidence_id(self):
        from stockcrewai.tools.validation_tool import FinancialValidationTool

        result = FinancialValidationTool().run(
            company_name="Apple Inc.",
            facts={"revenue": {"value": "100"}},
        )

        self.assertEqual(result.status, "invalid")
        self.assertTrue(any(issue.code == "missing_evidence_id" for issue in result.issues))

    def test_validator_reports_valid_evidence_and_calculation_ids(self):
        from stockcrewai.tools.calculator_tool import FinancialCalculatorTool
        from stockcrewai.tools.validation_tool import FinancialValidationTool

        facts = {
            "operating_cash_flow": {"value": "30", "evidence_id": "ev_ocf"},
            "capex": {"value": "10", "evidence_id": "ev_capex"},
        }
        calculations = FinancialCalculatorTool().run(
            company_name="Apple Inc.",
            facts=facts,
            formulas=["free_cash_flow"],
        )

        result = FinancialValidationTool().run(
            company_name="Apple Inc.",
            facts=facts,
            calculations=calculations.calculations,
        )

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.validated_evidence_ids, ["ev_capex", "ev_ocf"])
        self.assertEqual(result.validated_calculation_ids, ["calc_free_cash_flow"])

    def test_validator_excludes_unavailable_calculations_from_validated_ids(self):
        from stockcrewai.tools.calculator_tool import FinancialCalculatorTool
        from stockcrewai.tools.validation_tool import FinancialValidationTool

        facts = {
            "operating_cash_flow": {"value": "30", "evidence_id": "ev_ocf"},
            "capex": {"value": "10", "evidence_id": "ev_capex"},
            "revenue_current": {"value": "100", "evidence_id": "ev_revenue"},
        }
        calculations = FinancialCalculatorTool().run(
            company_name="Apple Inc.",
            facts=facts,
            formulas=["free_cash_flow", "revenue_growth"],
        )

        result = FinancialValidationTool().run(
            company_name="Apple Inc.",
            facts=facts,
            calculations=calculations.calculations,
        )

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.checked_calculation_ids, [
            "calc_free_cash_flow",
            "calc_revenue_growth",
        ])
        self.assertEqual(result.validated_calculation_ids, ["calc_free_cash_flow"])

    def test_validator_recomputes_and_rejects_a_wrong_calculation(self):
        from stockcrewai.tools.validation_tool import FinancialValidationTool

        result = FinancialValidationTool().run(
            company_name="Apple Inc.",
            ticker="AAPL",
            facts={
                "operating_cash_flow": {
                    "value": "30",
                    "evidence_id": "ev_ocf",
                },
                "capex": {"value": "10", "evidence_id": "ev_capex"},
            },
            calculations=[
                {
                    "calculation_id": "calc_free_cash_flow",
                    "formula_id": "free_cash_flow",
                    "formula_version": "v1",
                    "input_evidence_ids": ["ev_ocf", "ev_capex"],
                    "raw_inputs": {"operating_cash_flow": "30", "capex": "10"},
                    "raw_result": "25",
                    "normalized_result": "2.50000E+1",
                    "display_result": "25",
                    "unit": "currency",
                    "status": "available",
                    "warnings": [],
                }
            ],
        )

        self.assertEqual(result.status, "invalid")
        self.assertTrue(any(issue.code == "calculation_mismatch" for issue in result.issues))

    def test_validator_rejects_inputs_that_disagree_with_evidence(self):
        from stockcrewai.tools.validation_tool import FinancialValidationTool

        result = FinancialValidationTool().run(
            company_name="Apple Inc.",
            facts={
                "operating_cash_flow": {
                    "value": "30",
                    "evidence_id": "ev_ocf",
                },
                "capex": {"value": "10", "evidence_id": "ev_capex"},
            },
            calculations=[
                {
                    "calculation_id": "calc_free_cash_flow",
                    "formula_id": "free_cash_flow",
                    "formula_version": "v1",
                    "input_evidence_ids": ["ev_ocf", "ev_capex"],
                    "raw_inputs": {"operating_cash_flow": "31", "capex": "10"},
                    "raw_result": "21",
                    "normalized_result": "2.10000E+1",
                    "display_result": "21",
                    "unit": "currency",
                    "status": "available",
                    "warnings": [],
                }
            ],
        )

        self.assertEqual(result.status, "invalid")
        self.assertTrue(
            any(issue.code == "calculation_input_mismatch" for issue in result.issues)
        )


if __name__ == "__main__":
    unittest.main()
