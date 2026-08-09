from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from stockcrewai import pipeline_support
from stockcrewai.crews.report.crew import (
    build_deterministic_report_draft,
    parse_report_draft,
    validate_rendered_report,
)


FIXTURE = Path(__file__).parent / "fixtures" / "baseline" / "known_issues.json"


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _ready_risk_input() -> dict[str, object]:
    return {
        "validated_filing_ids": ["ev_risk"],
        "filings": [
            {
                "evidence_id": "ev_risk",
                "risk_eligibility": {"eligibility": "eligible"},
                "risk_sections": [{"complete": True, "text": "risk evidence"}],
            }
        ],
    }


def _ready_gate_state() -> dict[str, object]:
    return {
        "company_name": "Example Holdings",
        "ticker": "EXM",
        "facts": {"revenue": {"validation_status": "valid"}},
        "calculations": [{"validation_status": "valid"}],
        "validated_evidence_ids": ["ev_revenue"],
        "validated_calculation_ids": ["calc_margin"],
    }


def test_tsla_factual_text_is_not_treated_as_advice_or_verdict() -> None:
    text = _fixture()["tsla_text"]
    draft_payload = build_deterministic_report_draft().model_dump()
    draft_payload["company_quality"] = text

    draft = parse_report_draft(json.dumps(draft_payload, ensure_ascii=False))
    assert draft.company_quality == text

    report = (
        "## 公司质量\n"
        + text
        + "\n\n确定性状态：status=ready\n\n"
        + "## 非投资建议声明\n本文不构成投资建议。\n"
    )
    passed, reason = validate_rendered_report(report, "ready")
    assert passed is True, reason


def test_explicit_reverse_dcf_not_applicable_is_non_blocking() -> None:
    fixture = _fixture()
    gate = pipeline_support._analysis_gate(
        SimpleNamespace(status="valid", validated=True),
        {**_ready_gate_state(), "issuer_type": "bank"},
        _ready_risk_input(),
        {"readiness": "ready", "validation_status": "valid"},
        {"status": "ok", "validation_status": "valid"},
        fixture["reverse_dcf_not_applicable"],
    )

    assert gate["status"] == "ready"
    assert "reverse_dcf_required" not in gate["required_data"]
    assert gate["applicability"]["reverse_dcf"]["status"] == "not_applicable"


class _RecordingCrew:
    def __init__(self) -> None:
        self.inputs: dict[str, object] | None = None

    def kickoff(self, *, inputs: dict[str, object]) -> SimpleNamespace:
        self.inputs = inputs
        return SimpleNamespace(tasks_output=[])


def test_flow_profile_reaches_gate_and_analysis_downstream() -> None:
    from stockcrewai.main import ResearchFlow

    profile = _fixture()["profile"]
    flow = ResearchFlow()
    flow.state.profile = profile
    flow._pipeline_state = _ready_gate_state()
    flow._risk_input = _ready_risk_input()
    flow._validation_result = SimpleNamespace(status="valid", validated=True)

    captured_gate_state: dict[str, object] = {}

    def capture_gate(*args: object, **kwargs: object) -> dict[str, object]:
        captured_gate_state.update(args[1])
        return {
            "status": "ready",
            "required_data": [],
            "limitations": [],
            "applicability": {},
        }

    with patch("stockcrewai.main._analysis_gate", side_effect=capture_gate):
        assert flow.route_analysis() == "analysis_ready"

    analysis_crew = _RecordingCrew()
    flow._analysis_crew = analysis_crew
    flow.run_analysis()

    assert captured_gate_state["profile"] == profile
    assert analysis_crew.inputs is not None
    assert analysis_crew.inputs["financial_analysis_input"]["profile"] == profile
    assert analysis_crew.inputs["risk_analysis_input"]["profile"] == profile
