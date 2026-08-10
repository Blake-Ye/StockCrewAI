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


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "profiles" / "commodity_producer"
COMMODITY_METRIC_IDS = (
    "realized_price",
    "production",
    "realized_price_change",
    "production_change",
    "proved_reserves",
    "reserve_life_years",
    "impairment_charge",
    "impairment_to_commodity_revenue",
    "pe_ratio",
)
REQUIRED_METRIC_IDS = {"realized_price", "production"}


def _fixture(name: str = "complete") -> dict[str, Any]:
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


def _policy_context(fixture: dict[str, Any] | None = None) -> dict[str, Any]:
    fixture = fixture or _fixture()
    evidence_records, market_price_records = _typed_records(fixture)
    return build_profile_policy_context(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )


def _source_metadata(
    evidence_records: tuple[EvidenceRecord, ...],
    market_price_records: tuple[MarketPriceRecord, ...],
) -> dict[str, Any]:
    return {
        "facts": {
            record.evidence_id: record.model_dump(mode="json")
            for record in evidence_records
        },
        "market_price": (
            market_price_records[0].model_dump(mode="json")
            if market_price_records
            else {}
        ),
    }


def test_registry_exposes_frozen_commodity_matrix_and_versions() -> None:
    profile = ProfileResult(
        issuer_profile=IssuerProfile.COMMODITY_PRODUCER,
        security_profile=SecurityProfile.COMMON_STOCK,
        reporting_profile=ReportingProfile.DOMESTIC_US_GAAP,
        coverage_level=CoverageLevel.FULL,
        registry_version="profile-registry:test-input",
    )

    policies = metric_registry.resolve_metric_policies(profile)
    policy_version = "metric-policy:commodity:v1"
    expected_formula_ids = {
        "realized_price": "commodity-realized-price-direct-v1",
        "production": "commodity-production-direct-v1",
        "realized_price_change": "commodity-realized-price-change-v1",
        "production_change": "commodity-production-change-v1",
        "proved_reserves": "commodity-proved-reserves-direct-v1",
        "reserve_life_years": "commodity-reserve-life-years-v1",
        "impairment_charge": "commodity-impairment-charge-direct-v1",
        "impairment_to_commodity_revenue": (
            "commodity-impairment-to-commodity-revenue-v1"
        ),
        "pe_ratio": "commodity-pe-ratio-v1",
    }

    assert tuple(policy.metric_id for policy in policies) == COMMODITY_METRIC_IDS
    assert metric_registry.policy_version_for_profile(profile) == policy_version
    assert all(policy.policy_version == policy_version for policy in policies)
    assert {
        policy.metric_id for policy in policies if policy.applicability.value == "required"
    } == REQUIRED_METRIC_IDS
    assert all(
        policy.gate_effect.value == ("blocking" if policy.metric_id in REQUIRED_METRIC_IDS else "non_blocking")
        for policy in policies
    )
    assert {
        policy.metric_id: policy.formula_id for policy in policies
    } == expected_formula_ids
    assert all(
        policy.required_evidence and policy.period_basis and policy.unit_policy and policy.reason_code
        for policy in policies
    )


def test_typed_commodity_context_is_ready_and_preserves_provenance() -> None:
    context = _policy_context()
    decisions = {item["metric_id"]: item for item in context["policy_decisions"]}
    calculations = {
        item["formula_id"]: item for item in context["calculation_records"]
    }

    assert context["profile"]["issuer_profile"] == "commodity_producer"
    assert context["profile_version"] == "commodity-profile:v1"
    assert context["policy_version"] == "metric-policy:commodity:v1"
    assert context["profile_envelope"] == {
        "status": "valid",
        "reason_code": "typed_profile_envelope_valid",
    }
    assert context["gate"]["status"] == "ready"
    assert tuple(decisions) == COMMODITY_METRIC_IDS
    assert all(
        decisions[metric_id]["status"] == "available"
        for metric_id in COMMODITY_METRIC_IDS
    )
    assert context["values"]["realized_price"] == "100"
    assert context["values"]["production"] == "120"
    assert decisions["realized_price"]["calculation_ids"] == []
    assert decisions["realized_price"]["evidence_ids"] == [
        "ev_com_realized_price_current"
    ]
    assert decisions["pe_ratio"]["evidence_ids"] == [
        "ev_com_market_price",
        "ev_com_diluted_eps",
    ]
    assert calculations["commodity-realized-price-change-v1"][
        "input_evidence_ids"
    ] == ["ev_com_realized_price_current", "ev_com_realized_price_prior"]
    assert calculations["commodity-pe-ratio-v1"]["input_evidence_ids"] == [
        "ev_com_market_price",
        "ev_com_diluted_eps",
    ]
    assert len(context["evidence_records"]) == 9
    assert len(context["market_price_records"]) == 1


def test_invalid_commodity_profile_version_fails_closed() -> None:
    fixture = _fixture()
    profile = deepcopy(fixture["profile_input"])
    profile["profile_version"] = "commodity-profile:v0"
    evidence_records, market_price_records = _typed_records(fixture)

    context = build_profile_policy_context(
        profile=profile,
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )

    assert context["profile"]["issuer_profile"] == "commodity_producer"
    assert context["profile_envelope"] == {
        "status": "unavailable",
        "reason_code": "typed_profile_envelope_required",
    }
    assert context["calculation_records"] == []
    assert context["gate"]["status"] == "blocked"
    assert all(
        decision["status"] == "unavailable"
        for decision in context["policy_decisions"]
    )
    assert all(
        decision["blocking"] is (decision["metric_id"] in REQUIRED_METRIC_IDS)
        for decision in context["policy_decisions"]
    )


def test_report_context_and_renderer_show_commodity_metrics_and_units() -> None:
    fixture = _fixture()
    evidence_records, market_price_records = _typed_records(fixture)
    policy_context = _policy_context(fixture)
    report_context = build_report_context(
        company={"name": "Example Copper", "ticker": "COPR"},
        deterministic_verdict={"status": "ready"},
        source_metadata=_source_metadata(evidence_records, market_price_records),
        policy_context=policy_context,
    )

    profile_metrics = report_context["profile_metrics"]
    assert report_context["profile"]["issuer_profile"] == "commodity_producer"
    assert profile_metrics["metric_ids"] == list(COMMODITY_METRIC_IDS)
    direct_metrics = {
        metric["metric_id"]: metric
        for metric in report_context["metrics"]
        if metric["metric_id"] in {"realized_price", "production", "proved_reserves", "impairment_charge"}
    }
    assert direct_metrics["realized_price"]["provenance_type"] == "direct_evidence"
    assert direct_metrics["realized_price"]["calculation_id"] is None
    assert direct_metrics["realized_price"]["source_reference"].startswith("fixture:")
    assert all(
        metric["calculation_id"]
        for metric in report_context["metrics"]
        if metric["metric_id"] in {"realized_price_change", "production_change", "reserve_life_years", "impairment_to_commodity_revenue", "pe_ratio"}
    )
    assert all(
        metric["metric_id"] != "market_price" for metric in report_context["metrics"]
    )

    report = render_validated_report(
        report_context=report_context,
        report_draft=build_deterministic_report_draft(),
    )
    for text in (
        "### 商品生产商专用指标",
        "商品实现价格",
        "商品产量",
        "探明储量",
        "储量寿命",
        "商品资产减值损失",
        "P/E",
        "100.00 USD/lb",
        "120.00 kt",
        "1200.00 kt",
        "10.00 years",
        "50.00 USD millions",
        "25.00x",
        "商品实现价格：公司按主商品披露",
    ):
        assert text in report
    assert "商品产量变化率：-7.69%" in report
    assert "营业收入同比增长" not in report
    assert "resources" not in report
    assert "probable reserves" not in report
    assert "operating loss" not in report
    assert "restructuring charge" not in report

    unavailable_context = deepcopy(policy_context)
    unavailable_decision = next(
        item
        for item in unavailable_context["policy_decisions"]
        if item["metric_id"] == "impairment_charge"
    )
    unavailable_decision.update(
        status="unavailable",
        evidence_ids=[],
        calculation_ids=[],
        reason_code="impairment_charge_missing",
        blocking=False,
    )
    unavailable_context["values"]["impairment_charge"] = None
    unavailable_report_context = build_report_context(
        company={"name": "Example Copper", "ticker": "COPR"},
        deterministic_verdict={"status": "ready"},
        source_metadata=_source_metadata(evidence_records, market_price_records),
        policy_context=unavailable_context,
    )
    unavailable_report = render_validated_report(
        unavailable_report_context,
        build_deterministic_report_draft(),
    )
    assert "商品资产减值损失：unavailable（impairment_charge_missing）" in unavailable_report
    assert "商品资产减值损失：50.00" not in unavailable_report


class _FakeReportCrew:
    def kickoff(self, *, inputs: dict[str, Any]) -> SimpleNamespace:
        del inputs
        return SimpleNamespace(raw="{}")


def test_flow_refresh_and_generate_report_use_commodity_typed_records() -> None:
    fixture = _fixture()
    evidence_records, market_price_records = _typed_records(fixture)
    flow = ResearchFlow(
        profile_input=fixture["profile_input"],
        profile_evidence_records=evidence_records,
        profile_market_price_records=market_price_records,
    )
    flow._pipeline_state = {"facts": {}, "calculations": []}

    flow._refresh_profile_policy_context(None)

    assert flow.state.policy_context["policy_version"] == "metric-policy:commodity:v1"
    assert [
        item["metric_id"] for item in flow.state.policy_context["policy_decisions"]
    ] == list(COMMODITY_METRIC_IDS)
    assert flow.state.policy_context["evidence_records"] == [
        record.model_dump(mode="json") for record in evidence_records
    ]
    assert flow.state.policy_context["market_price_records"] == [
        record.model_dump(mode="json") for record in market_price_records
    ]

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
    assert source_metadata["facts"]["ev_com_realized_price_current"] == (
        evidence_records[0].model_dump(mode="json")
    )
    assert source_metadata["facts"]["ev_com_diluted_eps"] == (
        evidence_records[-1].model_dump(mode="json")
    )
    assert source_metadata["market_price"] == market_price_records[0].model_dump(
        mode="json"
    )
