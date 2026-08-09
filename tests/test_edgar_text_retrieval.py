from datetime import date
import os
import unittest
from unittest.mock import patch


class FakeFiling:
    def __init__(self, form: str, index: int):
        self.form = form
        self.form_type = form
        self.filing_date = date(2026, 8, 5)
        self.period_of_report = date(2025, 12, 31)
        self.accession_number = f"0000320193-26-{index:06d}"
        self.items = ["1A"] if form == "8-K" else []
        self.url = f"https://www.sec.gov/Archives/edgar/data/320193/{self.accession_number}"


class EmptyTextFiling(FakeFiling):
    def text(self):
        return ""


class MissingTextFiling(FakeFiling):
    pass


class RaisingTextFiling(FakeFiling):
    @property
    def text(self):
        raise RuntimeError("filing text failed")


class RaisingTextUrlFiling(FakeFiling):
    def text(self):
        return "retrieved filing text"

    @property
    def text_url(self):
        raise RuntimeError("text source failed")


class WhitespaceTextFiling(FakeFiling):
    def text(self):
        return " \n\t"


class RiskTextFiling(FakeFiling):
    ten_k_text = (
        "ITEM 1A. RISK FACTORS\n"
        + ("Risk body that should be retained.\n" * 100)
        + "ITEM 1B. UNRESOLVED STAFF COMMENTS\n"
        + "This is not part of the risk section.\n"
    )
    ten_q_text = (
        "PART II, ITEM 1A. RISK FACTORS\n"
        "10-Q risk body that should be retained.\n"
        "ITEM 2. UNREGISTERED SALES OF EQUITY SECURITIES\n"
        "This is not part of the risk section.\n"
    )
    eight_k_text = "ITEM 1. ENTRY INTO A MATERIAL DEFINITIVE AGREEMENT\nEvent body.\n"

    def text(self):
        if self.form == "10-K":
            return self.ten_k_text
        if self.form == "10-Q":
            return self.ten_q_text
        if self.form == "8-K":
            return self.eight_k_text
        return "No risk section in this filing."


class Shell8KTextFiling(RiskTextFiling):
    shell_8k_text = (
        "Item 2.02. Results of Operations and Financial Condition\n"
        "Item 9.01. Financial Statements and Exhibits\n"
        "Exhibit 99.1 is furnished herewith.\n"
    )

    def __init__(self, form: str, index: int):
        super().__init__(form, index)
        if form == "8-K":
            self.items = ["2.02", "9.01"]

    def text(self):
        return self.shell_8k_text if self.form == "8-K" else super().text()


class Substantive8KTextFiling(RiskTextFiling):
    substantive_8k_text = (
        "Item 5.02. Departure of Directors or Certain Officers\n"
        "The company appointed a new chief financial officer.\n"
    )

    def __init__(self, form: str, index: int):
        super().__init__(form, index)
        if form == "8-K":
            self.items = ["5.02"]

    def text(self):
        return self.substantive_8k_text if self.form == "8-K" else super().text()


class Unsupported8KTextFiling(RiskTextFiling):
    unsupported_8k_text = (
        "Item 1.01. Entry into a Material Definitive Agreement\n"
        "The company entered into a material agreement.\n"
    )

    def __init__(self, form: str, index: int):
        super().__init__(form, index)
        if form == "8-K":
            self.items = ["1.01"]

    def text(self):
        return self.unsupported_8k_text if self.form == "8-K" else super().text()


class DirectoryOnly8KTextFiling(RiskTextFiling):
    directory_only_8k_text = (
        "TABLE OF CONTENTS\n"
        "Item 5.02. Departure of Directors or Certain Officers ..........\n"
        "3 of 14\n"
    )

    def __init__(self, form: str, index: int):
        super().__init__(form, index)
        if form == "8-K":
            self.items = ["5.02"]

    def text(self):
        return self.directory_only_8k_text if self.form == "8-K" else super().text()


class DirectoryRiskTextFiling(RiskTextFiling):
    ten_k_text = (
        "TABLE OF CONTENTS\n"
        "Item 1A. Risk Factors .......... 12\n"
        "Item 1B. Unresolved Staff Comments .......... 13\n"
        "PART I\n"
        "Item 1. Business\n"
        "Item 1A. Risk Factors\n"
        "Actual risk body from the filing正文.\n"
        "Item 1B. Unresolved Staff Comments\n"
        "This is outside the risk section.\n"
    )


class FakeFilings:
    def __init__(self, filings):
        self.filings = filings

    def head(self, count):
        return self.filings[:count]


class FakeFacts:
    def get_concept(self, concept, period=None, return_metadata=False):
        if not return_metadata:
            return "1"
        return {
            "value": "1",
            "tag_used": f"us-gaap:{concept}",
            "period": period or "2024-FY",
            "period_start": date(2024, 1, 1),
            "period_end": date(2024, 12, 31),
            "filing_date": date(2025, 2, 1),
            "unit": "USD",
            "accession": "0000320193-25-000001",
            "form_type": "10-K",
            "period_type": "duration",
            "fiscal_year": 2024,
            "fiscal_period": "FY",
        }


class FakeCompany:
    filing_class = FakeFiling

    def __init__(self, identifier):
        self.identifier = identifier
        self.name = "Apple Inc."
        self.cik = 320193

    def get_ticker(self):
        return "AAPL"

    def get_exchanges(self):
        return ["NASDAQ"]

    def get_facts(self):
        return FakeFacts()

    def get_filings(self, **kwargs):
        return FakeFilings([self.filing_class(kwargs["form"], 1)])


class EmptyTextCompany(FakeCompany):
    filing_class = EmptyTextFiling


class MissingTextCompany(FakeCompany):
    filing_class = MissingTextFiling


class RaisingTextCompany(FakeCompany):
    filing_class = RaisingTextFiling


class RaisingTextUrlCompany(FakeCompany):
    filing_class = RaisingTextUrlFiling


class WhitespaceTextCompany(FakeCompany):
    filing_class = WhitespaceTextFiling


class FakeEdgar:
    company_class = FakeCompany

    def set_identity(self, identity):
        self.identity = identity

    def Company(self, identifier):
        return self.company_class(identifier)


class EmptyTextEdgar(FakeEdgar):
    company_class = EmptyTextCompany


class MissingTextEdgar(FakeEdgar):
    company_class = MissingTextCompany


class RaisingTextEdgar(FakeEdgar):
    company_class = RaisingTextCompany


class RaisingTextUrlEdgar(FakeEdgar):
    company_class = RaisingTextUrlCompany


class WhitespaceTextEdgar(FakeEdgar):
    company_class = WhitespaceTextCompany


class RiskTextCompany(FakeCompany):
    filing_class = RiskTextFiling


class RiskTextEdgar(FakeEdgar):
    company_class = RiskTextCompany


class DirectoryRiskTextCompany(RiskTextCompany):
    filing_class = DirectoryRiskTextFiling


class DirectoryRiskTextEdgar(FakeEdgar):
    company_class = DirectoryRiskTextCompany


class Shell8KTextCompany(RiskTextCompany):
    filing_class = Shell8KTextFiling


class Shell8KTextEdgar(FakeEdgar):
    company_class = Shell8KTextCompany


class Substantive8KTextCompany(RiskTextCompany):
    filing_class = Substantive8KTextFiling


class Substantive8KTextEdgar(FakeEdgar):
    company_class = Substantive8KTextCompany


class Unsupported8KTextCompany(RiskTextCompany):
    filing_class = Unsupported8KTextFiling


class Unsupported8KTextEdgar(FakeEdgar):
    company_class = Unsupported8KTextCompany


class DirectoryOnly8KTextCompany(RiskTextCompany):
    filing_class = DirectoryOnly8KTextFiling


class DirectoryOnly8KTextEdgar(FakeEdgar):
    company_class = DirectoryOnly8KTextCompany


class EdgarTextRetrievalTests(unittest.TestCase):
    def _run(self, edgar_module, max_text_chars=1000, include_filing_text=True):
        from stockcrewai.tools.edgar_tool import EdgarTool

        with patch.dict(os.environ, {"EDGAR_IDENTITY": "Test User test@example.com"}):
            return EdgarTool(
                edgar_module=edgar_module,
                as_of=date(2026, 8, 5),
            ).run(
                ticker="AAPL",
                include_filing_text=include_filing_text,
                max_text_chars=max_text_chars,
            )

    @staticmethod
    def _filing(result, form):
        return next(filing for filing in result.filings if filing.form == form)

    def _assert_rejected_risk_eligibility(self, filing, reason_code):
        self.assertEqual(filing.risk_eligibility.eligibility, "rejected")
        self.assertIsNone(filing.risk_eligibility.evidence_kind)
        self.assertEqual(
            filing.risk_eligibility.evidence_id,
            filing.evidence_id,
        )
        self.assertEqual(
            filing.risk_eligibility.source_reference,
            filing.source_reference,
        )
        self.assertEqual(filing.risk_eligibility.reason_code, reason_code)

    def test_empty_text_is_unavailable_with_warning(self):
        result = self._run(EmptyTextEdgar())

        filing = result.filings[0]
        self.assertIsNone(filing.text)
        self.assertEqual(filing.text_retrieval_status, "unavailable")
        self.assertEqual(filing.risk_sections, [])
        self._assert_rejected_risk_eligibility(filing, "missing_body")
        self.assertTrue(any("返回内容为空" in warning for warning in filing.warnings))

    def test_missing_text_accessor_is_unavailable_with_warning(self):
        result = self._run(MissingTextEdgar())

        filing = result.filings[0]
        self.assertIsNone(filing.text)
        self.assertEqual(filing.text_retrieval_status, "unavailable")
        self.assertEqual(filing.risk_sections, [])
        self._assert_rejected_risk_eligibility(filing, "missing_body")
        self.assertTrue(any("未提供 text" in warning for warning in filing.warnings))

    def test_text_accessor_exception_keeps_filing_unavailable_with_warning(self):
        result = self._run(RaisingTextEdgar())

        self.assertEqual(len(result.filings), 3)
        filing = result.filings[0]
        self.assertIsNone(filing.text)
        self.assertEqual(filing.text_retrieval_status, "unavailable")
        self.assertEqual(filing.risk_sections, [])
        self._assert_rejected_risk_eligibility(filing, "missing_body")
        self.assertTrue(any("RuntimeError" in warning for warning in filing.warnings))

    def test_text_source_exception_falls_back_to_filing_source(self):
        result = self._run(RaisingTextUrlEdgar())

        self.assertEqual(len(result.filings), 3)
        filing = result.filings[0]
        self.assertEqual(filing.text, "retrieved filing text")
        self.assertEqual(filing.text_retrieval_status, "available")
        self.assertEqual(filing.text_source_reference, filing.source_reference)
        self.assertFalse(filing.text_truncated)
        self.assertTrue(any("RuntimeError" in warning for warning in filing.warnings))

    def test_whitespace_text_has_no_risk_sections(self):
        result = self._run(WhitespaceTextEdgar())

        filing = result.filings[0]
        self.assertEqual(filing.text_retrieval_status, "available")
        self.assertEqual(filing.risk_sections, [])
        self._assert_rejected_risk_eligibility(filing, "missing_body")

    def test_not_requested_text_has_no_risk_sections(self):
        result = self._run(RiskTextEdgar(), include_filing_text=False)

        self.assertTrue(result.filings)
        for filing in result.filings:
            self.assertEqual(filing.text_retrieval_status, "not_requested")
            self.assertEqual(filing.risk_sections, [])
            self._assert_rejected_risk_eligibility(filing, "missing_body")

    def test_complete_10k_extracts_item_1a_without_item_1b(self):
        result = self._run(RiskTextEdgar(), max_text_chars=5000)

        filing = self._filing(result, "10-K")
        risk_sections = getattr(filing, "risk_sections", None)
        self.assertIsNotNone(risk_sections)
        self.assertEqual(len(risk_sections), 1)
        section = risk_sections[0]
        self.assertEqual(section.section_type, "10k_item_1a")
        self.assertIn("Risk body that should be retained.", section.text)
        self.assertNotIn("ITEM 1B", section.text)
        self.assertNotIn("This is not part of the risk section.", section.text)

    def test_10k_skips_table_of_contents_item_1a(self):
        result = self._run(DirectoryRiskTextEdgar(), max_text_chars=5000)

        filing = self._filing(result, "10-K")
        self.assertEqual(len(filing.risk_sections), 1)
        section = filing.risk_sections[0]
        self.assertIn("Actual risk body from the filing正文.", section.text)
        self.assertNotIn(".......... 12", section.text)

    def test_complete_10q_extracts_part_ii_item_1a_until_next_item(self):
        result = self._run(RiskTextEdgar(), max_text_chars=5000)

        filing = self._filing(result, "10-Q")
        self.assertEqual(len(filing.risk_sections), 1)
        section = filing.risk_sections[0]
        self.assertEqual(section.section_type, "10q_item_1a")
        self.assertIn("10-Q risk body that should be retained.", section.text)
        self.assertNotIn("ITEM 2", section.text)
        self.assertNotIn("This is not part of the risk section.", section.text)

    def test_truncated_10k_keeps_complete_item_1a_from_raw_text(self):
        result = self._run(RiskTextEdgar(), max_text_chars=1000)

        filing = self._filing(result, "10-K")
        self.assertTrue(filing.text_truncated)
        self.assertEqual(len(filing.risk_sections), 1)
        self.assertTrue(filing.risk_sections[0].complete)
        self.assertIn("Risk body that should be retained.", filing.risk_sections[0].text)
        self.assertNotIn("ITEM 1B", filing.risk_sections[0].text)
        self.assertEqual(filing.risk_eligibility.eligibility, "eligible")
        self.assertEqual(filing.risk_eligibility.reason_code, "eligible_item_1a")

    def test_8k_attachment_shell_is_rejected_without_risk_section(self):
        result = self._run(Shell8KTextEdgar(), max_text_chars=5000)

        filing = self._filing(result, "8-K")
        self.assertEqual(filing.risk_sections, [])
        self.assertEqual(filing.risk_eligibility.eligibility, "rejected")
        self.assertEqual(filing.risk_eligibility.reason_code, "attachment_shell")
        self.assertIsNone(filing.risk_eligibility.evidence_kind)

    def test_8k_substantive_item_is_eligible_event_section(self):
        result = self._run(Substantive8KTextEdgar(), max_text_chars=5000)

        filing = self._filing(result, "8-K")
        self.assertEqual(len(filing.risk_sections), 1)
        section = filing.risk_sections[0]
        self.assertEqual(section.section_type, "8k_event")
        self.assertEqual(
            section.section_title,
            "Item 5.02. Departure of Directors or Certain Officers",
        )
        self.assertTrue(section.complete)
        self.assertIn("appointed a new chief financial officer", section.text)
        self.assertEqual(filing.risk_eligibility.eligibility, "eligible")
        self.assertEqual(
            filing.risk_eligibility.reason_code,
            "eligible_8k_event",
        )

    def test_8k_unsupported_item_is_rejected(self):
        result = self._run(Unsupported8KTextEdgar(), max_text_chars=5000)

        filing = self._filing(result, "8-K")
        self.assertEqual(filing.risk_sections, [])
        self.assertEqual(filing.risk_eligibility.eligibility, "rejected")
        self.assertEqual(filing.risk_eligibility.reason_code, "unsupported_item")

    def test_8k_item_5_02_directory_page_range_entry_is_rejected(self):
        result = self._run(DirectoryOnly8KTextEdgar(), max_text_chars=5000)

        filing = self._filing(result, "8-K")
        self.assertEqual(filing.risk_sections, [])
        self._assert_rejected_risk_eligibility(filing, "truncated")


if __name__ == "__main__":
    unittest.main()
