from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from stockcrewai.models.quant import UniverseManifest
from stockcrewai.quant.dataset import build_point_in_time_dataset
from stockcrewai.quant.point_in_time import build_point_in_time_snapshot


CIK = "0000320193"
TICKER = "AAPL"
PROFILE = {
    "cik": CIK,
    "ticker": TICKER,
    "issuer_profile": "standard_operating",
    "security_profile": "common_stock",
    "reporting_profile": "domestic_us_gaap",
    "coverage_level": "full",
    "classification_evidence_ids": ["ev_profile"],
    "reason_codes": [],
    "registry_version": "profile-registry:v1",
}


def _evidence(
    evidence_id: str,
    *,
    metric_id: str | None,
    value: str | None,
    filed_at: str,
    period_start: str = "2025-01-01",
    period_end: str = "2025-12-31",
    validation_status: str = "valid",
    form: str = "10-K",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "evidence_id": evidence_id,
        "source_reference": f"fixture:{evidence_id}",
        "as_of": f"{filed_at}T12:00:00Z",
        "filed_at": filed_at,
        "period_start": period_start,
        "period_end": period_end,
        "unit": "USD",
        "currency": "USD",
        "value": value,
        "validation_status": validation_status,
        "form": form,
    }
    if metric_id is not None:
        payload["metric_id"] = metric_id
    return payload


def _calculation(
    calculation_id: str,
    *,
    metric_id: str | None,
    result: str | None,
    as_of: str,
    input_evidence_ids: list[str],
    validation_status: str = "valid",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "calculation_id": calculation_id,
        "formula_id": metric_id or calculation_id,
        "input_evidence_ids": input_evidence_ids,
        "source_reference": f"fixture:{calculation_id}",
        "as_of": f"{as_of}T12:00:00Z",
        "result": result,
        "unit": "ratio",
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "validation_status": validation_status,
    }
    if metric_id is not None:
        payload["metric_id"] = metric_id
    return payload


def _price(
    evidence_id: str,
    *,
    price: str,
    timestamp: str,
    adjustment_basis: str = "split_adjusted",
) -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "ticker": TICKER,
        "price": price,
        "currency": "USD",
        "price_timestamp": timestamp,
        "source_reference": f"fixture:{evidence_id}",
        "adjustment_basis": adjustment_basis,
        "validation_status": "valid",
    }


def _inputs() -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    evidence = [
        _evidence("ev_revenue_original", metric_id="revenue", value="100", filed_at="2026-02-01"),
        _evidence(
            "ev_revenue_amended",
            metric_id="revenue",
            value="110",
            filed_at="2026-03-01",
            form="10-K/A",
        ),
        _evidence(
            "ev_revenue_future_q",
            metric_id="revenue",
            value="999",
            filed_at="2026-08-11",
            period_start="2026-01-01",
            period_end="2026-06-30",
            form="10-Q",
        ),
    ]
    calculations = [
        _calculation(
            "calc_margin",
            metric_id="operating_margin",
            result="0.20",
            as_of="2026-03-02",
            input_evidence_ids=["ev_revenue_amended"],
        ),
        _calculation(
            "calc_future",
            metric_id="operating_margin",
            result="0.99",
            as_of="2026-08-11",
            input_evidence_ids=["ev_revenue_future_q"],
        ),
    ]
    prices = [
        _price(
            "price_at_cutoff",
            price="110.125",
            timestamp="2026-08-09T16:30:00Z",
        ),
        _price(
            "price_after_cutoff",
            price="120",
            timestamp="2026-08-09T16:30:01Z",
        ),
    ]
    return evidence, calculations, prices


def _snapshot_inputs() -> dict[str, object]:
    evidence, calculations, prices = _inputs()
    return {
        "as_of": datetime(2026, 8, 10, 0, 30, tzinfo=timezone(timedelta(hours=8))),
        "profile": PROFILE,
        "evidence": evidence,
        "calculations": calculations,
        "prices": prices,
        "builder_version": "point-in-time:v1",
    }


def test_snapshot_excludes_future_records_and_selects_amendment() -> None:
    snapshot = build_point_in_time_snapshot(**_snapshot_inputs())

    assert snapshot.financial_features["revenue"] == Decimal("110")
    assert snapshot.financial_features["operating_margin"] == Decimal("0.20")
    assert snapshot.market_features["price"] == Decimal("110.125")
    assert "ev_revenue_future_q" not in snapshot.available_evidence_ids
    assert "calc_future" not in snapshot.available_calculation_ids
    assert snapshot.data_quality["adjustment_basis"] == "split_adjusted"


def test_snapshot_hash_is_order_independent_and_preserves_decimal() -> None:
    inputs = _snapshot_inputs()
    first = build_point_in_time_snapshot(**inputs)
    reordered = dict(inputs)
    reordered["evidence"] = list(reversed(inputs["evidence"]))  # type: ignore[arg-type]
    reordered["calculations"] = list(reversed(inputs["calculations"]))  # type: ignore[arg-type]
    reordered["prices"] = list(reversed(inputs["prices"]))  # type: ignore[arg-type]
    second = build_point_in_time_snapshot(**reordered)

    assert first.snapshot_id == second.snapshot_id
    assert isinstance(first.financial_features["revenue"], Decimal)
    assert json.loads(first.model_dump_json()) == first.model_dump(mode="json")


def test_snapshot_rejects_mixed_price_adjustment_basis() -> None:
    inputs = _snapshot_inputs()
    inputs["prices"] = [
        _price("price_raw", price="110", timestamp="2026-08-09T16:00:00Z", adjustment_basis="raw"),
        _price("price_split", price="110", timestamp="2026-08-09T16:10:00Z"),
    ]

    with pytest.raises(ValueError, match="adjustment_basis"):
        build_point_in_time_snapshot(**inputs)


def test_snapshot_expresses_missing_values_without_zero_fill() -> None:
    inputs = _snapshot_inputs()
    inputs["evidence"] = [
        _evidence(
            "ev_eps_missing",
            metric_id="eps",
            value=None,
            filed_at="2026-02-01",
            validation_status="unvalidated",
        )
    ]
    inputs["calculations"] = []
    inputs["prices"] = [_price("price_future", price="120", timestamp="2026-08-11T00:00:00Z")]

    snapshot = build_point_in_time_snapshot(**inputs)

    assert snapshot.financial_features["eps"] is None
    assert snapshot.market_features["price"] is None
    assert snapshot.data_quality["financial_status"] == "unavailable"
    assert snapshot.data_quality["market_status"] == "unavailable"
    assert snapshot.financial_features["eps"] != Decimal("0")


def test_snapshot_as_of_change_has_explainable_hash_and_value_diff() -> None:
    inputs = _snapshot_inputs()
    early = build_point_in_time_snapshot(**inputs)
    late = build_point_in_time_snapshot(
        **{**inputs, "as_of": datetime(2026, 8, 11, 1, tzinfo=timezone.utc)}
    )

    assert early.snapshot_id != late.snapshot_id
    assert early.financial_features["revenue"] != late.financial_features["revenue"]
    assert early.market_features["price"] != late.market_features["price"]
    assert "ev_revenue_future_q" in late.available_evidence_ids


def test_dataset_builds_one_snapshot_per_rebalance_date() -> None:
    evidence, calculations, prices = _inputs()
    universe = UniverseManifest.model_validate(
        {
            "universe_id": "us-large-cap-v1",
            "tickers": [TICKER],
            "selection_as_of": datetime(2026, 1, 2, tzinfo=timezone.utc),
            "membership_source": "fixture:universe",
            "membership_basis": "fixed_synthetic_membership",
            "known_biases": ["survivorship_bias_known"],
            "manifest_version": "universe:v1",
        }
    )

    snapshots = build_point_in_time_dataset(
        universe=universe,
        rebalance_dates=[
            datetime(2026, 8, 10, 0, 30, tzinfo=timezone.utc),
            datetime(2026, 8, 11, 1, tzinfo=timezone.utc),
        ],
        evidence_by_cik={CIK: evidence},
        calculations_by_cik={CIK: calculations},
        prices_by_ticker={TICKER: prices},
        builder_version="point-in-time:v1",
    )

    assert len(snapshots) == 2
    assert snapshots[0].cik == CIK
    assert snapshots[0].ticker == TICKER
    assert snapshots[0].available_evidence_ids == sorted(snapshots[0].available_evidence_ids)


def test_manifest_fixture_has_required_bias_disclosure() -> None:
    manifest = UniverseManifest.model_validate(
        json.loads(
            open("examples/universes/us-large-cap-v1.json", encoding="utf-8").read()
        )
    )
    assert manifest.tickers
    assert "survivorship_bias_known" in manifest.known_biases
