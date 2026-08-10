from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "quant" / "backtest" / "backtest.json"
UTC = timezone.utc
ZERO = Decimal("0")
ONE = Decimal("1")


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _api() -> Any:
    try:
        from stockcrewai.quant.backtest import (
            RebalanceSpec,
            SnapshotScore,
            TotalReturnLevel,
            WalkForwardInput,
            run_walk_forward,
        )
        from stockcrewai.models.quant import UniverseManifest
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.fail(f"backtest engine is not implemented: {exc}", pytrace=False)
    return (
        UniverseManifest,
        SnapshotScore,
        TotalReturnLevel,
        RebalanceSpec,
        WalkForwardInput,
        run_walk_forward,
    )


def _month_start(value: date, offset: int) -> date:
    month = value.month - 1 + offset
    return date(value.year + month // 12, month % 12 + 1, 1)


def _period_dates() -> list[tuple[date, date, date | None]]:
    config = _fixture()["periods"]
    first_trade = date.fromisoformat(config["start_trade_date"])
    trades = [_month_start(first_trade, index).replace(day=3) for index in range(config["count"])]
    return [
        (
            _month_start(first_trade, index).replace(day=1),
            trade,
            trades[index + 1] if index + 1 < len(trades) else None,
        )
        for index, trade in enumerate(trades)
    ]


def _input_parts() -> tuple[Any, list[Any], list[Any], list[Any]]:
    UniverseManifest, SnapshotScore, TotalReturnLevel, RebalanceSpec, _, _ = _api()
    fixture = _fixture()
    universe = UniverseManifest.model_validate(fixture["universe"])
    snapshots: list[Any] = []
    for snapshot in fixture["snapshots"]:
        high_tickers = set(snapshot["high_tickers"])
        for ticker in universe.tickers:
            snapshots.append(
                SnapshotScore(
                    snapshot_id=f"{snapshot['snapshot_id_prefix']}-{ticker}",
                    ticker=ticker,
                    as_of=datetime.fromisoformat(snapshot["as_of"]),
                    filing_cutoff=datetime.fromisoformat(snapshot["filing_cutoff"]),
                    price_cutoff=datetime.fromisoformat(snapshot["price_cutoff"]),
                    score=Decimal(snapshot["high_score"] if ticker in high_tickers else snapshot["low_score"]),
                    score_version=snapshot["score_version"],
                )
            )

    periods = [
        RebalanceSpec(
            rebalance_anchor=anchor,
            trade_date=trade,
            next_trade_date=next_trade,
            period_id=f"P{index:03d}",
        )
        for index, (anchor, trade, next_trade) in enumerate(_period_dates())
    ]

    returns = fixture["levels"]["monthly_returns"]
    levels: list[Any] = []
    level_by_ticker = {ticker: Decimal(fixture["levels"]["initial"]) for ticker in ["SPY", *universe.tickers]}
    for _, trade, _ in periods:
        for ticker in ["SPY", *universe.tickers]:
            levels.append(TotalReturnLevel(ticker=ticker, trade_date=trade, level=level_by_ticker[ticker]))
        for ticker in level_by_ticker:
            level_by_ticker[ticker] *= ONE + Decimal(returns.get(ticker, returns["default"]))

    return universe, snapshots, periods, levels


def _build() -> Any:
    _, _, _, _, WalkForwardInput, run = _api()
    universe, snapshots, periods, levels = _input_parts()
    return run(WalkForwardInput(universe, tuple(snapshots), tuple(periods), tuple(levels)))


def test_t0_uses_only_shared_non_future_snapshot_and_target_weights() -> None:
    result = _build()
    first = result.periods[0]

    assert first.signal_as_of == datetime(2019, 12, 31, tzinfo=UTC)
    assert first.trade_date == date(2020, 2, 3)
    assert first.next_trade_date == date(2020, 3, 3)
    assert set(first.selected_tickers) == {f"T{index:02d}" for index in range(10)}
    assert all(snapshot_id.startswith("baseline-") for snapshot_id in first.snapshot_ids)
    assert first.provenance["snapshot_id_by_ticker"] == {
        ticker: f"baseline-{ticker}" for ticker in first.scores
    }
    assert all(first.signal_as_of >= record_as_of for record_as_of in [first.signal_as_of])
    assert first.target_weights is not None
    assert first.target_weights["T00"] > ZERO
    assert first.target_weights["T40"] == ZERO


def test_period_dates_are_strict_and_returns_start_at_trade_date() -> None:
    result = _build()

    for period in result.periods[:-1]:
        assert period.signal_as_of < datetime.combine(period.trade_date, datetime.min.time(), UTC)
        assert period.trade_date < period.next_trade_date
    assert result.periods[-1].status == "terminal"
    assert result.periods[-1].next_trade_date is None
    assert result.complete_period_count == 60
    assert result.baseline_summary.strategy["cagr"].status == "available"


@pytest.mark.parametrize("missing_ticker", ["T00", "SPY", "T20"])
def test_missing_strategy_spy_or_universe_return_is_typed_unavailable(
    missing_ticker: str,
) -> None:
    UniverseManifest, SnapshotScore, TotalReturnLevel, RebalanceSpec, WalkForwardInput, run = _api()
    universe, snapshots, periods, levels = _input_parts()
    missing_trade = periods[0].trade_date
    missing_next = periods[0].next_trade_date
    assert missing_next is not None
    levels = [
        level
        for level in levels
        if not (level.ticker == missing_ticker and level.trade_date in {missing_trade, missing_next})
    ]

    result = run(WalkForwardInput(universe, tuple(snapshots), tuple(periods), tuple(levels)))
    period = result.periods[0]

    assert period.status == "unavailable"
    assert period.gross_return.status == "unavailable"
    assert period.net_return.status == "unavailable"
    assert period.spy_return.status == "unavailable" if missing_ticker == "SPY" else True
    assert period.coverage["strategy"].ratio < ONE if missing_ticker == "T00" else True
    missing_id = f"{missing_ticker}@{missing_trade.isoformat()}->{missing_next.isoformat()}"
    assert missing_id in period.missing_return_ids
    assert all("@" in missing_id and "->" in missing_id for missing_id in period.missing_return_ids)
    assert result.complete_period_count == sum(item.status == "available" for item in result.periods)
    assert result.complete_period_count == 58


def test_cost_sensitivity_reuses_selection_and_does_not_cost_benchmarks() -> None:
    result = _build()
    sensitivity = result.cost_sensitivity
    bps = tuple(sensitivity)

    assert bps == (Decimal("0"), Decimal("5"), Decimal("10"), Decimal("20"))
    assert result.baseline_summary.net_cost_bps == Decimal("10")
    for period_index in range(result.complete_period_count):
        nets = [sensitivity[cost].net_returns[period_index].value for cost in bps]
        assert nets == sorted(nets, reverse=True)
    assert sensitivity[Decimal("0")].cagr.value > sensitivity[Decimal("20")].cagr.value
    assert result.periods[0].spy_return.value == sensitivity[Decimal("0")].benchmark_returns[0].value
    assert sensitivity[Decimal("0")].benchmark_returns == sensitivity[Decimal("20")].benchmark_returns


def test_survivorship_disclosure_input_order_and_hash_are_stable() -> None:
    result = _build()
    universe, snapshots, periods, levels = _input_parts()
    _, _, _, _, WalkForwardInput, run = _api()
    shuffled = run(
        WalkForwardInput(universe, tuple(reversed(snapshots)), tuple(reversed(periods)), tuple(reversed(levels)))
    )

    assert result.data_quality["survivorship_bias_known"] is True
    assert "survivorship_bias_known" in result.known_biases
    assert result.to_dict() == shuffled.to_dict()
    assert result.stable_hash == shuffled.stable_hash
    assert result.stable_hash == hashlib.sha256(result.to_json().encode("utf-8")).hexdigest()
    json.dumps(result.to_dict(), allow_nan=False, sort_keys=True)
    assert "NaN" not in result.to_json()
    assert "Infinity" not in result.to_json()


def test_terminal_no_snapshot_and_insufficient_history_are_typed() -> None:
    result = _build()
    assert result.periods[-1].reason_code == "no_next_trade_date"

    UniverseManifest, SnapshotScore, TotalReturnLevel, RebalanceSpec, WalkForwardInput, run = _api()
    universe, snapshots, periods, levels = _input_parts()
    no_snapshot = run(
        WalkForwardInput(universe, tuple(snapshots[50:]), tuple(periods[:1]), tuple(levels))
    )
    assert no_snapshot.periods[0].status == "unavailable"
    assert no_snapshot.periods[0].reason_code == "no_signal_snapshot"

    short = run(WalkForwardInput(universe, tuple(snapshots), tuple(periods[:5]), tuple(levels)))
    assert short.baseline_summary.strategy["cagr"].reason_code == "insufficient_history"


def test_backtest_artifact_is_stable_and_validates_output_path(tmp_path: Path) -> None:
    from stockcrewai.quant.backtest import write_backtest_artifact

    result = _build()
    output_path = tmp_path / "nested" / "backtest.json"
    written = write_backtest_artifact(result, output_path)
    first_bytes = written.read_bytes()
    second_path = write_backtest_artifact(result, tmp_path / "nested" / "repeat.json")
    second_bytes = second_path.read_bytes()
    payload = json.loads(first_bytes.decode("utf-8"))

    assert written == output_path
    assert first_bytes == second_bytes
    assert payload["artifact_hash"] == result.stable_hash
    assert {key: payload[key] for key in result.to_dict()} == result.to_dict()
    assert first_bytes.endswith(b"\n")
    assert b"NaN" not in first_bytes and b"Infinity" not in first_bytes

    with pytest.raises(ValueError):
        write_backtest_artifact(result, "")
    with pytest.raises(ValueError):
        write_backtest_artifact(result, tmp_path)
    with pytest.raises(ValueError):
        write_backtest_artifact(object(), output_path)
