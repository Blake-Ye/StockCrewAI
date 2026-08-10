from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from stockcrewai.models.evidence import EvidenceRecord, MarketPriceRecord
from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ProfileResult,
    ReportingProfile,
    SecurityProfile,
)
from stockcrewai.pipelines.evidence_pipeline import build_profile_policy_context
from stockcrewai.pipelines.metric_registry import policy_version_for_profile


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "profiles" / "holding_company"
POLICY_VERSION = "metric-policy:holding-company:v1"
METRIC_IDS = (
    "attributable_holdings_value",
    "holding_company_nav",
    "holding_company_market_cap",
    "holding_company_nav_discount",
    "pe_ratio",
    "fcf_yield",
    "historical_valuation",
    "reverse_dcf",
)


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _profile(fixture: dict[str, Any]) -> ProfileResult:
    payload = fixture["profile_input"]
    return ProfileResult(
        issuer_profile=IssuerProfile(payload["issuer_profile"]),
        security_profile=SecurityProfile(payload["security_profile"]),
        reporting_profile=ReportingProfile(payload["reporting_profile"]),
        coverage_level=CoverageLevel(payload["coverage_level"]),
        registry_version="profile-registry:test-input",
    )


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


def _context(name: str) -> tuple[ProfileResult, dict[str, Any]]:
    fixture = _fixture(name)
    profile = _profile(fixture)
    evidence_records, market_price_records = _typed_records(fixture)
    context = build_profile_policy_context(
        profile=fixture["profile_input"],
        evidence_records=evidence_records,
        market_price_records=market_price_records,
    )
    return profile, context


def test_holding_registry_and_complete_context_are_profile_specific() -> None:
    profile, context = _context("complete")

    assert policy_version_for_profile(profile) == POLICY_VERSION
    assert tuple(policy["metric_id"] for policy in context["policies"]) == METRIC_IDS
    assert all(policy["policy_version"] == POLICY_VERSION for policy in context["policies"])
    assert context["policy_version"] == POLICY_VERSION
    assert context["profile_version"] == "holding-company-profile:v1"
    assert context["profile_envelope"] == {
        "status": "valid",
        "reason_code": "typed_profile_envelope_valid",
    }
    assert Decimal(context["values"]["holding_company_nav"]) == Decimal("680")

    decisions = {item["metric_id"]: item for item in context["policy_decisions"]}
    assert decisions["holding_company_nav"]["status"] == "available"
    assert context["gate"]["status"] == "ready"
    assert decisions["pe_ratio"]["status"] == "not_applicable"
    assert decisions["fcf_yield"]["status"] == "not_applicable"


def test_missing_ownership_ratio_blocks_required_holding_decisions() -> None:
    _, context = _context("missing_ownership_ratio")

    decisions = {item["metric_id"]: item for item in context["policy_decisions"]}
    for metric_id in ("attributable_holdings_value", "holding_company_nav"):
        assert decisions[metric_id]["status"] == "unavailable"
        assert decisions[metric_id]["blocking"] is True
        assert decisions[metric_id]["reason_code"] == "holding_ownership_ratio_missing"
    assert context["gate"]["status"] == "blocked"
