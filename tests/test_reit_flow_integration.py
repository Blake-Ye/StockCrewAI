from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from stockcrewai.flow import ResearchFlow
from stockcrewai.models.evidence import EvidenceRecord, MarketPriceRecord
from stockcrewai.tools.valuation_tool import _market_price_evidence_id


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "profiles" / "reit"


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _typed_records(
    fixture: dict[str, Any],
) -> tuple[tuple[EvidenceRecord, ...], tuple[MarketPriceRecord, ...]]:
    evidence_records = tuple(
        EvidenceRecord.model_validate(item) for item in fixture["evidence_records"]
    )
    market_price_records = tuple(
        MarketPriceRecord.model_validate(item)
        for item in fixture["market_price_records"]
    )
    return evidence_records, market_price_records


class _FakeEdgarResult:
    company_name = "Synthetic REIT"
    ticker = "SREIT"
    facts: dict[str, Any] = {}
    filings: list[Any] = []
    ttm_inputs: dict[str, Any] = {}
    historical_financial_snapshots: list[Any] = []

    def __init__(self, issuer_profile: str = "reit") -> None:
        self.issuer_profile = issuer_profile

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        del mode
        return {
            "status": "ok",
            "company_name": self.company_name,
            "ticker": self.ticker,
            "sec_registrant_profile": self.issuer_profile,
            "facts": self.facts,
            "filings": self.filings,
            "ttm_inputs": self.ttm_inputs,
            "historical_financial_snapshots": self.historical_financial_snapshots,
        }


class _FakeEdgarTool:
    def __init__(self, issuer_profile: str = "reit") -> None:
        self.result = _FakeEdgarResult(issuer_profile)

    def run(self, **kwargs: Any) -> _FakeEdgarResult:
        del kwargs
        return self.result


class _FakeCalculationResult:
    calculations: list[Any] = []

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        del mode
        return {"status": "ok", "calculations": []}


class _FakeCalculatorTool:
    def run(self, **kwargs: Any) -> _FakeCalculationResult:
        del kwargs
        return _FakeCalculationResult()


class _FakeValidationResult:
    status = "valid"
    validated = True
    validated_evidence_ids: list[str] = []
    validated_calculation_ids: list[str] = []
    validated_filing_ids: list[str] = []

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        del mode
        return {
            "status": self.status,
            "validated": self.validated,
            "validated_evidence_ids": self.validated_evidence_ids,
            "validated_calculation_ids": self.validated_calculation_ids,
            "validated_filing_ids": self.validated_filing_ids,
        }


class _FakeValidationTool:
    def run(self, **kwargs: Any) -> _FakeValidationResult:
        del kwargs
        return _FakeValidationResult()


class _FakeTtmTool:
    def run(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {
            "status": "unavailable",
            "metrics": [],
            "warnings": [],
            "reason_code": "fixture_ttm_unavailable",
        }


class _FakeValuationTool:
    def run(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {
            "status": "not_ready",
            "readiness": "not_ready",
            "calculations": [],
        }


class _FakeHistoricalValuationTool:
    def run(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {"status": "unavailable", "input_evidence_ids": []}


class _FakeReverseDcfTool:
    def run(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {"status": "unavailable", "input_evidence_ids": []}


class _FakeMarketPriceTool:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def run(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return dict(self.payload)


class _FakeReportCrew:
    def kickoff(self, *, inputs: dict[str, Any]) -> SimpleNamespace:
        del inputs
        return SimpleNamespace(raw="{}")


def _flow(
    *,
    profile: Any = None,
    evidence_records: Any = (),
    market_price_records: Any = (),
    market_payload: dict[str, Any] | None = None,
    issuer_profile: str = "reit",
) -> ResearchFlow:
    return ResearchFlow(
        edgar_tool=_FakeEdgarTool(issuer_profile),
        calculator_tool=_FakeCalculatorTool(),
        validation_tool=_FakeValidationTool(),
        valuation_tool=_FakeValuationTool(),
        market_price_tool=_FakeMarketPriceTool(
            market_payload
            or {
                "status": "ok",
                "ticker": "SREIT",
                "market_price": "30",
                "price_timestamp": "2026-02-16T00:00:00Z",
                "currency": "USD",
                "source_reference": "fixture:market/reit/flow",
            }
        ),
        historical_valuation_tool=_FakeHistoricalValuationTool(),
        reverse_dcf_tool=_FakeReverseDcfTool(),
        ttm_builder_tool=_FakeTtmTool(),
        reit_profile_input=profile,
        reit_evidence_records=evidence_records,
        reit_market_price_records=market_price_records,
    )


def _run_to_evidence(
    flow: ResearchFlow,
) -> dict[str, Any]:
    return flow.prepare_evidence(
        {"company_name_guess": "Synthetic REIT", "ticker_guess": "SREIT"}
    )


def _decision_by_metric(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["metric_id"]: item for item in context["policy_decisions"]}


def test_reit_typed_dependencies_stay_private_and_plain_flow_still_constructs() -> None:
    fixture = _load_fixture("complete")
    evidence_records, market_price_records = _typed_records(fixture)
    flow = _flow(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )

    payload = flow.state.model_dump(mode="json")
    json.dumps(payload, ensure_ascii=False, allow_nan=False)

    assert isinstance(flow, ResearchFlow)
    assert flow._reit_profile_input == fixture["profile_input"]
    assert flow._reit_evidence_records == evidence_records
    assert flow._reit_market_price_records == market_price_records
    assert not {"reit_profile_input", "reit_evidence_records", "reit_market_price_records"} & payload.keys()
    assert ResearchFlow().state.model_dump(mode="json")["stage"] == "request"


def test_complete_reit_flow_uses_typed_policy_on_evidence_and_valuation_events() -> None:
    fixture = _load_fixture("complete")
    evidence_records, market_price_records = _typed_records(fixture)
    flow = _flow(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )

    evidence_state = _run_to_evidence(flow)
    evidence_context = flow.state.policy_context
    evidence_decisions = _decision_by_metric(evidence_context)
    assert evidence_context["profile"]["issuer_profile"] == "reit"
    assert evidence_context["policy_version"] == "metric-policy:v2"
    assert evidence_decisions["ffo_total"]["status"] == "available"
    assert evidence_decisions["ffo_per_share"]["status"] == "available"
    assert evidence_context["gate"]["status"] == "ready"
    assert set(evidence_decisions["ffo_total"]["evidence_ids"]) >= {
        "ev_reit_complete_net_income",
        "ev_reit_complete_ffo_total",
    }

    valuation_state = flow.prepare_valuation(evidence_state)
    refreshed_decisions = _decision_by_metric(flow.state.policy_context)
    assert refreshed_decisions["price_to_ffo"]["status"] == "available"
    assert "price_reit_complete_20260216" in refreshed_decisions["price_to_ffo"]["evidence_ids"]
    assert flow.state.policy_context["policy_activation"] == "explicit_profile"
    assert valuation_state["readiness"] == "not_ready"
    assert flow.route_analysis(valuation_state) == "analysis_ready"
    assert flow.state.required_data == []


def test_missing_affo_is_non_blocking_in_real_flow_policy_context() -> None:
    fixture = _load_fixture("missing_affo")
    evidence_records, market_price_records = _typed_records(fixture)
    flow = _flow(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )

    evidence_state = _run_to_evidence(flow)
    decisions = _decision_by_metric(flow.state.policy_context)
    assert decisions["ffo_total"]["status"] == "available"
    assert decisions["ffo_per_share"]["status"] == "available"
    assert decisions["affo"]["status"] == "unavailable"
    assert decisions["affo"]["reason_code"] == "affo_reconciliation_not_disclosed"
    assert decisions["affo"]["blocking"] is False
    assert all(
        item["formula_id"] != "company-disclosed-affo-reconciliation-v1"
        for item in flow.state.policy_context["calculation_records"]
    )

    valuation_state = flow.prepare_valuation(evidence_state)
    assert flow.route_analysis(valuation_state) == "analysis_ready"


def test_missing_typed_evidence_does_not_become_edgar_fact_or_ordinary_policy() -> None:
    fixture = _load_fixture("complete")
    incomplete_edgar_fact = {
        "evidence_id": "ev_reit_complete_net_income",
        "value": "100",
        "unit": "USD",
        "period_end": "2025-12-31",
        "source_reference": "fixture:edgar-fact-like",
        "validation_status": "valid",
    }
    flow = _flow(
        profile=fixture["profile_input"],
        evidence_records=(incomplete_edgar_fact,),
        market_price_records=(),
    )

    evidence_state = _run_to_evidence(flow)
    decisions = _decision_by_metric(flow.state.policy_context)
    assert flow._reit_evidence_records == ()
    assert decisions["ffo_total"]["status"] == "unavailable"
    assert decisions["ffo_total"]["blocking"] is True
    assert decisions["ffo_per_share"]["status"] == "unavailable"
    assert decisions["ffo_per_share"]["blocking"] is True
    assert flow.state.policy_context["policy_version"] == "metric-policy:v2"
    assert {item["metric_id"] for item in flow.state.policy_context["policies"]} == {
        "ffo_total",
        "ffo_per_share",
        "affo",
        "same_store_noi",
        "occupancy",
        "net_debt_to_ebitda",
        "dividend_coverage",
        "price_to_ffo",
        "pe",
        "fcf_yield",
    }
    assert flow.route_analysis(flow.prepare_valuation(evidence_state)) == "analysis_blocked"
    assert any(item.startswith("ffo_total:") for item in flow.state.required_data)


def test_reit_metadata_without_envelope_stays_reit_and_fails_closed() -> None:
    fixture = _load_fixture("complete")
    evidence_records, market_price_records = _typed_records(fixture)
    flow = _flow(
        profile=None,
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )

    _run_to_evidence(flow)
    decisions = _decision_by_metric(flow.state.policy_context)
    assert flow.state.policy_context["profile"]["issuer_profile"] == "reit"
    assert flow.state.policy_context["policy_version"] == "metric-policy:v2"
    assert decisions["ffo_total"]["status"] == "unavailable"
    assert decisions["ffo_total"]["blocking"] is True
    assert decisions["ffo_per_share"]["status"] == "unavailable"
    assert decisions["ffo_per_share"]["blocking"] is True


def test_valid_market_payload_without_explicit_record_is_normalized_for_reit_only() -> None:
    fixture = _load_fixture("complete")
    evidence_records, _ = _typed_records(fixture)
    payload = {
        "status": "ok",
        "ticker": "SREIT",
        "market_price": "30",
        "price_timestamp": "2026-02-16T00:00:00Z",
        "currency": "USD",
        "source_reference": "fixture:market/reit/fallback",
    }
    flow = _flow(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=(),
        market_payload=payload,
    )

    evidence_state = _run_to_evidence(flow)
    flow.prepare_valuation(evidence_state)
    expected_id = _market_price_evidence_id(
        "SREIT", "30", payload["price_timestamp"], "USD", payload["source_reference"]
    )
    assert expected_id is not None
    assert len(flow._reit_market_price_records) == 1
    assert flow._reit_market_price_records[0].evidence_id == expected_id
    decision = _decision_by_metric(flow.state.policy_context)["price_to_ffo"]
    assert decision["status"] == "available"
    assert expected_id in decision["evidence_ids"]


def test_incomplete_market_payload_cannot_create_reit_market_record() -> None:
    fixture = _load_fixture("complete")
    evidence_records, _ = _typed_records(fixture)
    flow = _flow(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=(),
        market_payload={
            "status": "ok",
            "ticker": "SREIT",
            "market_price": "30",
            "currency": "USD",
            "source_reference": "fixture:market/reit/missing-timestamp",
        },
    )

    evidence_state = _run_to_evidence(flow)
    flow.prepare_valuation(evidence_state)
    decision = _decision_by_metric(flow.state.policy_context)["price_to_ffo"]
    assert flow._reit_market_price_records == ()
    assert decision["status"] == "unavailable"
    assert decision["reason_code"] == "market_price_missing"


def test_error_market_payload_cannot_create_reit_market_record() -> None:
    fixture = _load_fixture("complete")
    evidence_records, _ = _typed_records(fixture)
    flow = _flow(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=(),
        market_payload={
            "status": "error",
            "ticker": "SREIT",
            "market_price": "30",
            "price_timestamp": "2026-02-16T00:00:00Z",
            "currency": "USD",
            "source_reference": "fixture:market/reit/error",
        },
    )

    evidence_state = _run_to_evidence(flow)
    flow.prepare_valuation(evidence_state)
    decision = _decision_by_metric(flow.state.policy_context)["price_to_ffo"]

    assert flow._reit_market_price_records == ()
    assert decision["status"] == "unavailable"
    assert decision["reason_code"] == "market_price_missing"


def test_generate_report_indexes_only_validated_typed_reit_sources() -> None:
    fixture = _load_fixture("complete")
    evidence_records, market_price_records = _typed_records(fixture)
    flow = _flow(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )
    _run_to_evidence(flow)
    flow.state.analysis = []
    flow._report_crew = _FakeReportCrew()
    captured: dict[str, Any] = {}

    def capture_context(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"quant": {}}

    with (
        patch(
            "stockcrewai.flow.pipeline_support._deterministic_verdict",
            return_value={"status": "ready"},
        ),
        patch("stockcrewai.flow.build_report_context", side_effect=capture_context),
        patch("stockcrewai.flow.build_narrative_context", return_value={}),
        patch("stockcrewai.flow.parse_report_draft", return_value=object()),
        patch("stockcrewai.flow.render_validated_report", return_value="# report"),
        patch("stockcrewai.flow.validate_rendered_report", return_value=(True, "")),
    ):
        flow.generate_report()

    source_metadata = captured["source_metadata"]
    assert source_metadata["facts"]["ev_reit_complete_ffo_total"] == (
        evidence_records[4].model_dump(mode="json")
    )
    assert source_metadata["market_price"] == market_price_records[0].model_dump(
        mode="json"
    )
    assert all(
        record["validation_status"] == "valid"
        for record in source_metadata["facts"].values()
        if isinstance(record, dict) and record.get("evidence_id", "").startswith("ev_reit_")
    )


def test_ordinary_flow_keeps_v1_policy_and_legacy_route() -> None:
    flow = _flow(
        profile={
            "issuer_profile": "standard_operating",
            "security_profile": "common_stock",
            "reporting_profile": "domestic_us_gaap",
            "coverage_level": "full",
            "registry_version": "profile-registry:test-input",
        },
        issuer_profile="standard_operating",
    )

    evidence_state = _run_to_evidence(flow)
    assert flow.state.policy_context["policy_version"] == "metric-policy:v1"
    assert {item["metric_id"] for item in flow.state.policy_context["policies"]} == {
        "revenue_growth",
        "operating_margin",
        "pe_ratio",
        "fcf_yield",
    }
    valuation_state = flow.prepare_valuation(evidence_state)
    assert flow.route_analysis(valuation_state) == "analysis_blocked"
    assert "price_to_ffo" not in {
        item["metric_id"] for item in flow.state.policy_context["policy_decisions"]
    }
