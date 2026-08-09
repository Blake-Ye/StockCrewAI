"""Offline point-in-time dataset assembly."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from stockcrewai.models.evidence import CalculationRecord, EvidenceRecord, MarketPriceRecord
from stockcrewai.models.profile import CoverageLevel, IssuerProfile, ReportingProfile, SecurityProfile
from stockcrewai.models.quant import PointInTimeSnapshot, UniverseManifest
from stockcrewai.quant.point_in_time import build_point_in_time_snapshot


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    raise TypeError("universe 必须是 UniverseManifest 或 JSON-safe Mapping")


def _cik_for_ticker(
    ticker: str,
    universe: Mapping[str, Any],
    evidence_by_cik: Mapping[str, Sequence[EvidenceRecord | Mapping[str, Any]]],
) -> str | None:
    mapping = universe.get("cik_by_ticker")
    if isinstance(mapping, Mapping) and isinstance(mapping.get(ticker), str):
        return mapping[ticker]
    if ticker in evidence_by_cik:
        return ticker
    tickers = universe.get("tickers")
    if (
        len(evidence_by_cik) == 1
        and isinstance(tickers, Sequence)
        and not isinstance(tickers, (str, bytes))
        and len(tickers) == 1
    ):
        return next(iter(evidence_by_cik))
    candidates: set[str] = set()
    for cik, records in evidence_by_cik.items():
        for record in records:
            if isinstance(record, Mapping) and record.get("ticker") == ticker:
                candidates.add(cik)
    return next(iter(candidates)) if len(candidates) == 1 else None


def _profile_for(
    *,
    ticker: str,
    cik: str,
    universe: Mapping[str, Any],
) -> dict[str, Any]:
    profiles = universe.get("profiles_by_cik")
    profile = profiles.get(cik) if isinstance(profiles, Mapping) else None
    if profile is None:
        profile = {
            "issuer_profile": IssuerProfile.UNKNOWN.value,
            "security_profile": SecurityProfile.UNKNOWN.value,
            "reporting_profile": ReportingProfile.UNKNOWN.value,
            "coverage_level": CoverageLevel.EVIDENCE_ONLY.value,
            "classification_evidence_ids": [],
            "reason_codes": ["profile_not_supplied_to_dataset_builder"],
            "registry_version": "profile-registry:unavailable",
        }
    payload = dict(profile.model_dump(mode="python") if hasattr(profile, "model_dump") else profile)
    payload.update({"cik": cik, "ticker": ticker})
    return payload


def _normalize_rebalance_dates(rebalance_dates: Sequence[datetime]) -> tuple[datetime, ...]:
    normalized: set[datetime] = set()
    for value in rebalance_dates:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("rebalance_dates 必须带时区")
        normalized.add(value.astimezone(timezone.utc))
    return tuple(sorted(normalized))


def build_point_in_time_dataset(
    *,
    universe: UniverseManifest | Mapping[str, Any],
    rebalance_dates: Sequence[datetime],
    evidence_by_cik: Mapping[str, Sequence[EvidenceRecord | Mapping[str, Any]]],
    calculations_by_cik: Mapping[str, Sequence[CalculationRecord | Mapping[str, Any]]],
    prices_by_ticker: Mapping[str, Sequence[MarketPriceRecord | Mapping[str, Any]]],
    builder_version: str,
) -> tuple[PointInTimeSnapshot, ...]:
    """Build snapshots from local normalized records only."""
    manifest = _payload(universe)
    tickers = manifest.get("tickers", [])
    if not isinstance(tickers, Sequence) or isinstance(tickers, (str, bytes)):
        raise TypeError("universe.tickers 必须是序列")
    dates = _normalize_rebalance_dates(rebalance_dates)
    snapshots: list[PointInTimeSnapshot] = []
    for as_of in dates:
        for ticker in sorted(str(item) for item in tickers):
            cik = _cik_for_ticker(ticker, manifest, evidence_by_cik)
            if cik is None:
                continue
            snapshots.append(
                build_point_in_time_snapshot(
                    as_of=as_of,
                    profile=_profile_for(ticker=ticker, cik=cik, universe=manifest),
                    evidence=evidence_by_cik.get(cik, ()),
                    calculations=calculations_by_cik.get(cik, ()),
                    prices=prices_by_ticker.get(ticker, ()),
                    builder_version=builder_version,
                )
            )
    return tuple(snapshots)


__all__ = ["build_point_in_time_dataset"]
