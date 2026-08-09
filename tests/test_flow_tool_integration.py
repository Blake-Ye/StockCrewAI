from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from crewai.tools import BaseTool

from stockcrewai.crews.analysis.crew import AnalysisCrew
from stockcrewai.flow import ResearchFlow
from stockcrewai.reporting.context import build_report_context
from stockcrewai.services.evidence_store import EvidenceStore


def _ready_flow() -> ResearchFlow:
    flow = ResearchFlow()
    flow._pipeline_state = {
        "company_name": "Acme Inc.",
        "ticker": "ACME",
        "facts": {
            "revenue": {
                "evidence_id": "ev_revenue",
                "metric_id": "revenue",
                "period": "FY2025",
                "period_end": "2025-12-31",
                "source_reference": "fixture:revenue",
                "value": "100",
                "validation_status": "valid",
            }
        },
        "calculations": [
            {
                "calculation_id": "calc_margin",
                "formula_id": "operating_margin",
                "input_evidence_ids": ["ev_revenue"],
                "raw_result": "0.25",
                "as_of": "2026-08-10T00:00:00Z",
                "source_reference": "fixture:margin",
                "validation_status": "valid",
            }
        ],
        "filings": [
            {
                "evidence_id": "ev_filing",
                "form": "10-K",
                "filed_at": "2026-02-01",
                "source_reference": "fixture:filing",
                "text": "供应链风险。",
                "validation_status": "valid",
            }
        ],
        "validated_evidence_ids": ["ev_revenue"],
        "validated_calculation_ids": ["calc_margin"],
        "validated_filing_ids": ["ev_filing"],
    }
    flow.state.validation = {
        "status": "valid",
        "validated": True,
        "validated_evidence_ids": ["ev_revenue"],
        "validated_calculation_ids": ["calc_margin"],
        "validated_filing_ids": ["ev_filing"],
    }
    flow.state.valuation = {
        "readiness": "ready",
        "validation_status": "valid",
        "calculations": [
            {
                "calculation_id": "calc_pe",
                "formula_id": "pe_ratio",
                "input_evidence_ids": ["ev_revenue"],
                "status": "available",
                "validation_status": "valid",
            }
        ],
    }
    flow._trusted_valuation_evidence_ids = {"ev_revenue"}
    flow._risk_input = {
        "status": "available",
        "filings": flow._pipeline_state["filings"],
        "validated_filing_ids": ["ev_filing"],
    }
    return flow


class _OfflineAnalysisCrew:
    def kickoff(self, *, inputs: dict[str, object]) -> SimpleNamespace:
        return SimpleNamespace(
            tasks_output=[
                SimpleNamespace(
                    raw=json.dumps(
                        {
                            "claims": [
                                {
                                    "claim_id": "claim_quality",
                                    "category": "financial_quality",
                                    "statement": "财务质量可验证。",
                                    "evidence_ids": ["ev_revenue"],
                                    "calculation_ids": ["calc_margin"],
                                    "confidence": 0.9,
                                },
                                {
                                    "claim_id": "claim_trend",
                                    "category": "financial_trend",
                                    "statement": "财务趋势可验证。",
                                    "evidence_ids": ["ev_revenue"],
                                    "calculation_ids": ["calc_margin"],
                                    "confidence": 0.9,
                                },
                            ]
                        },
                        ensure_ascii=False,
                    )
                ),
                SimpleNamespace(
                    raw=json.dumps(
                        {
                            "claims": [
                                {
                                    "claim_id": "claim_risk",
                                    "category": "risk",
                                    "statement": "申报文本包含风险。",
                                    "evidence_ids": ["ev_filing"],
                                    "calculation_ids": [],
                                    "confidence": 0.9,
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        )


class _OfflineAnalysisCrewFactory:
    def crew(self) -> _OfflineAnalysisCrew:
        return _OfflineAnalysisCrew()


class _AnalysisCrewProbe:
    store: object | None = None

    @classmethod
    def from_evidence_store(cls, store: object) -> _OfflineAnalysisCrewFactory:
        cls.store = store
        return _OfflineAnalysisCrewFactory()

    def crew(self) -> _OfflineAnalysisCrew:
        return _OfflineAnalysisCrew()


def _tool_store() -> EvidenceStore:
    return EvidenceStore(
        {
            "evidence": [
                {
                    "evidence_id": "ev_revenue",
                    "run_id": "tool-run",
                    "metric_id": "revenue",
                    "period": "FY2025",
                    "as_of": "2026-08-10T00:00:00Z",
                    "source_reference": "fixture:revenue",
                    "validation_status": "valid",
                }
            ],
            "calculations": [
                {
                    "calculation_id": "calc_margin",
                    "run_id": "tool-run",
                    "as_of": "2026-08-10T00:00:00Z",
                    "source_reference": "fixture:margin",
                    "validation_status": "valid",
                }
            ],
            "filing_sections": [
                {
                    "section_id": "section_risk",
                    "run_id": "tool-run",
                    "form": "10-K",
                    "filed_at": "2026-02-01",
                    "source_reference": "fixture:filing",
                    "section_title": "Item 1A Risk Factors",
                    "text": "供应链风险。",
                    "validation_status": "valid",
                }
            ],
            "allowlist": {
                "evidence_ids": ["ev_revenue"],
                "calculation_ids": ["calc_margin"],
                "filing_section_ids": ["section_risk"],
            },
        },
        run_id="tool-run",
    )


def test_default_analysis_factory_receives_private_store_not_state() -> None:
    _AnalysisCrewProbe.store = None
    flow = _ready_flow()
    with patch("stockcrewai.flow.AnalysisCrew", _AnalysisCrewProbe):
        flow.run_analysis()

    store = _AnalysisCrewProbe.store
    assert store is not None
    assert getattr(store, "run_id") == flow.state.id
    assert flow._evidence_store is store
    assert store.query_validated_evidence(["revenue"], ["FY2025"], 1)["status"] == "ok"

    state_payload = flow.state.model_dump(mode="json")
    json.dumps(state_payload, ensure_ascii=False, allow_nan=False)
    assert "analysis_evidence_store" not in state_payload
    assert "evidence_store" not in state_payload


def test_analysis_tools_are_role_scoped_and_keep_audit_fields() -> None:
    store = _tool_store()
    analysis_crew = AnalysisCrew.from_evidence_store(store)

    financial_names = {tool.name for tool in analysis_crew._financial_tools}
    risk_names = {tool.name for tool in analysis_crew._risk_tools}
    assert financial_names == {
        "query_validated_evidence",
        "get_validated_calculations",
        "get_quant_summary",
    }
    assert risk_names == {"search_validated_filing_sections"}
    assert not financial_names & risk_names
    assert all(isinstance(tool, BaseTool) for tool in [
        *analysis_crew._financial_tools,
        *analysis_crew._risk_tools,
    ])

    evidence = analysis_crew._financial_tools[0].run(
        metric_ids=["revenue"], periods=["FY2025"], limit=1
    )["records"][0]
    calculation = analysis_crew._financial_tools[1].run(
        calculation_ids=["calc_margin"]
    )["records"][0]
    filing = analysis_crew._risk_tools[0].run(
        query="risk", forms=["10-K"], limit=1
    )["records"][0]
    for record in (evidence, calculation, filing):
        assert record["source_reference"].startswith("fixture:")
        assert record.get("as_of") or record.get("filed_at")
        assert record["validation_status"] == "valid"


def test_store_rejects_unknown_run_mismatch_and_non_allowlisted_queries() -> None:
    store = EvidenceStore(
        {
            "calculations": [
                {
                    "calculation_id": "calc_current",
                    "run_id": "run-a",
                    "as_of": "2026-08-10T00:00:00Z",
                    "source_reference": "fixture:current",
                    "validation_status": "valid",
                },
                {
                    "calculation_id": "calc_other_run",
                    "run_id": "run-b",
                    "as_of": "2026-08-10T00:00:00Z",
                    "source_reference": "fixture:other",
                    "validation_status": "valid",
                },
                {
                    "calculation_id": "calc_out_of_scope",
                    "run_id": "run-a",
                    "as_of": "2026-08-10T00:00:00Z",
                    "source_reference": "fixture:out-of-scope",
                    "validation_status": "valid",
                },
            ],
            "allowlist": {"calculation_ids": ["calc_current"]},
        },
        run_id="run-a",
    )

    assert store.get_validated_calculations(["calc_missing"])["reason_code"] == (
        "calculation_id_unknown"
    )
    assert store.get_validated_calculations(["calc_other_run"])["reason_code"] == (
        "calculation_id_run_mismatch"
    )
    assert store.get_validated_calculations(["calc_out_of_scope"])["reason_code"] == (
        "calculation_id_not_allowlisted"
    )


def test_flow_and_report_context_remain_free_of_store_and_tool_instances() -> None:
    flow = _ready_flow()
    with patch("stockcrewai.flow.AnalysisCrew", _AnalysisCrewProbe):
        flow.run_analysis()

    state_payload = flow.state.model_dump(mode="json")
    result_payload = flow._flow_result()
    report_context = build_report_context(
        company={"name": "Acme Inc.", "ticker": "ACME"},
        validated_claims=[],
        deterministic_verdict={"status": "blocked"},
        calculations=[],
        valuation={},
        historical_valuation={},
        reverse_dcf={},
        source_metadata={},
    )

    def contains_runtime_object(value: object) -> bool:
        if isinstance(value, (EvidenceStore, BaseTool)):
            return True
        if isinstance(value, dict):
            return any(contains_runtime_object(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_runtime_object(item) for item in value)
        return False

    assert not contains_runtime_object(state_payload)
    assert not contains_runtime_object(result_payload)
    assert not contains_runtime_object(report_context)
    json.dumps(state_payload, ensure_ascii=False, allow_nan=False)
    json.dumps(result_payload, ensure_ascii=False, allow_nan=False)
    json.dumps(report_context, ensure_ascii=False, allow_nan=False)


def test_explicit_analysis_crew_keeps_original_injected_path() -> None:
    class ExplicitFakeCrew(_OfflineAnalysisCrew):
        kickoff_calls = 0

        def kickoff(self, *, inputs: dict[str, object]) -> SimpleNamespace:
            type(self).kickoff_calls += 1
            return super().kickoff(inputs=inputs)

    flow = _ready_flow()
    fake = ExplicitFakeCrew()
    flow._analysis_crew = fake
    with patch("stockcrewai.flow.AnalysisCrew") as default_factory:
        flow.run_analysis()

    assert ExplicitFakeCrew.kickoff_calls == 1
    default_factory.from_evidence_store.assert_not_called()
    assert flow._evidence_store is None
