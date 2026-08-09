from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from stockcrewai.models.request import CompanyIdentity, ParsedResearchRequest


_IDENTITY_FIELDS = (
    "company_name",
    "ticker",
    "cik",
    "exchange",
    "security_type",
    "source_reference",
)
_MATCH_FIELDS = ("cik", "ticker", "company_name", "exchange", "security_type")
_UNSUPPORTED_SECURITY_TYPES = frozenset(
    {
        "etf",
        "mutual_fund",
        "mutual fund",
        "closed_end_fund",
        "closed-end fund",
        "bond",
        "option",
        "warrant",
        "crypto",
        "investment_company",
        "investment_company/fund",
        "fund",
    }
)
_PLACEHOLDER_VALUES = frozenset({"unknown", "unavailable"})
_IdentityStatus = Literal["resolved", "ambiguous", "unsupported", "unavailable"]


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _cik(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    if text.isdigit():
        return text.zfill(10)
    return text


def _identity_value(field: str, value: Any) -> str | None:
    text = _cik(value) if field == "cik" else _text(value)
    if text is None:
        return None
    if field in _IDENTITY_FIELDS:
        if text.casefold() in _PLACEHOLDER_VALUES:
            return None
    return text


def _comparison_value(field: str, value: Any) -> str | None:
    text = _identity_value(field, value)
    return text.casefold() if text is not None else None


def _metadata_payload(
    security_metadata: Mapping[str, Any],
    candidate: CompanyIdentity | None = None,
) -> dict[str, str | None]:
    payload: dict[str, str | None] = {}
    for field in _IDENTITY_FIELDS:
        value = _identity_value(field, security_metadata.get(field))
        if value is None and candidate is not None:
            value = _identity_value(field, getattr(candidate, field))
        payload[field] = value
    return payload


def _result_from_metadata(
    security_metadata: Mapping[str, Any],
    status: _IdentityStatus,
    reason_code: str,
) -> CompanyIdentity:
    return CompanyIdentity(
        **_metadata_payload(security_metadata),
        status=status,
        reason_code=reason_code,
    )


def _result_from_candidate(
    candidate: CompanyIdentity,
    status: _IdentityStatus,
    reason_code: str,
) -> CompanyIdentity:
    return CompanyIdentity(
        **{
            field: _identity_value(field, getattr(candidate, field))
            for field in _IDENTITY_FIELDS
        },
        status=status,
        reason_code=reason_code,
    )


def _metadata_matches_candidate(
    candidate: CompanyIdentity,
    security_metadata: Mapping[str, Any],
) -> bool:
    for field in _MATCH_FIELDS:
        metadata_value = _comparison_value(field, security_metadata.get(field))
        if metadata_value is None:
            continue
        if metadata_value != _comparison_value(field, getattr(candidate, field)):
            return False
    return True


def _metadata_has_identity_signal(security_metadata: Mapping[str, Any]) -> bool:
    return any(
        _comparison_value(field, security_metadata.get(field)) is not None
        for field in _MATCH_FIELDS
    )


def _parsed_request_conflicts(
    parsed_request: ParsedResearchRequest,
    candidate: CompanyIdentity,
) -> bool:
    for request_field, candidate_field in (
        ("company_name_guess", "company_name"),
        ("ticker_guess", "ticker"),
        ("exchange_guess", "exchange"),
    ):
        request_value = _comparison_value(candidate_field, getattr(parsed_request, request_field))
        if request_value is None:
            continue
        if request_value != _comparison_value(candidate_field, getattr(candidate, candidate_field)):
            return True
    return False


def _is_unsupported_security(security_metadata: Mapping[str, Any]) -> bool:
    if security_metadata.get("is_supported") is False:
        return True
    return any(
        _comparison_value(field, security_metadata.get(field)) in _UNSUPPORTED_SECURITY_TYPES
        for field in ("security_type", "security_class")
    )


def _declared_candidate_count(security_metadata: Mapping[str, Any]) -> int | None:
    count = security_metadata.get("candidate_count")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        return count
    return None


def resolve_company(
    parsed_request: ParsedResearchRequest,
    sec_candidates: Sequence[CompanyIdentity],
    security_metadata: Mapping[str, Any],
) -> CompanyIdentity:
    """Resolve a company using only supplied SEC candidates and metadata."""
    source_status = _comparison_value("source_status", security_metadata.get("source_status"))
    if source_status in {"unavailable", "error"}:
        return _result_from_metadata(
            security_metadata,
            status="unavailable",
            reason_code="identity_source_unavailable",
        )

    if _is_unsupported_security(security_metadata):
        return _result_from_metadata(
            security_metadata,
            status="unsupported",
            reason_code="unsupported_security",
        )

    if not sec_candidates:
        return _result_from_metadata(
            security_metadata,
            status="unavailable",
            reason_code="identity_unavailable",
        )

    declared_count = _declared_candidate_count(security_metadata)
    if declared_count != len(sec_candidates) or declared_count != 1:
        return _result_from_metadata(
            security_metadata,
            status="ambiguous",
            reason_code="identity_candidate_conflict",
        )

    if not _metadata_has_identity_signal(security_metadata):
        return _result_from_metadata(
            security_metadata,
            status="ambiguous",
            reason_code="identity_candidate_conflict",
        )

    matching_candidates = [
        candidate
        for candidate in sec_candidates
        if _metadata_matches_candidate(candidate, security_metadata)
    ]
    if len(matching_candidates) != 1:
        return _result_from_metadata(
            security_metadata,
            status="ambiguous",
            reason_code="identity_candidate_conflict",
        )

    candidate = matching_candidates[0]
    if candidate.status == "unsupported":
        return _result_from_candidate(
            candidate,
            status="unsupported",
            reason_code="unsupported_security",
        )
    if candidate.status == "ambiguous":
        return _result_from_candidate(
            candidate,
            status="ambiguous",
            reason_code="identity_candidate_conflict",
        )
    if candidate.status == "unavailable":
        return _result_from_candidate(
            candidate,
            status="unavailable",
            reason_code="identity_source_unavailable",
        )

    if _parsed_request_conflicts(parsed_request, candidate):
        return _result_from_metadata(
            security_metadata,
            status="ambiguous",
            reason_code="identity_candidate_conflict",
        )

    return _result_from_candidate(
        candidate,
        status="resolved",
        reason_code="sec_exact_match",
    )


__all__ = ["resolve_company"]
