from __future__ import annotations

from pydantic import ValidationError
import pytest

from stockcrewai.models.request import CompanyIdentity, ParsedRequest, ParsedResearchRequest


PARSER_PAYLOAD = {
    "company_mention": "Apple",
    "company_name_guess": "Apple Inc.",
    "ticker_guess": "AAPL",
    "exchange_guess": "NASDAQ",
    "request_type": "equity_research",
    "investment_horizon": "long_term",
    "requested_focus": ["fundamentals", "valuation"],
    "language": "zh-CN",
    "confidence": 0.95,
}


def test_company_identity_accepts_resolved_candidate_and_json_dump() -> None:
    identity = CompanyIdentity(
        company_name="Apple Inc.",
        ticker="AAPL",
        cik="0000320193",
        exchange="NASDAQ",
        security_type="common_stock",
        source_reference="fixture:company-identity",
        status="resolved",
        reason_code="exact_ticker_match",
    )

    assert identity.model_dump(mode="json") == {
        "company_name": "Apple Inc.",
        "ticker": "AAPL",
        "cik": "0000320193",
        "exchange": "NASDAQ",
        "security_type": "common_stock",
        "source_reference": "fixture:company-identity",
        "status": "resolved",
        "reason_code": "exact_ticker_match",
    }


@pytest.mark.parametrize(
    "field",
    [
        "company_name",
        "ticker",
        "cik",
        "exchange",
        "security_type",
        "source_reference",
        "reason_code",
    ],
)
def test_company_identity_rejects_blank_required_strings(field: str) -> None:
    payload = {
        "company_name": "Apple Inc.",
        "ticker": "AAPL",
        "cik": "0000320193",
        "exchange": "NASDAQ",
        "security_type": "common_stock",
        "source_reference": "fixture:company-identity",
        "status": "resolved",
        "reason_code": "exact_ticker_match",
    }
    payload[field] = "  "

    with pytest.raises(ValidationError):
        CompanyIdentity.model_validate(payload)


def test_company_identity_rejects_unknown_fields_and_invalid_status() -> None:
    payload = {
        "company_name": "Apple Inc.",
        "ticker": "AAPL",
        "cik": "0000320193",
        "exchange": "NASDAQ",
        "security_type": "common_stock",
        "source_reference": "fixture:company-identity",
        "status": "guessed",
        "reason_code": "not_authoritative",
        "unexpected": "must fail",
    }

    with pytest.raises(ValidationError):
        CompanyIdentity.model_validate(payload)


def test_company_identity_allows_missing_fields_for_unavailable() -> None:
    identity = CompanyIdentity(
        status="unavailable",
        reason_code="identity_unavailable",
    )

    assert identity.company_name is None
    assert identity.ticker is None
    assert identity.cik is None
    assert identity.exchange is None
    assert identity.security_type is None
    assert identity.source_reference is None


def test_company_identity_resolved_requires_all_identity_fields() -> None:
    payload = {
        "company_name": None,
        "ticker": None,
        "cik": None,
        "exchange": None,
        "security_type": None,
        "source_reference": None,
        "status": "resolved",
        "reason_code": "sec_exact_match",
    }

    with pytest.raises(ValidationError, match="resolved"):
        CompanyIdentity.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    ["company_name", "ticker", "cik", "exchange", "security_type", "source_reference"],
)
@pytest.mark.parametrize("value", ["unknown", "unavailable"])
def test_company_identity_rejects_pseudo_missing_identity_values(
    field: str,
    value: str,
) -> None:
    payload = {
        "company_name": None,
        "ticker": None,
        "cik": None,
        "exchange": None,
        "security_type": None,
        "source_reference": None,
        "status": "unavailable",
        "reason_code": "identity_unavailable",
    }
    payload[field] = value

    with pytest.raises(ValidationError, match="placeholder"):
        CompanyIdentity.model_validate(payload)


def test_parsed_research_request_accepts_current_nine_field_payload() -> None:
    request = ParsedResearchRequest.model_validate(PARSER_PAYLOAD)

    assert request.model_dump() == PARSER_PAYLOAD
    assert set(type(request).model_fields) == set(PARSER_PAYLOAD)
    assert isinstance(request.confidence, float)


def test_parsed_request_name_remains_available_for_compatibility() -> None:
    request = ParsedRequest.model_validate(PARSER_PAYLOAD)

    assert type(request).__name__ == "ParsedRequest"
    assert request.model_dump() == PARSER_PAYLOAD


def test_parsed_research_request_rejects_extra_fields() -> None:
    payload = {**PARSER_PAYLOAD, "extra": "must fail"}

    with pytest.raises(ValidationError):
        ParsedResearchRequest.model_validate(payload)


@pytest.mark.parametrize("field", ["company_mention", "request_type", "language"])
def test_parsed_research_request_rejects_blank_required_strings(field: str) -> None:
    payload = {**PARSER_PAYLOAD, field: "  "}

    with pytest.raises(ValidationError):
        ParsedResearchRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("company_name_guess", "  "),
        ("ticker_guess", ""),
        ("exchange_guess", "  "),
        ("investment_horizon", ""),
    ],
)
def test_parsed_research_request_rejects_blank_optional_strings(
    field: str, value: str
) -> None:
    payload = {**PARSER_PAYLOAD, field: value}

    with pytest.raises(ValidationError):
        ParsedResearchRequest.model_validate(payload)


@pytest.mark.parametrize("confidence", [-0.01, 1.01, "0.5", True])
def test_parsed_research_request_preserves_strict_confidence_boundary(
    confidence: object,
) -> None:
    payload = {**PARSER_PAYLOAD, "confidence": confidence}

    with pytest.raises(ValidationError):
        ParsedResearchRequest.model_validate(payload)
