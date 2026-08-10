"""Small, deterministic walk-forward backtest over precomputed total-return levels."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from stockcrewai.models.quant import UniverseManifest
from stockcrewai.quant import statistics
from stockcrewai.quant.portfolio import (
    BASELINE_ROUND_TRIP_COST_BPS,
    CASH,
    COMPOSITE_SCORE_VERSION,
    ROUND_TRIP_COST_SENSITIVITY_BPS,
    build_target_portfolio,
    calculate_cost_and_net_return,
)


_SPY = "SPY"
_ZERO = Decimal("0")
_ONE = Decimal("1")
_UTC = timezone.utc
_BACKTEST_VERSION = "quant-backtest-v1"
_ARTIFACT_SCHEMA_VERSION = "quant-backtest-artifact-v1"
_STRATEGY_VERSION = "quant-walk-forward-v1"
_STATUS = Literal["available", "unavailable", "invalid"]
_PERIOD_STATUS = Literal["available", "unavailable", "terminal"]


def _finite_decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    return value


def _nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _aware_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(_UTC)


def _session_date(value: object, name: str) -> date:
    if isinstance(value, datetime):
        return _aware_datetime(value, name).date()
    if not isinstance(value, date):
        raise ValueError(f"{name} must be a date")
    return value


def _midnight(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=_UTC)


def _freeze_decimal_mapping(values: Mapping[str, Decimal], name: str) -> Mapping[str, Decimal]:
    if not isinstance(values, Mapping):
        raise ValueError(f"{name} must be a mapping")
    result: dict[str, Decimal] = {}
    for key in sorted(values):
        if not isinstance(key, str) or not key:
            raise ValueError(f"{name} keys must be non-empty strings")
        result[key] = _finite_decimal(values[key], f"{name}[{key}]")
    return MappingProxyType(result)


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in sorted(value.items(), key=lambda x: str(x[0]))}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, float):
        raise ValueError("float values are not allowed")
    if isinstance(value, Decimal):
        return _finite_decimal(value, "json value")
    return value


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(_finite_decimal(value, "json value"))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda x: str(x[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, float):
        raise ValueError("float values are not allowed")
    return value


@dataclass(frozen=True)
class SnapshotScore:
    snapshot_id: str
    ticker: str
    as_of: datetime
    score: Decimal
    filing_cutoff: datetime | None = None
    price_cutoff: datetime | None = None
    score_version: str = COMPOSITE_SCORE_VERSION

    def __post_init__(self) -> None:
        snapshot_id = _nonempty(self.snapshot_id, "snapshot_id")
        ticker = _nonempty(self.ticker, "ticker")
        if not ticker.isascii():
            raise ValueError("ticker must be ASCII")
        as_of = _aware_datetime(self.as_of, "as_of")
        filing_cutoff = as_of if self.filing_cutoff is None else _aware_datetime(
            self.filing_cutoff, "filing_cutoff"
        )
        price_cutoff = as_of if self.price_cutoff is None else _aware_datetime(
            self.price_cutoff, "price_cutoff"
        )
        if filing_cutoff > as_of or price_cutoff > as_of:
            raise ValueError("snapshot cutoffs must not be later than as_of")
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "filing_cutoff", filing_cutoff)
        object.__setattr__(self, "price_cutoff", price_cutoff)
        object.__setattr__(self, "score", _finite_decimal(self.score, "score"))
        score_version = _nonempty(self.score_version, "score_version")
        if score_version != COMPOSITE_SCORE_VERSION:
            raise ValueError(f"score_version must be {COMPOSITE_SCORE_VERSION}")
        object.__setattr__(self, "score_version", score_version)


@dataclass(frozen=True)
class TotalReturnLevel:
    ticker: str
    trade_date: date | datetime
    level: Decimal

    def __post_init__(self) -> None:
        ticker = _nonempty(self.ticker, "ticker")
        trade_date = _session_date(self.trade_date, "trade_date")
        level = _finite_decimal(self.level, "level")
        if level <= _ZERO:
            raise ValueError("total-return level must be positive")
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "trade_date", trade_date)
        object.__setattr__(self, "level", level)


@dataclass(frozen=True)
class RebalanceSpec:
    rebalance_anchor: date | datetime
    trade_date: date | datetime
    next_trade_date: date | datetime | None
    period_id: str | None = None

    def __post_init__(self) -> None:
        anchor = _session_date(self.rebalance_anchor, "rebalance_anchor")
        trade_date = _session_date(self.trade_date, "trade_date")
        next_trade_date = (
            None if self.next_trade_date is None else _session_date(self.next_trade_date, "next_trade_date")
        )
        if anchor >= trade_date:
            raise ValueError("rebalance_anchor must precede trade_date")
        if next_trade_date is not None and next_trade_date <= trade_date:
            raise ValueError("next_trade_date must follow trade_date")
        period_id = self.period_id or f"period-{trade_date.isoformat()}"
        object.__setattr__(self, "rebalance_anchor", anchor)
        object.__setattr__(self, "trade_date", trade_date)
        object.__setattr__(self, "next_trade_date", next_trade_date)
        object.__setattr__(self, "period_id", _nonempty(period_id, "period_id"))

    def __iter__(self):
        yield self.rebalance_anchor
        yield self.trade_date
        yield self.next_trade_date


@dataclass(frozen=True)
class WalkForwardInput:
    universe: UniverseManifest
    snapshot_scores: tuple[SnapshotScore, ...]
    rebalance_specs: tuple[RebalanceSpec, ...]
    total_return_levels: tuple[TotalReturnLevel, ...]
    strategy_version: str = _STRATEGY_VERSION
    backtest_version: str = _BACKTEST_VERSION
    artifact_schema_version: str = _ARTIFACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.universe, UniverseManifest):
            raise ValueError("universe must be a UniverseManifest")
        object.__setattr__(self, "snapshot_scores", tuple(self.snapshot_scores))
        object.__setattr__(self, "rebalance_specs", tuple(self.rebalance_specs))
        object.__setattr__(self, "total_return_levels", tuple(self.total_return_levels))
        for field_name in ("strategy_version", "backtest_version", "artifact_schema_version"):
            object.__setattr__(self, field_name, _nonempty(getattr(self, field_name), field_name))


@dataclass(frozen=True)
class TypedValue:
    value: Decimal | None
    status: _STATUS
    reason_code: str

    def __post_init__(self) -> None:
        if self.status not in {"available", "unavailable", "invalid"}:
            raise ValueError("invalid typed value status")
        object.__setattr__(self, "reason_code", _nonempty(self.reason_code, "reason_code"))
        if self.status == "available":
            if self.value is None:
                raise ValueError("available value must be present")
            object.__setattr__(self, "value", _finite_decimal(self.value, "value"))
        elif self.value is not None:
            raise ValueError("unavailable value must be None")

    @classmethod
    def available(cls, value: Decimal, reason_code: str = "computed") -> "TypedValue":
        return cls(value, "available", reason_code)

    @classmethod
    def unavailable(cls, reason_code: str) -> "TypedValue":
        return cls(None, "unavailable", reason_code)

    def to_dict(self) -> dict[str, object]:
        return {"value": None if self.value is None else str(self.value), "status": self.status, "reason_code": self.reason_code}


@dataclass(frozen=True)
class Coverage:
    required: int
    available: int
    ratio: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.required, int) or isinstance(self.required, bool) or self.required < 0:
            raise ValueError("coverage.required must be a non-negative integer")
        if not isinstance(self.available, int) or isinstance(self.available, bool):
            raise ValueError("coverage.available must be an integer")
        if not 0 <= self.available <= self.required:
            raise ValueError("coverage.available must be within required")
        object.__setattr__(self, "ratio", _finite_decimal(self.ratio, "coverage.ratio"))

    def to_dict(self) -> dict[str, object]:
        return {"required": self.required, "available": self.available, "ratio": str(self.ratio)}


def _coverage(required: Iterable[str], available: Mapping[str, Decimal]) -> Coverage:
    required_ids = tuple(required)
    count = sum(identifier in available for identifier in required_ids)
    ratio = _ZERO if not required_ids else Decimal(count) / Decimal(len(required_ids))
    return Coverage(len(required_ids), count, ratio)


@dataclass(frozen=True)
class BacktestPeriod:
    period_id: str
    rebalance_anchor: date
    signal_as_of: datetime | None
    trade_date: date
    next_trade_date: date | None
    snapshot_ids: tuple[str, ...]
    score_version: str | None
    eligible_count: int
    selected_tickers: tuple[str, ...]
    previous_weights: Mapping[str, Decimal]
    target_weights: Mapping[str, Decimal] | None
    scores: Mapping[str, Decimal]
    forward_returns: Mapping[str, Decimal]
    turnover: TypedValue
    cost: TypedValue
    round_trip_cost_bps: Decimal
    cost_return: TypedValue
    gross_return: TypedValue
    net_return: TypedValue
    spy_return: TypedValue
    universe_return: TypedValue
    status: _PERIOD_STATUS
    reason_code: str
    coverage: Mapping[str, Coverage]
    missing_return_ids: tuple[str, ...]
    provenance: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.status not in {"available", "unavailable", "terminal"}:
            raise ValueError("invalid period status")
        object.__setattr__(self, "period_id", _nonempty(self.period_id, "period_id"))
        object.__setattr__(self, "rebalance_anchor", _session_date(self.rebalance_anchor, "rebalance_anchor"))
        signal = None if self.signal_as_of is None else _aware_datetime(self.signal_as_of, "signal_as_of")
        trade = _session_date(self.trade_date, "trade_date")
        next_trade = None if self.next_trade_date is None else _session_date(self.next_trade_date, "next_trade_date")
        if signal is not None and _midnight(trade) <= signal:
            raise ValueError("trade_date must be later than signal_as_of")
        if next_trade is not None and next_trade <= trade:
            raise ValueError("next_trade_date must follow trade_date")
        object.__setattr__(self, "signal_as_of", signal)
        object.__setattr__(self, "trade_date", trade)
        object.__setattr__(self, "next_trade_date", next_trade)
        object.__setattr__(self, "snapshot_ids", tuple(sorted(self.snapshot_ids)))
        object.__setattr__(self, "selected_tickers", tuple(self.selected_tickers))
        object.__setattr__(self, "previous_weights", _freeze_decimal_mapping(self.previous_weights, "previous_weights"))
        object.__setattr__(self, "target_weights", None if self.target_weights is None else _freeze_decimal_mapping(self.target_weights, "target_weights"))
        object.__setattr__(self, "scores", _freeze_decimal_mapping(self.scores, "scores"))
        object.__setattr__(self, "forward_returns", _freeze_decimal_mapping(self.forward_returns, "forward_returns"))
        object.__setattr__(self, "round_trip_cost_bps", _finite_decimal(self.round_trip_cost_bps, "round_trip_cost_bps"))
        if self.cost_return.status != self.cost.status or self.cost_return.value != self.cost.value:
            raise ValueError("cost_return must match cost")
        object.__setattr__(self, "reason_code", _nonempty(self.reason_code, "reason_code"))
        object.__setattr__(self, "coverage", MappingProxyType(dict(sorted(self.coverage.items()))))
        object.__setattr__(self, "missing_return_ids", tuple(sorted(set(self.missing_return_ids))))
        object.__setattr__(self, "provenance", _freeze_json(self.provenance))

    @property
    def benchmark_returns(self) -> Mapping[str, TypedValue]:
        return MappingProxyType({"SPY_total_return": self.spy_return, "Universe_equal_weight": self.universe_return})

    def to_dict(self) -> dict[str, object]:
        return {
            "period_id": self.period_id,
            "rebalance_anchor": self.rebalance_anchor.isoformat(),
            "signal_as_of": None if self.signal_as_of is None else self.signal_as_of.isoformat(),
            "trade_date": self.trade_date.isoformat(),
            "next_trade_date": None if self.next_trade_date is None else self.next_trade_date.isoformat(),
            "snapshot_ids": list(self.snapshot_ids),
            "score_version": self.score_version,
            "eligible_count": self.eligible_count,
            "selected_tickers": list(self.selected_tickers),
            "previous_weights": _json_value(self.previous_weights),
            "target_weights": None if self.target_weights is None else _json_value(self.target_weights),
            "scores": _json_value(self.scores),
            "forward_returns": _json_value(self.forward_returns),
            "turnover": self.turnover.to_dict(),
            "cost": self.cost.to_dict(),
            "round_trip_cost_bps": str(self.round_trip_cost_bps),
            "cost_return": self.cost_return.to_dict(),
            "gross_return": self.gross_return.to_dict(),
            "net_return": self.net_return.to_dict(),
            "spy_return": self.spy_return.to_dict(),
            "universe_return": self.universe_return.to_dict(),
            "status": self.status,
            "reason_code": self.reason_code,
            "coverage": {key: value.to_dict() for key, value in sorted(self.coverage.items())},
            "missing_return_ids": list(self.missing_return_ids),
            "provenance": _json_value(self.provenance),
        }


@dataclass(frozen=True)
class CostSensitivity:
    round_trip_cost_bps: Decimal
    net_returns: tuple[TypedValue, ...]
    cagr: statistics.StatisticResult
    max_drawdown: statistics.StatisticResult
    benchmark_returns: tuple[TypedValue, ...]
    universe_returns: tuple[TypedValue, ...]
    status: _STATUS
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "round_trip_cost_bps", _finite_decimal(self.round_trip_cost_bps, "round_trip_cost_bps"))
        object.__setattr__(self, "net_returns", tuple(self.net_returns))
        object.__setattr__(self, "benchmark_returns", tuple(self.benchmark_returns))
        object.__setattr__(self, "universe_returns", tuple(self.universe_returns))
        if self.status not in {"available", "unavailable", "invalid"}:
            raise ValueError("invalid sensitivity status")
        object.__setattr__(self, "reason_code", _nonempty(self.reason_code, "reason_code"))

    def to_dict(self) -> dict[str, object]:
        return {
            "round_trip_cost_bps": str(self.round_trip_cost_bps),
            "net_returns": [item.to_dict() for item in self.net_returns],
            "cagr": self.cagr.to_dict(),
            "max_drawdown": self.max_drawdown.to_dict(),
            "benchmark_returns": [item.to_dict() for item in self.benchmark_returns],
            "universe_returns": [item.to_dict() for item in self.universe_returns],
            "status": self.status,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class BaselineSummary:
    net_cost_bps: Decimal
    complete_period_count: int
    strategy: Mapping[str, statistics.StatisticResult]
    benchmarks: Mapping[str, Mapping[str, statistics.StatisticResult]]
    excess_cagrs: Mapping[str, statistics.StatisticResult]
    aggregate_spearman_ic: statistics.StatisticResult
    quintile_returns: Mapping[str, tuple[statistics.StatisticResult, ...]]
    quintile_aggregates: Mapping[str, Mapping[str, statistics.StatisticResult]]
    average_turnover: statistics.StatisticResult
    annualized_turnover: statistics.StatisticResult
    status: _STATUS
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "net_cost_bps", _finite_decimal(self.net_cost_bps, "net_cost_bps"))
        object.__setattr__(self, "strategy", MappingProxyType(dict(sorted(self.strategy.items()))))
        object.__setattr__(self, "benchmarks", MappingProxyType({key: MappingProxyType(dict(sorted(value.items()))) for key, value in sorted(self.benchmarks.items())}))
        object.__setattr__(self, "excess_cagrs", MappingProxyType(dict(sorted(self.excess_cagrs.items()))))
        object.__setattr__(self, "quintile_returns", MappingProxyType({key: tuple(value) for key, value in sorted(self.quintile_returns.items())}))
        object.__setattr__(
            self,
            "quintile_aggregates",
            MappingProxyType(
                {
                    key: MappingProxyType(dict(sorted(value.items())))
                    for key, value in sorted(self.quintile_aggregates.items())
                }
            ),
        )
        if self.status not in {"available", "unavailable", "invalid"}:
            raise ValueError("invalid baseline status")
        object.__setattr__(self, "reason_code", _nonempty(self.reason_code, "reason_code"))

    def to_dict(self) -> dict[str, object]:
        return {
            "net_cost_bps": str(self.net_cost_bps),
            "complete_period_count": self.complete_period_count,
            "strategy": {key: value.to_dict() for key, value in sorted(self.strategy.items())},
            "benchmarks": {
                key: {name: value.to_dict() for name, value in sorted(metrics.items())}
                for key, metrics in sorted(self.benchmarks.items())
            },
            "excess_cagrs": {key: value.to_dict() for key, value in sorted(self.excess_cagrs.items())},
            "aggregate_spearman_ic": self.aggregate_spearman_ic.to_dict(),
            "quintile_returns": {
                key: [value.to_dict() for value in values]
                for key, values in sorted(self.quintile_returns.items())
            },
            "quintile_aggregates": {
                key: {
                    name: value.to_dict()
                    for name, value in sorted(metrics.items())
                }
                for key, metrics in sorted(self.quintile_aggregates.items())
            },
            "average_turnover": self.average_turnover.to_dict(),
            "annualized_turnover": self.annualized_turnover.to_dict(),
            "status": self.status,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class WalkForwardResult:
    universe_id: str
    strategy_version: str
    backtest_version: str
    artifact_schema_version: str
    periods_per_year: int
    conversion_version: str
    tolerance: Decimal
    known_biases: tuple[str, ...]
    data_quality: Mapping[str, object]
    periods: tuple[BacktestPeriod, ...]
    baseline_summary: BaselineSummary
    cost_sensitivity: Mapping[Decimal, CostSensitivity]
    provenance: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "universe_id", _nonempty(self.universe_id, "universe_id"))
        object.__setattr__(self, "strategy_version", _nonempty(self.strategy_version, "strategy_version"))
        object.__setattr__(self, "backtest_version", _nonempty(self.backtest_version, "backtest_version"))
        object.__setattr__(self, "artifact_schema_version", _nonempty(self.artifact_schema_version, "artifact_schema_version"))
        object.__setattr__(self, "tolerance", _finite_decimal(self.tolerance, "tolerance"))
        object.__setattr__(self, "known_biases", tuple(sorted(set(self.known_biases))))
        object.__setattr__(self, "data_quality", _freeze_json(self.data_quality))
        object.__setattr__(self, "periods", tuple(self.periods))
        object.__setattr__(self, "cost_sensitivity", MappingProxyType(dict(sorted(self.cost_sensitivity.items()))))
        object.__setattr__(self, "provenance", _freeze_json(self.provenance))

    @property
    def complete_period_count(self) -> int:
        return sum(period.status == "available" for period in self.periods)

    @property
    def stable_hash(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "universe_id": self.universe_id,
            "strategy_version": self.strategy_version,
            "backtest_version": self.backtest_version,
            "artifact_schema_version": self.artifact_schema_version,
            "periods_per_year": self.periods_per_year,
            "conversion_version": self.conversion_version,
            "tolerance": str(self.tolerance),
            "known_biases": list(self.known_biases),
            "data_quality": _json_value(self.data_quality),
            "periods": [period.to_dict() for period in self.periods],
            "baseline_summary": self.baseline_summary.to_dict(),
            "cost_sensitivity": {
                str(bps): sensitivity.to_dict()
                for bps, sensitivity in sorted(self.cost_sensitivity.items())
            },
            "provenance": _json_value(self.provenance),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))

    def to_artifact_dict(self) -> dict[str, object]:
        payload = self.to_dict()
        payload["artifact_hash"] = self.stable_hash
        return payload

    def to_artifact_json(self) -> str:
        return json.dumps(
            self.to_artifact_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"


def _unavailable(reason_code: str) -> TypedValue:
    return TypedValue.unavailable(reason_code)


def _level_map(levels: Iterable[TotalReturnLevel], allowed: set[str]) -> dict[tuple[str, date], Decimal]:
    result: dict[tuple[str, date], Decimal] = {}
    for level in levels:
        if not isinstance(level, TotalReturnLevel):
            raise ValueError("total_return_levels must contain TotalReturnLevel values")
        if level.ticker not in allowed:
            raise ValueError("total-return level ticker is outside the fixed universe")
        key = (level.ticker, level.trade_date)
        if key in result:
            raise ValueError(f"duplicate total-return level: {key}")
        result[key] = level.level
    return result


def _snapshot_groups(scores: Iterable[SnapshotScore], universe: tuple[str, ...]) -> dict[datetime, tuple[SnapshotScore, ...]]:
    required = set(universe)
    groups: dict[datetime, dict[str, SnapshotScore]] = {}
    metadata: dict[str, tuple[datetime, datetime | None, datetime | None, str]] = {}
    for record in scores:
        if not isinstance(record, SnapshotScore):
            raise ValueError("snapshot_scores must contain SnapshotScore values")
        if record.ticker not in required:
            raise ValueError("snapshot score ticker is outside the fixed universe")
        if record.score_version != COMPOSITE_SCORE_VERSION:
            raise ValueError(f"score_version must be {COMPOSITE_SCORE_VERSION}")
        previous = metadata.get(record.snapshot_id)
        current = (record.as_of, record.filing_cutoff, record.price_cutoff, record.score_version)
        if previous is not None and previous != current:
            raise ValueError("snapshot_id metadata must be consistent")
        metadata[record.snapshot_id] = current
        by_ticker = groups.setdefault(record.as_of, {})
        if record.ticker in by_ticker:
            raise ValueError("duplicate snapshot score for ticker and as_of")
        by_ticker[record.ticker] = record

    complete: dict[datetime, tuple[SnapshotScore, ...]] = {}
    for as_of, by_ticker in groups.items():
        if set(by_ticker) != required:
            continue
        versions = {record.score_version for record in by_ticker.values()}
        if len(versions) != 1:
            continue
        complete[as_of] = tuple(by_ticker[ticker] for ticker in sorted(by_ticker))
    return complete


def _select_snapshot(
    groups: Mapping[datetime, tuple[SnapshotScore, ...]], anchor: date
) -> tuple[datetime, tuple[SnapshotScore, ...]] | None:
    # A session date is an anchor, so snapshots through its midnight are eligible.
    candidates = [as_of for as_of in groups if as_of <= _midnight(anchor)]
    if not candidates:
        return None
    selected = max(candidates)
    records = groups[selected]
    for record in records:
        if record.filing_cutoff is not None and record.filing_cutoff > selected:
            return None
        if record.price_cutoff is not None and record.price_cutoff > selected:
            return None
    return selected, records


def _forward_returns(
    levels: Mapping[tuple[str, date], Decimal], tickers: Iterable[str], trade_date: date, next_trade_date: date
) -> tuple[dict[str, Decimal], tuple[str, ...]]:
    returns: dict[str, Decimal] = {}
    missing: list[str] = []
    for ticker in sorted(set(tickers)):
        start = levels.get((ticker, trade_date))
        end = levels.get((ticker, next_trade_date))
        if start is None or end is None:
            missing.append(ticker)
            continue
        with localcontext() as context:
            context.prec = 28
            value = end / start - _ONE
        if not value.is_finite():
            raise ValueError("computed return must be finite")
        returns[ticker] = value
    return returns, tuple(sorted(missing))


def _mean(values: Iterable[Decimal]) -> Decimal:
    items = tuple(values)
    if not items:
        raise ValueError("cannot average an empty sequence")
    with localcontext() as context:
        context.prec = 28
        return sum(items, _ZERO) / Decimal(len(items))


def _decimal_values(values: Iterable[Decimal | None]) -> tuple[Decimal, ...]:
    result: list[Decimal] = []
    for value in values:
        if value is None:
            raise ValueError("complete period metric must be available")
        result.append(value)
    return tuple(result)


def _missing_return_id(ticker: str, trade_date: date, next_trade_date: date) -> str:
    return f"{ticker}@{trade_date.isoformat()}->{next_trade_date.isoformat()}"


def _metric_stats(values: tuple[Decimal, ...]) -> dict[str, statistics.StatisticResult]:
    return {
        "cagr": statistics.cagr(values),
        "annualized_volatility": statistics.annualized_volatility(values),
        "sharpe_ratio": statistics.sharpe_ratio(values),
        "max_drawdown": statistics.max_drawdown(values),
    }


def _quintile_aggregate(
    values: tuple[statistics.StatisticResult, ...],
) -> dict[str, statistics.StatisticResult]:
    if not values:
        unavailable = statistics.StatisticResult(None, "unavailable", "no_complete_periods")
        return {"average_return": unavailable, "cagr": unavailable}
    first_unavailable = next((item for item in values if item.status != "available"), None)
    if first_unavailable is not None:
        unavailable = statistics.StatisticResult(
            None,
            first_unavailable.status,
            first_unavailable.reason_code,
        )
        return {"average_return": unavailable, "cagr": unavailable}
    returns = _decimal_values(item.value for item in values)
    return {
        "average_return": statistics.average_turnover(returns),
        "cagr": statistics.cagr(returns),
    }


def _sensitivity(
    bps: Decimal, periods: tuple[BacktestPeriod, ...], complete: tuple[BacktestPeriod, ...]
) -> CostSensitivity:
    net_returns: list[TypedValue] = []
    spy_returns: list[TypedValue] = []
    universe_returns: list[TypedValue] = []
    complete_values: list[Decimal] = []
    for period in periods:
        if period.status == "available":
            assert period.gross_return.value is not None and period.turnover.value is not None
            _, net = calculate_cost_and_net_return(period.gross_return.value, period.turnover.value, bps)
            net_returns.append(TypedValue.available(net))
            spy_returns.append(period.spy_return)
            universe_returns.append(period.universe_return)
            complete_values.append(net)
        else:
            net_returns.append(_unavailable(period.reason_code))
            spy_returns.append(_unavailable(period.reason_code))
            universe_returns.append(_unavailable(period.reason_code))
    status: _STATUS = "available" if complete else "unavailable"
    reason = "computed" if complete else "no_complete_periods"
    return CostSensitivity(
        round_trip_cost_bps=bps,
        net_returns=tuple(net_returns),
        cagr=statistics.cagr(tuple(complete_values)),
        max_drawdown=statistics.max_drawdown(tuple(complete_values)),
        benchmark_returns=tuple(spy_returns),
        universe_returns=tuple(universe_returns),
        status=status,
        reason_code=reason,
    )


def _baseline_summary(
    periods: tuple[BacktestPeriod, ...],
    complete: tuple[BacktestPeriod, ...],
    baseline: CostSensitivity,
) -> BaselineSummary:
    strategy_returns = _decimal_values(
        baseline.net_returns[index].value
        for index, item in enumerate(periods)
        if item.status == "available"
    )
    spy_returns = _decimal_values(item.spy_return.value for item in complete)
    universe_returns = _decimal_values(item.universe_return.value for item in complete)
    strategy = _metric_stats(strategy_returns)
    benchmarks = {
        "SPY_total_return": _metric_stats(spy_returns),
        "Universe_equal_weight": _metric_stats(universe_returns),
    }
    excess = statistics.excess_cagrs(strategy_returns, {
        "SPY_total_return": spy_returns,
        "Universe_equal_weight": universe_returns,
    })
    ic_inputs: list[tuple[Mapping[object, object], Mapping[object, object]]] = [
        (
            cast(Mapping[object, object], item.scores),
            cast(Mapping[object, object], item.forward_returns),
        )
        for item in complete
    ]
    aggregate_ic = statistics.aggregate_spearman_ic(ic_inputs)
    quintiles: dict[str, list[statistics.StatisticResult]] = {f"Q{index}": [] for index in range(1, 6)}
    for item in complete:
        result = statistics.quintile_returns(
            cast(Mapping[object, object], item.scores),
            cast(Mapping[object, object], item.forward_returns),
        )
        for name in quintiles:
            quintiles[name].append(result[name])
    quintile_returns = {name: tuple(values) for name, values in quintiles.items()}
    return BaselineSummary(
        net_cost_bps=BASELINE_ROUND_TRIP_COST_BPS,
        complete_period_count=len(complete),
        strategy=strategy,
        benchmarks=benchmarks,
        excess_cagrs=excess,
        aggregate_spearman_ic=aggregate_ic,
        quintile_returns=quintile_returns,
        quintile_aggregates={
            name: _quintile_aggregate(values) for name, values in quintile_returns.items()
        },
        average_turnover=statistics.average_turnover(
            _decimal_values(item.turnover.value for item in complete)
        ),
        annualized_turnover=statistics.annualized_turnover(
            _decimal_values(item.turnover.value for item in complete)
        ),
        status="available" if complete else "unavailable",
        reason_code="computed" if complete else "no_complete_periods",
    )


def run_walk_forward(inputs: WalkForwardInput) -> WalkForwardResult:
    """Run the fixed-universe monthly walk-forward calculation."""
    if not isinstance(inputs, WalkForwardInput):
        raise ValueError("run_walk_forward requires WalkForwardInput")
    manifest = inputs.universe
    if "survivorship_bias_known" not in manifest.known_biases:
        raise ValueError("UniverseManifest must disclose survivorship_bias_known")
    universe = tuple(sorted(manifest.tickers))
    if len(set(universe)) != len(universe):
        raise ValueError("UniverseManifest tickers must be unique")
    if not 50 <= len(universe) <= 100:
        raise ValueError("UniverseManifest tickers must contain 50 to 100 tickers")
    specs = tuple(sorted(inputs.rebalance_specs, key=lambda item: (item.trade_date, item.period_id or "")))
    if len({item.period_id for item in specs}) != len(specs):
        raise ValueError("rebalance period_id values must be unique")
    if any(specs[index].trade_date >= specs[index + 1].trade_date for index in range(len(specs) - 1)):
        raise ValueError("trade dates must be strictly increasing")
    if any(spec.next_trade_date is None for spec in specs[:-1]):
        raise ValueError("only the final period may be terminal")

    levels = _level_map(inputs.total_return_levels, {*universe, _SPY})
    groups = _snapshot_groups(inputs.snapshot_scores, universe)
    previous: dict[str, Decimal] = {**{ticker: _ZERO for ticker in universe}, CASH: _ONE}
    periods: list[BacktestPeriod] = []

    for spec in specs:
        selection = _select_snapshot(groups, spec.rebalance_anchor)
        snapshot_id_by_ticker: dict[str, str] = {}
        if selection is None:
            selected_ids: tuple[str, ...] = ()
            signal_as_of = None
            score_version = None
            scores: dict[str, Decimal] = {}
            eligible_count = 0
            selected_tickers: tuple[str, ...] = ()
            target_weights = None
            turnover = _unavailable("no_signal_snapshot")
            cost = _unavailable("no_signal_snapshot")
            gross = _unavailable("no_signal_snapshot")
            net = _unavailable("no_signal_snapshot")
            spy = _unavailable("no_signal_snapshot")
            universe_return = _unavailable("no_signal_snapshot")
            coverage = {
                "strategy": Coverage(0, 0, _ZERO),
                "SPY": Coverage(0, 0, _ZERO),
                "Universe_equal_weight": Coverage(0, 0, _ZERO),
            }
            forward: dict[str, Decimal] = {}
            missing: tuple[str, ...] = ()
            status: _PERIOD_STATUS = "unavailable"
            reason = "no_signal_snapshot"
        else:
            signal_as_of, records = selection
            selected_ids = tuple(sorted(record.snapshot_id for record in records))
            snapshot_id_by_ticker = {
                ticker: snapshot_id
                for ticker, snapshot_id in sorted(
                    (record.ticker, record.snapshot_id) for record in records
                )
            }
            score_version = records[0].score_version
            scores = {record.ticker: record.score for record in records}
            portfolio = build_target_portfolio(universe, signal_as_of, scores, previous)
            eligible_count = portfolio.eligible_count
            selected_tickers = portfolio.selected_tickers
            target_weights = portfolio.target_weights
            if target_weights is None or portfolio.turnover is None:
                turnover = _unavailable(portfolio.reason_code)
                cost = _unavailable(portfolio.reason_code)
                gross = _unavailable(portfolio.reason_code)
                net = _unavailable(portfolio.reason_code)
                spy = _unavailable(portfolio.reason_code)
                universe_return = _unavailable(portfolio.reason_code)
                coverage = {
                    "strategy": Coverage(0, 0, _ZERO),
                    "SPY": Coverage(0, 0, _ZERO),
                    "Universe_equal_weight": Coverage(0, 0, _ZERO),
                }
                forward = {}
                missing = ()
                status = "unavailable"
                reason = portfolio.reason_code
            elif spec.next_trade_date is None:
                turnover = TypedValue.available(portfolio.turnover)
                cost_value, _ = calculate_cost_and_net_return(_ZERO, portfolio.turnover, BASELINE_ROUND_TRIP_COST_BPS)
                cost = TypedValue.available(cost_value)
                gross = _unavailable("no_next_trade_date")
                net = _unavailable("no_next_trade_date")
                spy = _unavailable("no_next_trade_date")
                universe_return = _unavailable("no_next_trade_date")
                coverage = {
                    "strategy": Coverage(len(selected_tickers), 0, _ZERO),
                    "SPY": Coverage(1, 0, _ZERO),
                    "Universe_equal_weight": Coverage(len(universe), 0, _ZERO),
                }
                forward = {}
                missing = ()
                status = "terminal"
                reason = "no_next_trade_date"
            else:
                turnover = TypedValue.available(portfolio.turnover)
                forward, strategy_missing = _forward_returns(levels, selected_tickers, spec.trade_date, spec.next_trade_date)
                benchmark_returns, spy_missing = _forward_returns(levels, (_SPY,), spec.trade_date, spec.next_trade_date)
                universe_returns, universe_missing = _forward_returns(levels, universe, spec.trade_date, spec.next_trade_date)
                coverage = {
                    "strategy": _coverage(selected_tickers, forward),
                    "SPY": _coverage((_SPY,), benchmark_returns),
                    "Universe_equal_weight": _coverage(universe, universe_returns),
                }
                missing_tickers = tuple(
                    sorted(set(strategy_missing) | set(spy_missing) | set(universe_missing))
                )
                missing = tuple(
                    _missing_return_id(ticker, spec.trade_date, spec.next_trade_date)
                    for ticker in missing_tickers
                )
                complete_period = not missing and len(forward) == len(selected_tickers) and len(benchmark_returns) == 1 and len(universe_returns) == len(universe)
                if complete_period:
                    forward = {**universe_returns, **benchmark_returns}
                    with localcontext() as context:
                        context.prec = 28
                        gross_value = sum(
                            (
                                target_weights[ticker] * forward[ticker]
                                for ticker in selected_tickers
                            ),
                            _ZERO,
                        )
                    spy_value = forward[_SPY]
                    universe_value = _mean(forward[ticker] for ticker in universe)
                    cost_value, net_value = calculate_cost_and_net_return(gross_value, portfolio.turnover, BASELINE_ROUND_TRIP_COST_BPS)
                    gross = TypedValue.available(gross_value)
                    cost = TypedValue.available(cost_value)
                    net = TypedValue.available(net_value)
                    spy = TypedValue.available(spy_value)
                    universe_return = TypedValue.available(universe_value)
                    status = "available"
                    reason = "complete_period"
                else:
                    cost_value, _ = calculate_cost_and_net_return(_ZERO, portfolio.turnover, BASELINE_ROUND_TRIP_COST_BPS)
                    gross = _unavailable("missing_next_period_return")
                    cost = TypedValue.available(cost_value)
                    net = _unavailable("missing_next_period_return")
                    spy = _unavailable("missing_next_period_return")
                    universe_return = _unavailable("missing_next_period_return")
                    status = "unavailable"
                    reason = "missing_next_period_return"

        cost_return = cost
        period = BacktestPeriod(
            period_id=spec.period_id or "",
            rebalance_anchor=spec.rebalance_anchor,
            signal_as_of=signal_as_of,
            trade_date=spec.trade_date,
            next_trade_date=spec.next_trade_date,
            snapshot_ids=selected_ids,
            score_version=score_version,
            eligible_count=eligible_count,
            selected_tickers=selected_tickers,
            previous_weights=previous,
            target_weights=target_weights,
            scores=scores,
            forward_returns=forward,
            turnover=turnover,
            cost=cost,
            round_trip_cost_bps=BASELINE_ROUND_TRIP_COST_BPS,
            cost_return=cost_return,
            gross_return=gross,
            net_return=net,
            spy_return=spy,
            universe_return=universe_return,
            status=status,
            reason_code=reason,
            coverage=coverage,
            missing_return_ids=missing,
            provenance={
                "universe_id": manifest.universe_id,
                "snapshot_ids": selected_ids,
                "snapshot_id_by_ticker": snapshot_id_by_ticker,
                "signal_as_of": signal_as_of,
                "trade_date": spec.trade_date,
                "next_trade_date": spec.next_trade_date,
                "source": "total_return_levels",
            },
        )
        periods.append(period)
        if period.status == "available" and period.target_weights is not None:
            previous = dict(period.target_weights)

    period_tuple = tuple(periods)
    complete = tuple(period for period in period_tuple if period.status == "available")
    sensitivities = {
        bps: _sensitivity(bps, period_tuple, complete) for bps in ROUND_TRIP_COST_SENSITIVITY_BPS
    }
    baseline = sensitivities[BASELINE_ROUND_TRIP_COST_BPS]
    summary = _baseline_summary(period_tuple, complete, baseline)
    known_biases = tuple(sorted(manifest.known_biases))
    return WalkForwardResult(
        universe_id=manifest.universe_id,
        strategy_version=inputs.strategy_version,
        backtest_version=inputs.backtest_version,
        artifact_schema_version=inputs.artifact_schema_version,
        periods_per_year=statistics.PERIODS_PER_YEAR,
        conversion_version=statistics.CONVERSION_VERSION,
        tolerance=statistics.TOLERANCE,
        known_biases=known_biases,
        data_quality={
            "survivorship_bias_known": "survivorship_bias_known" in known_biases,
            "complete_period_count": len(complete),
            "period_count": len(period_tuple),
        },
        periods=period_tuple,
        baseline_summary=summary,
        cost_sensitivity=sensitivities,
        provenance={
            "universe_manifest_version": manifest.manifest_version,
            "membership_source": manifest.membership_source,
            "membership_basis": manifest.membership_basis,
            "snapshot_count": len(inputs.snapshot_scores),
            "level_count": len(inputs.total_return_levels),
        },
    )


def write_backtest_artifact(result: WalkForwardResult, output_path: str | Path) -> Path:
    """Write one deterministic, UTF-8 JSON backtest artifact."""
    if not isinstance(result, WalkForwardResult):
        raise ValueError("result must be a WalkForwardResult")
    if not isinstance(output_path, (str, Path)):
        raise ValueError("output_path must be a string or Path")
    if isinstance(output_path, str) and not output_path.strip():
        raise ValueError("output_path must not be empty")
    path = Path(output_path)
    if path.exists() and path.is_dir():
        raise ValueError("output_path must be a file path")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(result.to_artifact_json().encode("utf-8"))
    except OSError as exc:
        raise ValueError("unable to write backtest artifact") from exc
    return path


__all__ = [
    "BacktestPeriod",
    "BaselineSummary",
    "CostSensitivity",
    "Coverage",
    "RebalanceSpec",
    "SnapshotScore",
    "TotalReturnLevel",
    "TypedValue",
    "WalkForwardInput",
    "WalkForwardResult",
    "run_walk_forward",
    "write_backtest_artifact",
]
