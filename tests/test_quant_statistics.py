from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "quant" / "backtest" / "statistics.json"
TOLERANCE = Decimal("1e-12")


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _case(name: str) -> dict[str, Any]:
    return _fixture()["cases"][name]


def _decimal_series(values: list[str]) -> list[Decimal]:
    return [Decimal(value) for value in values]


def _decimal_mapping(values: dict[str, str]) -> dict[str, Decimal]:
    return {key: Decimal(value) for key, value in values.items()}


def _api() -> Any:
    try:
        from stockcrewai.quant import statistics
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.fail(f"statistics engine is not implemented: {exc}", pytrace=False)
    return statistics


def _assert_result(result: Any, expected_value: str | None, status: str, reason_code: str) -> None:
    assert result.value is None if expected_value is None else isinstance(result.value, Decimal)
    if expected_value is None:
        assert result.value is None
    else:
        assert result.value is not None
        assert abs(result.value - Decimal(expected_value)) <= TOLERANCE
        assert result.value.is_finite()
    assert result.status == status
    assert result.reason_code == reason_code

    payload = result.to_dict()
    if expected_value is None:
        assert payload["value"] is None
    else:
        assert abs(Decimal(payload["value"]) - Decimal(expected_value)) <= TOLERANCE
    assert payload["periods_per_year"] == _fixture()["protocol"]["periods_per_year"]
    assert payload["conversion_version"] == _fixture()["protocol"]["conversion_version"]
    assert Decimal(payload["tolerance"]) == Decimal(_fixture()["protocol"]["tolerance"])
    json.dumps(payload, allow_nan=False, sort_keys=True)
    assert "NaN" not in result.to_json()
    assert "Infinity" not in result.to_json()


def test_cagr_matches_independent_decimal_fixture_and_is_order_independent() -> None:
    stats = _api()
    case = _case("return_series")
    returns = _decimal_series(case["returns"])

    first = stats.cagr(returns)
    second = stats.cagr(list(reversed(returns)))

    _assert_result(first, case["expected_cagr"], "available", "computed")
    assert first.to_dict() == second.to_dict()
    assert first.statistic_version == _fixture()["protocol"]["statistics_version"]


def test_cagr_marks_short_history_and_non_positive_cumulative_factor() -> None:
    stats = _api()
    case = _case("return_series")

    _assert_result(
        stats.cagr(_decimal_series(case["short_returns"])),
        None,
        "unavailable",
        "insufficient_history",
    )
    _assert_result(
        stats.cagr(_decimal_series(case["invalid_factor_returns"])),
        None,
        "unavailable",
        "invalid_return_factor",
    )


def test_volatility_and_sharpe_use_sample_std_and_zero_risk_free_version() -> None:
    stats = _api()
    case = _case("volatility_and_sharpe")
    returns = _decimal_series(case["returns"])

    _assert_result(
        stats.annualized_volatility(returns),
        case["expected_annualized_volatility"],
        "available",
        "computed",
    )
    sharpe = stats.sharpe_ratio(returns)
    _assert_result(sharpe, case["expected_sharpe"], "available", "computed")
    assert sharpe.statistic_version == _fixture()["protocol"]["sharpe_version"]


def test_zero_volatility_is_finite_but_sharpe_is_typed_unavailable() -> None:
    stats = _api()
    returns = [Decimal("0.02"), Decimal("0.02"), Decimal("0.02")]

    _assert_result(stats.annualized_volatility(returns), "0", "available", "zero_volatility")
    _assert_result(stats.sharpe_ratio(returns), None, "unavailable", "zero_volatility")


def test_volatility_and_sharpe_mark_insufficient_history() -> None:
    stats = _api()

    _assert_result(
        stats.annualized_volatility([Decimal("0.01")]),
        None,
        "unavailable",
        "insufficient_history",
    )
    _assert_result(
        stats.sharpe_ratio([Decimal("0.01")]),
        None,
        "unavailable",
        "insufficient_history",
    )


def test_max_drawdown_uses_wealth_zero_and_preserves_negative_returns() -> None:
    stats = _api()
    case = _case("drawdown")

    _assert_result(
        stats.max_drawdown(_decimal_series(case["returns"])),
        case["expected_max_drawdown"],
        "available",
        "computed",
    )
    _assert_result(stats.max_drawdown([]), None, "unavailable", "no_complete_periods")


def test_excess_cagr_returns_both_fixed_benchmark_results() -> None:
    stats = _api()
    case = _case("return_series")
    strategy = _decimal_series(case["returns"])
    benchmarks = {
        name: _decimal_series(values) for name, values in case["benchmark_returns"].items()
    }

    result = stats.excess_cagrs(strategy, benchmarks)
    assert list(result) == ["SPY_total_return", "Universe_equal_weight"]
    for benchmark_name, expected in case["expected_excess_cagr"].items():
        _assert_result(result[benchmark_name], expected, "available", "computed")


def test_spearman_ic_uses_average_ranks_for_ties_and_ignores_mapping_order() -> None:
    stats = _api()
    case = _case("ic_with_ties")
    scores = _decimal_mapping(case["scores"])
    returns = _decimal_mapping(case["next_returns"])

    first = stats.spearman_ic(scores, returns)
    shuffled_scores = dict(reversed(list(scores.items())))
    shuffled_returns = dict(reversed(list(returns.items())))
    second = stats.spearman_ic(shuffled_scores, shuffled_returns)

    _assert_result(first, case["expected_ic"], "available", "computed")
    assert first.to_dict() == second.to_dict()


def test_spearman_ic_typed_states_do_not_use_zero_for_small_or_constant_ranks() -> None:
    stats = _api()
    returns = {"A": Decimal("0.01"), "B": Decimal("0.02"), "C": Decimal("0.03")}

    _assert_result(
        stats.spearman_ic({"A": Decimal("1")}, {"A": Decimal("0.01")}),
        None,
        "unavailable",
        "insufficient_cross_section",
    )
    _assert_result(
        stats.spearman_ic({"A": Decimal("1"), "B": Decimal("1"), "C": Decimal("1")}, returns),
        None,
        "unavailable",
        "zero_rank_variance",
    )
    _assert_result(
        stats.spearman_ic(
            {"A": Decimal("1"), "B": Decimal("2"), "C": Decimal("3")},
            {"A": Decimal("0.01"), "B": Decimal("0.01"), "C": Decimal("0.01")},
        ),
        None,
        "unavailable",
        "zero_rank_variance",
    )


def test_aggregate_spearman_ic_averages_only_available_rebalances() -> None:
    stats = _api()
    first_case = _case("ic_with_ties")
    second_case = _case("ic_aggregate")
    rebalances = [
        (
            _decimal_mapping(first_case["scores"]),
            _decimal_mapping(first_case["next_returns"]),
        ),
        (
            {"A": Decimal("1"), "B": Decimal("1"), "C": Decimal("1")},
            _decimal_mapping(second_case["second_returns"]),
        ),
        (
            _decimal_mapping(second_case["second_scores"]),
            _decimal_mapping(second_case["second_returns"]),
        ),
    ]

    result = stats.aggregate_spearman_ic(rebalances)
    _assert_result(result, second_case["expected_mean_ic"], "available", "computed")


def test_quintile_returns_are_fixed_q1_to_q5_with_ascii_tie_breaking() -> None:
    stats = _api()
    case = _case("quintiles")
    scores = _decimal_mapping(case["scores"])
    returns = _decimal_mapping(case["next_returns"])

    result = stats.quintile_returns(scores, returns)
    assert list(result) == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    for quantile, expected in case["expected"].items():
        _assert_result(result[quantile], expected, "available", "computed")

    shuffled = stats.quintile_returns(
        dict(reversed(list(scores.items()))), dict(reversed(list(returns.items())))
    )
    assert {key: value.to_dict() for key, value in result.items()} == {
        key: value.to_dict() for key, value in shuffled.items()
    }


def test_quintile_returns_typed_empty_groups_and_missing_returns() -> None:
    stats = _api()
    scores = {"A": Decimal("3"), "B": Decimal("2"), "C": Decimal("1")}
    returns = {"A": Decimal("0.03"), "B": Decimal("0.02"), "C": Decimal("0.01")}

    result = stats.quintile_returns(scores, returns)
    for quantile in ("Q5",):
        _assert_result(result[quantile], None, "unavailable", "empty_group")

    missing = stats.quintile_returns(
        {"A": Decimal("6"), "B": Decimal("5"), "C": Decimal("4"), "D": Decimal("3"), "E": Decimal("2"), "F": Decimal("1")},
        {"A": Decimal("0.03"), "C": Decimal("0.02"), "D": Decimal("0.01"), "E": Decimal("0.00"), "F": Decimal("-0.01")},
    )
    _assert_result(missing["Q1"], None, "unavailable", "missing_return")


def test_turnover_average_and_annualization_use_fixed_twelve_periods() -> None:
    stats = _api()
    case = _case("turnover")
    turnovers = _decimal_series(case["turnovers"])

    _assert_result(
        stats.average_turnover(turnovers),
        case["expected_average"],
        "available",
        "computed",
    )
    _assert_result(
        stats.annualized_turnover(turnovers),
        case["expected_annualized"],
        "available",
        "computed",
    )
    _assert_result(stats.average_turnover([]), None, "unavailable", "no_complete_periods")
    _assert_result(stats.annualized_turnover([]), None, "unavailable", "no_complete_periods")


@pytest.mark.parametrize(
    "bad_returns",
    [
        [Decimal("NaN")] * 12,
        [Decimal("Infinity")] * 12,
        [0.01] * 12,
    ],
)
def test_non_decimal_or_non_finite_inputs_are_typed_invalid_not_silently_accepted(
    bad_returns: list[object],
) -> None:
    stats = _api()

    result = stats.cagr(bad_returns)
    assert result.value is None
    assert result.status == "invalid"
    assert result.reason_code in {"non_finite_input", "decimal_required"}
    json.dumps(result.to_dict(), allow_nan=False)
