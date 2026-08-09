from __future__ import annotations

import importlib
import ssl
import time
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr, model_validator


class MarketPriceToolInput(BaseModel):
    ticker: str = Field(description="股票代码")

    @model_validator(mode="after")
    def normalize_ticker(self) -> "MarketPriceToolInput":
        self.ticker = self.ticker.strip().upper()
        if not self.ticker:
            raise ValueError("ticker 不能为空")
        return self


class MarketPriceResult(BaseModel):
    status: Literal["ok", "unavailable"]
    ticker: str
    market_price: str | None = None
    price_timestamp: str | None = None
    currency: str | None = None
    source_reference: str | None = None
    historical_prices: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _decimal_price(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("价格缺失")
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("价格格式无效") from exc
    if not price.is_finite() or price <= 0:
        raise ValueError("价格必须为正的有限数值")
    return price


def _timestamp(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError("价格时间戳缺失")
    try:
        seconds = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("价格时间戳格式无效") from exc
    if seconds <= 0:
        raise ValueError("价格时间戳必须为正数")
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _datetime_timestamp(value: Any) -> str:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        raise ValueError("历史行情时间戳格式无效")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _historical_point_date(value: Any) -> tuple[datetime, str]:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        ordering_value = value.astimezone(timezone.utc) if value.tzinfo else value.replace(
            tzinfo=timezone.utc
        )
        return ordering_value, value.date().isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc), value.isoformat()
    raise ValueError("历史行情日期格式无效")


class MarketPriceTool(BaseTool):
    name: str = "market_price_quote"
    description: str = (
        "从公开行情接口获取 ticker 的最新市场价格、UTC 时间戳、币种和来源 URL；"
        "获取失败时返回 unavailable，不生成价格。"
    )
    args_schema: Type[BaseModel] = MarketPriceToolInput

    _yfinance_module: Any = PrivateAttr(default=None)
    _max_retries: int = PrivateAttr(default=1)
    _retry_delay: float = PrivateAttr(default=1.0)
    _sleeper: Callable[[float], None] = PrivateAttr(default=time.sleep)
    _include_history: bool = PrivateAttr(default=False)

    def __init__(
        self,
        *,
        yfinance_module: Any | None = None,
        include_history: bool = False,
        max_retries: int = 1,
        retry_delay: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if not isinstance(max_retries, int) or not 0 <= max_retries <= 3:
            raise ValueError("max_retries 必须是 0 到 3 之间的整数")
        if retry_delay < 0:
            raise ValueError("retry_delay 不能为负数")
        self._yfinance_module = yfinance_module
        self._include_history = include_history
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._sleeper = sleeper

    @staticmethod
    def _source_url(ticker: str) -> str:
        return f"https://finance.yahoo.com/quote/{ticker}"

    def _load_yfinance(self) -> Any:
        return self._yfinance_module or importlib.import_module("yfinance")

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, (ConnectionError, TimeoutError, ssl.SSLError)):
            return True
        return any(
            name
            in {
                "YFRateLimitError",
                "YFConnectionError",
                "YFTimeoutError",
                "SSLError",
                "ProxyError",
                "ConnectionError",
                "Timeout",
                "ConnectTimeout",
                "ReadTimeout",
            }
            for name in (cls.__name__ for cls in type(exc).__mro__)
        )

    def _retry_call(self, operation: Callable[[], Any], budget: dict[str, int]) -> Any:
        while True:
            try:
                return operation()
            except Exception as exc:
                if not self._is_retryable(exc) or budget["remaining"] <= 0:
                    raise
                retries_used = self._max_retries - budget["remaining"] + 1
                budget["remaining"] -= 1
                self._sleeper(self._retry_delay * (2 ** (retries_used - 1)))

    @staticmethod
    def _info_quote(quote: Any) -> tuple[Decimal, str, str]:
        info = quote.info
        if not isinstance(info, dict):
            info = dict(info)
        price_value = info.get("regularMarketPrice")
        if price_value is None:
            price_value = info.get("currentPrice")
        price = _decimal_price(price_value)
        price_timestamp = _timestamp(info.get("regularMarketTime"))
        currency = str(info.get("currency") or "").strip().upper()
        if not currency:
            raise ValueError("价格币种缺失")
        return price, price_timestamp, currency

    @staticmethod
    def _fast_info_value(fast_info: Any, key: str) -> Any:
        if fast_info is None:
            return None
        getter = getattr(fast_info, "get", None)
        if callable(getter):
            return getter(key)
        try:
            return fast_info[key]
        except (KeyError, TypeError, IndexError):
            return None

    def _history_quote(
        self, quote: Any, retry_budget: dict[str, int]
    ) -> tuple[Decimal, str, str]:
        history = self._retry_call(
            lambda: quote.history(
                period="5d",
                interval="1d",
                auto_adjust=False,
                actions=False,
            ),
            retry_budget,
        )
        if history is None or getattr(history, "empty", False):
            raise ValueError("历史行情为空")
        try:
            history_metadata = quote.history_metadata
            if not isinstance(history_metadata, Mapping):
                history_metadata = {}
        except Exception:
            history_metadata = {}
        price_value = history_metadata.get("regularMarketPrice")
        currency_value = history_metadata.get("currency")
        if price_value is None or not currency_value:
            try:
                fast_info = self._retry_call(lambda: quote.fast_info, retry_budget)
            except Exception:
                fast_info = None
            if not currency_value:
                try:
                    currency_value = self._retry_call(
                        lambda: self._fast_info_value(fast_info, "currency"),
                        retry_budget,
                    )
                except Exception:
                    currency_value = None
            if price_value is None:
                try:
                    price_value = self._retry_call(
                        lambda: self._fast_info_value(fast_info, "lastPrice"),
                        retry_budget,
                    )
                except Exception:
                    price_value = None
            if price_value is None:
                try:
                    price_value = self._retry_call(
                        lambda: self._fast_info_value(fast_info, "last_price"),
                        retry_budget,
                    )
                except Exception:
                    price_value = None
        close = history["Close"]
        if hasattr(close, "dropna"):
            close = close.dropna()
        if getattr(close, "empty", False):
            raise ValueError("历史收盘价缺失")
        if price_value is None:
            price_value = close.iloc[-1] if hasattr(close, "iloc") else close[-1]
        price = _decimal_price(price_value)
        market_timestamp = history_metadata.get("regularMarketTime")
        if market_timestamp is not None:
            try:
                price_timestamp = _timestamp(market_timestamp)
            except ValueError:
                price_timestamp = None
        else:
            price_timestamp = None
        if price_timestamp is None:
            index = getattr(history, "index", None)
            if index is None or len(index) == 0:
                raise ValueError("历史行情时间戳缺失")
            price_timestamp = _datetime_timestamp(index[-1])
        currency = str(currency_value or "").strip().upper()
        if not currency:
            raise ValueError("价格币种缺失")
        return price, price_timestamp, currency

    def _historical_prices(self, quote: Any, ticker: str) -> list[dict[str, str]]:
        history = self._retry_call(
            lambda: quote.history(
                period="6y",
                interval="1mo",
                auto_adjust=False,
                actions=False,
            ),
            {"remaining": self._max_retries},
        )
        if history is None or getattr(history, "empty", False):
            raise ValueError("历史行情为空")
        close = history["Close"]
        index = getattr(close, "index", None)
        if index is None:
            index = getattr(history, "index", None)
        if index is None:
            raise ValueError("历史行情日期缺失")

        selected: dict[str, tuple[datetime, str, Decimal]] = {}
        for point_date, raw_price in zip(index, close):
            try:
                ordering_date, date_text = _historical_point_date(point_date)
                price = _decimal_price(raw_price)
            except (TypeError, ValueError):
                continue
            month = date_text[:7]
            previous = selected.get(month)
            if previous is None or ordering_date >= previous[0]:
                selected[month] = (
                    ordering_date,
                    ordering_date.date().isoformat(),
                    price,
                )

        if not selected:
            raise ValueError("历史收盘价缺失")
        recent_points = sorted(selected.values(), key=lambda point: point[0])[-60:]
        return [
            {
                "date": date_text,
                "price": format(price, "f"),
                "evidence_id": f"ev_market_price_history_{ticker}_{date_text[:7]}",
            }
            for _, date_text, price in recent_points
        ]

    def _run(self, ticker: str) -> MarketPriceResult:
        normalized_ticker = ticker.strip().upper()
        source_reference = self._source_url(normalized_ticker)
        retry_budget = {"remaining": self._max_retries}
        try:
            quote = self._retry_call(
                lambda: self._load_yfinance().Ticker(normalized_ticker), retry_budget
            )
        except Exception as exc:
            return MarketPriceResult(
                status="unavailable",
                ticker=normalized_ticker,
                source_reference=source_reference,
                warnings=[f"行情请求失败：{type(exc).__name__}"],
            )

        try:
            price, price_timestamp, currency = self._history_quote(
                quote, retry_budget
            )
        except Exception as history_exc:
            try:
                price, price_timestamp, currency = self._retry_call(
                    lambda: self._info_quote(quote), retry_budget
                )
            except Exception as info_exc:
                return MarketPriceResult(
                    status="unavailable",
                    ticker=normalized_ticker,
                    source_reference=source_reference,
                    warnings=[
                        "行情数据不可用："
                        f"info={type(info_exc).__name__},"
                        f"history={type(history_exc).__name__}"
                    ],
                )

        warnings: list[str] = []
        historical_prices: list[dict[str, str]] = []
        if self._include_history:
            try:
                historical_prices = self._historical_prices(
                    quote, normalized_ticker
                )
            except Exception as exc:
                warnings.append(f"历史行情不可用：{type(exc).__name__}")

        return MarketPriceResult(
            status="ok",
            ticker=normalized_ticker,
            market_price=format(price, "f"),
            price_timestamp=price_timestamp,
            currency=currency,
            source_reference=source_reference,
            historical_prices=historical_prices,
            warnings=warnings,
        )
