from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from stockcrewai.flow import ResearchFlow
from stockcrewai.models.evidence import EvidenceRecord, MarketPriceRecord
from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ProfileResult,
    ReportingProfile,
    SecurityProfile,
)
from stockcrewai.pipelines.evidence_pipeline import build_profile_policy_context
import stockcrewai.pipelines.metric_registry as metric_registry
from stockcrewai.reporting.context import build_report_context
from stockcrewai.reporting.renderer import (
    build_deterministic_report_draft,
    render_validated_report,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "profiles" / "utility"
UTILITY_METRIC_IDS = (
    "utility_operating_margin",
    "rate_base",
    "capex_intensity",
    "interest_coverage",
    "utility_roe",
    "price_to_book",
    "pe_ratio",
    "fcf_yield",
)


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


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


def _policy_context(name: str) -> dict[str, Any]:
    fixture = _fixture(name)
    evidence_records, market_price_records = _typed_records(fixture)
    return build_profile_policy_context(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )


def test_registry_exposes_the_frozen_utility_policy_matrix() -> None:
    profile = ProfileResult(
        issuer_profile=IssuerProfile.UTILITY,
        security_profile=SecurityProfile.COMMON_STOCK,
        reporting_profile=ReportingProfile.DOMESTIC_US_GAAP,
        coverage_level=CoverageLevel.FULL,
        registry_version="profile-registry:test-input",
    )

    policies = metric_registry.resolve_metric_policies(profile)
    utility_policy_version = getattr(metric_registry, "UTILITY_POLICY_VERSION", None)

    assert tuple(policy.metric_id for policy in policies) == UTILITY_METRIC_IDS
    assert metric_registry.policy_version_for_profile(profile) == utility_policy_version == (
        "metric-policy:utility:v1"
    )
    assert all(policy.policy_version == utility_policy_version for policy in policies)
    assert policies[0].required_evidence == ["operating_income", "revenue"]
    assert policies[0].formula_id == "utility-operating-margin-v1"
    assert policies[0].reason_code == "utility_operating_margin_missing"
    assert policies[0].gate_effect.value == "blocking"
    assert policies[1].required_evidence == ["rate_base"]
    assert policies[1].formula_id == "utility-rate-base-direct-v1"
    assert policies[1].gate_effect.value == "non_blocking"


def test_typed_utility_context_uses_adapter_and_keeps_direct_rate_base() -> None:
    context = _policy_context("complete")
    decisions = {item["metric_id"]: item for item in context["policy_decisions"]}

    assert context["policy_version"] == "metric-policy:utility:v1"
    assert context["profile_envelope"] == {
        "status": "valid",
        "reason_code": "typed_profile_envelope_valid",
    }
    assert context["gate"]["status"] == "ready"
    assert tuple(decisions) == UTILITY_METRIC_IDS
    assert decisions["utility_operating_margin"]["status"] == "available"
    assert decisions["rate_base"]["evidence_ids"] == ["ev_util_rate_base"]
    assert decisions["rate_base"]["calculation_ids"] == []
    assert context["values"]["fcf_yield"] == "0.03"
    assert all(
        item["formula_id"] != "utility-rate-base-direct-v1"
        for item in context["calculation_records"]
    )


def test_invalid_utility_envelope_fails_closed_without_generic_profile_fallback() -> None:
    fixture = _fixture("complete")
    profile = deepcopy(fixture["profile_input"])
    profile["profile_version"] = "utility-profile:v0"
    evidence_records, market_price_records = _typed_records(fixture)

    context = build_profile_policy_context(
        profile=profile,
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )

    assert context["profile"]["issuer_profile"] == "utility"
    assert context["policy_version"] == "metric-policy:utility:v1"
    assert context["profile_envelope"]["reason_code"] == "typed_profile_envelope_required"
    assert context["calculation_records"] == []
    assert all(item["status"] == "unavailable" for item in context["policy_decisions"])


def test_report_context_and_renderer_show_utility_metrics_and_rate_base() -> None:
    fixture = _fixture("complete")
    evidence_records, market_price_records = _typed_records(fixture)
    policy_context = _policy_context("complete")
    report_context = build_report_context(
        company={"name": "Example Utility", "ticker": "UTIL"},
        deterministic_verdict={"status": "ready"},
        source_metadata={
            "facts": {
                record.evidence_id: record.model_dump(mode="json")
                for record in evidence_records
            },
            "market_price": market_price_records[0].model_dump(mode="json"),
        },
        policy_context=policy_context,
    )

    profile_metrics = report_context["profile_metrics"]
    assert report_context["profile"]["issuer_profile"] == "utility"
    assert profile_metrics["metric_ids"] == list(UTILITY_METRIC_IDS)
    rate_base = next(
        metric for metric in report_context["metrics"] if metric["metric_id"] == "rate_base"
    )
    assert rate_base["provenance_type"] == "direct_evidence"
    assert rate_base["calculation_id"] is None

    report = render_validated_report(
        report_context=report_context,
        report_draft=build_deterministic_report_draft(),
    )
    assert "### 公用事业专用指标" in report
    assert "Rate Base（费率基数）" in report
    assert "FCF Yield（自由现金流收益率）" in report
    assert "公用事业" in report


class _FakeReportCrew:
    def kickoff(self, *, inputs: dict[str, Any]) -> SimpleNamespace:
        del inputs
        return SimpleNamespace(raw="{}")


def test_flow_refresh_and_generate_report_use_utility_typed_records() -> None:
    fixture = _fixture("complete")
    evidence_records, market_price_records = _typed_records(fixture)
    flow = ResearchFlow(
        profile_input=fixture["profile_input"],
        profile_evidence_records=evidence_records,
        profile_market_price_records=market_price_records,
    )
    flow._pipeline_state = {"facts": {}, "calculations": []}

    flow._refresh_profile_policy_context(None)

    assert flow.state.policy_context["policy_version"] == "metric-policy:utility:v1"
    assert [
        item["metric_id"] for item in flow.state.policy_context["policy_decisions"]
    ] == list(UTILITY_METRIC_IDS)

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
    assert source_metadata["facts"]["ev_util_rate_base"] == (
        evidence_records[6].model_dump(mode="json")
    )
    assert source_metadata["market_price"] == market_price_records[0].model_dump(
        mode="json"
    )
