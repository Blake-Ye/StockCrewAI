from calendar import monthrange
from datetime import datetime, timezone
import ssl
import unittest

import pandas as pd
from yfinance.exceptions import YFRateLimitError


class SSLError(Exception):
    pass


class _FakeTicker:
    def __init__(self, info):
        self.info = info


class _FakeYFinance:
    def __init__(self, info):
        self.info = info
        self.requested_tickers = []

    def Ticker(self, ticker):
        self.requested_tickers.append(ticker)
        return _FakeTicker(self.info)


class _LazyFastInfo:
    def get(self, key, default=None):
        return {"lastPrice": 219.75, "currency": "USD"}.get(key, default)

    def __iter__(self):
        raise AssertionError("FastInfo should not be materialized into a dict")


class _FallbackTicker:
    @property
    def info(self):
        raise TimeoutError("info endpoint unavailable")

    @property
    def fast_info(self):
        return _LazyFastInfo()

    def history(self, **kwargs):
        return pd.DataFrame(
            {"Close": [218.50]},
            index=pd.DatetimeIndex(
                [datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc)]
            ),
        )


class _FallbackYFinance:
    def Ticker(self, ticker):
        return _FallbackTicker()


class _RateLimitedInfoTicker:
    def __init__(self):
        self.info_attempts = 0

    @property
    def info(self):
        self.info_attempts += 1
        if self.info_attempts == 1:
            raise YFRateLimitError()
        return {
            "regularMarketPrice": 220.50,
            "regularMarketTime": 1700000000,
            "currency": "USD",
        }


class _RateLimitedInfoYFinance:
    def __init__(self):
        self.quote = _RateLimitedInfoTicker()

    def Ticker(self, ticker):
        return self.quote


class _RateLimitedHistoryTicker:
    def __init__(self):
        self.history_attempts = 0

    @property
    def info(self):
        return {}

    @property
    def fast_info(self):
        return {"lastPrice": 219.75, "currency": "USD"}

    def history(self, **kwargs):
        self.history_attempts += 1
        if self.history_attempts == 1:
            raise YFRateLimitError()
        return pd.DataFrame(
            {"Close": [218.50]},
            index=pd.DatetimeIndex(
                [datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc)]
            ),
        )


class _RateLimitedHistoryYFinance:
    def __init__(self):
        self.quote = _RateLimitedHistoryTicker()

    def Ticker(self, ticker):
        return self.quote


class _HistoryMetadataTicker:
    @property
    def info(self):
        raise YFRateLimitError()

    @property
    def fast_info(self):
        raise YFRateLimitError()

    def history(self, **kwargs):
        return pd.DataFrame(
            {"Close": [218.50]},
            index=pd.DatetimeIndex(
                [datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc)]
            ),
        )

    @property
    def history_metadata(self):
        return {
            "regularMarketPrice": 219.75,
            "regularMarketTime": 1700000000,
            "currency": "USD",
        }


class _HistoryMetadataYFinance:
    def Ticker(self, ticker):
        return _HistoryMetadataTicker()


class _HistoryFirstTicker:
    def __init__(self):
        self.info_attempts = 0

    @property
    def info(self):
        self.info_attempts += 1
        raise TypeError("Yahoo quote info response was malformed")

    def history(self, **kwargs):
        return pd.DataFrame(
            {"Close": [218.50]},
            index=pd.DatetimeIndex(
                [datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc)]
            ),
        )

    @property
    def history_metadata(self):
        return {
            "regularMarketPrice": 219.75,
            "regularMarketTime": 1700000000,
            "currency": "USD",
        }


class _HistoryFirstYFinance:
    def __init__(self):
        self.quote = _HistoryFirstTicker()

    def Ticker(self, ticker):
        return self.quote


class _SslRetryTicker:
    def __init__(self):
        self.history_attempts = 0

    @property
    def info(self):
        raise AssertionError("info should not be used after history succeeds")

    def history(self, **kwargs):
        self.history_attempts += 1
        if self.history_attempts == 1:
            raise ssl.SSLError("temporary TLS failure")
        return pd.DataFrame(
            {"Close": [218.50]},
            index=pd.DatetimeIndex(
                [datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc)]
            ),
        )

    @property
    def history_metadata(self):
        return {
            "regularMarketPrice": 219.75,
            "regularMarketTime": 1700000000,
            "currency": "USD",
        }


class _SslRetryYFinance:
    def __init__(self):
        self.quote = _SslRetryTicker()

    def Ticker(self, ticker):
        return self.quote


class _ThirdPartySslRetryTicker:
    def __init__(self):
        self.history_attempts = 0

    @property
    def info(self):
        raise AssertionError("info should not be used after history succeeds")

    def history(self, **kwargs):
        self.history_attempts += 1
        if self.history_attempts == 1:
            raise SSLError("temporary third-party TLS failure")
        return pd.DataFrame(
            {"Close": [218.50]},
            index=pd.DatetimeIndex(
                [datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc)]
            ),
        )

    @property
    def history_metadata(self):
        return {
            "regularMarketPrice": 219.75,
            "regularMarketTime": 1700000000,
            "currency": "USD",
        }


class _ThirdPartySslRetryYFinance:
    def __init__(self):
        self.quote = _ThirdPartySslRetryTicker()

    def Ticker(self, ticker):
        return self.quote


class _AlwaysRateLimitedTicker:
    def __init__(self):
        self.info_attempts = 0
        self.fast_info_attempts = 0
        self.history_attempts = 0

    @property
    def info(self):
        self.info_attempts += 1
        raise YFRateLimitError()

    @property
    def fast_info(self):
        self.fast_info_attempts += 1
        raise YFRateLimitError()

    def history(self, **kwargs):
        self.history_attempts += 1
        raise YFRateLimitError()


class _AlwaysRateLimitedYFinance:
    def __init__(self):
        self.quote = _AlwaysRateLimitedTicker()

    def Ticker(self, ticker):
        return self.quote


class _MonthlyHistoryTicker:
    def __init__(self):
        self.history_calls = []
        dates = []
        prices = []
        year, month = 2019, 1
        for index in range(61):
            dates.extend(
                [
                    datetime(year, month, 15, tzinfo=timezone.utc),
                    datetime(
                        year,
                        month,
                        monthrange(year, month)[1],
                        tzinfo=timezone.utc,
                    ),
                ]
            )
            prices.extend([index + 1, 1000 + index])
            month += 1
            if month == 13:
                year += 1
                month = 1
        self.monthly_history = pd.DataFrame(
            {"Close": prices}, index=pd.DatetimeIndex(dates)
        )

    @property
    def info(self):
        raise AssertionError("info should not be used when history metadata is present")

    @property
    def history_metadata(self):
        return {
            "regularMarketPrice": 219.75,
            "regularMarketTime": 1700000000,
            "currency": "USD",
        }

    def history(self, **kwargs):
        self.history_calls.append(kwargs)
        if kwargs["interval"] == "1mo":
            return self.monthly_history
        return pd.DataFrame(
            {"Close": [218.50]},
            index=pd.DatetimeIndex(
                [datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc)]
            ),
        )


class _MonthlyHistoryYFinance:
    def __init__(self):
        self.quote = _MonthlyHistoryTicker()
        self.requested_tickers = []

    def Ticker(self, ticker):
        self.requested_tickers.append(ticker)
        return self.quote


class _MonthStartHistoryTicker:
    @property
    def history_metadata(self):
        return {
            "regularMarketPrice": 219.75,
            "regularMarketTime": 1700000000,
            "currency": "USD",
        }

    def history(self, **kwargs):
        if kwargs["interval"] == "1mo":
            return pd.DataFrame(
                {"Close": [100, 110]},
                index=pd.DatetimeIndex(
                    [
                        datetime(2026, 7, 1, tzinfo=timezone.utc),
                        datetime(2026, 8, 1, tzinfo=timezone.utc),
                    ]
                ),
            )
        return pd.DataFrame(
            {"Close": [218.50]},
            index=pd.DatetimeIndex(
                [datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc)]
            ),
        )


class _MonthStartHistoryYFinance:
    def Ticker(self, ticker):
        return _MonthStartHistoryTicker()


class _CurrentMonthObservationTicker:
    def __init__(self):
        dates = []
        year, month = 2021, 9
        for _ in range(59):
            dates.append(
                datetime(
                    year,
                    month,
                    monthrange(year, month)[1],
                    tzinfo=timezone.utc,
                )
            )
            month += 1
            if month == 13:
                year += 1
                month = 1
        dates.append(datetime(2026, 8, 7, tzinfo=timezone.utc))
        self.monthly_history = pd.DataFrame(
            {"Close": range(1, 61)}, index=pd.DatetimeIndex(dates)
        )

    def history(self, **kwargs):
        if kwargs["interval"] != "1mo":
            raise AssertionError("only monthly history is expected")
        return self.monthly_history


class _HistoricalFailureTicker:
    def __init__(self):
        self.history_calls = []

    @property
    def info(self):
        raise AssertionError("info should not be used when history metadata is present")

    @property
    def history_metadata(self):
        return {
            "regularMarketPrice": 219.75,
            "regularMarketTime": 1700000000,
            "currency": "USD",
        }

    def history(self, **kwargs):
        self.history_calls.append(kwargs)
        if kwargs["interval"] == "1mo":
            raise TimeoutError("historical endpoint unavailable")
        return pd.DataFrame(
            {"Close": [218.50]},
            index=pd.DatetimeIndex(
                [datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc)]
            ),
        )


class _HistoricalFailureYFinance:
    def __init__(self):
        self.quote = _HistoricalFailureTicker()

    def Ticker(self, ticker):
        return self.quote


class MarketPriceToolTests(unittest.TestCase):
    def test_month_start_yahoo_index_preserves_real_observation_date(self):
        from stockcrewai.tools.market_price_tool import MarketPriceTool

        result = MarketPriceTool(
            yfinance_module=_MonthStartHistoryYFinance(),
            include_history=True,
            max_retries=0,
        ).run(ticker="AAPL")

        self.assertEqual(result.historical_prices[-1]["date"], "2026-08-01")

    def test_historical_prices_preserve_current_month_observation_date(self):
        from stockcrewai.tools.market_price_tool import MarketPriceTool

        historical_prices = MarketPriceTool(max_retries=0)._historical_prices(
            _CurrentMonthObservationTicker(), "AAPL"
        )

        self.assertEqual(len(historical_prices), 60)
        self.assertEqual(
            historical_prices[-1],
            {
                "date": "2026-08-07",
                "price": "60",
                "evidence_id": "ev_market_price_history_AAPL_2026-08",
            },
        )
        self.assertNotEqual(historical_prices[-1]["date"], "2026-08-31")

    def test_opt_in_history_returns_latest_monthly_close_for_recent_61_points(self):
        from stockcrewai.tools.market_price_tool import MarketPriceTool

        yfinance = _MonthlyHistoryYFinance()
        result = MarketPriceTool(
            yfinance_module=yfinance,
            include_history=True,
            max_retries=0,
        ).run(ticker="aapl")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.market_price, "219.75")
        self.assertEqual(len(result.historical_prices), 61)
        self.assertEqual(
            result.historical_prices[0],
            {
                "date": "2019-01-31",
                "price": "1000",
                "evidence_id": "ev_market_price_history_AAPL_2019-01",
            },
        )
        self.assertEqual(
            result.historical_prices[-1],
            {
                "date": "2024-01-31",
                "price": "1060",
                "evidence_id": "ev_market_price_history_AAPL_2024-01",
            },
        )
        self.assertEqual(yfinance.requested_tickers, ["AAPL"])
        self.assertEqual(
            [call["interval"] for call in yfinance.quote.history_calls],
            ["1d", "1mo"],
        )
        self.assertEqual(
            yfinance.quote.history_calls[-1]["period"],
            "6y",
        )
        self.assertEqual(
            result.source_reference,
            "https://finance.yahoo.com/quote/AAPL",
        )

    def test_historical_failure_keeps_current_quote_ok_with_history_warning(self):
        from stockcrewai.tools.market_price_tool import MarketPriceTool

        result = MarketPriceTool(
            yfinance_module=_HistoricalFailureYFinance(),
            include_history=True,
            max_retries=0,
        ).run(ticker="AAPL")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.market_price, "219.75")
        self.assertEqual(result.historical_prices, [])
        self.assertTrue(any(warning.startswith("历史行情") for warning in result.warnings))

    def test_rate_limit_retries_info_once_without_waiting(self):
        from stockcrewai.tools.market_price_tool import MarketPriceTool

        yfinance = _RateLimitedInfoYFinance()
        sleep_calls = []
        result = MarketPriceTool(
            yfinance_module=yfinance,
            max_retries=1,
            retry_delay=0.25,
            sleeper=sleep_calls.append,
        ).run(ticker="AAPL")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.market_price, "220.5")
        self.assertEqual(yfinance.quote.info_attempts, 2)
        self.assertEqual(sleep_calls, [0.25])

    def test_rate_limit_retries_history_fallback_once_without_waiting(self):
        from stockcrewai.tools.market_price_tool import MarketPriceTool

        yfinance = _RateLimitedHistoryYFinance()
        sleep_calls = []
        result = MarketPriceTool(
            yfinance_module=yfinance,
            max_retries=1,
            retry_delay=0.25,
            sleeper=sleep_calls.append,
        ).run(ticker="AAPL")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.market_price, "219.75")
        self.assertEqual(result.currency, "USD")
        self.assertEqual(yfinance.quote.history_attempts, 2)
        self.assertEqual(sleep_calls, [0.25])

    def test_continuous_rate_limit_is_unavailable_without_fabricated_fields(self):
        from stockcrewai.tools.market_price_tool import MarketPriceTool

        yfinance = _AlwaysRateLimitedYFinance()
        sleep_calls = []
        result = MarketPriceTool(
            yfinance_module=yfinance,
            max_retries=2,
            retry_delay=0.25,
            sleeper=sleep_calls.append,
        ).run(ticker="AAPL")

        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.market_price)
        self.assertIsNone(result.price_timestamp)
        self.assertIsNone(result.currency)
        self.assertEqual(
            result.source_reference,
            "https://finance.yahoo.com/quote/AAPL",
        )
        self.assertIn("YFRateLimitError", " ".join(result.warnings))
        self.assertEqual(yfinance.quote.info_attempts, 1)
        self.assertEqual(yfinance.quote.fast_info_attempts, 0)
        self.assertEqual(yfinance.quote.history_attempts, 3)
        self.assertEqual(sleep_calls, [0.25, 0.5])

    def test_history_metadata_completes_quote_when_info_and_fast_info_are_limited(self):
        from stockcrewai.tools.market_price_tool import MarketPriceTool

        result = MarketPriceTool(
            yfinance_module=_HistoryMetadataYFinance(),
            max_retries=0,
        ).run(ticker="AAPL")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.market_price, "219.75")
        self.assertEqual(result.price_timestamp, "2023-11-14T22:13:20Z")
        self.assertEqual(result.currency, "USD")

    def test_history_metadata_is_used_before_noisy_info_endpoint(self):
        from stockcrewai.tools.market_price_tool import MarketPriceTool

        yfinance = _HistoryFirstYFinance()
        result = MarketPriceTool(
            yfinance_module=yfinance,
            max_retries=0,
        ).run(ticker="AAPL")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.market_price, "219.75")
        self.assertEqual(result.price_timestamp, "2023-11-14T22:13:20Z")
        self.assertEqual(result.currency, "USD")
        self.assertEqual(yfinance.quote.info_attempts, 0)

    def test_history_retries_transient_ssl_error(self):
        from stockcrewai.tools.market_price_tool import MarketPriceTool

        yfinance = _SslRetryYFinance()
        sleep_calls = []
        result = MarketPriceTool(
            yfinance_module=yfinance,
            max_retries=1,
            retry_delay=0.25,
            sleeper=sleep_calls.append,
        ).run(ticker="AAPL")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.market_price, "219.75")
        self.assertEqual(result.price_timestamp, "2023-11-14T22:13:20Z")
        self.assertEqual(result.currency, "USD")
        self.assertEqual(yfinance.quote.history_attempts, 2)
        self.assertEqual(sleep_calls, [0.25])

    def test_history_retries_third_party_ssl_error(self):
        from stockcrewai.tools.market_price_tool import MarketPriceTool

        self.assertFalse(issubclass(SSLError, ssl.SSLError))
        yfinance = _ThirdPartySslRetryYFinance()
        sleep_calls = []
        result = MarketPriceTool(
            yfinance_module=yfinance,
            max_retries=1,
            retry_delay=0.25,
            sleeper=sleep_calls.append,
        ).run(ticker="AAPL")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.market_price, "219.75")
        self.assertEqual(result.price_timestamp, "2023-11-14T22:13:20Z")
        self.assertEqual(result.currency, "USD")
        self.assertEqual(
            result.source_reference,
            "https://finance.yahoo.com/quote/AAPL",
        )
        self.assertEqual(yfinance.quote.history_attempts, 2)
        self.assertEqual(sleep_calls, [0.25])

    def test_returns_yfinance_price_and_iso_timestamp(self):
        from stockcrewai.tools.market_price_tool import MarketPriceTool

        yfinance = _FakeYFinance(
            {
                "regularMarketPrice": 220.50,
                "regularMarketTime": 1700000000,
                "currency": "USD",
            }
        )

        result = MarketPriceTool(yfinance_module=yfinance).run(ticker="aapl")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.ticker, "AAPL")
        self.assertEqual(result.market_price, "220.5")
        self.assertEqual(result.price_timestamp, "2023-11-14T22:13:20Z")
        self.assertEqual(result.currency, "USD")
        self.assertEqual(
            result.source_reference,
            "https://finance.yahoo.com/quote/AAPL",
        )
        self.assertEqual(yfinance.requested_tickers, ["AAPL"])

    def test_yfinance_failure_returns_unavailable_without_a_price(self):
        from stockcrewai.tools.market_price_tool import MarketPriceTool

        class RaisingYFinance:
            def Ticker(self, ticker):
                raise TimeoutError("test timeout")

        result = MarketPriceTool(
            yfinance_module=RaisingYFinance(), sleeper=lambda _: None
        ).run(ticker="AAPL")

        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.market_price)
        self.assertIsNone(result.price_timestamp)
        self.assertIsNone(result.currency)
        self.assertEqual(
            result.source_reference,
            "https://finance.yahoo.com/quote/AAPL",
        )
        self.assertTrue(result.warnings)

    def test_missing_required_quote_fields_returns_unavailable(self):
        from stockcrewai.tools.market_price_tool import MarketPriceTool

        yfinance = _FakeYFinance({"regularMarketPrice": 220.50})
        result = MarketPriceTool(yfinance_module=yfinance).run(ticker="AAPL")

        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.market_price)
        self.assertTrue(result.warnings)

    def test_history_fallback_returns_a_complete_sourced_quote(self):
        from stockcrewai.tools.market_price_tool import MarketPriceTool

        result = MarketPriceTool(
            yfinance_module=_FallbackYFinance(), sleeper=lambda _: None
        ).run(ticker="AAPL")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.market_price, "219.75")
        self.assertEqual(result.price_timestamp, "2026-08-05T20:00:00Z")
        self.assertEqual(result.currency, "USD")
        self.assertEqual(
            result.source_reference,
            "https://finance.yahoo.com/quote/AAPL",
        )


if __name__ == "__main__":
    unittest.main()
