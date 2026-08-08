import unittest

from stockcrewai.tools.calculator_tool import CalculationResult
from stockcrewai.tools.edgar_tool import EdgarFact
from stockcrewai.tools.validation_tool import ValidationResult, sync_validation_status


class ValidationStatusSyncTests(unittest.TestCase):
    def test_valid_result_marks_only_whitelisted_serialized_records(self):
        facts = {
            "revenue": EdgarFact(
                metric_id="revenue",
                evidence_id="ev_revenue",
                value="100",
                source_reference="sec:test-revenue",
            ),
            "unvalidated": EdgarFact(
                metric_id="unvalidated",
                evidence_id="ev_unvalidated",
                value="999",
                source_reference="sec:test-unvalidated",
            ),
        }
        calculations = [
            CalculationResult(
                calculation_id="calc_valid",
                formula_id="free_cash_flow",
                raw_result="20",
                status="available",
            ),
            CalculationResult(
                calculation_id="calc_unvalidated",
                formula_id="net_margin",
                raw_result="0.20",
                status="available",
            ),
        ]
        validation_result = ValidationResult(
            status="valid",
            validated=True,
            validated_evidence_ids=["ev_revenue"],
            validated_calculation_ids=["calc_valid"],
        )

        synced = sync_validation_status(facts, calculations, validation_result)

        self.assertEqual(synced["facts"]["revenue"]["validation_status"], "valid")
        self.assertEqual(
            synced["facts"]["unvalidated"]["validation_status"], "unvalidated"
        )
        self.assertEqual(synced["calculations"][0]["validation_status"], "valid")
        self.assertEqual(
            synced["calculations"][1]["validation_status"], "unvalidated"
        )
        self.assertEqual(facts["revenue"].validation_status, "unvalidated")
        self.assertEqual(calculations[0].validation_status, "unvalidated")

    def test_non_valid_result_does_not_mark_records_valid(self):
        for status in ("invalid", "unavailable"):
            with self.subTest(status=status):
                facts = {
                    "revenue": EdgarFact(
                        metric_id="revenue",
                        evidence_id="ev_revenue",
                        value="100",
                        source_reference="sec:test-revenue",
                    )
                }
                calculations = [
                    CalculationResult(
                        calculation_id="calc_revenue",
                        formula_id="free_cash_flow",
                        raw_result="20",
                        status="available",
                    )
                ]
                validation_result = ValidationResult(
                    status=status,
                    validated=False,
                    validated_evidence_ids=["ev_revenue"],
                    validated_calculation_ids=["calc_revenue"],
                )

                synced = sync_validation_status(facts, calculations, validation_result)

                self.assertEqual(
                    synced["facts"]["revenue"]["validation_status"], "unvalidated"
                )
                self.assertEqual(
                    synced["calculations"][0]["validation_status"], "unvalidated"
                )

    def test_existing_invalid_status_is_preserved_in_serialized_copy(self):
        facts = {
            "revenue": EdgarFact(
                metric_id="revenue",
                evidence_id="ev_revenue",
                value="100",
                source_reference="sec:test-revenue",
                validation_status="invalid",
            )
        }
        calculations = [
            CalculationResult(
                calculation_id="calc_revenue",
                formula_id="free_cash_flow",
                raw_result="20",
                status="available",
                validation_status="invalid",
            )
        ]
        validation_result = ValidationResult(
            status="unavailable",
            validated=False,
            validated_evidence_ids=["ev_revenue"],
            validated_calculation_ids=["calc_revenue"],
        )

        synced = sync_validation_status(facts, calculations, validation_result)

        self.assertEqual(synced["facts"]["revenue"]["validation_status"], "invalid")
        self.assertEqual(synced["calculations"][0]["validation_status"], "invalid")


if __name__ == "__main__":
    unittest.main()
