from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from stockcrewai.models.evidence import EvidenceRecord, MarketPriceRecord, ValidationStatus
from stockcrewai.pipelines.metric_registry import resolve_metric_policies
from stockcrewai.pipelines.profile_registry import classify_profiles
from stockcrewai.pipelines.evidence_pipeline import profile_metadata_from_edgar
from stockcrewai.tools.edgar_tool import EdgarFact, EdgarFilingEvidence, EdgarResult
from stockcrewai.profiles.foreign_issuer import (
    POLICY_VERSION,
    PROFILE_VERSION,
    evaluate_foreign_issuer_profile,
)


AS_OF = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _profile(
    *,
    security: str = "adr",
    metric_inputs: dict[str, str] | None = None,
    as_of: datetime = AS_OF,
) -> dict[str, object]:
    return {
        "profile_version": PROFILE_VERSION,
        "policy_version": POLICY_VERSION,
        "issuer_profile": "standard_operating",
        "security_profile": security,
        "reporting_profile": "foreign_private_issuer_ifrs",
        "coverage_level": "full",
        "as_of": as_of.isoformat(),
        "metric_inputs": metric_inputs or {},
    }


def _evidence(
    evidence_id: str,
    value: str,
    *,
    unit: str,
    currency: str,
    as_of: datetime = AS_OF,
    validation_status: ValidationStatus = ValidationStatus.VALID,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        source_reference=f"sec:test/{evidence_id}",
        as_of=as_of,
        filed_at=date(2026, 2, 20),
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        unit=unit,
        currency=currency,
        value=value,
        validation_status=validation_status,
    )


def _market_price(currency: str = "USD") -> MarketPriceRecord:
    return MarketPriceRecord(
        evidence_id="ev_adr_price",
        ticker="FPI",
        price="100",
        currency=currency,
        price_timestamp=AS_OF,
        source_reference="market:test/FPI",
        adjustment_basis="raw",
        validation_status=ValidationStatus.VALID,
    )


def _complete_sources() -> tuple[dict[str, str], list[EvidenceRecord], list[MarketPriceRecord]]:
    ratio = _evidence(
        "ev_ratio",
        "2",
        unit="ratio",
        currency="ratio",
    )
    ordinary = _evidence(
        "ev_ordinary_shares",
        "1000",
        unit="shares",
        currency="shares",
    )
    return (
        {
            "ordinary_shares_per_adr": ratio.evidence_id,
            "ordinary_shares_outstanding": ordinary.evidence_id,
        },
        [ratio, ordinary],
        [_market_price()],
    )


def test_complete_adr_uses_decimal_calculations_and_full_provenance() -> None:
    metric_inputs, evidence, prices = _complete_sources()

    values, decisions, calculations = evaluate_foreign_issuer_profile(
        _profile(metric_inputs=metric_inputs), evidence, prices
    )

    assert values == {
        "adr_ratio": Decimal("2"),
        "adr_equivalent_shares": Decimal("500"),
        "adr_market_cap": Decimal("50000"),
    }
    assert {decision.status for decision in decisions} == {"available"}
    assert {calculation.formula_id for calculation in calculations} == {
        "foreign-adr-ratio-direct-v1",
        "foreign-adr-equivalent-shares-v1",
        "foreign-adr-market-cap-v1",
    }
    by_metric = {decision.metric_id: decision for decision in decisions}
    by_formula = {calculation.formula_id: calculation for calculation in calculations}
    assert by_metric["adr_ratio"].calculation_ids == [
        by_formula["foreign-adr-ratio-direct-v1"].calculation_id
    ]
    assert set(by_formula["foreign-adr-market-cap-v1"].input_evidence_ids) == {
        "ev_ratio",
        "ev_ordinary_shares",
        "ev_adr_price",
    }
    assert all(
        set(decision.evidence_ids).issubset(
            {"ev_ratio", "ev_ordinary_shares", "ev_adr_price"}
        )
        for decision in decisions
    )


def test_common_stock_foreign_profile_marks_adr_metrics_not_applicable() -> None:
    values, decisions, calculations = evaluate_foreign_issuer_profile(
        _profile(security="common_stock"), [], []
    )

    assert all(value is None for value in values.values())
    assert {decision.status for decision in decisions} == {"not_applicable"}
    assert all(not decision.blocking for decision in decisions)
    assert calculations == ()


def test_foreign_filing_envelope_preserves_form_accession_source_date() -> None:
    result = EdgarResult(
        status="ok",
        ticker="FPI",
        filings=[
            EdgarFilingEvidence(
                evidence_id="ev_20f",
                cik="0000000001",
                form="20-F",
                filed_at="2026-02-20",
                period_end="2025-12-31",
                accession_number="acc-20f",
                source_reference="sec:test/20f",
            ),
            EdgarFilingEvidence(
                evidence_id="ev_6k",
                cik="0000000001",
                form="6-K",
                filed_at="2026-03-01",
                accession_number="acc-6k",
                source_reference="sec:test/6k",
            ),
        ],
        facts={
            "ifrs_revenue": EdgarFact(
                metric_id="ifrs_revenue",
                evidence_id="ev_ifrs",
                value="100",
                unit="EUR",
                period_type="duration",
                period="2025-FY",
                period_start="2025-01-01",
                period_end="2025-12-31",
                filed_at="2026-02-20",
                form="20-F",
                accession_number="acc-20f",
                taxonomy="ifrs-full",
                xbrl_tag="ifrs-full:Revenue",
                source_reference="sec:test/ifrs",
                validation_status="valid",
            )
        },
    )

    metadata = profile_metadata_from_edgar(result)

    assert [(item["form"], item["accession_number"], item["source_reference"], item["filed_at"])
            for item in metadata["filing_envelopes"]] == [
        ("20-F", "acc-20f", "sec:test/20f", "2026-02-20"),
        ("6-K", "acc-6k", "sec:test/6k", "2026-03-01"),
    ]
    assert classify_profiles(metadata).reporting_profile.value == "foreign_private_issuer_ifrs"


@pytest.mark.parametrize(
    "metadata",
    [
        {"filing_forms": ["20-F"], "taxonomy": []},
        {"filing_forms": ["6-K"], "taxonomy": ["ifrs-full"]},
        {"filing_forms": ["20-F"], "taxonomy": ["us-gaap"]},
    ],
)
def test_missing_20f_or_ifrs_fails_closed(metadata: dict[str, list[str]]) -> None:
    source_metadata = {
        "sec_registrant_profile": "standard_operating",
        "sec_security_profile": "common_stock",
        "has_revenue": True,
        **metadata,
    }

    result = classify_profiles(source_metadata)

    assert result.reporting_profile.value == "unknown"
    assert resolve_metric_policies(result) == ()


@pytest.mark.parametrize("ratio_value", [None, "0", "-1"])
def test_missing_zero_or_negative_ratio_never_calculates(ratio_value: str | None) -> None:
    ordinary = _evidence(
        "ev_ordinary_shares",
        "1000",
        unit="shares",
        currency="shares",
    )
    evidence = [ordinary]
    metric_inputs = {"ordinary_shares_outstanding": ordinary.evidence_id}
    if ratio_value is not None:
        ratio = _evidence("ev_ratio", ratio_value, unit="ratio", currency="ratio")
        evidence.insert(0, ratio)
        metric_inputs["ordinary_shares_per_adr"] = ratio.evidence_id

    _, decisions, calculations = evaluate_foreign_issuer_profile(
        _profile(metric_inputs=metric_inputs), evidence, [_market_price()]
    )

    assert calculations == ()
    assert all(not decision.calculation_ids for decision in decisions)

def test_currency_domains_never_mix_non_usd_price_into_adr_market_cap() -> None:
    metric_inputs, evidence, _ = _complete_sources()

    values, decisions, calculations = evaluate_foreign_issuer_profile(
        _profile(metric_inputs=metric_inputs), evidence, [_market_price("EUR")]
    )

    by_metric = {decision.metric_id: decision for decision in decisions}
    assert values["adr_ratio"] == Decimal("2")
    assert values["adr_equivalent_shares"] == Decimal("500")
    assert values["adr_market_cap"] is None
    assert by_metric["adr_market_cap"].status == "unavailable"
    assert by_metric["adr_market_cap"].reason_code == "market_currency_required"
    assert all(calculation.formula_id != "foreign-adr-market-cap-v1" for calculation in calculations)


@pytest.mark.parametrize("case", ["duplicate", "unvalidated", "future"])
def test_duplicate_unvalidated_or_future_evidence_never_calculates(case: str) -> None:
    metric_inputs, evidence, prices = _complete_sources()
    if case == "duplicate":
        evidence = [*evidence, evidence[0]]
    elif case == "unvalidated":
        evidence[0] = evidence[0].model_copy(
            update={"validation_status": ValidationStatus.UNVALIDATED}
        )
    else:
        evidence[0] = evidence[0].model_copy(
            update={"as_of": datetime(2026, 8, 9, tzinfo=UTC)}
        )

    _, decisions, calculations = evaluate_foreign_issuer_profile(
        _profile(metric_inputs=metric_inputs), evidence, prices
    )

    assert calculations == ()
    assert all(not decision.calculation_ids for decision in decisions)
