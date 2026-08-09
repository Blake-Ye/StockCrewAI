from __future__ import annotations

from types import SimpleNamespace
import unittest

import stockcrewai.pipeline_support as pipeline_support
from stockcrewai.tools.edgar_tool import (
    EdgarFilingEvidence,
    EdgarResult,
    EdgarRiskEligibility,
    EdgarRiskSection,
)


class AnalysisGateRiskEvidenceTests(unittest.TestCase):
    @staticmethod
    def _filing(
        evidence_id: str,
        *,
        eligibility: str,
        reason_code: str,
        evidence_kind: str | None,
        form: str,
        section_type: str,
        section_title: str,
        section_text: str,
        text: str | None = "filing body",
        text_retrieval_status: str = "available",
    ) -> EdgarFilingEvidence:
        return EdgarFilingEvidence(
            evidence_id=evidence_id,
            cik="0000320193",
            form=form,
            filed_at="2026-01-01",
            period_end="2025-12-31",
            accession_number=f"0000320193-26-{evidence_id[-3:]}",
            source_reference=f"sec:{evidence_id}",
            text_source_reference=f"sec:{evidence_id}:text",
            text=text,
            risk_sections=[
                EdgarRiskSection(
                    section_type=section_type,
                    section_title=section_title,
                    text=section_text,
                    complete=True,
                )
            ],
            risk_eligibility=EdgarRiskEligibility(
                evidence_id=evidence_id,
                eligibility=eligibility,
                evidence_kind=evidence_kind,
                reason_code=reason_code,
                section_title=section_title if evidence_kind else None,
                filed_at="2026-01-01",
                source_reference=f"sec:{evidence_id}",
            ),
            text_retrieval_status=text_retrieval_status,
            text_truncated=False,
        )

    def _filings(self) -> list[EdgarFilingEvidence]:
        return [
            self._filing(
                "ev_item1a",
                eligibility="eligible",
                reason_code="eligible_item_1a",
                evidence_kind="item_1a",
                form="10-K",
                section_type="10k_item_1a",
                section_title="Item 1A. Risk Factors",
                section_text="供应链与客户集中风险因素。",
            ),
            self._filing(
                "ev_shell",
                eligibility="rejected",
                reason_code="attachment_shell",
                evidence_kind=None,
                form="8-K",
                section_type="8k_event",
                section_title="Item 2.02",
                section_text="附件已提交。",
            ),
            self._filing(
                "ev_missing",
                eligibility="rejected",
                reason_code="missing_body",
                evidence_kind=None,
                form="10-K",
                section_type="10k_item_1a",
                section_title="Item 1A. Risk Factors",
                section_text="不可用正文的残留章节。",
                text=None,
            ),
        ]

    def test_risk_input_only_keeps_eligible_complete_filing(self):
        edgar_result = EdgarResult(status="ok", filings=self._filings())
        packet = pipeline_support._risk_analysis_input(
            edgar_result,
            {"validated_filing_ids": ["ev_item1a", "ev_shell", "ev_missing"]},
        )

        self.assertEqual(packet["status"], "available")
        self.assertEqual(packet["validated_filing_ids"], ["ev_item1a"])
        self.assertEqual(
            [filing["evidence_id"] for filing in packet["filings"]],
            ["ev_item1a"],
        )
        self.assertEqual(
            packet["filings"][0]["risk_eligibility"]["evidence_kind"],
            "item_1a",
        )
        self.assertTrue(packet["filings"][0]["risk_sections"][0]["complete"])
        self.assertNotIn("text", packet["filings"][0])

    def test_analysis_gate_uses_risk_evidence_missing_without_eligible_filing(self):
        edgar_result = EdgarResult(status="ok", filings=self._filings()[1:])
        risk_input = pipeline_support._risk_analysis_input(
            edgar_result,
            {"validated_filing_ids": ["ev_shell", "ev_missing"]},
        )
        validation_result = SimpleNamespace(status="valid", validated=True)
        gate = pipeline_support._analysis_gate(
            validation_result,
            {
                "facts": {"revenue": {"validation_status": "valid"}},
                "calculations": [{"validation_status": "valid"}],
                "validated_evidence_ids": ["ev_revenue"],
                "validated_calculation_ids": ["calc_margin"],
            },
            risk_input,
            {"readiness": "ready", "validation_status": "valid"},
            {"status": "ok", "validation_status": "valid"},
            {"status": "ok", "validation_status": "valid"},
        )

        self.assertEqual(gate["status"], "blocked")
        self.assertEqual(gate["required_data"], ["risk_evidence_missing"])

    def test_risk_builder_emits_only_disclosure_fact_claims(self):
        packet = pipeline_support._risk_analysis_input(
            EdgarResult(status="ok", filings=self._filings()),
            {"validated_filing_ids": ["ev_item1a", "ev_shell", "ev_missing"]},
        )
        builder = getattr(
            pipeline_support,
            "build_deterministic_risk_disclosure_claims",
            None,
        )
        self.assertIsNotNone(builder)

        claims = builder(packet)

        self.assertEqual(len(claims), 1)
        claim = claims[0]
        self.assertEqual(claim["category"], "risk")
        self.assertEqual(claim["calculation_ids"], [])
        self.assertIs(type(claim["confidence"]), float)
        self.assertEqual(claim["confidence"], 1.0)
        self.assertEqual(claim["evidence_ids"], ["ev_item1a"])
        self.assertIn("披露了", claim["statement"])
        self.assertIn("Item 1A", claim["statement"])
        forbidden = (
            "概率",
            "严重度",
            "损失",
            "评级",
            "买卖建议",
            "投资建议",
            "未来",
            "probability",
            "severity",
            "loss",
            "rating",
        )
        self.assertFalse(
            any(word.lower() in claim["statement"].lower() for word in forbidden),
            claim["statement"],
        )

    def test_risk_builder_returns_empty_without_eligible_evidence(self):
        packet = pipeline_support._risk_analysis_input(
            EdgarResult(status="ok", filings=self._filings()[1:]),
            {"validated_filing_ids": ["ev_shell", "ev_missing"]},
        )
        builder = getattr(
            pipeline_support,
            "build_deterministic_risk_disclosure_claims",
            None,
        )
        self.assertIsNotNone(builder)

        self.assertEqual(builder(packet), [])

    def test_risk_builder_orders_multiple_filings_by_allowlist_with_stable_ids(self):
        item1a = self._filing(
            "ev_item1a",
            eligibility="eligible",
            reason_code="eligible_item_1a",
            evidence_kind="item_1a",
            form="10-K",
            section_type="10k_item_1a",
            section_title="Item 1A. Risk Factors",
            section_text="供应链与客户集中风险因素。",
        )
        event = self._filing(
            "ev_8k",
            eligibility="eligible",
            reason_code="eligible_8k_event",
            evidence_kind="substantive_8k_event",
            form="8-K",
            section_type="8k_event",
            section_title="Item 2.02 Results of Operations",
            section_text="公司披露了经营结果事件。",
        )
        shell = self._filings()[1]
        packet = pipeline_support._risk_analysis_input(
            EdgarResult(status="ok", filings=[item1a, event, shell]),
            {"validated_filing_ids": ["ev_item1a", "ev_8k", "ev_shell"]},
        )

        claims = pipeline_support.build_deterministic_risk_disclosure_claims(packet)

        expected_evidence_ids = ["ev_8k", "ev_item1a"]
        expected_claim_ids = [
            "claim_risk_disclosure_ev_8k",
            "claim_risk_disclosure_ev_item1a",
        ]
        self.assertEqual(
            [claim["evidence_ids"][0] for claim in claims],
            expected_evidence_ids,
        )
        self.assertEqual(
            [claim["claim_id"] for claim in claims],
            expected_claim_ids,
        )
        self.assertEqual(
            [claim["statement"] for claim in claims],
            [
                "该 filing 披露了 Item 2.02 事件。",
                "该 filing 披露了 Item 1A 风险因素章节。",
            ],
        )
        self.assertNotIn("ev_shell", [claim["evidence_ids"][0] for claim in claims])


if __name__ == "__main__":
    unittest.main()
