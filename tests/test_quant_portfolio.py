from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "quant" / "backtest" / "portfolio.json"
ZERO = Decimal("0")
ONE = Decimal("1")


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _portfolio_api() -> tuple[Any, Any, Any, Any, Any]:
    try:
        from stockcrewai.quant.portfolio import (
            BASELINE_ROUND_TRIP_COST_BPS,
            ROUND_TRIP_COST_SENSITIVITY_BPS,
            PortfolioResult,
            build_target_portfolio,
            calculate_cost_and_net_return,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.fail(f"portfolio engine is not implemented: {exc}", pytrace=False)
    return (
        PortfolioResult,
        build_target_portfolio,
        calculate_cost_and_net_return,
        BASELINE_ROUND_TRIP_COST_BPS,
        ROUND_TRIP_COST_SENSITIVITY_BPS,
    )


def _case(name: str) -> dict[str, Any]:
    return _fixture()["cases"][name]


def _previous_weights(tickers: list[str]) -> dict[str, Decimal]:
    return {**{ticker: ZERO for ticker in tickers}, "CASH": ONE}


def _build_case(name: str) -> Any:
    _, build, _, _, _ = _portfolio_api()
    case = _case(name)
    tickers = case["tickers"]
    scores = {ticker: Decimal(case["uniform_score"]) for ticker in tickers}
    return build(
        tickers,
        datetime.fromisoformat(_fixture()["signal_as_of"]),
        scores,
        _previous_weights(tickers),
    )


def test_ceil_top_twenty_percent_for_50_and_51_and_exact_decimal_weights() -> None:
    for name in ("universe_50", "universe_51"):
        result = _build_case(name)
        case = _case(name)
        tickers = case["tickers"]
        expected_count = case["expected_selected_count"]

        assert result.eligible_count == case["expected_eligible_count"]
        assert len(result.selected_tickers) == expected_count
        assert result.selected_tickers == tuple(sorted(tickers)[:expected_count])
        assert result.target_weights is not None
        assert set(result.target_weights) == {*tickers, "CASH"}
        assert all(isinstance(value, Decimal) and value.is_finite() for value in result.target_weights.values())
        assert sum(result.target_weights.values(), ZERO) == ONE

        with localcontext() as context:
            context.prec = 28
            base_weight = ONE / Decimal(expected_count)
            for ticker in result.selected_tickers[:-1]:
                assert result.target_weights[ticker] == base_weight
            final_ticker = result.selected_tickers[-1]
            manual_residual = ONE - sum(
                (base_weight for _ in result.selected_tickers[:-1]), ZERO
            )
        assert result.target_weights[final_ticker] == manual_residual
        assert result.target_weights["CASH"] == ZERO


def test_score_sort_and_result_hash_ignore_universe_and_mapping_input_order() -> None:
    _, build, _, _, _ = _portfolio_api()
    tickers = _case("universe_50")["tickers"]
    scores = {ticker: Decimal("1") for ticker in tickers}
    scores["T48"] = Decimal("2")
    scores["T49"] = Decimal("2")
    previous = _previous_weights(tickers)
    signal_as_of = datetime.fromisoformat(_fixture()["signal_as_of"])

    first = build(tickers, signal_as_of, scores, previous)
    second = build(
        list(reversed(tickers)),
        signal_as_of,
        dict(reversed(list(scores.items()))),
        dict(reversed(list(previous.items()))),
    )

    assert first.selected_tickers[:2] == ("T48", "T49")
    assert first.to_dict() == second.to_dict()
    assert first.stable_hash == second.stable_hash
    assert first.stable_hash == hashlib.sha256(first.to_json().encode("utf-8")).hexdigest()


def test_initial_cash_turnover_matches_independent_decimal_hand_calculation() -> None:
    result = _build_case("universe_50")
    assert result.target_weights is not None
    assert result.turnover is not None

    manual_half_l1 = sum(
        (abs(result.target_weights[ticker] - ZERO) for ticker in result.target_weights if ticker != "CASH"),
        ZERO,
    ) + abs(result.target_weights["CASH"] - ONE)
    manual_turnover = Decimal("0.5") * manual_half_l1
    assert result.turnover == manual_turnover == ONE


def test_unavailable_empty_scores_has_typed_outcome_without_fabricated_weights() -> None:
    _, build, _, _, _ = _portfolio_api()
    tickers = _case("universe_50")["tickers"]
    previous = _previous_weights(tickers)

    result = build(
        tickers,
        datetime.fromisoformat(_fixture()["signal_as_of"]),
        {},
        previous,
    )

    assert result.status == "unavailable"
    assert result.reason_code == "no_eligible_scores"
    assert result.eligible_count == 0
    assert result.selected_tickers == ()
    assert result.target_weights is None
    assert result.turnover is None
    assert result.previous_weights == previous


@pytest.mark.parametrize(
    "tickers",
    [
        [f"T{index:02d}" for index in range(49)],
        [f"T{index:02d}" for index in range(101)],
        [*(_case("universe_50")["tickers"][:-1]), _case("universe_50")["tickers"][0]],
    ],
)
def test_universe_must_have_50_to_100_unique_tickers(tickers: list[str]) -> None:
    _, build, _, _, _ = _portfolio_api()
    with pytest.raises(ValueError):
        build(
            tickers,
            datetime.fromisoformat(_fixture()["signal_as_of"]),
            {ticker: Decimal("1") for ticker in set(tickers)},
            _previous_weights(list(dict.fromkeys(tickers))),
        )


def test_missing_previous_ticker_is_rejected_instead_of_filled() -> None:
    _, build, _, _, _ = _portfolio_api()
    tickers = _case("universe_50")["tickers"]
    previous = _previous_weights(tickers)
    previous.pop(tickers[0])

    with pytest.raises(ValueError):
        build(
            tickers,
            datetime.fromisoformat(_fixture()["signal_as_of"]),
            {ticker: Decimal("1") for ticker in tickers},
            previous,
        )


@pytest.mark.parametrize("bad_score", [1.0, Decimal("NaN"), Decimal("Infinity")])
def test_scores_must_be_finite_decimal_values(bad_score: object) -> None:
    _, build, _, _, _ = _portfolio_api()
    tickers = _case("universe_50")["tickers"]
    scores: dict[str, object] = {ticker: Decimal("1") for ticker in tickers}
    scores[tickers[0]] = bad_score

    with pytest.raises(ValueError):
        build(
            tickers,
            datetime.fromisoformat(_fixture()["signal_as_of"]),
            scores,
            _previous_weights(tickers),
        )


def test_cost_formula_is_decimal_monotonic_and_matches_hand_fixture() -> None:
    _, _, calculate, baseline_bps, sensitivity_bps = _portfolio_api()
    fixture = _fixture()["cost_case"]
    gross_return = Decimal(fixture["gross_return"])
    turnover = Decimal(fixture["turnover"])
    expected_bps = tuple(Decimal(value) for value in fixture["expected_by_bps"])

    assert baseline_bps == Decimal("10")
    assert sensitivity_bps == (Decimal("0"), Decimal("5"), Decimal("10"), Decimal("20"))
    assert sensitivity_bps == expected_bps

    costs: list[Decimal] = []
    nets: list[Decimal] = []
    for bps in sensitivity_bps:
        cost_return, net_return = calculate(gross_return, turnover, bps)
        expected = fixture["expected_by_bps"][str(bps)]
        manual_cost = turnover * bps / Decimal("10000")
        manual_net = gross_return - manual_cost
        assert cost_return == manual_cost == Decimal(expected["cost_return"])
        assert net_return == manual_net == Decimal(expected["net_return"])
        costs.append(cost_return)
        nets.append(net_return)

    assert costs == sorted(costs)
    assert nets == sorted(nets, reverse=True)


@pytest.mark.parametrize(
    "gross_return, turnover, bps",
    [
        (1.0, Decimal("0.1"), Decimal("10")),
        (Decimal("0.1"), 0.1, Decimal("10")),
        (Decimal("0.1"), Decimal("0.1"), -Decimal("1")),
        (Decimal("0.1"), Decimal("0.1"), Decimal("NaN")),
        (Decimal("0.1"), Decimal("0.1"), Decimal("Infinity")),
    ],
)
def test_cost_rejects_float_nonfinite_or_negative_inputs(
    gross_return: object, turnover: object, bps: object
) -> None:
    _, _, calculate, _, _ = _portfolio_api()
    with pytest.raises(ValueError):
        calculate(gross_return, turnover, bps)


def test_result_is_immutable() -> None:
    _, _, _, _, _ = _portfolio_api()
    result = _build_case("universe_50")

    with pytest.raises(FrozenInstanceError):
        result.status = "unavailable"
    assert result.target_weights is not None
    with pytest.raises(TypeError):
        result.target_weights["T00"] = ZERO
