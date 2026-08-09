from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest
from pydantic import ValidationError

from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ReportingProfile,
    SecurityProfile,
)
from stockcrewai.models.quant import (
    FactorObservation,
    PointInTimeSnapshot,
    QuantResearchPacket,
    UniverseManifest,
)


AS_OF = datetime(2026, 8, 10, 1, 2, 3, tzinfo=timezone.utc)
FILING_CUTOFF = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
PRICE_CUTOFF = datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)


def snapshot_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "snapshot_id": "snapshot_aapl_20260810",
        "as_of": AS_OF,
        "cik": "0000320193",
        "ticker": "AAPL",
        "issuer_profile": IssuerProfile.STANDARD_OPERATING,
        "security_profile": SecurityProfile.COMMON_STOCK,
        "reporting_profile": ReportingProfile.DOMESTIC_US_GAAP,
        "filing_cutoff": FILING_CUTOFF,
        "price_cutoff": PRICE_CUTOFF,
        "available_evidence_ids": ["ev_revenue_2025"],
        "available_calculation_ids": ["calc_fcf_2025"],
        "financial_features": {
            "roe": Decimal("0.25"),
            "missing_feature": None,
        },
        "market_features": {"momentum_12m": Decimal("0.18")},
        "data_quality": {
            "financial": "complete",
            "coverage_ratio": Decimal("0.95"),
            "is_stale": False,
            "missing_detail": None,
        },
        "builder_version": "snapshot-builder-v1",
    }
    payload.update(overrides)
    return payload


def factor_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "factor_id": "quality_roe",
        "formula_version": "quality-roe-v1",
        "snapshot_id": "snapshot_aapl_20260810",
        "as_of": AS_OF,
        "ticker": "AAPL",
        "raw_value": Decimal("0.25"),
        "normalized_value": Decimal("0.75"),
        "peer_group": "technology-large-cap",
        "peer_count": 12,
        "evidence_ids": ["ev_net_income_2025"],
        "calculation_ids": ["calc_roe_2025"],
        "status": "available",
        "reason_code": "validated_inputs",
    }
    payload.update(overrides)
    return payload


def packet_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "as_of": AS_OF,
        "universe_id": "us-large-cap-v1",
        "strategy_version": "quality-value-v1",
        "coverage": CoverageLevel.FULL,
        "factor_summary": {
            "roe": Decimal("0.25"),
            "status": "available",
            "is_complete": True,
            "missing_factor": None,
        },
        "ranking_summary": {"top_ticker": "AAPL"},
        "backtest_summary": {"period": "2018-2025"},
        "benchmark_summary": {"benchmark": "SPY"},
        "data_quality": {"source": "offline-fixture", "is_valid": True},
        "limitations": ["small_fixture_universe"],
        "artifact_ids": ["artifact_quant_packet_001"],
    }
    payload.update(overrides)
    return payload


def universe_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "universe_id": "us-large-cap-v1",
        "tickers": ["AAPL", "MSFT"],
        "selection_as_of": AS_OF,
        "membership_source": "fixture:universe-membership",
        "membership_basis": "market-cap-threshold",
        "known_biases": ["survivorship_bias_known"],
        "manifest_version": "universe-manifest-v1",
    }
    payload.update(overrides)
    return payload


def test_quant_models_accept_valid_payloads_and_json_dump() -> None:
    snapshot = PointInTimeSnapshot.model_validate(snapshot_payload())
    factor = FactorObservation.model_validate(factor_payload())
    packet = QuantResearchPacket.model_validate(packet_payload())
    universe = UniverseManifest.model_validate(universe_payload())

    for model in (snapshot, factor, packet, universe):
        dumped = model.model_dump(mode="json")
        json.dumps(dumped, allow_nan=False)
        assert json.loads(model.model_dump_json()) == dumped

    assert snapshot.financial_features["missing_feature"] is None
    assert snapshot.model_dump(mode="json")["financial_features"]["roe"] == "0.25"
    assert factor.model_dump(mode="json")["raw_value"] == "0.25"
    assert packet.model_dump(mode="json")["factor_summary"]["roe"] == "0.25"
    assert universe.model_dump(mode="json")["known_biases"] == ["survivorship_bias_known"]


@pytest.mark.parametrize(
    ("model_type", "payload_factory", "expected_fields"),
    [
        (
            PointInTimeSnapshot,
            snapshot_payload,
            {
                "snapshot_id",
                "as_of",
                "cik",
                "ticker",
                "issuer_profile",
                "security_profile",
                "reporting_profile",
                "filing_cutoff",
                "price_cutoff",
                "available_evidence_ids",
                "available_calculation_ids",
                "financial_features",
                "market_features",
                "data_quality",
                "builder_version",
            },
        ),
        (
            FactorObservation,
            factor_payload,
            {
                "factor_id",
                "formula_version",
                "snapshot_id",
                "as_of",
                "ticker",
                "raw_value",
                "normalized_value",
                "peer_group",
                "peer_count",
                "evidence_ids",
                "calculation_ids",
                "status",
                "reason_code",
            },
        ),
        (
            QuantResearchPacket,
            packet_payload,
            {
                "as_of",
                "universe_id",
                "strategy_version",
                "coverage",
                "factor_summary",
                "ranking_summary",
                "backtest_summary",
                "benchmark_summary",
                "data_quality",
                "limitations",
                "artifact_ids",
            },
        ),
        (
            UniverseManifest,
            universe_payload,
            {
                "universe_id",
                "tickers",
                "selection_as_of",
                "membership_source",
                "membership_basis",
                "known_biases",
                "manifest_version",
            },
        ),
    ],
)
def test_quant_models_expose_the_frozen_field_sets(
    model_type: type[object],
    payload_factory: object,
    expected_fields: set[str],
) -> None:
    model = model_type.model_validate(payload_factory())  # type: ignore[attr-defined]

    assert set(type(model).model_fields) == expected_fields


@pytest.mark.parametrize(
    ("model_type", "payload_factory"),
    [
        (PointInTimeSnapshot, snapshot_payload),
        (FactorObservation, factor_payload),
        (QuantResearchPacket, packet_payload),
        (UniverseManifest, universe_payload),
    ],
)
def test_quant_models_forbid_extra_fields(
    model_type: type[object], payload_factory: object
) -> None:
    payload = payload_factory()  # type: ignore[operator]

    with pytest.raises(ValidationError):
        model_type.model_validate({**payload, "unexpected": "must fail"})  # type: ignore[attr-defined]


def test_snapshot_reuses_the_existing_profile_enums() -> None:
    snapshot = PointInTimeSnapshot.model_validate(snapshot_payload())

    assert isinstance(snapshot.issuer_profile, IssuerProfile)
    assert isinstance(snapshot.security_profile, SecurityProfile)
    assert isinstance(snapshot.reporting_profile, ReportingProfile)
    assert snapshot.issuer_profile is IssuerProfile.STANDARD_OPERATING
    assert snapshot.security_profile is SecurityProfile.COMMON_STOCK
    assert snapshot.reporting_profile is ReportingProfile.DOMESTIC_US_GAAP


@pytest.mark.parametrize("field", ["snapshot_id", "cik", "ticker", "builder_version"])
def test_snapshot_rejects_blank_identifiers(field: str) -> None:
    with pytest.raises(ValidationError):
        PointInTimeSnapshot.model_validate(snapshot_payload(**{field: "  "}))


@pytest.mark.parametrize("field", ["available_evidence_ids", "available_calculation_ids"])
def test_snapshot_rejects_blank_provenance_ids(field: str) -> None:
    with pytest.raises(ValidationError):
        PointInTimeSnapshot.model_validate(snapshot_payload(**{field: [""]}))


def test_quant_models_reject_bytes_in_string_boundaries() -> None:
    with pytest.raises(ValidationError):
        PointInTimeSnapshot.model_validate(snapshot_payload(snapshot_id=b"snapshot_bytes"))

    with pytest.raises(ValidationError):
        PointInTimeSnapshot.model_validate(
            snapshot_payload(available_evidence_ids=[b"evidence_bytes"])
        )

    with pytest.raises(ValidationError):
        UniverseManifest.model_validate(
            universe_payload(known_biases=[b"survivorship_bias_known"])
        )


@pytest.mark.parametrize(
    "field",
    ["factor_id", "formula_version", "snapshot_id", "ticker", "peer_group", "reason_code"],
)
def test_factor_observation_rejects_blank_identifiers(field: str) -> None:
    with pytest.raises(ValidationError):
        FactorObservation.model_validate(factor_payload(**{field: ""}))


@pytest.mark.parametrize("field", ["evidence_ids", "calculation_ids"])
def test_factor_observation_rejects_blank_provenance_ids(field: str) -> None:
    with pytest.raises(ValidationError):
        FactorObservation.model_validate(factor_payload(**{field: ["  "]}))


@pytest.mark.parametrize(
    ("model_type", "payload_factory", "field"),
    [
        (PointInTimeSnapshot, snapshot_payload, "as_of"),
        (PointInTimeSnapshot, snapshot_payload, "filing_cutoff"),
        (PointInTimeSnapshot, snapshot_payload, "price_cutoff"),
        (FactorObservation, factor_payload, "as_of"),
        (QuantResearchPacket, packet_payload, "as_of"),
        (UniverseManifest, universe_payload, "selection_as_of"),
    ],
)
def test_quant_models_reject_naive_datetimes(
    model_type: type[object], payload_factory: object, field: str
) -> None:
    payload = payload_factory()  # type: ignore[operator]
    payload[field] = datetime(2026, 8, 10, 1, 2, 3)

    with pytest.raises(ValidationError):
        model_type.model_validate(payload)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "bad_value",
    [0.1, float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity")],
)
def test_snapshot_numeric_features_reject_float_and_nonfinite_values(bad_value: object) -> None:
    for field in ("financial_features", "market_features"):
        with pytest.raises(ValidationError):
            PointInTimeSnapshot.model_validate(snapshot_payload(**{field: {"metric": bad_value}}))


@pytest.mark.parametrize(
    "bad_value",
    [0.1, float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity")],
)
def test_factor_values_reject_float_and_nonfinite_values(bad_value: object) -> None:
    for field in ("raw_value", "normalized_value"):
        with pytest.raises(ValidationError):
            FactorObservation.model_validate(factor_payload(**{field: bad_value}))


@pytest.mark.parametrize(
    "bad_value",
    [0.1, float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity")],
)
def test_packet_scalar_summaries_reject_float_and_nonfinite_values(bad_value: object) -> None:
    for field in (
        "factor_summary",
        "ranking_summary",
        "backtest_summary",
        "benchmark_summary",
        "data_quality",
    ):
        with pytest.raises(ValidationError):
            QuantResearchPacket.model_validate(packet_payload(**{field: {"metric": bad_value}}))


def test_factor_observation_rejects_negative_peer_count() -> None:
    with pytest.raises(ValidationError):
        FactorObservation.model_validate(factor_payload(peer_count=-1))


def test_factor_observation_uses_the_explicit_status_literal() -> None:
    for status in ("available", "unavailable", "not_applicable", "invalid"):
        observation = FactorObservation.model_validate(factor_payload(status=status))
        assert observation.status == status

    with pytest.raises(ValidationError):
        FactorObservation.model_validate(factor_payload(status="ready"))


@pytest.mark.parametrize("field", ["universe_id", "strategy_version"])
def test_packet_rejects_blank_required_strings(field: str) -> None:
    with pytest.raises(ValidationError):
        QuantResearchPacket.model_validate(packet_payload(**{field: ""}))


@pytest.mark.parametrize("field", ["limitations", "artifact_ids"])
def test_packet_rejects_blank_string_elements(field: str) -> None:
    with pytest.raises(ValidationError):
        QuantResearchPacket.model_validate(packet_payload(**{field: [""]}))


def test_packet_scalar_summaries_are_json_safe() -> None:
    packet = QuantResearchPacket.model_validate(packet_payload())
    dumped = packet.model_dump(mode="json")

    assert dumped["factor_summary"] == {
        "roe": "0.25",
        "status": "available",
        "is_complete": True,
        "missing_factor": None,
    }
    json.dumps(dumped, allow_nan=False)


@pytest.mark.parametrize(
    "field",
    ["universe_id", "membership_source", "membership_basis", "manifest_version"],
)
def test_universe_manifest_rejects_blank_required_strings(field: str) -> None:
    with pytest.raises(ValidationError):
        UniverseManifest.model_validate(universe_payload(**{field: "  "}))


def test_universe_manifest_rejects_blank_tickers() -> None:
    with pytest.raises(ValidationError):
        UniverseManifest.model_validate(universe_payload(tickers=["AAPL", ""]))


def test_universe_manifest_requires_nonempty_known_biases_with_survivorship_bias() -> None:
    for known_biases in ([], ["lookahead_bias"]):
        with pytest.raises(ValidationError):
            UniverseManifest.model_validate(universe_payload(known_biases=known_biases))
