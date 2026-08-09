from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

from stockcrewai.models.quant import UniverseManifest
from stockcrewai.quant.dataset import build_point_in_time_dataset


FIXTURE_DIR = Path("tests/fixtures/quant/point_in_time")


def _load(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_offline_fixture_has_financial_calculation_and_price_provenance() -> None:
    financial = _load("financial.json")
    calculations = _load("calculations.json")
    prices = _load("prices.json")

    assert financial["fixture_version"] == "point-in-time-fixture:v1"
    assert financial["records"][0]["source_reference"].startswith("fixture:")  # type: ignore[index]
    assert financial["records"][0]["filed_at"]  # type: ignore[index]
    assert calculations["records"][0]["calculation_id"]  # type: ignore[index]
    assert prices["records"][0]["price_timestamp"]  # type: ignore[index]
    assert isinstance(prices["records"][0]["price"], str)  # type: ignore[index]


def test_dataset_is_order_independent_for_fixture_records() -> None:
    manifest = UniverseManifest.model_validate(
        json.loads(Path("examples/universes/us-large-cap-v1.json").read_text(encoding="utf-8"))
    )
    financial = _load("financial.json")
    calculations = _load("calculations.json")
    prices = _load("prices.json")
    evidence = [
        {**record, "ticker": financial["ticker"]}
        for record in financial["records"]  # type: ignore[index]
    ]
    rebalance_dates = [datetime(2026, 8, 10, 12, tzinfo=timezone.utc)]

    first = build_point_in_time_dataset(
        universe=manifest,
        rebalance_dates=rebalance_dates,
        evidence_by_cik={"0000320193": evidence},  # type: ignore[dict-item]
        calculations_by_cik={"0000320193": calculations["records"]},  # type: ignore[dict-item]
        prices_by_ticker={"AAPL": prices["records"]},  # type: ignore[dict-item]
        builder_version="point-in-time:v1",
    )
    second = build_point_in_time_dataset(
        universe=manifest,
        rebalance_dates=rebalance_dates,
        evidence_by_cik={"0000320193": list(reversed(evidence))},  # type: ignore[arg-type]
        calculations_by_cik={"0000320193": list(reversed(calculations["records"]))},  # type: ignore[arg-type]
        prices_by_ticker={"AAPL": list(reversed(prices["records"]))},  # type: ignore[arg-type]
        builder_version="point-in-time:v1",
    )

    assert [item.snapshot_id for item in first] == [item.snapshot_id for item in second]
    assert [item.ticker for item in first] == ["AAPL"]
    assert first[0].financial_features["revenue"] == Decimal("110")


def test_dataset_skips_ticker_without_provable_cik() -> None:
    manifest = UniverseManifest.model_validate(
        json.loads(Path("examples/universes/us-large-cap-v1.json").read_text(encoding="utf-8"))
    )
    financial = _load("financial.json")
    calculations = _load("calculations.json")
    prices = _load("prices.json")
    evidence = [
        {**record, "ticker": financial["ticker"]}
        for record in financial["records"]  # type: ignore[index]
    ]

    snapshots = build_point_in_time_dataset(
        universe=manifest,
        rebalance_dates=[datetime(2026, 8, 10, 12, tzinfo=timezone.utc)],
        evidence_by_cik={"0000320193": evidence},  # type: ignore[dict-item]
        calculations_by_cik={"0000320193": calculations["records"]},  # type: ignore[dict-item]
        prices_by_ticker={"AAPL": prices["records"]},  # type: ignore[dict-item]
        builder_version="point-in-time:v1",
    )

    assert [snapshot.ticker for snapshot in snapshots] == ["AAPL"]
    assert snapshots[0].financial_features["revenue"] == Decimal("110")


def test_dataset_deduplicates_unsorted_timezone_equivalent_rebalance_dates() -> None:
    manifest = UniverseManifest.model_validate(
        json.loads(Path("examples/universes/us-large-cap-v1.json").read_text(encoding="utf-8"))
    )
    financial = _load("financial.json")
    calculations = _load("calculations.json")
    prices = _load("prices.json")
    evidence = [
        {**record, "ticker": financial["ticker"]}
        for record in financial["records"]  # type: ignore[index]
    ]
    unique_sorted_dates = [
        datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
        datetime(2026, 8, 11, 1, tzinfo=timezone.utc),
    ]
    duplicate_unsorted_dates = [
        datetime(2026, 8, 11, 9, tzinfo=timezone(timedelta(hours=8))),
        unique_sorted_dates[0],
        datetime(2026, 8, 10, 20, tzinfo=timezone(timedelta(hours=8))),
        unique_sorted_dates[1],
    ]

    def build(dates: list[datetime]) -> tuple:
        return build_point_in_time_dataset(
            universe=manifest,
            rebalance_dates=dates,
            evidence_by_cik={"0000320193": evidence},  # type: ignore[dict-item]
            calculations_by_cik={"0000320193": calculations["records"]},  # type: ignore[dict-item]
            prices_by_ticker={"AAPL": prices["records"]},  # type: ignore[dict-item]
            builder_version="point-in-time:v1",
        )

    expected = build(unique_sorted_dates)
    actual = build(duplicate_unsorted_dates)

    assert [item.snapshot_id for item in actual] == [item.snapshot_id for item in expected]
    assert len(actual) == len(expected) == 2
