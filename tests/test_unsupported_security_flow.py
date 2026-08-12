from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

import stockcrewai.pipeline_support as pipeline_support
from stockcrewai.flow import ResearchFlow
from stockcrewai.models.request import CompanyIdentity
from stockcrewai.pipelines.evidence_pipeline import build_profile_policy_context
from stockcrewai.pipelines.profile_registry import classify_profiles
from stockcrewai.tools.calculator_tool import CalculationBatch
from stockcrewai.tools.edgar_tool import EdgarResult
from stockcrewai.tools.validation_tool import ValidationResult


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "profiles" / "unsupported_security"
FIXTURE_NAMES = ("etf", "mutual_fund", "closed_end_fund")


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("fixture_name", FIXTURE_NAMES)
def test_unsupported_security_fixtures_publish_profile_context_and_gate(
    fixture_name: str,
) -> None:
    fixture = _fixture(fixture_name)

    assert fixture["offline"] is True
    assert fixture["no_network"] is True
    identity = CompanyIdentity.model_validate(fixture["identity"])
    assert identity.model_dump(mode="json") == fixture["identity"]

    profile = classify_profiles(fixture["source_metadata"])
    assert profile.model_dump(mode="json") == fixture["expected_profile"]
    assert profile.security_profile.value == "unsupported_fund_security"
    assert profile.reporting_profile.value == "investment_company_reporting"
    assert profile.coverage_level.value == "unsupported_security"
    assert "unsupported_security" in profile.reason_codes

    context = build_profile_policy_context(
        source_metadata=fixture["source_metadata"],
    )
    assert context["profile"] == fixture["expected_profile"]
    assert context["policies"] == []
    assert context["gate"]["status"] == "unsupported"
    assert context["gate"]["coverage_level"] == "unsupported_security"
    assert "unsupported_security" in context["gate"]["reason_codes"]


def _unsupported_flow(
    fixture: dict[str, Any],
    *,
    progress_callback: Any = None,
) -> tuple[ResearchFlow, dict[str, Mock]]:
    context = build_profile_policy_context(
        source_metadata=fixture["source_metadata"],
    )
    context["policy_activation"] = "sec_metadata"
    identity = CompanyIdentity.model_validate(fixture["identity"])
    ordinary_tools = {
        name: Mock(name=name)
        for name in (
            "market_price_tool",
            "valuation_tool",
            "historical_valuation_tool",
            "reverse_dcf_tool",
        )
    }
    ordinary_tools["market_price_tool"].run.return_value = {
        "status": "ok",
        "ticker": fixture["identity"]["ticker"],
        "market_price": "100",
        "price_timestamp": "2026-08-11T00:00:00Z",
        "currency": "USD",
        "source_reference": "fixture:wp12-s06/market-price",
        "historical_prices": [],
    }
    ordinary_tools["valuation_tool"].run.return_value = {
        "status": "ok",
        "readiness": "ready",
        "calculations": [],
    }
    for name in ("historical_valuation_tool", "reverse_dcf_tool"):
        ordinary_tools[name].run.return_value = {
            "status": "unavailable",
            "calculations": [],
        }
    analysis_crew = Mock(name="analysis_crew")
    report_crew = Mock(name="report_crew")
    flow = ResearchFlow(
        **ordinary_tools,
        analysis_crew=analysis_crew,
        report_crew=report_crew,
        progress_callback=progress_callback,
    )
    flow.state.profile = context["profile"]
    flow.state.policy_context = context
    flow.state.edgar = identity.model_dump(mode="json")
    flow._pipeline_state = {
        "company_name": identity.company_name,
        "ticker": identity.ticker,
        "profile": context["profile"],
        "policy_context": context,
        "facts": {},
        "calculations": [],
        "filings": [],
        "validated_evidence_ids": [],
        "validated_calculation_ids": [],
        "validated_filing_ids": [],
    }
    flow._parser_failed = False
    return flow, {
        **ordinary_tools,
        "analysis_crew": analysis_crew,
        "report_crew": report_crew,
    }


def test_sic_and_fund_unsupported_profiles_share_scope_gate_output() -> None:
    events: list[Any] = []
    fund_flow, _ = _unsupported_flow(_fixture("etf"), progress_callback=events.append)

    bank_edgar = EdgarResult(status="ok", ticker="JPM", sic="6020")
    bank_context = build_profile_policy_context(
        source_metadata=pipeline_support.profile_metadata_from_edgar(bank_edgar),
        facts=bank_edgar.facts,
        calculations=[],
    )
    bank_flow = ResearchFlow(progress_callback=events.append)
    bank_flow.state.profile = bank_context["profile"]
    bank_flow.state.policy_context = bank_context
    bank_flow.state.edgar = {"sic": "6020"}
    bank_flow._pipeline_state = {
        "profile": bank_context["profile"],
        "policy_context": bank_context,
        "facts": {},
        "calculations": [],
    }

    fund_flow.prepare_valuation(fund_flow._pipeline_state)
    fund_event = events[-1]
    bank_flow.prepare_valuation(bank_flow._pipeline_state)
    bank_event = events[-1]

    assert fund_event.actor == bank_event.actor == "Python：SEC Scope/Profile Gate"
    assert fund_event.status == bank_event.status == "blocked"
    assert "unsupported scope" in fund_event.output_summary
    assert "unsupported scope" in bank_event.output_summary


@pytest.mark.parametrize("fixture_name", FIXTURE_NAMES)
def test_unsupported_flow_skips_ordinary_tools_and_finalizes_blocked(
    fixture_name: str,
) -> None:
    flow, tools = _unsupported_flow(_fixture(fixture_name))

    valuation = flow.prepare_valuation(flow._pipeline_state)
    expected_valuation = {
        "status": "not_applicable",
        "readiness": "not_applicable",
        "validation_status": "unvalidated",
        "reason_code": "unsupported_security",
        "calculations": [],
    }

    assert valuation == expected_valuation
    assert flow.state.valuation == expected_valuation
    assert flow.state.historical_valuation == expected_valuation
    assert flow.state.reverse_dcf == expected_valuation
    for name in (
        "market_price_tool",
        "valuation_tool",
        "historical_valuation_tool",
        "reverse_dcf_tool",
    ):
        tools[name].run.assert_not_called()

    assert flow.route_analysis(valuation) == "analysis_blocked"
    assert flow.state.policy_context["gate"]["status"] == "unsupported"
    assert flow.state.required_data == [
        "unsupported_security:security_profile=unsupported_fund_security",
        "unsupported_security:coverage_level=unsupported_security",
    ]
    result = flow.finalize_analysis_blocked()

    assert result["status"] == "blocked"
    assert result["report"] is None
    assert result["analysis"] == []
    assert result["edgar"]["company_name"] == flow._pipeline_state["company_name"]
    assert result["edgar"]["ticker"] == flow._pipeline_state["ticker"]
    assert result["profile"] == flow.state.policy_context["profile"]
    tools["analysis_crew"].kickoff.assert_not_called()
    tools["report_crew"].kickoff.assert_not_called()


def test_automatic_unsupported_metadata_uses_sec_metadata_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture("etf")
    identity = CompanyIdentity.model_validate(fixture["identity"])
    edgar_tool = Mock(name="edgar_tool")
    edgar_tool.run.return_value = EdgarResult(
        status="ok",
        company_name=identity.company_name,
        ticker=identity.ticker,
        cik=identity.cik,
        exchange=[identity.exchange],
        facts={},
        filings=[],
    )
    calculator_tool = Mock(name="calculator_tool")
    calculator_tool.run.return_value = CalculationBatch(
        status="ok",
        company_name=identity.company_name,
        ticker=identity.ticker,
        calculations=[],
    )
    validation_tool = Mock(name="validation_tool")
    validation_tool.run.return_value = ValidationResult(
        status="valid",
        validated=True,
        company_name=identity.company_name,
        ticker=identity.ticker,
    )
    ttm_builder_tool = Mock(name="ttm_builder_tool")
    ttm_builder_tool.run.return_value = {
        "status": "unavailable",
        "company_name": identity.company_name,
        "ticker": identity.ticker,
        "metrics": [],
        "warnings": [],
        "reason_code": "fixture_ttm_unavailable",
    }
    monkeypatch.setattr(
        pipeline_support,
        "profile_metadata_from_edgar",
        lambda _edgar_result: fixture["source_metadata"],
    )

    flow = ResearchFlow(
        edgar_tool=edgar_tool,
        calculator_tool=calculator_tool,
        validation_tool=validation_tool,
        ttm_builder_tool=ttm_builder_tool,
    )
    flow._parser_failed = False

    flow.prepare_evidence(
        {
            "company_name_guess": identity.company_name,
            "ticker_guess": identity.ticker,
        }
    )

    assert flow.state.policy_context["policy_activation"] == "sec_metadata"
    assert flow.state.policy_context["policies"] == []
    assert flow.state.policy_context["gate"]["status"] == "unsupported"
    assert flow.state.profile["coverage_level"] == "unsupported_security"
    assert flow.state.profile["security_profile"] == "unsupported_fund_security"
    assert flow.state.profile["reporting_profile"] == "investment_company_reporting"


def test_direct_unsupported_profile_refreshes_gate_before_route() -> None:
    fixture = _fixture("etf")
    flow, _ = _unsupported_flow(fixture)
    flow._profile_input = fixture["expected_profile"]
    flow.state.profile = {}
    flow.state.policy_context = {}
    flow._pipeline_state["policy_context"] = {}
    flow._validation_result = Mock(status="valid", validated=True)

    valuation = flow.prepare_valuation(flow._pipeline_state)

    assert flow.route_analysis(valuation) == "analysis_blocked"
    assert flow.state.policy_context["gate"]["status"] == "unsupported"
