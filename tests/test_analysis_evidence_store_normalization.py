from __future__ import annotations

from stockcrewai.flow import ResearchFlow
from stockcrewai.tools.calculator_tool import CalculationResult


def test_legacy_calculations_are_normalized_only_for_analysis_store() -> None:
    flow = ResearchFlow()
    flow._pipeline_state = {
        "facts": {
            "revenue_current": {
                "evidence_id": "ev_revenue_current",
                "source_reference": "fixture:revenue-current",
                "period": "FY2025",
                "period_start": "2025-01-01",
                "period_end": "2025-12-31",
                "filed_at": "2026-02-01",
                "as_of": "2026-02-01T00:00:00Z",
                "validation_status": "valid",
            },
            "revenue_prior": {
                "evidence_id": "ev_revenue_prior",
                "source_reference": "fixture:revenue-prior",
                "period": "FY2024",
                "period_start": "2024-01-01",
                "period_end": "2024-12-31",
                "filed_at": "2025-02-01",
                "as_of": "2025-02-01T00:00:00Z",
                "validation_status": "valid",
            },
        },
        "calculations": [
            CalculationResult(
                calculation_id="calc_revenue_growth",
                formula_id="revenue_growth",
                input_evidence_ids=["ev_revenue_current", "ev_revenue_prior"],
                raw_result="0.1",
                normalized_result="1.0E-1",
                display_result="10.00%",
                unit="ratio",
                status="available",
                validation_status="valid",
            ),
            CalculationResult(
                calculation_id="calc_missing_time",
                formula_id="operating_margin",
                input_evidence_ids=["ev_unknown"],
                raw_result="0.2",
                normalized_result="2.0E-1",
                display_result="20.00%",
                unit="ratio",
                status="available",
                validation_status="valid",
            ),
        ],
        "validated_evidence_ids": [
            "ev_revenue_current",
            "ev_revenue_prior",
        ],
        "validated_calculation_ids": [
            "calc_revenue_growth",
            "calc_missing_time",
        ],
    }

    store = flow._build_analysis_evidence_store()

    complete = store.get_validated_calculations(["calc_revenue_growth"])
    assert complete["status"] == "ok"
    record = complete["records"][0]
    assert record["source_reference"] == "derived:revenue_growth"
    assert record["period"] == "FY2025"
    assert record["period_start"] == "2025-01-01"
    assert record["period_end"] == "2025-12-31"
    assert record["filed_at"] == "2026-02-01"
    assert record["as_of"] == "2026-02-01T00:00:00Z"
    assert "calc_missing_time" not in store._allowlist["calculations"]
