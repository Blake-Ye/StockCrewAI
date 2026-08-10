"""Deterministic composite factor scores and stable ordinal ranks."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from typing import Literal

from stockcrewai.models.quant import FactorObservation
from stockcrewai.quant.factors import FACTOR_DIRECTIONS


_AVAILABLE_REASON = "scored"
_UNAVAILABLE_REASON = "no_available_factors"
_ZERO = Decimal("0")
_ONE = Decimal("1")
_STATUS = Literal["available", "unavailable"]


@dataclass(frozen=True)
class CompositeScore:
    """A JSON-safe, versioned composite score for one ticker and peer group."""

    composite_version: str
    as_of: datetime
    peer_group: str
    ticker: str
    score: Decimal | None
    available_factor_count: int
    factor_ids: tuple[str, ...]
    rank: int | None
    status: _STATUS
    reason_code: str

    def to_dict(self) -> dict[str, object]:
        return {
            "composite_version": self.composite_version,
            "as_of": self.as_of.isoformat(),
            "peer_group": self.peer_group,
            "ticker": self.ticker,
            "score": None if self.score is None else str(self.score),
            "available_factor_count": self.available_factor_count,
            "factor_ids": list(self.factor_ids),
            "rank": self.rank,
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
    def json(self) -> str:
        return self.to_json()

    @property
    def stable_hash(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    @property
    def hash(self) -> str:
        return self.stable_hash


def _canonical_as_of(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of 必须是 timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _validate_observation(observation: object) -> datetime:
    if not isinstance(observation, FactorObservation):
        raise ValueError("observations 只接受 FactorObservation")
    if observation.factor_id not in FACTOR_DIRECTIONS:
        raise ValueError(f"unknown factor id: {observation.factor_id}")
    if observation.status not in {"available", "unavailable", "not_applicable", "invalid"}:
        raise ValueError(f"invalid observation status: {observation.status}")
    normalized_value = observation.normalized_value
    if normalized_value is not None:
        if not isinstance(normalized_value, Decimal) or not normalized_value.is_finite():
            raise ValueError("normalized_value 必须是有限 Decimal")
        if not _ZERO <= normalized_value <= _ONE:
            raise ValueError("normalized_value 必须满足 0 <= value <= 1")
    if observation.status == "available" and normalized_value is None:
        raise ValueError("available observation normalized_value 不能为空")
    return _canonical_as_of(observation.as_of)


def _average(values: Sequence[Decimal]) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        score = sum(values, Decimal("0")) / Decimal(len(values))
    if not score.is_finite():
        raise ValueError("composite score 必须是有限 Decimal")
    return score


def compute_composite_scores(
    observations: Sequence[FactorObservation],
    composite_version: str,
) -> tuple[CompositeScore, ...]:
    """Average available normalized factors and assign deterministic ranks."""
    if not isinstance(composite_version, str) or not composite_version.strip():
        raise ValueError("composite_version 不能为空")
    version = composite_version.strip()

    try:
        items = list(observations)
    except TypeError as exc:
        raise ValueError("observations 必须是 FactorObservation 序列") from exc

    groups: defaultdict[tuple[datetime, str, str], list[FactorObservation]] = defaultdict(list)
    seen: set[tuple[datetime, str, str, str]] = set()
    for observation in items:
        as_of = _validate_observation(observation)
        assert isinstance(observation, FactorObservation)
        duplicate_key = (as_of, observation.peer_group, observation.ticker, observation.factor_id)
        if duplicate_key in seen:
            raise ValueError(
                "duplicate observation for "
                f"{observation.ticker}/{observation.factor_id}/{as_of.isoformat()}/{observation.peer_group}"
            )
        seen.add(duplicate_key)
        groups[(as_of, observation.peer_group, observation.ticker)].append(observation)

    by_partition: defaultdict[tuple[datetime, str], list[CompositeScore]] = defaultdict(list)
    for (as_of, peer_group, ticker), members in groups.items():
        available = [item for item in members if item.status == "available"]
        if available:
            ordered = sorted(available, key=lambda item: item.factor_id)
            available_values: list[Decimal] = []
            for available_observation in ordered:
                normalized_value = available_observation.normalized_value
                assert isinstance(normalized_value, Decimal) and normalized_value.is_finite()
                available_values.append(normalized_value)
            score = _average(available_values)
            result = CompositeScore(
                composite_version=version,
                as_of=as_of,
                peer_group=peer_group,
                ticker=ticker,
                score=score,
                available_factor_count=len(ordered),
                factor_ids=tuple(item.factor_id for item in ordered),
                rank=None,
                status="available",
                reason_code=_AVAILABLE_REASON,
            )
        else:
            result = CompositeScore(
                composite_version=version,
                as_of=as_of,
                peer_group=peer_group,
                ticker=ticker,
                score=None,
                available_factor_count=0,
                factor_ids=(),
                rank=None,
                status="unavailable",
                reason_code=_UNAVAILABLE_REASON,
            )
        by_partition[(as_of, peer_group)].append(result)

    results: list[CompositeScore] = []
    for composite_members in by_partition.values():
        def score_key(composite: CompositeScore) -> tuple[Decimal, str]:
            score = composite.score
            assert score is not None
            return -score, composite.ticker

        ranked = sorted(
            (composite for composite in composite_members if composite.score is not None),
            key=score_key,
        )
        ranks = {composite.ticker: index for index, composite in enumerate(ranked, start=1)}
        results.extend(
            replace(composite, rank=ranks[composite.ticker])
            if composite.score is not None
            else composite
            for composite in composite_members
        )

    return tuple(
        sorted(
            results,
            key=lambda item: (
                item.as_of,
                item.peer_group,
                item.rank is None,
                item.rank if item.rank is not None else 0,
                item.ticker,
            ),
        )
    )


__all__ = ["CompositeScore", "compute_composite_scores"]
