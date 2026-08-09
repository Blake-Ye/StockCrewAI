"""Offline market-record validation and explicitly injected collection."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from pydantic import ValidationError

from stockcrewai.models.evidence import MarketPriceRecord


class MarketDataError(Exception):
    """Base error for the market-data boundary."""

    reason_code = "market_data_error"


class MarketDataValidationError(MarketDataError):
    """The supplied market record is not a valid normalized record."""

    reason_code = "market_record_invalid"


class MarketDataCollectionError(MarketDataError):
    """An explicitly injected market collector failed."""

    reason_code = "market_collection_failed"


_MODEL_FIELDS = frozenset(MarketPriceRecord.model_fields)


def normalize_market_price_record(value: object) -> MarketPriceRecord:
    """Validate one explicit market record without creating a network client."""

    if isinstance(value, MarketPriceRecord):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            value = model_dump(mode="python")
        except (TypeError, ValueError) as exc:
            raise MarketDataValidationError("行情记录无法转换为 JSON 对象") from exc
    if not isinstance(value, Mapping):
        raise MarketDataValidationError("行情记录必须是 JSON 对象")

    payload = dict(value)
    if payload.get("status") == "unavailable":
        raise MarketDataCollectionError("显式行情 collector 返回 unavailable")
    if "price" not in payload and "market_price" in payload:
        payload["price"] = payload["market_price"]
    payload.pop("status", None)
    for field in tuple(payload):
        if field not in _MODEL_FIELDS and field not in {"market_price", "warnings", "historical_prices"}:
            raise MarketDataValidationError("行情记录包含未声明字段")
    payload = {key: value for key, value in payload.items() if key in _MODEL_FIELDS}
    if "ticker" in payload and isinstance(payload["ticker"], str):
        payload["ticker"] = payload["ticker"].strip().upper()
    if "currency" in payload and isinstance(payload["currency"], str):
        payload["currency"] = payload["currency"].strip().upper()
    try:
        normalized = MarketPriceRecord.model_validate(payload)
        normalized.model_dump(mode="json")
    except (ValidationError, TypeError, ValueError) as exc:
        raise MarketDataValidationError("行情记录不满足 MarketPriceRecord 契约") from exc
    return normalized


def collect_market_record(
    fetcher: Callable[[str], object],
    ticker: str,
) -> MarketPriceRecord:
    """Call only a caller-supplied fetcher once and normalize its result."""

    if not callable(fetcher):
        raise MarketDataCollectionError("行情 collector 必须是可调用对象")
    normalized_ticker = ticker.strip().upper() if isinstance(ticker, str) else ""
    if not normalized_ticker:
        raise MarketDataValidationError("ticker 不能为空")
    try:
        result = fetcher(normalized_ticker)
    except MarketDataError:
        raise
    except Exception as exc:
        raise MarketDataCollectionError("显式行情 collector 失败") from exc
    try:
        return normalize_market_price_record(result)
    except MarketDataCollectionError:
        raise
    except MarketDataValidationError as exc:
        raise MarketDataCollectionError("显式行情 collector 返回无效记录") from exc


def collect_market_data(fetcher: Callable[[str], object], ticker: str) -> MarketPriceRecord:
    """Explicit-name alias for callers that inject a market data fetcher."""

    return collect_market_record(fetcher, ticker)


__all__ = [
    "MarketDataCollectionError",
    "MarketDataError",
    "MarketDataValidationError",
    "collect_market_data",
    "collect_market_record",
    "normalize_market_price_record",
]
