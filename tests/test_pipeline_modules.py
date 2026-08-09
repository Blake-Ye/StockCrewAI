from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace


def _valuation_payload() -> dict[str, object]:
    return {
        "company_name": "Example Holdings",
        "ticker": "ZZZ",
        "valuation_result": {
            "readiness": "ready",
            "validation_status": "valid",
            "calculations": [
                {
                    "calculation_id": "calc_pe_ratio",
                    "formula_id": "pe_ratio",
                    "status": "available",
                    "validation_status": "valid",
                    "input_evidence_ids": ["ev_price", "ev_eps"],
                },
                {
                    "calculation_id": "calc_fcf_yield",
                    "formula_id": "fcf_yield",
                    "status": "available",
                    "validation_status": "valid",
                    "input_evidence_ids": ["ev_price", "ev_fcf"],
                },
            ],
        },
        "historical_valuation_result": {
            "status": "ok",
            "validation_status": "valid",
            "calculation_id": "calc_historical_pe",
            "input_evidence_ids": ["ev_history"],
        },
        "reverse_dcf_result": {
            "status": "ok",
            "validation_status": "valid",
            "calculation_id": "calc_reverse_dcf_growth",
            "input_evidence_ids": ["ev_price", "ev_fcf"],
        },
        "validated_evidence_ids": ["ev_price", "ev_eps", "ev_fcf", "ev_history"],
        "validated_calculation_ids": [
            "calc_pe_ratio",
            "calc_fcf_yield",
            "calc_historical_pe",
            "calc_reverse_dcf_growth",
        ],
    }


def _claim_output(claims: list[dict[str, object]]) -> str:
    return json.dumps({"claims": claims}, ensure_ascii=False)


def _analysis_output() -> SimpleNamespace:
    return SimpleNamespace(
        tasks_output=[
            SimpleNamespace(
                raw=_claim_output(
                    [
                        {
                            "claim_id": "claim_financial_quality",
                            "category": "financial_quality",
                            "statement": "财务质量可由已验证输入解释。",
                            "evidence_ids": ["ev_revenue"],
                            "calculation_ids": ["calc_margin"],
                            "confidence": 0.9,
                        },
                        {
                            "claim_id": "claim_financial_trend",
                            "category": "financial_trend",
                            "statement": "财务趋势可由已验证输入解释。",
                            "evidence_ids": ["ev_revenue"],
                            "calculation_ids": ["calc_margin"],
                            "confidence": 0.9,
                        },
                    ]
                )
            ),
            SimpleNamespace(
                raw=_claim_output(
                    [
                        {
                            "claim_id": "claim_risk",
                            "category": "risk",
                            "statement": "申报文本包含可审计风险。",
                            "evidence_ids": ["ev_filing"],
                            "calculation_ids": [],
                            "confidence": 0.9,
                        }
                    ]
                )
            ),
            SimpleNamespace(
                raw=_claim_output(
                    [
                        {
                            "claim_id": "claim_current_valuation",
                            "category": "current_valuation",
                            "statement": "当前估值输入可验证。",
                            "evidence_ids": ["ev_price"],
                            "calculation_ids": ["calc_pe_ratio"],
                            "confidence": 1.0,
                        }
                    ]
                )
            ),
        ]
    )


class PipelineModuleTests(unittest.TestCase):
    def test_legacy_pipeline_support_reexports_canonical_functions(self):
        from stockcrewai import pipeline_support
        from stockcrewai.pipelines import analysis_pipeline, evidence_pipeline, valuation_pipeline
        from stockcrewai.validators import claim_gate

        self.assertIs(pipeline_support._calculation_facts, evidence_pipeline._calculation_facts)
        self.assertIs(
            pipeline_support._filter_analysis_claims_with_diagnostics,
            analysis_pipeline._filter_analysis_claims_with_diagnostics,
        )
        self.assertIs(pipeline_support._valuation_analysis_input, valuation_pipeline._valuation_analysis_input)
        self.assertIs(pipeline_support._deterministic_verdict, valuation_pipeline._deterministic_verdict)
        self.assertIs(pipeline_support.validate_claim, claim_gate.validate_claim)

    def test_new_modules_do_not_import_crew_or_legacy_support(self):
        root = Path(__file__).parents[1] / "src" / "stockcrewai"
        for relative_path in (
            "pipelines/evidence_pipeline.py",
            "pipelines/analysis_pipeline.py",
            "pipelines/valuation_pipeline.py",
            "validators/claim_gate.py",
        ):
            source = (root / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("stockcrewai.crews", source)
            self.assertNotIn("pipeline_support", source)

    def test_evidence_helpers_preserve_fact_price_history_and_validation_shape(self):
        from stockcrewai.pipelines.evidence_pipeline import (
            _calculation_facts,
            _edgar_error,
            _historical_financial_snapshots,
            _historical_prices,
            _market_price_kwargs,
            _ttm_unavailable,
            _with_validation_status,
        )

        edgar_result = SimpleNamespace(
            facts={"revenue": {"evidence_id": "ev_revenue", "value": "10"}},
            historical_financial_snapshots=[{"as_of": "2024-12-31", "evidence_id": "ev_history"}],
        )
        facts = _calculation_facts(edgar_result)
        self.assertEqual(facts["revenue_current"], facts["revenue"])
        self.assertEqual(
            _market_price_kwargs(
                {
                    "price": "25",
                    "timestamp": "2025-01-02T00:00:00+00:00",
                    "currency": "USD",
                    "source": "offline://price",
                }
            ),
            {
                "market_price": "25",
                "price_timestamp": "2025-01-02T00:00:00+00:00",
                "currency": "USD",
                "source_reference": "offline://price",
            },
        )
        self.assertEqual(
            _historical_prices(
                {"historical_prices": [{"date": "2024-01-01", "price": "20"}, "bad"]}
            ),
            [{"date": "2024-01-01", "price": "20"}],
        )
        self.assertEqual(
            _historical_financial_snapshots(edgar_result),
            edgar_result.historical_financial_snapshots,
        )
        self.assertEqual(
            _with_validation_status(
                {"status": "ok", "input_evidence_ids": ["ev_price"]},
                allowed_evidence_ids={"ev_price"},
                base_valid=True,
            )["validation_status"],
            "valid",
        )
        self.assertEqual(
            _with_validation_status(
                {"status": "ok", "input_evidence_ids": ["ev_untrusted"]},
                allowed_evidence_ids={"ev_price"},
                base_valid=True,
            )["validation_status"],
            "unvalidated",
        )
        self.assertEqual(
            _edgar_error("identity_unavailable", "offline failure", "Example", "ZZZ").errors[0].code,
            "identity_unavailable",
        )
        self.assertEqual(
            _ttm_unavailable("Example", "ZZZ", "ttm_evidence_missing")["reason_code"],
            "ttm_evidence_missing",
        )

    def test_ttm_validation_projects_allowlisted_status_without_network(self):
        from stockcrewai.pipelines.evidence_pipeline import validate_ttm_evidence
        from stockcrewai.tools.validation_tool import ValidationResult

        class OfflineValidationTool:
            def run(self, **kwargs: object) -> ValidationResult:
                self.kwargs = kwargs
                return ValidationResult(
                    status="valid",
                    validated=True,
                    validated_evidence_ids=["ev_revenue"],
                    validated_calculation_ids=[],
                )

        projected, diagnostic = validate_ttm_evidence(
            {"revenue": {"latest_fy": {"evidence_id": "ev_revenue", "value": "10"}}},
            company_name="Example",
            ticker="ZZZ",
            validation_tool=OfflineValidationTool(),
        )
        self.assertEqual(projected["revenue"]["latest_fy"]["validation_status"], "valid")
        self.assertEqual(diagnostic["reason_code"] if "reason_code" in diagnostic else None, None)
        self.assertEqual(diagnostic["fact_keys"], ["revenue:latest_fy"])

    def test_analysis_inputs_diagnostics_and_claim_order_match_legacy_contract(self):
        from stockcrewai.pipelines.analysis_pipeline import (
            _analysis_diagnostic,
            _financial_analysis_input,
            _filter_analysis_claims_with_diagnostics,
        )

        state = {
            "company_name": "Example Holdings",
            "ticker": "ZZZ",
            "facts": {"revenue": {"evidence_id": "ev_revenue", "value": "10"}},
            "calculations": [{"calculation_id": "calc_margin"}],
            "validated_evidence_ids": ["ev_revenue"],
            "validated_calculation_ids": ["calc_margin", "calc_pe_ratio"],
            "policy_context": {"policy_version": "metric-policy:v1"},
        }
        self.assertEqual(
            set(_financial_analysis_input(state)),
            {
                "company_name",
                "ticker",
                "facts",
                "calculations",
                "validated_evidence_ids",
                "validated_calculation_ids",
                "policy_context",
            },
        )
        claims, required_data, diagnostics = _filter_analysis_claims_with_diagnostics(
            _analysis_output(),
            ["ev_revenue"],
            ["ev_filing"],
            ["ev_price"],
            ["calc_margin", "calc_pe_ratio"],
        )
        self.assertEqual(
            [claim["category"] for claim in claims],
            ["financial_quality", "financial_trend", "risk", "current_valuation"],
        )
        self.assertEqual(required_data, [])
        self.assertIsNone(diagnostics)
        diagnostic = _analysis_diagnostic(
            [SimpleNamespace(raw='API_KEY="secret-value"')],
            "financial",
            "raw_json_invalid",
        )
        self.assertEqual(diagnostic["reason_code"], "raw_json_invalid")
        self.assertNotIn("secret-value", json.dumps(diagnostic, ensure_ascii=False))

    def test_claim_gate_preserves_reason_codes_for_category_and_id_rejection(self):
        from stockcrewai.validators.claim_gate import (
            ANALYSIS_DOMAIN_RULES,
            AnalysisClaim,
            Claim,
            validate_claim,
        )
        from stockcrewai.models.evidence import ClaimRecord

        self.assertIs(Claim, ClaimRecord)
        self.assertIs(AnalysisClaim, ClaimRecord)
        self.assertIn("financial", ANALYSIS_DOMAIN_RULES)
        valid_claim, reason = validate_claim(
            {
                "claim_id": "claim_financial_quality",
                "category": "financial_quality",
                "statement": "财务质量可由已验证输入解释。",
                "evidence_ids": ["ev_revenue"],
                "calculation_ids": ["calc_margin"],
                "confidence": 0.9,
            },
            allowed_categories={"financial_quality", "financial_trend"},
            evidence_allowlist={"ev_revenue"},
            calculation_allowlist={"calc_margin"},
            requires_calculations=True,
        )
        self.assertIsNone(reason)
        self.assertEqual(valid_claim["claim_id"], "claim_financial_quality")
        _, reason = validate_claim(
            {
                "claim_id": "claim_bad",
                "category": "unsupported",
                "statement": "invalid",
                "evidence_ids": ["ev_revenue"],
                "calculation_ids": ["calc_margin"],
                "confidence": 0.9,
            },
            allowed_categories={"financial_quality"},
            evidence_allowlist={"ev_revenue"},
            calculation_allowlist={"calc_margin"},
            requires_calculations=True,
        )
        self.assertEqual(reason, "category_invalid")
        _, reason = validate_claim(
            {
                "claim_id": "claim_bad_id",
                "category": "financial_quality",
                "statement": "invalid",
                "evidence_ids": ["ev_untrusted"],
                "calculation_ids": ["calc_margin"],
                "confidence": 0.9,
            },
            allowed_categories={"financial_quality"},
            evidence_allowlist={"ev_revenue"},
            calculation_allowlist={"calc_margin"},
            requires_calculations=True,
        )
        self.assertEqual(reason, "evidence_ids_invalid")

    def test_valuation_input_and_deterministic_claims_keep_allowlist_and_order(self):
        from stockcrewai.pipelines.valuation_pipeline import (
            _reverse_dcf_inputs,
            _valuation_analysis_input,
            build_deterministic_valuation_claims,
        )

        state = {
            "company_name": "Example Holdings",
            "ticker": "ZZZ",
            "facts": {},
            "calculations": [],
            "validated_evidence_ids": ["ev_state"],
            "validated_calculation_ids": ["calc_state"],
        }
        payload = _valuation_analysis_input(
            state,
            _valuation_payload()["valuation_result"],
            _valuation_payload()["historical_valuation_result"],
            _valuation_payload()["reverse_dcf_result"],
            trusted_evidence_ids={"ev_state", "ev_price", "ev_history"},
        )
        self.assertNotIn("ev_fcf", payload["validated_evidence_ids"])
        self.assertIn("calc_state", payload["validated_calculation_ids"])
        self.assertNotIn("calc_injected", payload["validated_calculation_ids"])
        claims = build_deterministic_valuation_claims(_valuation_payload())
        self.assertEqual(
            [claim["category"] for claim in claims],
            ["current_valuation", "historical_valuation", "reverse_dcf"],
        )
        self.assertEqual(claims[0]["calculation_ids"], ["calc_pe_ratio", "calc_fcf_yield"])
        self.assertTrue(all(claim["confidence"] == 1.0 for claim in claims))
        reverse_inputs = _reverse_dcf_inputs(
            {
                "facts": {
                    "common_shares_outstanding": {"raw_result": "10"},
                },
                "ttm": {
                    "metrics": [
                        {
                            "metric_id": "free_cash_flow",
                            "status": "available",
                            "raw_result": "100",
                            "period_basis": "TTM",
                            "validation_status": "valid",
                        }
                    ]
                },
            },
            {"market_price": "25", "market_price_evidence_id": "ev_price"},
        )
        self.assertEqual(reverse_inputs["market_price"], {"value": "25", "evidence_id": "ev_price"})
        self.assertEqual(reverse_inputs["fcf"]["raw_result"], "100")


if __name__ == "__main__":
    unittest.main()
