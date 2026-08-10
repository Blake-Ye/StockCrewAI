"""Deterministic, Decimal-first statistics for monthly backtest returns."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext
import json
from typing import Literal, TypeAlias


PERIODS_PER_YEAR = 12
STATISTICS_VERSION = "quant-statistics-v1"
SHARPE_VERSION = "sharpe-zero-rf-period-v1"
CONVERSION_VERSION = "decimal-to-float64-v1"
TOLERANCE = Decimal("1e-12")
_DECIMAL_CONTEXT_PRECISION = 28
_ZERO = Decimal("0")
_ONE = Decimal("1")
_PERIODS_PER_YEAR = Decimal(PERIODS_PER_YEAR)

StatisticStatus: TypeAlias = Literal["available", "unavailable", "invalid"]


@dataclass(frozen=True)
class StatisticResult:
    """A finite Decimal result or an explicit typed unavailable/invalid state."""

    value: Decimal | None
    status: StatisticStatus
    reason_code: str
    statistic_version: str = STATISTICS_VERSION
    periods_per_year: int = PERIODS_PER_YEAR
    conversion_version: str = CONVERSION_VERSION
    tolerance: Decimal = TOLERANCE

    def __post_init__(self) -> None:
        if self.status not in {"available", "unavailable", "invalid"}:
            raise ValueError(f"unknown statistic status: {self.status}")
        if not isinstance(self.reason_code, str) or not self.reason_code.strip():
            raise ValueError("reason_code must be a non-empty string")
        if not isinstance(self.statistic_version, str) or not self.statistic_version.strip():
            raise ValueError("statistic_version must be a non-empty string")
        if self.periods_per_year != PERIODS_PER_YEAR:
            raise ValueError("periods_per_year is fixed at 12")
        if self.conversion_version != CONVERSION_VERSION:
            raise ValueError("conversion_version is fixed")
        if not isinstance(self.tolerance, Decimal) or not self.tolerance.is_finite():
            raise ValueError("tolerance must be a finite Decimal")
        if self.value is not None:
            if not isinstance(self.value, Decimal) or not self.value.is_finite():
                raise ValueError("statistic value must be a finite Decimal or None")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation without converting core values to float."""
        return {
            "value": None if self.value is None else str(self.value),
            "status": self.status,
            "reason_code": self.reason_code,
            "statistic_version": self.statistic_version,
            "periods_per_year": self.periods_per_year,
            "conversion_version": self.conversion_version,
            "tolerance": str(self.tolerance),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @property
    def json(self) -> str:
        return self.to_json()


def _result(
    value: Decimal | None,
    status: StatisticStatus,
    reason_code: str,
    *,
    version: str = STATISTICS_VERSION,
) -> StatisticResult:
    return StatisticResult(
        value=value,
        status=status,
        reason_code=reason_code,
        statistic_version=version,
    )


def _invalid(reason_code: str = "invalid_input", *, version: str = STATISTICS_VERSION) -> StatisticResult:
    return _result(None, "invalid", reason_code, version=version)


def _unavailable(
    reason_code: str, *, version: str = STATISTICS_VERSION
) -> StatisticResult:
    return _result(None, "unavailable", reason_code, version=version)


def _ordered_values(values: Iterable[object]) -> tuple[object, ...] | None:
    if isinstance(values, Mapping):
        try:
            return tuple(values[key] for key in sorted(values, key=lambda item: str(item)))
        except (KeyError, TypeError):
            return None
    try:
        return tuple(values)
    except TypeError:
        return None


def _validated_series(
    values: Iterable[object], *, version: str = STATISTICS_VERSION
) -> tuple[tuple[Decimal, ...] | None, StatisticResult | None]:
    ordered = _ordered_values(values)
    if ordered is None:
        return None, _invalid("invalid_input", version=version)
    for value in ordered:
        if not isinstance(value, Decimal):
            return None, _invalid("decimal_required", version=version)
        if not value.is_finite():
            return None, _invalid("non_finite_input", version=version)
    return tuple(ordered), None  # type: ignore[arg-type]


def _validated_mapping(
    values: Mapping[object, object], *, allow_none: bool = False, version: str = STATISTICS_VERSION
) -> tuple[dict[object, Decimal] | None, StatisticResult | None]:
    if not isinstance(values, Mapping):
        return None, _invalid("invalid_input", version=version)
    result: dict[object, Decimal] = {}
    for key in sorted(values, key=lambda item: str(item)):
        value = values[key]
        if value is None and allow_none:
            continue
        if not isinstance(value, Decimal):
            return None, _invalid("decimal_required", version=version)
        if not value.is_finite():
            return None, _invalid("non_finite_input", version=version)
        result[key] = value
    return result, None


def _decimal_mean(values: tuple[Decimal, ...]) -> Decimal:
    with localcontext() as context:
        context.prec = _DECIMAL_CONTEXT_PRECISION
        return sum(values, _ZERO) / Decimal(len(values))


def _sample_standard_deviation(values: tuple[Decimal, ...]) -> Decimal:
    mean = _decimal_mean(values)
    with localcontext() as context:
        context.prec = _DECIMAL_CONTEXT_PRECISION
        variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values) - 1)
        return variance.sqrt()


def cagr(returns: Iterable[object]) -> StatisticResult:
    """Calculate monthly-return CAGR using the fixed twelve-period year."""
    values, error = _validated_series(returns)
    if error is not None:
        return error
    assert values is not None
    if len(values) < PERIODS_PER_YEAR:
        return _unavailable("insufficient_history")
    try:
        with localcontext() as context:
            context.prec = _DECIMAL_CONTEXT_PRECISION
            growth_factor = _ONE
            for value in values:
                growth_factor *= _ONE + value
            if not growth_factor.is_finite():
                return _invalid("invalid_result")
            if growth_factor <= _ZERO:
                return _unavailable("invalid_return_factor")
            exponent = _PERIODS_PER_YEAR / Decimal(len(values))
            result = (growth_factor.ln() * exponent).exp() - _ONE
        if not result.is_finite():
            return _invalid("invalid_result")
        return _result(result, "available", "computed")
    except (DecimalException, ValueError):
        return _invalid("invalid_result")


annualized_return = cagr


def annualized_volatility(returns: Iterable[object]) -> StatisticResult:
    """Calculate sample standard deviation (ddof=1) times sqrt(12)."""
    values, error = _validated_series(returns)
    if error is not None:
        return error
    assert values is not None
    if len(values) < 2:
        return _unavailable("insufficient_history")
    try:
        with localcontext() as context:
            context.prec = _DECIMAL_CONTEXT_PRECISION
            standard_deviation = _sample_standard_deviation(values)
            if standard_deviation == _ZERO:
                return _result(_ZERO, "available", "zero_volatility")
            result = standard_deviation * _PERIODS_PER_YEAR.sqrt()
        if not result.is_finite():
            return _invalid("invalid_result")
        return _result(result, "available", "computed")
    except (DecimalException, ValueError):
        return _invalid("invalid_result")


def sharpe_ratio(returns: Iterable[object]) -> StatisticResult:
    """Calculate Sharpe with a fixed zero risk-free return per period."""
    values, error = _validated_series(returns, version=SHARPE_VERSION)
    if error is not None:
        return error
    assert values is not None
    if len(values) < 2:
        return _unavailable("insufficient_history", version=SHARPE_VERSION)
    try:
        with localcontext() as context:
            context.prec = _DECIMAL_CONTEXT_PRECISION
            standard_deviation = _sample_standard_deviation(values)
            if standard_deviation == _ZERO:
                return _unavailable("zero_volatility", version=SHARPE_VERSION)
            result = (
                _decimal_mean(values) / standard_deviation * _PERIODS_PER_YEAR.sqrt()
            )
        if not result.is_finite():
            return _invalid("invalid_result", version=SHARPE_VERSION)
        return _result(result, "available", "computed", version=SHARPE_VERSION)
    except (DecimalException, ValueError):
        return _invalid("invalid_result", version=SHARPE_VERSION)


sharpe = sharpe_ratio


def max_drawdown(returns: Iterable[object]) -> StatisticResult:
    """Calculate the minimum peak-relative drawdown from wealth_0=1."""
    values, error = _validated_series(returns)
    if error is not None:
        return error
    assert values is not None
    if not values:
        return _unavailable("no_complete_periods")
    wealth = _ONE
    peak = wealth
    minimum_drawdown = _ZERO
    try:
        with localcontext() as context:
            context.prec = _DECIMAL_CONTEXT_PRECISION
            for value in values:
                wealth *= _ONE + value
                if not wealth.is_finite():
                    return _invalid("invalid_result")
                if wealth < _ZERO:
                    return _unavailable("invalid_return_factor")
                if wealth > peak:
                    peak = wealth
                drawdown = wealth / peak - _ONE
                if drawdown < minimum_drawdown:
                    minimum_drawdown = drawdown
        return _result(minimum_drawdown, "available", "computed")
    except (DecimalException, ValueError):
        return _invalid("invalid_result")


def excess_cagr(
    strategy_returns: Iterable[object], benchmark_returns: Iterable[object]
) -> StatisticResult:
    """Return strategy CAGR minus one benchmark CAGR over the same periods."""
    strategy = cagr(strategy_returns)
    benchmark = cagr(benchmark_returns)
    if strategy.status != "available":
        return _result(None, strategy.status, strategy.reason_code)
    if benchmark.status != "available":
        return _result(None, benchmark.status, benchmark.reason_code)
    assert strategy.value is not None and benchmark.value is not None
    with localcontext() as context:
        context.prec = _DECIMAL_CONTEXT_PRECISION
        result = strategy.value - benchmark.value
    if not result.is_finite():
        return _invalid("invalid_result")
    return _result(result, "available", "computed")


def excess_cagrs(
    strategy_returns: Iterable[object],
    benchmarks: Mapping[str, Iterable[object]],
) -> dict[str, StatisticResult]:
    """Calculate excess CAGR for each fixed benchmark in stable key order."""
    if not isinstance(benchmarks, Mapping):
        return {"invalid": _invalid("invalid_input")}
    return {
        name: excess_cagr(strategy_returns, benchmarks[name])
        for name in sorted(benchmarks)
    }


def _average_ranks(values: Mapping[object, Decimal], keys: tuple[object, ...]) -> dict[object, Decimal]:
    ordered = sorted(keys, key=lambda key: (values[key], str(key)))
    ranks: dict[object, Decimal] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[index]]:
            end += 1
        average_rank = (Decimal(index + 1) + Decimal(end)) / Decimal(2)
        for key in ordered[index:end]:
            ranks[key] = average_rank
        index = end
    return ranks


def _rank_correlation(
    scores: Mapping[object, Decimal], returns: Mapping[object, Decimal], keys: tuple[object, ...]
) -> Decimal | None:
    score_ranks = _average_ranks(scores, keys)
    return_ranks = _average_ranks(returns, keys)
    score_mean = _decimal_mean(tuple(score_ranks[key] for key in keys))
    return_mean = _decimal_mean(tuple(return_ranks[key] for key in keys))
    with localcontext() as context:
        context.prec = _DECIMAL_CONTEXT_PRECISION
        score_variance = sum((score_ranks[key] - score_mean) ** 2 for key in keys)
        return_variance = sum((return_ranks[key] - return_mean) ** 2 for key in keys)
        if score_variance == _ZERO or return_variance == _ZERO:
            return None
        covariance = sum(
            (score_ranks[key] - score_mean) * (return_ranks[key] - return_mean)
            for key in keys
        )
        result = covariance / (score_variance * return_variance).sqrt()
    return result if result.is_finite() else None


def spearman_ic(
    scores: Mapping[object, object], next_returns: Mapping[object, object]
) -> StatisticResult:
    """Calculate one rebalance's Spearman score-to-next-return IC."""
    valid_scores, score_error = _validated_mapping(scores, allow_none=True)
    if score_error is not None:
        return score_error
    valid_returns, return_error = _validated_mapping(next_returns, allow_none=True)
    if return_error is not None:
        return return_error
    assert valid_scores is not None and valid_returns is not None
    keys = tuple(sorted(set(valid_scores) & set(valid_returns), key=lambda item: str(item)))
    if len(keys) < 2:
        return _unavailable("insufficient_cross_section")
    try:
        result = _rank_correlation(valid_scores, valid_returns, keys)
    except (DecimalException, ValueError):
        return _invalid("invalid_result")
    if result is None:
        return _unavailable("zero_rank_variance")
    return _result(result, "available", "computed")


def aggregate_spearman_ic(
    rebalances: Iterable[tuple[Mapping[object, object], Mapping[object, object]]],
) -> StatisticResult:
    """Average available ICs across rebalances, excluding typed unavailable ICs."""
    try:
        items = tuple(rebalances)
    except TypeError:
        return _invalid("invalid_input")
    available: list[Decimal] = []
    for item in items:
        if not isinstance(item, tuple) or len(item) != 2:
            return _invalid("invalid_input")
        result = spearman_ic(item[0], item[1])
        if result.status == "invalid":
            return result
        if result.status == "available":
            assert result.value is not None
            available.append(result.value)
    if not available:
        return _unavailable("no_complete_periods")
    with localcontext() as context:
        context.prec = _DECIMAL_CONTEXT_PRECISION
        result = sum(available, _ZERO) / Decimal(len(available))
    if not result.is_finite():
        return _invalid("invalid_result")
    return _result(result, "available", "computed")


def _quintile_states(reason_code: str) -> dict[str, StatisticResult]:
    return {f"Q{index}": _unavailable(reason_code) for index in range(1, 6)}


def quintile_returns(
    scores: Mapping[object, object], next_returns: Mapping[object, object]
) -> dict[str, StatisticResult]:
    """Calculate deterministic Q1 (highest score) through Q5 returns."""
    valid_scores, score_error = _validated_mapping(scores, allow_none=True)
    if score_error is not None:
        return {f"Q{index}": score_error for index in range(1, 6)}
    valid_returns, return_error = _validated_mapping(next_returns, allow_none=True)
    if return_error is not None:
        return {f"Q{index}": return_error for index in range(1, 6)}
    assert valid_scores is not None and valid_returns is not None
    if not valid_scores:
        return _quintile_states("insufficient_cross_section")

    try:
        ordered = sorted(valid_scores, key=lambda key: (-valid_scores[key], str(key)))
    except (DecimalException, TypeError):
        return {f"Q{index}": _invalid("invalid_input") for index in range(1, 6)}

    groups: dict[str, list[object]] = {f"Q{index}": [] for index in range(1, 6)}
    eligible_count = len(ordered)
    for index, key in enumerate(ordered, start=1):
        group_number = min(5, ((index - 1) * 5) // eligible_count + 1)
        groups[f"Q{group_number}"].append(key)

    result: dict[str, StatisticResult] = {}
    for name, keys in groups.items():
        if not keys:
            result[name] = _unavailable("empty_group")
            continue
        missing = [key for key in keys if key not in valid_returns]
        if missing:
            result[name] = _unavailable("missing_return")
            continue
        try:
            value = _decimal_mean(tuple(valid_returns[key] for key in keys))
        except (DecimalException, ValueError):
            result[name] = _invalid("invalid_result")
            continue
        result[name] = (
            _result(value, "available", "computed")
            if value.is_finite()
            else _invalid("invalid_result")
        )
    return result


def average_turnover(turnovers: Iterable[object]) -> StatisticResult:
    """Calculate the arithmetic mean of complete-period turnover values."""
    values, error = _validated_series(turnovers)
    if error is not None:
        return error
    assert values is not None
    if not values:
        return _unavailable("no_complete_periods")
    try:
        result = _decimal_mean(values)
    except (DecimalException, ValueError):
        return _invalid("invalid_result")
    return _result(result, "available", "computed") if result.is_finite() else _invalid("invalid_result")


def annualized_turnover(turnovers: Iterable[object]) -> StatisticResult:
    """Calculate mean turnover times the fixed twelve periods per year."""
    average = average_turnover(turnovers)
    if average.status != "available":
        return average
    assert average.value is not None
    with localcontext() as context:
        context.prec = _DECIMAL_CONTEXT_PRECISION
        result = average.value * _PERIODS_PER_YEAR
    if not result.is_finite():
        return _invalid("invalid_result")
    return _result(result, "available", "computed")


__all__ = [
    "CONVERSION_VERSION",
    "PERIODS_PER_YEAR",
    "SHARPE_VERSION",
    "STATISTICS_VERSION",
    "TOLERANCE",
    "StatisticResult",
    "aggregate_spearman_ic",
    "annualized_return",
    "annualized_turnover",
    "annualized_volatility",
    "average_turnover",
    "cagr",
    "excess_cagr",
    "excess_cagrs",
    "max_drawdown",
    "quintile_returns",
    "sharpe",
    "sharpe_ratio",
    "spearman_ic",
]
