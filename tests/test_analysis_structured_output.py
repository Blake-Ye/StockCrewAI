import re
import unittest
import json

from crewai import TaskOutput
from pydantic import ValidationError


class AnalysisStructuredOutputTests(unittest.TestCase):
    @staticmethod
    def _task_output(payload):
        return TaskOutput(
            description="analysis",
            agent="analysis-agent",
            raw=json.dumps(payload, ensure_ascii=False),
        )

    @staticmethod
    def _claim(category, *, calculation_ids=None):
        return {
            "claim_id": f"claim_{category}",
            "category": category,
            "statement": "已验证分析结论。",
            "evidence_ids": ["ev_any"],
            "calculation_ids": calculation_ids if calculation_ids is not None else ["calc_any"],
            "confidence": 0.9,
        }

    def test_claims_only_output_accepts_empty_claims(self):
        from stockcrewai.crews.analysis.crew import AnalysisTaskOutput

        output = AnalysisTaskOutput.model_validate({"claims": []})

        self.assertEqual(output.model_dump(), {"claims": []})

    def test_claims_only_output_rejects_legacy_metadata(self):
        from stockcrewai.crews.analysis.crew import AnalysisTaskOutput

        for field in ("limitations", "warnings"):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    AnalysisTaskOutput.model_validate({"claims": [], field: []})

    def test_claim_model_rejects_unknown_fields(self):
        from stockcrewai.crews.analysis.crew import AnalysisClaim

        with self.assertRaises(ValidationError):
            AnalysisClaim.model_validate(
                {
                    "claim_id": "claim_margin",
                    "category": "financial_quality",
                    "statement": "营业利润率稳定。",
                    "evidence_ids": ["ev_margin"],
                    "calculation_ids": ["calc_margin"],
                    "confidence": 0.9,
                    "unexpected": "must be rejected",
                }
            )

    def test_claim_confidence_is_constrained_to_zero_through_one(self):
        from stockcrewai.crews.analysis.crew import AnalysisClaim

        claim = AnalysisClaim(
            claim_id="claim_margin",
            category="financial_quality",
            statement="营业利润率稳定。",
            evidence_ids=["ev_margin"],
            calculation_ids=["calc_margin"],
            confidence=1,
        )
        self.assertEqual(claim.confidence, 1)

        for confidence in (-0.01, 1.01):
            with self.subTest(confidence=confidence):
                with self.assertRaises(ValidationError):
                    AnalysisClaim(
                        claim_id="claim_margin",
                        category="financial_quality",
                        statement="营业利润率稳定。",
                        evidence_ids=["ev_margin"],
                        calculation_ids=["calc_margin"],
                        confidence=confidence,
                    )

    def test_financial_task_uses_local_guardrail_without_provider_structured_output(self):
        from stockcrewai.crews.analysis.crew import AnalysisCrew, AnalysisTaskOutput

        analysis_crew = AnalysisCrew()
        task = analysis_crew.financial_quality_analysis_task()

        self.assertIsNotNone(task.guardrail)
        self.assertEqual(task.guardrail_max_retries, 2)
        self.assertIsNone(task.output_json)
        self.assertIs(task.output_pydantic, AnalysisTaskOutput)

    def test_all_analysis_tasks_require_exact_claims_only_json(self):
        from stockcrewai.crews.analysis.crew import AnalysisCrew

        analysis_crew = AnalysisCrew()
        task_names = (
            "financial_quality_analysis_task",
            "risk_analysis_task",
        )

        for task_name in task_names:
            expected_output = analysis_crew.tasks_config[task_name]["expected_output"]
            self.assertIn('"claims"', expected_output)

        financial_expected = analysis_crew.tasks_config[
            "financial_quality_analysis_task"
        ]["expected_output"]
        for field in (
            "claim_id",
            "category",
            "statement",
            "evidence_ids",
            "calculation_ids",
            "confidence",
        ):
            self.assertIn(field, financial_expected)

    def test_task_prompts_enforce_category_and_provenance_contracts(self):
        from stockcrewai.crews.analysis.crew import AnalysisCrew

        analysis_crew = AnalysisCrew()
        contracts = {
            "financial_quality_analysis_task": {
                "categories": {"financial_quality", "financial_trend"},
                "required": ("Evidence ID", "Calculation ID"),
            },
            "risk_analysis_task": {
                "categories": {"risk"},
                "required": ("filing Evidence ID", "calculation_ids 必须是空列表"),
            },
        }
        category_tokens = {
            "financial_quality",
            "financial_trend",
            "risk",
            "current_valuation",
            "historical_valuation",
            "reverse_dcf",
        }

        for task_name, contract in contracts.items():
            with self.subTest(task=task_name):
                description = analysis_crew.tasks_config[task_name]["description"]
                categories = set(re.findall(r"[a-z]+(?:_[a-z]+)*", description))
                self.assertEqual(categories & category_tokens, contract["categories"])
                for required in contract["required"]:
                    self.assertIn(required, description)

    def test_financial_prompt_forbids_display_only_fields(self):
        from stockcrewai.crews.analysis.crew import AnalysisCrew

        description = AnalysisCrew().tasks_config[
            "financial_quality_analysis_task"
        ]["description"]
        self.assertIn("metric", description)
        self.assertIn("value", description)
        self.assertIn("禁止", description)

    def test_financial_guardrail_rejects_metric_and_value_fields(self):
        from stockcrewai.crews.analysis.crew import (
            validate_financial_analysis_output,
        )

        output = TaskOutput(
            description="financial analysis",
            agent="FinancialQualityAgent",
            raw=json.dumps(
                {
                    "claims": [
                        {
                            "claim_id": "claim_001",
                            "category": "financial_quality",
                            "metric": "operating_margin",
                            "value": "33.6%",
                            "statement": "营业利润率为 33.6%。",
                            "evidence_ids": ["ev_margin"],
                            "calculation_ids": ["calc_margin"],
                        }
                    ]
                }
            ),
        )

        passed, message = validate_financial_analysis_output(output)

        self.assertFalse(passed)
        self.assertIn("confidence", str(message))

    def test_financial_guardrail_accepts_exact_claim_schema(self):
        from stockcrewai.crews.analysis.crew import (
            validate_financial_analysis_output,
        )

        output = TaskOutput(
            description="financial analysis",
            agent="FinancialQualityAgent",
            raw=json.dumps(
                {
                    "claims": [
                        {
                            "claim_id": "claim_001",
                            "category": "financial_quality",
                            "statement": "营业利润率为 33.6%。",
                            "evidence_ids": ["ev_margin"],
                            "calculation_ids": ["calc_margin"],
                            "confidence": 0.9,
                        },
                        {
                            "claim_id": "claim_002",
                            "category": "financial_trend",
                            "statement": "营业收入同比增长 16.15%。",
                            "evidence_ids": ["ev_revenue"],
                            "calculation_ids": ["calc_growth"],
                            "confidence": 0.9,
                        },
                    ]
                }
            ),
        )

        passed, message = validate_financial_analysis_output(output)

        self.assertTrue(passed)
        self.assertEqual(message, output.raw)

    def test_all_analysis_tasks_attach_local_guardrails_and_two_retries(self):
        from stockcrewai.crews.analysis.crew import AnalysisCrew

        analysis_crew = AnalysisCrew()
        for task_name in (
            "financial_quality_analysis_task",
            "risk_analysis_task",
        ):
            with self.subTest(task=task_name):
                task = getattr(analysis_crew, task_name)()
                self.assertIsNotNone(task.guardrail)
                self.assertEqual(task.guardrail_max_retries, 2)

    def test_each_analysis_guardrail_rejects_missing_confidence_and_extra_fields(self):
        from stockcrewai.crews.analysis.crew import (
            validate_financial_analysis_output,
            validate_risk_analysis_output,
        )

        guardrails = {
            "financial": validate_financial_analysis_output,
            "risk": validate_risk_analysis_output,
        }
        categories = {
            "financial": "financial_quality",
            "risk": "risk",
        }
        for domain, guardrail in guardrails.items():
            with self.subTest(domain=domain, failure="missing confidence"):
                missing_confidence = self._claim(categories[domain])
                missing_confidence.pop("confidence")
                passed, _ = guardrail(
                    self._task_output({"claims": [missing_confidence]})
                )
                self.assertFalse(passed)

            with self.subTest(domain=domain, failure="extra field"):
                extra_field = self._claim(categories[domain])
                extra_field["unexpected"] = "must fail"
                passed, _ = guardrail(self._task_output({"claims": [extra_field]}))
                self.assertFalse(passed)

    def test_each_analysis_guardrail_enforces_domain_category_and_calculation_rule(self):
        from stockcrewai.crews.analysis.crew import (
            validate_financial_analysis_output,
            validate_risk_analysis_output,
        )

        cases = (
            (
                "financial",
                validate_financial_analysis_output,
                "risk",
                ["calc_any"],
            ),
            (
                "risk",
                validate_risk_analysis_output,
                "risk",
                ["calc_any"],
            ),
        )
        for domain, guardrail, category, calculation_ids in cases:
            with self.subTest(domain=domain):
                claim = self._claim(category, calculation_ids=calculation_ids)
                passed, _ = guardrail(self._task_output({"claims": [claim]}))
                self.assertFalse(passed)

    def test_analysis_guardrails_accept_valid_claims_without_runtime_id_whitelist(self):
        from stockcrewai.crews.analysis.crew import (
            validate_financial_analysis_output,
            validate_risk_analysis_output,
        )

        cases = (
            (
                validate_financial_analysis_output,
                [
                    self._claim("financial_quality", calculation_ids=["calc_unknown"]),
                    self._claim("financial_trend", calculation_ids=["calc_unknown"]),
                ],
            ),
            (validate_risk_analysis_output, [self._claim("risk", calculation_ids=[])]),
        )
        for guardrail, claims in cases:
            with self.subTest(category=claims[0]["category"]):
                passed, _ = guardrail(self._task_output({"claims": claims}))
                self.assertTrue(passed)

    def test_all_analysis_task_prompts_repeat_the_complete_six_field_contract(self):
        from stockcrewai.crews.analysis.crew import AnalysisCrew

        analysis_crew = AnalysisCrew()
        fields = (
            "claim_id",
            "category",
            "statement",
            "evidence_ids",
            "calculation_ids",
            "confidence",
        )
        for task_name in (
            "financial_quality_analysis_task",
            "risk_analysis_task",
        ):
            with self.subTest(task=task_name):
                description = analysis_crew.tasks_config[task_name]["description"]
                for field in fields:
                    self.assertIn(field, description)
                self.assertIn("禁止额外字段", description)
                self.assertIn("claims", description)

    def test_task_descriptions_use_only_their_designated_payload(self):
        from stockcrewai.crews.analysis.crew import AnalysisCrew

        analysis_crew = AnalysisCrew()
        payloads = {
            "financial_quality_analysis_task": "financial_analysis_input",
            "risk_analysis_task": "risk_analysis_input",
        }
        forbidden = ("validated_state", "risk_input", "valuation_result")

        for task_name, payload in payloads.items():
            with self.subTest(task=task_name):
                description = analysis_crew.tasks_config[task_name]["description"]
                self.assertEqual(
                    re.findall(r"\{[^{}]+\}", description), [f"{{{payload}}}"]
                )
                for token in forbidden:
                    self.assertNotIn(token, description)

    def test_agent_role_and_backstory_are_domain_scoped(self):
        from stockcrewai.crews.analysis.crew import AnalysisCrew

        analysis_crew = AnalysisCrew()
        payloads = {
            "financial_quality_agent": "financial_analysis_input",
            "risk_analysis_agent": "risk_analysis_input",
        }

        for agent_name, payload in payloads.items():
            with self.subTest(agent=agent_name):
                config = analysis_crew.agents_config[agent_name]
                expected_payload = {f"{{{payload}}}"}
                for field in ("role", "backstory"):
                    with self.subTest(field=field):
                        self.assertEqual(
                            set(re.findall(r"\{[^{}]+\}", config[field])),
                            expected_payload,
                        )
                        self.assertIn("前序任务输出", config[field])


if __name__ == "__main__":
    unittest.main()
