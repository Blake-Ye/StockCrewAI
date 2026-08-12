from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from stockcrewai.models.evidence import (
    CalculationRecord,
    EvidenceRecord,
    MarketPriceRecord,
    ValidationStatus,
)
from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ReportingProfile,
    SecurityProfile,
)
from stockcrewai.pipelines.metric_registry import (
    policy_version_for_profile,
    resolve_metric_policies,
)
from stockcrewai.pipelines.profile_registry import classify_profiles
from stockcrewai.profiles.foreign_issuer import evaluate_foreign_issuer_profile
from stockcrewai.reporting.context import build_report_context
from stockcrewai.reporting.renderer import (
    build_deterministic_report_draft,
    render_validated_report,
)
from stockcrewai.validators.analysis_gate import evaluate_analysis_gate


PROFILE_REGISTRY_FIXTURE = (
    Path(__file__).parent / "fixtures" / "profiles" / "profile_registry.json"
)
FOREIGN_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "profiles" / "foreign_issuer"
def _foreign_fixture(name: str) -> dict[str, Any]:
    return json.loads((FOREIGN_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def test_foreign_issuer_uses_reproducible_complete_and_missing_fixtures() -> None:
    for name in ("complete", "missing_ratio"):
        fixture = _foreign_fixture(name)
        assert set(fixture) == {
            "fixture_version",
            "synthetic",
            "offline",
            "no_network",
            "source_note",
            "source_metadata",
            "profile_input",
            "evidence_records",
            "market_price_records",
            "expected",
        }
        assert fixture["synthetic"] is True
        assert fixture["offline"] is True
        assert fixture["no_network"] is True
        assert "offline" in fixture["source_note"].lower()
        assert "network" in fixture["source_note"].lower()

        source_metadata = fixture["source_metadata"]
        assert source_metadata["filing_forms"] == ["20-F", "6-K"]
        assert source_metadata["taxonomy"] == ["ifrs-full"]
        assert [
            envelope["form"] for envelope in source_metadata["filing_envelopes"]
        ] == ["20-F", "6-K"]
        assert (
            classify_profiles(source_metadata).model_dump(mode="json")
            == fixture["expected"]["registry"]
        )

        evidence_records = tuple(
            EvidenceRecord.model_validate(item) for item in fixture["evidence_records"]
        )
        market_price_records = tuple(
            MarketPriceRecord.model_validate(item)
            for item in fixture["market_price_records"]
        )
        values, decisions, calculations = evaluate_foreign_issuer_profile(
            fixture["profile_input"], evidence_records, market_price_records
        )
        expected = fixture["expected"]
        assert {
            metric_id: None if value is None else str(value)
            for metric_id, value in values.items()
        } == expected["values"]
        assert {
            decision.metric_id: decision.status for decision in decisions
        } == expected["statuses"]
        assert {
            decision.metric_id: decision.reason_code for decision in decisions
        } == expected["reason_codes"]
        assert [calculation.calculation_id for calculation in calculations] == expected[
            "calculation_ids"
        ]

        source_ids = {
            record.evidence_id for record in evidence_records
        } | {record.evidence_id for record in market_price_records}
        calculation_ids = {
            calculation.calculation_id for calculation in calculations
        }
        assert len(calculation_ids) == len(calculations)
        for decision in decisions:
            if decision.status != "available":
                continue
            assert decision.evidence_ids
            assert len(decision.evidence_ids) == len(set(decision.evidence_ids))
            assert set(decision.evidence_ids) <= source_ids
            assert set(decision.calculation_ids) <= calculation_ids

        if name == "complete":
            assert all(isinstance(calculation, CalculationRecord) for calculation in calculations)

            for calculation in calculations:
                assert calculation.calculation_id in calculation_ids
                assert calculation.formula_id
                assert calculation.formula_id.startswith("foreign-")
                assert calculation.source_reference
                assert calculation.source_reference.startswith("derived:")
                assert calculation.input_evidence_ids
                assert len(calculation.input_evidence_ids) == len(
                    set(calculation.input_evidence_ids)
                )
                assert set(calculation.input_evidence_ids) <= source_ids
                assert calculation.result is not None
                assert calculation.result.is_finite()
                assert calculation.as_of.tzinfo is not None
                assert calculation.period_start is not None
                assert calculation.period_end is not None
                assert calculation.period_start <= calculation.period_end
                assert calculation.validation_status is ValidationStatus.VALID

            metric_inputs = fixture["profile_input"]["metric_inputs"]
            ratio_evidence_id = metric_inputs["ordinary_shares_per_adr"]
            ordinary_shares_evidence_id = metric_inputs["ordinary_shares_outstanding"]
            market_price_evidence_id = fixture["market_price_records"][0]["evidence_id"]
            evidence_by_id = {record.evidence_id: record for record in evidence_records}
            market_price = next(
                record
                for record in market_price_records
                if record.evidence_id == market_price_evidence_id
            )
            expected_calculations_by_metric = {
                "adr_ratio": {
                    "calculation_id": "calc_foreign_adr_ratio_direct_v1",
                    "formula_id": "foreign-adr-ratio-direct-v1",
                    "input_evidence_ids": [ratio_evidence_id],
                    "source_reference": "derived:foreign-adr-ratio-direct-v1",
                    "result": expected["values"]["adr_ratio"],
                    "unit": "ratio",
                    "period_start": evidence_by_id[ratio_evidence_id].period_start,
                    "period_end": evidence_by_id[ratio_evidence_id].period_end,
                    "as_of": evidence_by_id[ratio_evidence_id].as_of,
                    "validation_status": ValidationStatus.VALID,
                },
                "adr_equivalent_shares": {
                    "calculation_id": "calc_foreign_adr_equivalent_shares_v1",
                    "formula_id": "foreign-adr-equivalent-shares-v1",
                    "input_evidence_ids": [
                        ordinary_shares_evidence_id,
                        ratio_evidence_id,
                    ],
                    "source_reference": "derived:foreign-adr-equivalent-shares-v1",
                    "result": expected["values"]["adr_equivalent_shares"],
                    "unit": "shares",
                    "period_start": evidence_by_id[ordinary_shares_evidence_id].period_start,
                    "period_end": evidence_by_id[ordinary_shares_evidence_id].period_end,
                    "as_of": evidence_by_id[ordinary_shares_evidence_id].as_of,
                    "validation_status": ValidationStatus.VALID,
                },
                "adr_market_cap": {
                    "calculation_id": "calc_foreign_adr_market_cap_v1",
                    "formula_id": "foreign-adr-market-cap-v1",
                    "input_evidence_ids": [
                        market_price_evidence_id,
                        ordinary_shares_evidence_id,
                        ratio_evidence_id,
                    ],
                    "source_reference": "derived:foreign-adr-market-cap-v1",
                    "result": expected["values"]["adr_market_cap"],
                    "unit": "USD",
                    "period_start": evidence_by_id[ordinary_shares_evidence_id].period_start,
                    "period_end": evidence_by_id[ordinary_shares_evidence_id].period_end,
                    "as_of": market_price.price_timestamp,
                    "validation_status": ValidationStatus.VALID,
                },
            }
            assert len(calculations) == len(expected_calculations_by_metric) == 3
            assert tuple(
                calculation["calculation_id"]
                for calculation in expected_calculations_by_metric.values()
            ) == tuple(expected["calculation_ids"])
            assert set(calculation_ids) == {
                calculation["calculation_id"]
                for calculation in expected_calculations_by_metric.values()
            }

            available_decisions = {
                decision.metric_id: decision
                for decision in decisions
                if decision.status == "available"
            }
            assert set(available_decisions) == set(expected_calculations_by_metric)
            for metric_id, expected_calculation in expected_calculations_by_metric.items():
                decision = available_decisions[metric_id]
                assert decision.evidence_ids == expected_calculation["input_evidence_ids"]
                assert decision.calculation_ids == [
                    expected_calculation["calculation_id"]
                ]

            calculations_by_id = {
                calculation.calculation_id: calculation for calculation in calculations
            }
            for expected_calculation in expected_calculations_by_metric.values():
                calculation = calculations_by_id[expected_calculation["calculation_id"]]
                assert calculation.calculation_id == expected_calculation["calculation_id"]
                assert calculation.formula_id == expected_calculation["formula_id"]
                assert (
                    calculation.input_evidence_ids
                    == expected_calculation["input_evidence_ids"]
                )
                assert calculation.source_reference == expected_calculation["source_reference"]
                assert calculation.result is not None
                assert str(calculation.result) == expected_calculation["result"]
                assert calculation.unit == expected_calculation["unit"]
                assert calculation.period_start == expected_calculation["period_start"]
                assert calculation.period_end == expected_calculation["period_end"]
                assert calculation.as_of == expected_calculation["as_of"]
                assert calculation.validation_status is expected_calculation[
                    "validation_status"
                ]
        else:
            assert calculations == ()
            assert all(not decision.calculation_ids for decision in decisions)
            for decision in decisions:
                assert decision.status == expected["statuses"][decision.metric_id]
                assert decision.reason_code == expected["reason_codes"][decision.metric_id]
                assert decision.evidence_ids == []
                assert decision.calculation_ids == []


def _profile_registry_cases() -> dict[str, Any]:
    payload = json.loads(PROFILE_REGISTRY_FIXTURE.read_text(encoding="utf-8"))
    return payload["cases"]


def _holding_registry_metadata(cases: dict[str, Any]) -> dict[str, Any]:
    metadata = deepcopy(cases["standard_operating"]["source_metadata"])
    metadata.update(
        {
            "sec_registrant_profile": None,
            "sec_reporting_profile": None,
            "sec_security_profile": None,
            "sic": None,
            "taxonomy": ["holding_company", "us-gaap"],
            "classification_evidence_ids": ["ev_holding_registry"],
        }
    )
    return metadata


def _wp12_profile_metadata(cases: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "utility": cases["utility"]["source_metadata"],
        "commodity_producer": cases["commodity_producer"]["source_metadata"],
        "foreign_issuer": cases["adr"]["source_metadata"],
        "holding_company": _holding_registry_metadata(cases),
        "spac": cases["spac"]["source_metadata"],
    }


def _classify_cases(
    case_names: tuple[str, ...], *, reverse_fields: bool
) -> list[tuple[str, dict[str, Any]]]:
    cases = _profile_registry_cases()
    results: list[tuple[str, dict[str, Any]]] = []
    for name in case_names:
        metadata = deepcopy(cases[name]["source_metadata"])
        if reverse_fields:
            for key, value in metadata.items():
                if isinstance(value, list):
                    metadata[key] = list(reversed(value))
        results.append(
            (name, classify_profiles(metadata).model_dump(mode="json"))
        )
    return results


def _canonical_profile_dump(dump: dict[str, Any]) -> dict[str, Any]:
    return {
        **dump,
        "classification_evidence_ids": sorted(dump["classification_evidence_ids"]),
    }


def test_profile_registry_is_independent_of_case_and_metadata_order_and_unknown_stays_unknown() -> None:
    cases = _profile_registry_cases()
    case_names = tuple(cases)

    forward = _classify_cases(case_names, reverse_fields=False)
    reordered = _classify_cases(tuple(reversed(case_names)), reverse_fields=True)

    assert tuple(name for name, _ in forward) == case_names
    assert tuple(name for name, _ in reordered) == tuple(reversed(case_names))
    forward_by_name = dict(forward)
    reordered_by_name = dict(reordered)
    for name in case_names:
        expected = cases[name]["expected"]
        assert forward_by_name[name] == expected
        assert _canonical_profile_dump(reordered_by_name[name]) == _canonical_profile_dump(
            expected
        )
        assert _canonical_profile_dump(reordered_by_name[name]) == _canonical_profile_dump(
            forward_by_name[name]
        )


def test_unknown_profile_is_evidence_only_without_policies_or_ready_gate() -> None:
    case = _profile_registry_cases()["unknown_empty"]
    unknown_profile = classify_profiles(case["source_metadata"])

    assert unknown_profile.model_dump(mode="json") == case["expected"]
    assert unknown_profile.issuer_profile is IssuerProfile.UNKNOWN
    assert unknown_profile.security_profile is SecurityProfile.UNKNOWN
    assert unknown_profile.reporting_profile is ReportingProfile.UNKNOWN
    assert unknown_profile.coverage_level is CoverageLevel.EVIDENCE_ONLY
    assert resolve_metric_policies(unknown_profile) == ()

    gate = evaluate_analysis_gate(unknown_profile, [])
    assert gate.status == "evidence_only"
    assert gate.coverage_level is CoverageLevel.EVIDENCE_ONLY
    assert "evidence_only_coverage" in gate.reason_codes


def test_wp12_registry_profiles_feed_the_gate_with_explicit_policy_versions() -> None:
    cases = _profile_registry_cases()
    metadata_by_profile = _wp12_profile_metadata(cases)
    expected_versions = {
        "utility": "metric-policy:utility:v1",
        "commodity_producer": "metric-policy:commodity:v1",
        "foreign_issuer": "metric-policy:foreign-issuer:v1",
        "holding_company": "metric-policy:holding-company:v1",
        "spac": "metric-policy:spac:v1",
    }

    for name, metadata in metadata_by_profile.items():
        profile = classify_profiles(metadata)
        assert profile.coverage_level is CoverageLevel.FULL
        assert resolve_metric_policies(profile)
        assert policy_version_for_profile(profile) == expected_versions[name]

        gate = evaluate_analysis_gate(profile, [])
        expected_gate_status = (
            "evidence_only"
            if name == "spac"
            else "unsupported"
            if name in {"utility", "commodity_producer"}
            else "ready"
        )
        assert gate.status == expected_gate_status


def _not_applicable_valuation_payload() -> dict[str, Any]:
    return {
        "status": "not_applicable",
        "readiness": "not_applicable",
        "validation_status": "unvalidated",
        "reason_code": "profile_not_applicable",
        "calculations": [],
    }


@pytest.mark.parametrize(
    ("profile_name", "company_name", "ticker"),
    [
        ("utility", "Synthetic Utility", "UTIL"),
        ("commodity_producer", "Synthetic Commodity", "COMM"),
        ("foreign_issuer", "Synthetic Foreign Issuer", "FPI"),
        ("holding_company", "Synthetic Holding Company", "HOLD"),
        ("spac", "Synthetic SPAC", "SPAC"),
    ],
)
def test_wp12_profiles_reach_report_renderer_with_identity_and_profile_boundary(
    profile_name: str, company_name: str, ticker: str
) -> None:
    cases = _profile_registry_cases()
    profile = classify_profiles(_wp12_profile_metadata(cases)[profile_name])
    company = {"name": company_name, "ticker": ticker}
    policy_context = {
        "profile": profile.model_dump(mode="json"),
        "coverage_level": profile.coverage_level.value,
        "policy_version": policy_version_for_profile(profile),
        "policy_decisions": [],
        "values": {},
        "calculation_records": [],
    }
    not_applicable = _not_applicable_valuation_payload()

    report_context = build_report_context(
        company=company,
        validated_claims=[],
        deterministic_verdict={"status": "evidence_only"},
        valuation=not_applicable,
        historical_valuation=not_applicable,
        reverse_dcf=not_applicable,
        policy_context=policy_context,
    )
    report = render_validated_report(
        report_context=report_context,
        report_draft=build_deterministic_report_draft(),
    )

    assert report_context["company"] == company
    assert report_context["profile"] == profile.model_dump(mode="json")
    assert "Profile：" in report
    assert f"issuer={profile.issuer_profile.value}" in report
    assert f"security={profile.security_profile.value}" in report
    assert f"reporting={profile.reporting_profile.value}" in report
