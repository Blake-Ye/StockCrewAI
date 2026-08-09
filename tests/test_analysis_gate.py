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


class AnalysisGateApplicabilityTests(unittest.TestCase):
    @staticmethod
    def _ready_risk_input() -> dict[str, object]:
        return {
            "validated_filing_ids": ["ev_risk"],
            "filings": [
                {
                    "evidence_id": "ev_risk",
                    "risk_eligibility": {"eligibility": "eligible"},
                    "risk_sections": [{"complete": True, "text": "risk evidence"}],
                }
            ],
        }

    @classmethod
    def _gate(
        cls,
        *,
        state: dict[str, object] | None = None,
        valuation: dict[str, object] | None = None,
        reverse_dcf: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return pipeline_support._analysis_gate(
            SimpleNamespace(status="valid", validated=True),
            {
                "company_name": "Example Holdings",
                "ticker": "EXM",
                "facts": {"revenue": {"validation_status": "valid"}},
                "calculations": [{"validation_status": "valid"}],
                "validated_evidence_ids": ["ev_revenue"],
                "validated_calculation_ids": ["calc_margin"],
                **(state or {}),
            },
            cls._ready_risk_input(),
            valuation
            or {"readiness": "ready", "validation_status": "valid"},
            {"status": "ok", "validation_status": "valid"},
            reverse_dcf or {"status": "ok", "validation_status": "valid"},
        )

    def test_invalid_fcf_is_non_blocking_for_deterministic_negative_fcf_policy(self):
        gate = self._gate(
            state={
                "facts": {
                    "current_fcf": {
                        "value": "-10",
                        "validation_status": "valid",
                    }
                }
            },
            reverse_dcf={
                "status": "unavailable",
                "reasons": ["invalid_fcf"],
            },
        )

        self.assertEqual(gate["status"], "ready")
        self.assertNotIn("reverse_dcf_required", gate["required_data"])
        self.assertEqual(
            gate["applicability"]["reverse_dcf"]["status"], "not_applicable"
        )
        self.assertTrue(
            any("反向 DCF" in note for note in gate["limitations"]),
            gate["limitations"],
        )

    def test_ttm_fcf_required_is_non_blocking_for_deterministic_bank_policy(self):
        gate = self._gate(
            state={"issuer_type": "bank"},
            reverse_dcf={
                "status": "unavailable",
                "reasons": ["ttm_fcf_required"],
            },
        )

        self.assertEqual(gate["status"], "ready")
        self.assertNotIn("reverse_dcf_required", gate["required_data"])
        self.assertEqual(
            gate["applicability"]["reverse_dcf"]["status"], "not_applicable"
        )
        self.assertEqual(
            gate["applicability"]["reverse_dcf"]["reason_code"],
            "issuer_type_bank",
        )

    def test_reverse_dcf_stays_required_without_applicability_policy(self):
        gate = self._gate(
            reverse_dcf={
                "status": "unavailable",
                "reasons": ["ttm_fcf_required"],
            }
        )

        self.assertEqual(gate["status"], "blocked")
        self.assertIn("reverse_dcf_required", gate["required_data"])
        self.assertEqual(
            gate["applicability"]["reverse_dcf"]["status"], "required"
        )

    def test_partial_current_valuation_is_ready_with_auditable_fcf_yield(self):
        gate = self._gate(
            valuation={
                "status": "partial",
                "readiness": "not_ready",
                "validation_status": "unvalidated",
                "readiness_reasons": ["diluted_eps_positive"],
                "calculations": [
                    {
                        "calculation_id": "calc_market_capitalization",
                        "formula_id": "market_capitalization",
                        "status": "available",
                        "validation_status": "valid",
                        "input_evidence_ids": ["ev_price", "ev_shares"],
                        "raw_result": "100",
                    },
                    {
                        "calculation_id": "calc_pe_ratio",
                        "formula_id": "pe_ratio",
                        "status": "unavailable",
                        "validation_status": "unvalidated",
                        "input_evidence_ids": ["ev_price"],
                    },
                    {
                        "calculation_id": "calc_fcf_yield",
                        "formula_id": "fcf_yield",
                        "status": "available",
                        "validation_status": "valid",
                        "input_evidence_ids": ["ev_price", "ev_fcf"],
                        "raw_result": "0.05",
                    },
                ],
            }
        )

        self.assertEqual(gate["status"], "ready")
        self.assertNotIn("current_valuation_required", gate["required_data"])
        self.assertTrue(
            any("P/E" in note or "估值" in note for note in gate["limitations"]),
            gate["limitations"],
        )


if __name__ == "__main__":
    unittest.main()
