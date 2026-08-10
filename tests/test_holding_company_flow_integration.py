from __future__ import annotations

from stockcrewai.flow import _allow_empty_foreign_valuation_claims


def test_ready_holding_nav_allows_empty_valuation_claims() -> None:
    valuation = {
        "status": "not_applicable",
        "readiness": "not_applicable",
        "validation_status": "unvalidated",
        "reason_code": "holding_company_nav_primary_valuation",
    }
    policy_context = {
        "profile": {
            "issuer_profile": "holding_company",
            "reporting_profile": "domestic_us_gaap",
        },
        "policy_decisions": [
            {"metric_id": "holding_company_nav", "status": "available"}
        ],
        "gate": {"status": "ready"},
    }

    assert _allow_empty_foreign_valuation_claims(
        policy_context,
        valuation,
        valuation,
        valuation,
    )
