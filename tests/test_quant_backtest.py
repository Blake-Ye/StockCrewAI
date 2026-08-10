from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
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

    assert first.signal_as_of == datetime(2020, 2, 1, tzinfo=UTC)
    assert first.trade_date == date(2020, 2, 3)
    assert first.next_trade_date == date(2020, 3, 3)
    assert set(first.selected_tickers) == {f"T{index:02d}" for index in range(40, 50)}
    assert all(snapshot_id.startswith("future-reversal-") for snapshot_id in first.snapshot_ids)
    assert first.provenance["snapshot_id_by_ticker"] == {
        ticker: f"future-reversal-{ticker}" for ticker in first.scores
    }
    assert all(first.signal_as_of >= record_as_of for record_as_of in [first.signal_as_of])
    assert first.target_weights is not None
    assert first.target_weights["T00"] == ZERO
    assert first.target_weights["T40"] > ZERO


def test_anchor_day_snapshot_wins_without_reading_future_snapshot() -> None:
    UniverseManifest, SnapshotScore, _, _, WalkForwardInput, run = _api()
    universe, snapshots, periods, levels = _input_parts()
    anchor_as_of = datetime.combine(periods[0].rebalance_anchor, datetime.min.time(), UTC)
    anchor_high_tickers = {f"T{index:02d}" for index in range(40, 50)}
    snapshots = [
        snapshot for snapshot in snapshots if not snapshot.snapshot_id.startswith("future-reversal-")
    ]
    snapshots.extend(
        SnapshotScore(
            snapshot_id=f"anchor-day-{ticker}",
            ticker=ticker,
            as_of=anchor_as_of,
            filing_cutoff=anchor_as_of,
            price_cutoff=anchor_as_of,
            score=Decimal("100") if ticker in anchor_high_tickers else Decimal("1"),
            score_version="composite-ranking-v1",
        )
        for ticker in universe.tickers
    )
    future_as_of = anchor_as_of.replace(day=anchor_as_of.day + 1)
    snapshots.extend(
        SnapshotScore(
            snapshot_id=f"future-after-anchor-{ticker}",
            ticker=ticker,
            as_of=future_as_of,
            filing_cutoff=future_as_of,
            price_cutoff=future_as_of,
            score=Decimal("100") if ticker not in anchor_high_tickers else Decimal("1"),
            score_version="composite-ranking-v1",
        )
        for ticker in universe.tickers
    )

    result = run(WalkForwardInput(universe, tuple(snapshots), tuple(periods), tuple(levels)))
    first = result.periods[0]

    assert first.signal_as_of == anchor_as_of
    assert set(first.snapshot_ids) == {f"anchor-day-{ticker}" for ticker in universe.tickers}
    assert set(first.selected_tickers) == anchor_high_tickers
    assert all(not snapshot_id.startswith("future-reversal-") for snapshot_id in first.snapshot_ids)
    assert first.signal_as_of < datetime.combine(first.trade_date, datetime.min.time(), UTC)


def test_period_dates_are_strict_and_returns_start_at_trade_date() -> None:
    result = _build()

    for period in result.periods[:-1]:
        assert period.signal_as_of < datetime.combine(period.trade_date, datetime.min.time(), UTC)
        assert period.trade_date < period.next_trade_date
    assert result.periods[-1].status == "terminal"
    assert result.periods[-1].next_trade_date is None
    assert result.complete_period_count == 60
    assert result.baseline_summary.strategy["cagr"].status == "available"


def test_gross_return_uses_residual_target_weights() -> None:
    UniverseManifest, SnapshotScore, TotalReturnLevel, RebalanceSpec, WalkForwardInput, run = _api()
    tickers = tuple(f"W{index:02d}" for index in range(51))
    universe_data = dict(_fixture()["universe"])
    universe_data.update(
        {
            "universe_id": "weighted-universe-51",
            "tickers": list(tickers),
            "selection_as_of": "2023-12-31T00:00:00+00:00",
        }
    )
    universe = UniverseManifest.model_validate(universe_data)
    signal_as_of = datetime(2023, 12, 31, tzinfo=UTC)
    trade_date = date(2024, 1, 2)
    next_trade_date = date(2024, 2, 2)
    snapshots = tuple(
        SnapshotScore(
            snapshot_id=f"weighted-{ticker}",
            ticker=ticker,
            as_of=signal_as_of,
            filing_cutoff=signal_as_of,
            price_cutoff=signal_as_of,
            score=Decimal("100") if index < 11 else Decimal("1"),
            score_version="composite-ranking-v1",
        )
        for index, ticker in enumerate(tickers)
    )
    periods = (
        RebalanceSpec(
            rebalance_anchor=date(2024, 1, 1),
            trade_date=trade_date,
            next_trade_date=next_trade_date,
            period_id="weighted-period",
        ),
    )
    levels = tuple(
        level
        for ticker in ("SPY", *tickers)
        for level in (
            TotalReturnLevel(ticker=ticker, trade_date=trade_date, level=ONE),
            TotalReturnLevel(
                ticker=ticker,
                trade_date=next_trade_date,
                level=Decimal("100000000000000000001") if ticker == "W10" else Decimal("1.01"),
            ),
        )
    )

    period = run(WalkForwardInput(universe, snapshots, periods, levels)).periods[0]
    assert period.target_weights is not None
    assert len(period.selected_tickers) == 11
    with localcontext() as context:
        context.prec = 28
        expected = sum(
            (
                period.target_weights[ticker] * period.forward_returns[ticker]
                for ticker in period.selected_tickers
            ),
            ZERO,
        )
        simple_average = sum(
            (period.forward_returns[ticker] for ticker in period.selected_tickers),
            ZERO,
        ) / Decimal(len(period.selected_tickers))
    assert period.gross_return.value == expected
    assert period.gross_return.value != simple_average


@pytest.mark.parametrize("missing_ticker", ["T40", "SPY", "T20"])
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
    assert period.reason_code == "missing_next_period_return"
    for value in (period.gross_return, period.net_return, period.spy_return, period.universe_return):
        assert value.status == "unavailable"
        assert value.reason_code == "missing_next_period_return"
    assert period.spy_return.status == "unavailable" if missing_ticker == "SPY" else True
    assert period.coverage["strategy"].ratio < ONE if missing_ticker == "T40" else True
    missing_id = f"{missing_ticker}@{missing_trade.isoformat()}->{missing_next.isoformat()}"
    assert missing_id in period.missing_return_ids
    assert all("@" in missing_id and "->" in missing_id for missing_id in period.missing_return_ids)
    assert result.complete_period_count == sum(item.status == "available" for item in result.periods)
    assert result.complete_period_count == 58


def test_period_persists_baseline_cost_fields_and_keeps_cost_compatible() -> None:
    result = _build()
    universe, snapshots, periods, levels = _input_parts()
    _, _, _, _, WalkForwardInput, run = _api()
    no_snapshot = run(
        WalkForwardInput(universe, (), tuple(periods[:1]), tuple(levels))
    )
    missing_next = periods[0].next_trade_date
    assert missing_next is not None
    missing_levels = [
        level
        for level in levels
        if not (level.ticker == "T40" and level.trade_date == missing_next)
    ]
    missing = run(WalkForwardInput(universe, tuple(snapshots), tuple(periods), tuple(missing_levels)))

    for period in (result.periods[0], result.periods[-1], no_snapshot.periods[0], missing.periods[0]):
        assert period.round_trip_cost_bps == Decimal("10")
        assert period.cost_return == period.cost
        payload = period.to_dict()
        assert payload["round_trip_cost_bps"] == "10"
        assert payload["cost_return"] == payload["cost"]


def test_baseline_summary_has_complete_period_quintile_aggregates() -> None:
    result = _build()
    summary = result.baseline_summary

    assert set(summary.quintile_aggregates) == {f"Q{index}" for index in range(1, 6)}
    for name, metrics in summary.quintile_aggregates.items():
        assert set(metrics) == {"average_return", "cagr"}
        assert metrics["average_return"].status == "available"
        assert metrics["cagr"].status == "available"
        assert metrics["average_return"].value is not None
        values = tuple(item.value for item in summary.quintile_returns[name])
        assert all(value is not None for value in values)
        with localcontext() as context:
            context.prec = 28
            expected_average = sum((value for value in values if value is not None), ZERO) / Decimal(len(values))
        assert metrics["average_return"].value == expected_average
        assert metrics["cagr"].value is not None

    UniverseManifest, _, _, _, WalkForwardInput, run = _api()
    universe, snapshots, periods, levels = _input_parts()
    no_complete_result = run(
        WalkForwardInput(universe, (), tuple(periods[:1]), tuple(levels))
    )
    no_complete = no_complete_result.baseline_summary.quintile_aggregates
    assert set(no_complete) == {f"Q{index}" for index in range(1, 6)}
    assert all(
        metric.status == "unavailable" and metric.reason_code == "no_complete_periods"
        for metrics in no_complete.values()
        for metric in metrics.values()
    )


def test_snapshot_score_rejects_non_composite_version() -> None:
    from stockcrewai.quant.backtest import SnapshotScore

    with pytest.raises(ValueError, match="score_version"):
        SnapshotScore(
            snapshot_id="invalid-version",
            ticker="T00",
            as_of=datetime(2019, 12, 31, tzinfo=UTC),
            score=Decimal("1"),
            score_version="legacy-ranking-v0",
        )


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
        WalkForwardInput(universe, (), tuple(periods[:1]), tuple(levels))
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
