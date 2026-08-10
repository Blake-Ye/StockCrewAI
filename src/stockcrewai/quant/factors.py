"""Deterministic, offline factor calculations for point-in-time snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, DecimalException, localcontext
from typing import Literal

from stockcrewai.models.profile import IssuerProfile
from stockcrewai.models.quant import FactorObservation, PointInTimeSnapshot


FactorDirection = Literal["high", "low"]
_FeatureDomain = Literal["financial", "market"]
_ObservationStatus = Literal["available", "unavailable", "not_applicable", "invalid"]


FACTOR_DIRECTIONS: dict[str, FactorDirection] = {
    "value.earnings_yield": "high",
    "value.fcf_yield": "high",
    "value.price_to_book": "low",
    "value.ev_to_ebitda": "low",
    "quality.roe": "high",
    "quality.roic": "high",
    "quality.operating_margin": "high",
    "quality.fcf_margin": "high",
    "quality.cash_conversion": "high",
    "quality.debt_to_equity": "low",
    "growth.revenue_cagr_3y": "high",
    "growth.eps_growth_3y": "high",
    "growth.fcf_growth_3y": "high",
    "market.momentum_12_1": "high",
    "risk.volatility_12m": "low",
    "risk.beta_12m": "low",
    "risk.max_drawdown_12m": "low",
}

_OPERATING_PROFILES = frozenset(
    {
        IssuerProfile.STANDARD_OPERATING,
        IssuerProfile.UTILITY,
        IssuerProfile.COMMODITY_PRODUCER,
        IssuerProfile.HOLDING_COMPANY,
    }
)
_PRICE_TO_BOOK_PROFILES = _OPERATING_PROFILES | frozenset(
    {IssuerProfile.BANK, IssuerProfile.INSURANCE, IssuerProfile.REIT}
)
_ROE_PROFILES = _PRICE_TO_BOOK_PROFILES
_DEBT_TO_EQUITY_PROFILES = _OPERATING_PROFILES | frozenset({IssuerProfile.REIT})
_MARKET_PROFILES = _OPERATING_PROFILES | frozenset(
    {IssuerProfile.BANK, IssuerProfile.INSURANCE, IssuerProfile.REIT}
)

_APPLICABLE_PROFILES: dict[str, frozenset[IssuerProfile]] = {
    "value.earnings_yield": _OPERATING_PROFILES,
    "value.fcf_yield": _OPERATING_PROFILES,
    "value.price_to_book": _PRICE_TO_BOOK_PROFILES,
    "value.ev_to_ebitda": _OPERATING_PROFILES,
    "quality.roe": _ROE_PROFILES,
    "quality.roic": _OPERATING_PROFILES,
    "quality.operating_margin": _OPERATING_PROFILES,
    "quality.fcf_margin": _OPERATING_PROFILES,
    "quality.cash_conversion": _OPERATING_PROFILES,
    "quality.debt_to_equity": _DEBT_TO_EQUITY_PROFILES,
    "growth.revenue_cagr_3y": _OPERATING_PROFILES,
    "growth.eps_growth_3y": _OPERATING_PROFILES,
    "growth.fcf_growth_3y": _OPERATING_PROFILES,
    "market.momentum_12_1": _MARKET_PROFILES,
    "risk.volatility_12m": _MARKET_PROFILES,
    "risk.beta_12m": _MARKET_PROFILES,
    "risk.max_drawdown_12m": _MARKET_PROFILES,
}

_Feature = tuple[_FeatureDomain, str]


def _read(snapshot: PointInTimeSnapshot, feature: _Feature) -> tuple[Decimal | None, str | None]:
    domain, key = feature
    source = getattr(snapshot, f"{domain}_features", None)
    if not isinstance(source, Mapping):
        return None, "invalid_input"
    if key not in source or source[key] is None:
        return None, "missing_input"
    value = source[key]
    if not isinstance(value, Decimal) or not value.is_finite():
        return None, "invalid_input"
    return value, None


def _read_many(
    snapshot: PointInTimeSnapshot, features: Sequence[_Feature]
) -> tuple[tuple[Decimal, ...] | None, str | None]:
    values: list[Decimal] = []
    for feature in features:
        value, reason = _read(snapshot, feature)
        if reason is not None:
            return None, reason
        assert value is not None
        values.append(value)
    return tuple(values), None


def _finite_result(value: Decimal) -> tuple[Decimal | None, str]:
    if not value.is_finite():
        return None, "invalid_result"
    return value, "validated_inputs"


def _divide(
    snapshot: PointInTimeSnapshot,
    numerator: _Feature,
    denominator: Sequence[_Feature],
    *,
    denominator_must_be_positive: bool,
) -> tuple[Decimal | None, str]:
    values, reason = _read_many(snapshot, (numerator, *denominator))
    if reason is not None:
        return None, reason
    assert values is not None
    numerator_value = values[0]
    denominator_values = values[1:]
    if denominator_must_be_positive and any(value <= 0 for value in denominator_values):
        return None, "non_positive_input"
    denominator_value = Decimal(1)
    try:
        for value in denominator_values:
            denominator_value *= value
        if not denominator_value.is_finite():
            return None, "invalid_result"
        if denominator_value == 0:
            return None, "zero_denominator"
        return _finite_result(numerator_value / denominator_value)
    except DecimalException:
        return None, "invalid_result"


def _subtract(
    snapshot: PointInTimeSnapshot, left: _Feature, right: _Feature
) -> tuple[Decimal | None, str]:
    values, reason = _read_many(snapshot, (left, right))
    if reason is not None:
        return None, reason
    assert values is not None
    try:
        return _finite_result(values[0] - values[1])
    except DecimalException:
        return None, "invalid_result"


def _single(snapshot: PointInTimeSnapshot, feature: _Feature) -> tuple[Decimal | None, str]:
    value, reason = _read(snapshot, feature)
    if reason is not None:
        return None, reason
    assert value is not None
    return value, "validated_inputs"


def _cagr(
    snapshot: PointInTimeSnapshot,
    current: _Feature,
    base: _Feature,
    *,
    bases_must_be_positive: bool,
) -> tuple[Decimal | None, str]:
    values, reason = _read_many(snapshot, (current, base))
    if reason is not None:
        return None, reason
    assert values is not None
    current_value, base_value = values
    if bases_must_be_positive:
        if current_value <= 0 or base_value <= 0:
            return None, "non_positive_input"
    elif current_value == 0 or base_value == 0:
        return None, "non_positive_input"
    elif (current_value < 0) != (base_value < 0):
        return None, "growth_base_sign_mismatch"

    try:
        with localcontext() as context:
            context.prec = 28
            ratio = current_value / base_value
            result = (ratio.ln() / Decimal(3)).exp() - Decimal(1)
        return _finite_result(result)
    except (DecimalException, ValueError):
        return None, "invalid_result"


def _raw_factor(
    snapshot: PointInTimeSnapshot, factor_id: str
) -> tuple[Decimal | None, str]:
    financial: _FeatureDomain = "financial"
    market: _FeatureDomain = "market"
    if factor_id == "value.earnings_yield":
        return _divide(snapshot, (financial, "diluted_eps"), ((market, "price"),), denominator_must_be_positive=True)
    if factor_id == "value.fcf_yield":
        return _divide(
            snapshot,
            (financial, "free_cash_flow"),
            ((market, "price"), (market, "shares")),
            denominator_must_be_positive=True,
        )
    if factor_id == "value.price_to_book":
        return _divide(
            snapshot,
            (market, "price"),
            ((financial, "book_value_per_share"),),
            denominator_must_be_positive=True,
        )
    if factor_id == "value.ev_to_ebitda":
        return _divide(
            snapshot,
            (financial, "enterprise_value"),
            ((financial, "ebitda"),),
            denominator_must_be_positive=True,
        )
    if factor_id == "quality.roe":
        return _divide(
            snapshot,
            (financial, "net_income"),
            ((financial, "average_equity"),),
            denominator_must_be_positive=True,
        )
    if factor_id == "quality.roic":
        return _divide(
            snapshot,
            (financial, "nopat"),
            ((financial, "invested_capital"),),
            denominator_must_be_positive=True,
        )
    if factor_id == "quality.operating_margin":
        return _divide(
            snapshot,
            (financial, "operating_income"),
            ((financial, "revenue"),),
            denominator_must_be_positive=True,
        )
    if factor_id == "quality.fcf_margin":
        return _divide(
            snapshot,
            (financial, "free_cash_flow"),
            ((financial, "revenue"),),
            denominator_must_be_positive=True,
        )
    if factor_id == "quality.cash_conversion":
        return _divide(
            snapshot,
            (financial, "cash_from_operations"),
            ((financial, "net_income"),),
            denominator_must_be_positive=False,
        )
    if factor_id == "quality.debt_to_equity":
        return _divide(
            snapshot,
            (financial, "total_debt"),
            ((financial, "total_equity"),),
            denominator_must_be_positive=True,
        )
    if factor_id == "growth.revenue_cagr_3y":
        return _cagr(
            snapshot,
            (financial, "revenue"),
            (financial, "revenue_3y_ago"),
            bases_must_be_positive=True,
        )
    if factor_id == "growth.eps_growth_3y":
        return _cagr(
            snapshot,
            (financial, "diluted_eps"),
            (financial, "eps_3y_ago"),
            bases_must_be_positive=False,
        )
    if factor_id == "growth.fcf_growth_3y":
        return _cagr(
            snapshot,
            (financial, "free_cash_flow"),
            (financial, "fcf_3y_ago"),
            bases_must_be_positive=False,
        )
    if factor_id == "market.momentum_12_1":
        return _subtract(snapshot, (market, "return_12m"), (market, "return_1m"))
    if factor_id == "risk.volatility_12m":
        return _single(snapshot, (market, "volatility_12m"))
    if factor_id == "risk.beta_12m":
        return _single(snapshot, (market, "beta_12m"))
    if factor_id == "risk.max_drawdown_12m":
        return _single(snapshot, (market, "max_drawdown_12m"))
    raise KeyError(f"unknown factor id: {factor_id}")


def _peer_group(snapshot: PointInTimeSnapshot) -> str:
    profile = snapshot.issuer_profile
    profile_value = profile.value if isinstance(profile, IssuerProfile) else "unknown"
    data_quality = snapshot.data_quality
    industry = data_quality.get("industry") if isinstance(data_quality, Mapping) else None
    industry_value = industry.strip() if isinstance(industry, str) and industry.strip() else "unknown"
    return f"{profile_value}:{industry_value}"


def _provenance(snapshot: PointInTimeSnapshot) -> tuple[list[str], list[str], bool]:
    evidence_ids = snapshot.available_evidence_ids
    calculation_ids = snapshot.available_calculation_ids
    source_ids = evidence_ids + calculation_ids
    valid = bool(source_ids) and all(isinstance(item, str) and item.strip() for item in source_ids)
    return sorted(set(evidence_ids)), sorted(set(calculation_ids)), valid


def _observation(
    snapshot: PointInTimeSnapshot,
    factor_id: str,
    formula_version: str,
    raw_value: Decimal | None,
    status: _ObservationStatus,
    reason_code: str,
    evidence_ids: list[str],
    calculation_ids: list[str],
) -> FactorObservation:
    return FactorObservation(
        factor_id=factor_id,
        formula_version=formula_version,
        snapshot_id=snapshot.snapshot_id,
        as_of=snapshot.as_of,
        ticker=snapshot.ticker,
        raw_value=raw_value,
        normalized_value=None,
        peer_group=_peer_group(snapshot),
        peer_count=0,
        evidence_ids=evidence_ids if status == "available" else [],
        calculation_ids=calculation_ids if status == "available" else [],
        status=status,
        reason_code=reason_code,
    )


def compute_factor_observations(
    snapshots: Sequence[PointInTimeSnapshot], formula_version: str
) -> tuple[FactorObservation, ...]:
    """Compute all fixed factors in stable snapshot and registry order."""
    if not isinstance(formula_version, str) or not formula_version.strip():
        raise ValueError("formula_version 不能为空")
    formula_version = formula_version.strip()
    observations: list[FactorObservation] = []
    evidence_by_snapshot: dict[str, tuple[list[str], list[str], bool]] = {}
    for snapshot in sorted(snapshots, key=lambda item: item.snapshot_id):
        evidence_ids, calculation_ids, provenance_valid = _provenance(snapshot)
        evidence_by_snapshot[snapshot.snapshot_id] = (
            evidence_ids,
            calculation_ids,
            provenance_valid,
        )
        profile = snapshot.issuer_profile
        for factor_id in FACTOR_DIRECTIONS:
            if not isinstance(profile, IssuerProfile):
                observations.append(
                    _observation(
                        snapshot,
                        factor_id,
                        formula_version,
                        None,
                        "invalid",
                        "invalid_input",
                        [],
                        [],
                    )
                )
                continue
            if profile not in _APPLICABLE_PROFILES[factor_id]:
                observations.append(
                    _observation(
                        snapshot,
                        factor_id,
                        formula_version,
                        None,
                        "not_applicable",
                        "profile_not_applicable",
                        [],
                        [],
                    )
                )
                continue
            if not provenance_valid:
                observations.append(
                    _observation(
                        snapshot,
                        factor_id,
                        formula_version,
                        None,
                        "invalid",
                        "invalid_input",
                        [],
                        [],
                    )
                )
                continue
            raw_value, reason_code = _raw_factor(snapshot, factor_id)
            status: _ObservationStatus = "available" if reason_code == "validated_inputs" else (
                "invalid" if reason_code in {"invalid_input", "invalid_result"} else "unavailable"
            )
            observations.append(
                _observation(
                    snapshot,
                    factor_id,
                    formula_version,
                    raw_value if status == "available" else None,
                    status,
                    reason_code,
                    evidence_ids,
                    calculation_ids,
                )
            )
    return tuple(observations)


__all__ = ["FACTOR_DIRECTIONS", "compute_factor_observations"]
