from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from stockcrewai.quant.cli import (
    SECDataCollectionError,
    build_parser,
    main,
    run,
)
from stockcrewai.services.market_data import (
    MarketDataCollectionError,
    MarketDataValidationError,
    collect_market_record,
    normalize_market_price_record,
)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _evidence(value: str = "110") -> dict[str, object]:
    return {
        "evidence_id": "ev_revenue",
        "metric_id": "revenue",
        "source_reference": "fixture:sec:revenue",
        "as_of": "2026-03-01T12:00:00Z",
        "filed_at": "2026-03-01",
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "unit": "USD",
        "currency": "USD",
        "value": value,
        "validation_status": "valid",
        "form": "10-K/A",
        "cik": "0000320193",
    }


def _calculation() -> dict[str, object]:
    return {
        "calculation_id": "calc_margin",
        "metric_id": "operating_margin",
        "formula_id": "operating_margin:v1",
        "input_evidence_ids": ["ev_revenue"],
        "source_reference": "fixture:calc:margin",
        "as_of": "2026-03-02T12:00:00Z",
        "result": "0.20",
        "unit": "ratio",
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "validation_status": "valid",
        "cik": "0000320193",
    }


def _price() -> dict[str, object]:
    return {
        "evidence_id": "price_at_cutoff",
        "ticker": "AAPL",
        "price": "110.125",
        "currency": "USD",
        "price_timestamp": "2026-08-09T16:30:00Z",
        "source_reference": "fixture:market:price",
        "adjustment_basis": "split_adjusted",
        "validation_status": "valid",
    }


def _universe() -> dict[str, object]:
    return {
        "universe_id": "fixture-universe",
        "tickers": ["AAPL"],
        "selection_as_of": "2026-01-02T00:00:00Z",
        "membership_source": "fixture:universe",
        "membership_basis": "fixed_synthetic_membership",
        "known_biases": ["survivorship_bias_known"],
        "manifest_version": "universe:v1",
        "cik_by_ticker": {"AAPL": "0000320193"},
    }


def test_parser_help_lists_all_explicit_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit) as raised:
        parser.parse_args(["--help"])

    assert raised.value.code == 0
    help_text = capsys.readouterr().out
    assert "collect-sec" in help_text
    assert "collect-market" in help_text
    assert "build" in help_text


def test_collect_sec_normalizes_decimal_records_to_local_json(tmp_path: Path) -> None:
    input_path = tmp_path / "sec-input.json"
    output_path = tmp_path / "sec-normalized.json"
    _write_json(
        input_path,
        {
            "cik": "0000320193",
            "ticker": "AAPL",
            "evidence": [_evidence()],
            "calculations": [_calculation()],
        },
    )

    assert main(["collect-sec", "--input", str(input_path), "--output", str(output_path)]) == 0

    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert output["command"] == "collect-sec"
    assert output["status"] == "ok"
    assert output["evidence"][0]["value"] == "110"
    assert output["calculations"][0]["result"] == "0.20"
    assert output["evidence"][0]["metric_id"] == "revenue"


def test_collect_market_rejects_missing_fields_and_invalid_decimal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    input_path = tmp_path / "market-input.json"
    output_path = tmp_path / "market-normalized.json"
    payload = _price()
    del payload["ticker"]
    payload["price"] = "not-a-decimal"
    _write_json(input_path, {"records": [payload]})

    result = main(
        ["collect-market", "--input", str(input_path), "--output", str(output_path)]
    )

    assert result != 0
    error = json.loads(capsys.readouterr().err)
    assert error["error_type"] == "MarketDataValidationError"
    assert error["reason_code"] == "market_record_invalid"
    assert not output_path.exists()


@pytest.mark.parametrize("missing", ["ticker", "price", "price_timestamp", "source_reference"])
def test_collect_market_rejects_each_required_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], missing: str
) -> None:
    input_path = tmp_path / f"market-{missing}-input.json"
    output_path = tmp_path / f"market-{missing}-output.json"
    payload = _price()
    del payload[missing]
    _write_json(input_path, {"records": [payload]})

    assert main(["collect-market", "--input", str(input_path), "--output", str(output_path)]) != 0

    error = json.loads(capsys.readouterr().err)
    assert error["error_type"] == "MarketDataValidationError"
    assert error["reason_code"] == "market_record_invalid"


def test_build_writes_local_storage_metadata_with_provenance_ids(tmp_path: Path) -> None:
    universe_path = tmp_path / "universe.json"
    evidence_path = tmp_path / "evidence.json"
    calculations_path = tmp_path / "calculations.json"
    prices_path = tmp_path / "prices.json"
    artifact_root = tmp_path / "artifacts"
    _write_json(universe_path, _universe())
    _write_json(evidence_path, {"cik": "0000320193", "records": [_evidence()]})
    _write_json(
        calculations_path,
        {"cik": "0000320193", "records": [_calculation()]},
    )
    _write_json(prices_path, {"ticker": "AAPL", "records": [_price()]})

    assert (
        main(
            [
                "build",
                "--universe",
                str(universe_path),
                "--evidence",
                str(evidence_path),
                "--calculations",
                str(calculations_path),
                "--prices",
                str(prices_path),
                "--artifact-root",
                str(artifact_root),
                "--as-of",
                "2026-08-10T00:00:00Z",
            ]
        )
        == 0
    )

    output = json.loads((artifact_root / "metadata.json").read_text(encoding="utf-8"))
    assert output["artifacts"]["snapshots"]["row_count"] == 1
    assert output["artifacts"]["snapshots"]["evidence_ids"] == [
        "ev_revenue",
        "price_at_cutoff",
    ]
    assert output["artifacts"]["snapshots"]["calculation_ids"] == ["calc_margin"]


def test_default_market_command_does_not_import_or_call_network_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "market-input.json"
    output_path = tmp_path / "market-normalized.json"
    _write_json(input_path, {"records": [_price()]})

    def fail_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("default CLI must not create a network client")

    monkeypatch.setattr("importlib.import_module", fail_network)

    assert main(["collect-market", "--input", str(input_path), "--output", str(output_path)]) == 0


def test_sec_collector_failure_is_typed_and_does_not_call_market_collector(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "sec-request.json"
    output_path = tmp_path / "sec-output.json"
    _write_json(input_path, {"records": []})
    market_called = False

    def fail_sec(_: object) -> object:
        raise RuntimeError("sec collector failed")

    def market_collector(_: object) -> object:
        nonlocal market_called
        market_called = True
        return {}

    with pytest.raises(SECDataCollectionError):
        run(
            ["collect-sec", "--input", str(input_path), "--output", str(output_path)],
            sec_collector=fail_sec,
            market_collector=market_collector,
        )

    assert not market_called
    assert not output_path.exists()


def test_market_collector_failure_is_typed_without_sec_fallback(tmp_path: Path) -> None:
    input_path = tmp_path / "market-request.json"
    output_path = tmp_path / "market-output.json"
    _write_json(input_path, {"ticker": "AAPL"})
    sec_called = False

    def fail_market(_: object) -> object:
        raise RuntimeError("market collector failed")

    def sec_collector(_: object) -> object:
        nonlocal sec_called
        sec_called = True
        return {}

    with pytest.raises(MarketDataCollectionError):
        run(
            ["collect-market", "--input", str(input_path), "--output", str(output_path)],
            sec_collector=sec_collector,
            market_collector=fail_market,
        )

    assert not sec_called
    assert not output_path.exists()


def test_build_requires_explicit_as_of_and_returns_json_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = {
        "--universe": tmp_path / "universe.json",
        "--evidence": tmp_path / "evidence.json",
        "--calculations": tmp_path / "calculations.json",
        "--prices": tmp_path / "prices.json",
    }
    _write_json(paths["--universe"], _universe())
    _write_json(paths["--evidence"], {"cik": "0000320193", "records": [_evidence()]})
    _write_json(paths["--calculations"], {"cik": "0000320193", "records": []})
    _write_json(paths["--prices"], {"ticker": "AAPL", "records": [_price()]})

    args = ["build"]
    for flag, path in paths.items():
        args.extend([flag, str(path)])
    args.extend(["--artifact-root", str(tmp_path / "artifacts")])

    assert main(args) != 0
    error = json.loads(capsys.readouterr().err)
    assert error["reason_code"] == "as_of_required"


def test_market_boundary_preserves_decimal_and_wraps_fetcher_failures() -> None:
    record = normalize_market_price_record(_price())
    assert record.price == Decimal("110.125")
    assert record.price_timestamp == datetime(2026, 8, 9, 16, 30, tzinfo=timezone.utc)

    with pytest.raises(MarketDataValidationError):
        normalize_market_price_record({**_price(), "source_reference": ""})

    with pytest.raises(MarketDataCollectionError):
        collect_market_record(lambda _: (_ for _ in ()).throw(RuntimeError("no network")), "AAPL")
