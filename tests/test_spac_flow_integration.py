from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

from stockcrewai.flow import ResearchFlow
from stockcrewai.models.evidence import EvidenceRecord, MarketPriceRecord
from stockcrewai.pipelines.evidence_pipeline import build_profile_policy_context


FIXTURE = Path(__file__).parent / "fixtures" / "profiles" / "spac" / "complete_pre_merger.json"


def _fixture() -> dict[str, Any]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    profile = dict(payload["profile_input"])
    profile.update(
        {
            "profile_version": "spac-profile:v1",
            "policy_version": "metric-policy:spac:v1",
            "issuer_profile": "standard_operating",
            "reporting_profile": "domestic_us_gaap",
            "coverage_level": "full",
        }
    )
    payload["profile_input"] = profile
    return payload


def _typed_records(
    fixture: dict[str, Any],
) -> tuple[tuple[EvidenceRecord, ...], tuple[MarketPriceRecord, ...]]:
    return (
        tuple(EvidenceRecord.model_validate(item) for item in fixture["evidence_records"]),
        tuple(
            MarketPriceRecord.model_validate(item)
            for item in fixture["market_price_records"]
        ),
    )


def _flow(monkeypatch: Any) -> tuple[ResearchFlow, dict[str, Any], list[Any], dict[str, Mock]]:
    monkeypatch.setenv("STOCKCREWAI_RUNTIME_METRICS", "0")
    fixture = _fixture()
    evidence_records, market_price_records = _typed_records(fixture)
    context = build_profile_policy_context(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )
    events: list[Any] = []
    ordinary_tools = {
        name: Mock(name=name)
        for name in ("market_price_tool", "valuation_tool", "historical_valuation_tool", "reverse_dcf_tool")
    }
    analysis_crew = Mock(name="analysis_crew")
    flow = ResearchFlow(
        profile_input=fixture["profile_input"],
        profile_evidence_records=evidence_records,
        profile_market_price_records=market_price_records,
        market_price_tool=ordinary_tools["market_price_tool"],
        valuation_tool=ordinary_tools["valuation_tool"],
        historical_valuation_tool=ordinary_tools["historical_valuation_tool"],
        reverse_dcf_tool=ordinary_tools["reverse_dcf_tool"],
        analysis_crew=analysis_crew,
        progress_callback=events.append,
    )
    flow.state.profile = context["profile"]
    flow.state.policy_context = context
    flow._pipeline_state = {
        "company_name": "Synthetic SPAC",
        "ticker": "SPAC",
        "profile": context["profile"],
        "policy_context": context,
        "facts": {},
        "calculations": [],
        "validated_evidence_ids": [],
        "validated_calculation_ids": [],
        "validated_filing_ids": [],
    }
    flow._parser_failed = False
    return flow, context, events, {**ordinary_tools, "analysis_crew": analysis_crew}


def test_spac_flow_skips_all_ordinary_valuation_tools_and_routes_analysis_ready(
    monkeypatch: Any,
) -> None:
    flow, context, events, tools = _flow(monkeypatch)

    valuation = flow.prepare_valuation(flow._pipeline_state)

    expected = {
        "status": "not_applicable",
        "readiness": "not_applicable",
        "validation_status": "unvalidated",
        "reason_code": "spac_security_structure_not_applicable",
        "calculations": [],
    }
    assert valuation == expected
    assert flow.state.valuation == expected
    assert flow.state.historical_valuation == expected
    assert flow.state.reverse_dcf == expected
    for tool in tools.values():
        if tool is not tools["analysis_crew"]:
            tool.run.assert_not_called()
    assert context["gate"]["status"] == "evidence_only"
    assert flow.route_analysis(valuation) == "analysis_ready"
    assert flow.state.required_data == []
    assert flow.state.status == "running"
    trace = " ".join(
        f"{event.title} {event.actor} {event.output_summary} {event.reason}"
        for event in events
    )
    assert "SPAC evidence-only" in trace


def test_spac_flow_uses_python_evidence_only_analysis_without_analysis_crew(
    monkeypatch: Any,
) -> None:
    flow, _, events, tools = _flow(monkeypatch)
    valuation = flow.prepare_valuation(flow._pipeline_state)
    assert flow.route_analysis(valuation) == "analysis_ready"

    analysis_result = flow.run_analysis()

    assert tools["analysis_crew"].kickoff.call_count == 0
    assert flow.state.analysis == []
    assert flow.state.analysis_attempts == 0
    assert flow._analysis_inputs["mode"] == "spac_evidence_only"
    assert flow._valuation_analysis_input["mode"] == "spac_evidence_only"
    assert isinstance(analysis_result, SimpleNamespace)
    assert "SPAC evidence-only" in " ".join(
        f"{event.title} {event.actor} {event.output_summary} {event.reason}"
        for event in events
    )

    assert flow.route_claims(analysis_result) == "claims_ready"
    assert flow.state.analysis == []
    assert flow.state.required_data == []
    assert flow.state.stage == "report"


def test_spac_claim_gate_rejects_invalid_typed_envelope(monkeypatch: Any) -> None:
    flow, _, _, _ = _flow(monkeypatch)
    flow.state.policy_context["profile_envelope"] = {
        "status": "unavailable",
        "reason_code": "typed_profile_envelope_required",
    }
    flow.state.valuation = {
        "status": "not_applicable",
        "readiness": "not_applicable",
        "validation_status": "unvalidated",
        "reason_code": "spac_security_structure_not_applicable",
        "calculations": [],
    }
    flow.state.historical_valuation = deepcopy(flow.state.valuation)
    flow.state.reverse_dcf = deepcopy(flow.state.valuation)

    assert flow.route_claims(SimpleNamespace()) == "claims_blocked"
    assert flow.state.required_data == ["spac_typed_envelope_invalid"]
    assert flow.state.analysis == []
