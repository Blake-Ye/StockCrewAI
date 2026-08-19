from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest

from stockcrewai import pipeline_support
from stockcrewai.flow import ResearchFlow


def _ordinary_metadata(*, sic: str = "3571") -> dict[str, object]:
    return {
        "sic": sic,
        "filing_forms": ["10-K", "10-Q", "8-K"],
        "taxonomy": ["us-gaap"],
        "security_type": "common_stock",
        "security_class": "common_stock",
        "is_foreign_private_issuer": False,
        "is_investment_company": False,
        "has_revenue": True,
        "classification_evidence_ids": ["ev_company", "ev_filing"],
    }


def _etf_metadata() -> dict[str, object]:
    return {
        "sic": None,
        "filing_forms": ["N-1A"],
        "taxonomy": ["investment_company"],
        "security_type": "ETF",
        "security_class": "etf",
        "is_foreign_private_issuer": False,
        "is_investment_company": True,
        "has_revenue": None,
        "classification_evidence_ids": ["ev_etf_security"],
    }


def _ordinary_calculations() -> list[dict[str, object]]:
    return [
        {
            "calculation_id": "calc_revenue_growth",
            "formula_id": "revenue_growth",
            "input_evidence_ids": ["ev_revenue_current", "ev_revenue_prior"],
            "raw_result": "0.10",
            "validation_status": "valid",
        },
        {
            "calculation_id": "calc_operating_margin",
            "formula_id": "operating_margin",
            "input_evidence_ids": ["ev_operating_income", "ev_revenue"],
            "raw_result": "0.20",
            "validation_status": "valid",
        },
        {
            "calculation_id": "calc_pe_ratio",
            "formula_id": "pe_ratio",
            "input_evidence_ids": ["ev_market_price", "ev_eps"],
            "raw_result": "20",
            "validation_status": "valid",
        },
        {
            "calculation_id": "calc_fcf_yield",
            "formula_id": "fcf_yield",
            "input_evidence_ids": ["ev_free_cash_flow", "ev_market_cap"],
            "raw_result": "0.05",
            "validation_status": "valid",
        },
    ]


def test_standard_operating_company_reaches_ready_research_path() -> None:
    context = pipeline_support.build_profile_policy_context(
        source_metadata=_ordinary_metadata(),
        additional_evidence_ids=[
            "ev_revenue_current",
            "ev_revenue_prior",
            "ev_operating_income",
            "ev_revenue",
            "ev_market_price",
            "ev_eps",
            "ev_free_cash_flow",
            "ev_market_cap",
        ],
        calculations=_ordinary_calculations(),
    )

    assert context["profile"]["issuer_profile"] == "standard_operating"
    assert context["policies"]
    assert context["policy_decisions"]
    assert context["gate"]["status"] == "ready"

    flow = ResearchFlow()
    flow.state.profile = context["profile"]
    flow.state.policy_context = context
    flow._pipeline_state = {
        "profile": context["profile"],
        "policy_context": context,
        "facts": {},
        "calculations": [],
    }

    assert flow.route_analysis() == "analysis_ready"


@pytest.mark.parametrize(
    ("sic", "issuer_profile"),
    [
        ("6020", "bank"),
        ("6300", "insurance"),
        ("6798", "reit"),
        ("4911", "utility"),
        ("1000", "commodity_producer"),
    ],
)
def test_special_profile_policy_context_is_empty(
    sic: str, issuer_profile: str
) -> None:
    context = pipeline_support.build_profile_policy_context(
        source_metadata=_ordinary_metadata(sic=sic)
    )

    assert context["profile"]["issuer_profile"] == issuer_profile
    assert context["policies"] == []
    assert context["policy_decisions"] == []
    assert context["gate"]["status"] == "unsupported"
    assert context["gate"]["reason_codes"] == ["unsupported_category_sic"]


def _blocked_flow(
    context: dict[str, Any],
    *,
    sic: str | None,
) -> tuple[ResearchFlow, dict[str, Mock]]:
    tools = {
        name: Mock(name=name)
        for name in (
            "market_price_tool",
            "valuation_tool",
            "historical_valuation_tool",
            "reverse_dcf_tool",
            "analysis_crew",
            "report_crew",
        )
    }
    flow = ResearchFlow(**tools)
    flow.state.profile = context["profile"]
    flow.state.policy_context = context
    flow.state.edgar = {"sic": sic, "ticker": "TEST"}
    flow._pipeline_state = {
        "company_name": "Unsupported Scope Example",
        "ticker": "TEST",
        "profile": context["profile"],
        "policy_context": context,
        "facts": {},
        "calculations": [],
    }
    flow._parser_failed = False
    return flow, tools


@pytest.mark.parametrize(
    ("profile_kind", "sic", "reason_code"),
    [
        ("bank", "6020", "unsupported_category_sic"),
        ("insurance", "6300", "unsupported_category_sic"),
        ("reit", "6798", "unsupported_category_sic"),
        ("utility", "4911", "unsupported_category_sic"),
        ("commodity", "1000", "unsupported_category_sic"),
        ("etf", None, "unsupported_security"),
    ],
)
def test_special_scope_is_blocked_before_market_valuation_analysis_or_report(
    profile_kind: str, sic: str | None, reason_code: str
) -> None:
    metadata = _etf_metadata() if profile_kind == "etf" else _ordinary_metadata(sic=sic or "")
    context = pipeline_support.build_profile_policy_context(source_metadata=metadata)
    flow, tools = _blocked_flow(context, sic=sic)

    valuation = flow.prepare_valuation(flow._pipeline_state)

    assert valuation["reason_code"] == reason_code
    assert context["gate"]["reason_codes"] == [reason_code]
    for name in (
        "market_price_tool",
        "valuation_tool",
        "historical_valuation_tool",
        "reverse_dcf_tool",
    ):
        tools[name].run.assert_not_called()

    assert flow.route_analysis(valuation) == "analysis_blocked"
    result = flow.finalize_analysis_blocked()

    assert result["status"] == "blocked"
    assert result["report"] is None
    tools["analysis_crew"].kickoff.assert_not_called()
    tools["report_crew"].kickoff.assert_not_called()
