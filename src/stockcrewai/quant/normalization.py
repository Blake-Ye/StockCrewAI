"""Deterministic cross-sectional factor normalization."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal

from stockcrewai.models.quant import FactorObservation
from stockcrewai.quant.factors import FACTOR_DIRECTIONS


_ZERO = Decimal("0")
_ONE = Decimal("1")
_TWO = Decimal("2")


def _validate_parameters(
    winsor_lower: Decimal, winsor_upper: Decimal, normalization_version: str
) -> None:
    if not isinstance(winsor_lower, Decimal) or not winsor_lower.is_finite():
        raise ValueError("winsor_lower 必须是有限 Decimal")
    if not isinstance(winsor_upper, Decimal) or not winsor_upper.is_finite():
        raise ValueError("winsor_upper 必须是有限 Decimal")
    if not _ZERO <= winsor_lower < winsor_upper <= _ONE:
        raise ValueError("winsor 边界必须满足 0 <= lower < upper <= 1")
    if not isinstance(normalization_version, str) or not normalization_version.strip():
        raise ValueError("normalization_version 不能为空")


def _linear_quantile(values: Sequence[Decimal], probability: Decimal) -> Decimal:
    ordered = sorted(values)
    position = probability * Decimal(len(ordered) - 1)
    lower_index = int(position)
    if lower_index >= len(ordered) - 1:
        return ordered[-1]
    fraction = position - Decimal(lower_index)
    lower = ordered[lower_index]
    upper = ordered[lower_index + 1]
    return lower + (upper - lower) * fraction


def _winsorize(values: Sequence[Decimal], lower: Decimal, upper: Decimal) -> tuple[Decimal, ...]:
    lower_bound = _linear_quantile(values, lower)
    upper_bound = _linear_quantile(values, upper)
    return tuple(min(max(value, lower_bound), upper_bound) for value in values)


def _midrank_percentiles(values: Sequence[Decimal]) -> tuple[Decimal, ...]:
    ordered = sorted(values)
    denominator = Decimal(len(values) - 1)
    percentiles: list[Decimal] = []
    for value in values:
        left = bisect_left(ordered, value)
        right = bisect_right(ordered, value)
        average_rank = Decimal(left + 1 + right) / _TWO
        percentiles.append((average_rank - _ONE) / denominator)
    return tuple(percentiles)


def _is_available(observation: FactorObservation) -> bool:
    return (
        observation.status == "available"
        and isinstance(observation.raw_value, Decimal)
        and observation.raw_value.is_finite()
    )


def _sort_key(observation: FactorObservation) -> tuple[object, ...]:
    return (
        observation.factor_id,
        observation.as_of.astimezone(timezone.utc).isoformat(),
        observation.peer_group,
        observation.snapshot_id,
        observation.ticker,
        observation.formula_version,
        observation.status,
        observation.reason_code,
        "" if observation.raw_value is None else str(observation.raw_value),
        "" if observation.normalized_value is None else str(observation.normalized_value),
        tuple(observation.evidence_ids),
        tuple(observation.calculation_ids),
    )


def normalize_cross_section(
    observations: Sequence[FactorObservation],
    winsor_lower: Decimal,
    winsor_upper: Decimal,
    normalization_version: str,
) -> tuple[FactorObservation, ...]:
    """Winsorize and rank observations within their fixed peer groups."""
    _validate_parameters(winsor_lower, winsor_upper, normalization_version)

    indexed = list(enumerate(observations))
    groups: defaultdict[tuple[str, datetime, str], list[tuple[int, FactorObservation]]] = defaultdict(list)
    for index, observation in indexed:
        if observation.factor_id not in FACTOR_DIRECTIONS:
            raise ValueError(f"unknown factor id: {observation.factor_id}")
        groups[(observation.factor_id, observation.as_of, observation.peer_group)].append(
            (index, observation)
        )

    updates: dict[int, dict[str, object]] = {}
    for (factor_id, _, _), members in groups.items():
        available = [(index, observation) for index, observation in members if _is_available(observation)]
        peer_count = len(available)
        if peer_count == 0:
            continue
        if peer_count < 2:
            index, _ = available[0]
            updates[index] = {
                "normalized_value": None,
                "peer_count": peer_count,
                "status": "unavailable",
                "reason_code": "insufficient_peer_sample",
            }
            continue

        raw_values = [observation.raw_value for _, observation in available]
        assert all(isinstance(value, Decimal) for value in raw_values)
        clipped_values = _winsorize(raw_values, winsor_lower, winsor_upper)
        percentiles = _midrank_percentiles(clipped_values)
        direction = FACTOR_DIRECTIONS[factor_id]
        for (index, _), percentile in zip(available, percentiles, strict=True):
            normalized_value = percentile if direction == "high" else _ONE - percentile
            if not normalized_value.is_finite():
                raise ValueError("normalized_value 必须是有限 Decimal")
            updates[index] = {
                "normalized_value": normalized_value,
                "peer_count": peer_count,
            }

    return tuple(
        observation.model_copy(deep=True, update=updates.get(index, {}))
        for index, observation in sorted(indexed, key=lambda item: _sort_key(item[1]))
    )


__all__ = ["normalize_cross_section"]
