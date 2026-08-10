"""Deterministic top-quintile portfolio construction and transaction costs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, localcontext
import hashlib
import json
from types import MappingProxyType
from typing import Literal


CASH = "CASH"
COMPOSITE_SCORE_VERSION = "composite-ranking-v1"
BASELINE_ROUND_TRIP_COST_BPS = Decimal("10")
ROUND_TRIP_COST_SENSITIVITY_BPS = (
    Decimal("0"),
    Decimal("5"),
    Decimal("10"),
    Decimal("20"),
)
_ZERO = Decimal("0")
_ONE = Decimal("1")
_TOP_FRACTION = Decimal("0.20")
_DECIMAL_PRECISION = 28


def _finite_decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} 必须是有限 Decimal")
    return value


def _canonical_signal_as_of(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("signal_as_of 必须是 timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _freeze_weights(weights: Mapping[str, Decimal]) -> Mapping[str, Decimal]:
    if not isinstance(weights, Mapping):
        raise ValueError("weights 必须是 mapping")
    values: list[tuple[str, Decimal]] = []
    for ticker, weight in weights.items():
        if not isinstance(ticker, str) or not ticker:
            raise ValueError("weight key 必须是非空 ticker")
        values.append((ticker, _finite_decimal(weight, f"weight[{ticker}]")))
    return MappingProxyType(dict(sorted(values)))


def _weights_to_dict(weights: Mapping[str, Decimal]) -> dict[str, str]:
    return {ticker: str(weight) for ticker, weight in weights.items()}


@dataclass(frozen=True)
class PortfolioResult:
    """Immutable target weights and turnover for one signal period."""

    signal_as_of: datetime
    score_version: str
    eligible_count: int
    selected_tickers: tuple[str, ...]
    previous_weights: Mapping[str, Decimal]
    target_weights: Mapping[str, Decimal] | None
    turnover: Decimal | None
    status: Literal["available", "unavailable"]
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal_as_of", _canonical_signal_as_of(self.signal_as_of))
        object.__setattr__(self, "selected_tickers", tuple(self.selected_tickers))
        object.__setattr__(self, "previous_weights", _freeze_weights(self.previous_weights))
        if self.target_weights is not None:
            object.__setattr__(self, "target_weights", _freeze_weights(self.target_weights))
        if self.turnover is not None:
            _finite_decimal(self.turnover, "turnover")

    def to_dict(self) -> dict[str, object]:
        return {
            "signal_as_of": self.signal_as_of.isoformat(),
            "score_version": self.score_version,
            "eligible_count": self.eligible_count,
            "selected_tickers": list(self.selected_tickers),
            "previous_weights": _weights_to_dict(self.previous_weights),
            "target_weights": (
                None if self.target_weights is None else _weights_to_dict(self.target_weights)
            ),
            "turnover": None if self.turnover is None else str(self.turnover),
            "status": self.status,
            "reason_code": self.reason_code,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def stable_hash(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    @property
    def hash(self) -> str:
        return self.stable_hash

    def __hash__(self) -> int:
        return int(self.stable_hash[:16], 16)


def _validate_universe(universe: Sequence[str]) -> tuple[str, ...]:
    if isinstance(universe, (str, bytes)):
        raise ValueError("universe 必须是 ticker 序列")
    try:
        tickers = list(universe)
    except TypeError as exc:
        raise ValueError("universe 必须是 ticker 序列") from exc
    if not 50 <= len(tickers) <= 100:
        raise ValueError("universe 必须包含 50 到 100 个 ticker")
    for ticker in tickers:
        if (
            not isinstance(ticker, str)
            or not ticker
            or ticker.strip() != ticker
            or not ticker.isascii()
        ):
            raise ValueError("universe ticker 必须是非空 ASCII 字符串")
    if len(set(tickers)) != len(tickers):
        raise ValueError("universe ticker 必须唯一")
    if CASH in tickers:
        raise ValueError("universe 不得包含保留 ticker CASH")
    return tuple(sorted(tickers))


def _validate_scores(scores: Mapping[str, Decimal]) -> dict[str, Decimal]:
    if not isinstance(scores, Mapping):
        raise ValueError("scores 必须是 mapping")
    validated: dict[str, Decimal] = {}
    for ticker, score in scores.items():
        if not isinstance(ticker, str) or not ticker:
            raise ValueError("score key 必须是非空 ticker")
        validated[ticker] = _finite_decimal(score, f"score[{ticker}]")
    return validated


def _validate_previous_weights(
    tickers: Sequence[str], previous_weights: Mapping[str, Decimal]
) -> Mapping[str, Decimal]:
    if not isinstance(previous_weights, Mapping):
        raise ValueError("previous_weights 必须是 mapping")
    expected_keys = {*tickers, CASH}
    actual_keys = set(previous_weights)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise ValueError(f"previous_weights keys 不匹配: missing={missing}, extra={extra}")

    validated: dict[str, Decimal] = {}
    for ticker in expected_keys:
        weight = _finite_decimal(previous_weights[ticker], f"previous_weights[{ticker}]")
        if not _ZERO <= weight <= _ONE:
            raise ValueError("previous_weights 必须满足 0 <= weight <= 1")
        validated[ticker] = weight
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        if sum(validated.values(), _ZERO) != _ONE:
            raise ValueError("previous_weights 权重和必须精确等于 1")
    return _freeze_weights(validated)


def _target_weights(tickers: Sequence[str], selected: Sequence[str]) -> Mapping[str, Decimal]:
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        selected_count = len(selected)
        base_weight = _ONE / Decimal(selected_count)
        weights = {ticker: _ZERO for ticker in (*tickers, CASH)}
        for ticker in selected[:-1]:
            weights[ticker] = base_weight
        base_total = sum((base_weight for _ in selected[:-1]), _ZERO)
        weights[selected[-1]] = _ONE - base_total
    return _freeze_weights(weights)


def _turnover(
    target_weights: Mapping[str, Decimal], previous_weights: Mapping[str, Decimal]
) -> Decimal:
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        turnover = Decimal("0.5") * sum(
            (abs(target_weights[ticker] - previous_weights[ticker]) for ticker in target_weights),
            _ZERO,
        )
    return _finite_decimal(turnover, "turnover")


def build_target_portfolio(
    universe: Sequence[str],
    signal_as_of: datetime,
    scores: Mapping[str, Decimal],
    previous_weights: Mapping[str, Decimal],
) -> PortfolioResult:
    """Build a deterministic top-20% equal-weight target portfolio."""
    tickers = _validate_universe(universe)
    score_by_ticker = _validate_scores(scores)
    previous = _validate_previous_weights(tickers, previous_weights)
    eligible = [(ticker, score_by_ticker[ticker]) for ticker in tickers if ticker in score_by_ticker]
    ranked = sorted(eligible, key=lambda item: (-item[1], item[0]))
    eligible_count = len(ranked)

    if eligible_count == 0:
        return PortfolioResult(
            signal_as_of=signal_as_of,
            score_version=COMPOSITE_SCORE_VERSION,
            eligible_count=0,
            selected_tickers=(),
            previous_weights=previous,
            target_weights=None,
            turnover=None,
            status="unavailable",
            reason_code="no_eligible_scores",
        )

    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        selected_count = int(
            (_TOP_FRACTION * Decimal(eligible_count)).to_integral_value(rounding=ROUND_CEILING)
        )
    selected = tuple(ticker for ticker, _ in ranked[:selected_count])
    target = _target_weights(tickers, selected)
    return PortfolioResult(
        signal_as_of=signal_as_of,
        score_version=COMPOSITE_SCORE_VERSION,
        eligible_count=eligible_count,
        selected_tickers=selected,
        previous_weights=previous,
        target_weights=target,
        turnover=_turnover(target, previous),
        status="available",
        reason_code="target_weights_built",
    )


def calculate_cost_and_net_return(
    gross_return: Decimal,
    turnover: Decimal,
    round_trip_cost_bps: Decimal,
) -> tuple[Decimal, Decimal]:
    """Return ``(cost_return, net_return)`` using the fixed bps denominator."""
    gross = _finite_decimal(gross_return, "gross_return")
    turnover_value = _finite_decimal(turnover, "turnover")
    bps = _finite_decimal(round_trip_cost_bps, "round_trip_cost_bps")
    if turnover_value < _ZERO:
        raise ValueError("turnover 必须非负")
    if bps < _ZERO:
        raise ValueError("round_trip_cost_bps 必须非负")
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        cost_return = turnover_value * bps / Decimal("10000")
        net_return = gross - cost_return
    _finite_decimal(cost_return, "cost_return")
    _finite_decimal(net_return, "net_return")
    return cost_return, net_return


__all__ = [
    "BASELINE_ROUND_TRIP_COST_BPS",
    "CASH",
    "COMPOSITE_SCORE_VERSION",
    "PortfolioResult",
    "ROUND_TRIP_COST_SENSITIVITY_BPS",
    "build_target_portfolio",
    "calculate_cost_and_net_return",
]
