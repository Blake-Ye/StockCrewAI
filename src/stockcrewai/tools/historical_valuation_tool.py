from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_EVEN, localcontext
from typing import Any, Literal, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, model_validator


_EVIDENCE_ID = re.compile(r"ev_[A-Za-z0-9][A-Za-z0-9_.:-]*")
HISTORICAL_VALUATION_CALCULATION_ID = "calc_historical_pe"


def _copy_alias(payload: dict[str, Any], target: str, aliases: tuple[str, ...]) -> None:
    if payload.get(target) is not None:
        return
    for alias in aliases:
        if payload.get(alias) is not None:
            payload[target] = payload[alias]
            return


class HistoricalPricePoint(BaseModel):
    """带来源的历史价格观察值。日期是价格可观察的日期。"""

    model_config = ConfigDict(extra="allow")

    date: Any | None = None
    price: Any | None = None
    evidence_id: Any | None = None
    evidence_ids: Any | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_common_aliases(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        _copy_alias(payload, "date", ("price_date", "timestamp", "as_of"))
        _copy_alias(payload, "price", ("market_price", "value"))
        _copy_alias(payload, "evidence_id", ("price_evidence_id",))
        return payload


class PointInTimeFinancialSnapshot(BaseModel):
    """在 ``as_of``/``filed_at`` 时点已可获得的财务快照。"""

    model_config = ConfigDict(extra="allow")

    as_of: Any | None = None
    filed_at: Any | None = None
    period_end: Any | None = None
    eps: Any | None = None
    diluted_eps: Any | None = None
    earnings_per_share: Any | None = None
    net_income: Any | None = None
    shares_outstanding: Any | None = None
    value: Any | None = None
    evidence_id: Any | None = None
    evidence_ids: Any | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    financials: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def accept_common_aliases(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        _copy_alias(payload, "as_of", ("snapshot_date", "available_at", "date"))
        _copy_alias(
            payload,
            "eps",
            ("diluted_eps", "earnings_per_share", "earnings_per_share_diluted"),
        )
        _copy_alias(payload, "evidence_id", ("financial_evidence_id",))
        return payload


class HistoricalValuationToolInput(BaseModel):
    """历史估值工具的显式价格序列与 point-in-time 财务输入。"""

    model_config = ConfigDict(extra="allow")

    company_name: str | None = None
    ticker: str | None = None
    as_of: Any | None = None
    metric: str = "pe_ratio"
    historical_prices: list[HistoricalPricePoint] = Field(default_factory=list)
    financial_snapshots: list[PointInTimeFinancialSnapshot] = Field(
        default_factory=list
    )

    @model_validator(mode="before")
    @classmethod
    def accept_common_aliases(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        _copy_alias(payload, "as_of", ("analysis_date", "valuation_date"))
        _copy_alias(
            payload,
            "historical_prices",
            ("prices", "price_series", "historical_price_series", "price_points"),
        )
        _copy_alias(
            payload,
            "financial_snapshots",
            (
                "snapshots",
                "point_in_time_snapshots",
                "point_in_time_financial_snapshots",
                "financial_history",
            ),
        )
        return payload


class HistoricalValuationResult(BaseModel):
    status: Literal["ok", "unavailable"]
    calculation_id: str = HISTORICAL_VALUATION_CALCULATION_ID
    company_name: str | None = None
    ticker: str | None = None
    metric: str = "pe_ratio"
    current_value: str | None = None
    five_year_median: str | None = None
    percentile_25: str | None = None
    percentile_75: str | None = None
    current_percentile: str | None = None
    history_count: int = 0
    selected_dates: list[str] = Field(default_factory=list)
    input_evidence_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def median_5y(self) -> str | None:
        return self.five_year_median

    @property
    def p25(self) -> str | None:
        return self.percentile_25

    @property
    def p75(self) -> str | None:
        return self.percentile_75


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(
                text[:-1] + "+00:00" if text.endswith("Z") else text
            ).date()
        except ValueError:
            return None


def _valid_evidence_id(value: Any) -> bool:
    return isinstance(value, str) and _EVIDENCE_ID.fullmatch(value.strip()) is not None


def _evidence_ids(value: Any, additional: Any = None) -> list[str]:
    values: list[Any] = []
    if value is not None:
        values.extend(value if isinstance(value, list) else [value])
    if additional is not None:
        values.extend(additional if isinstance(additional, list) else [additional])
    result: list[str] = []
    for item in values:
        if isinstance(item, str) and item.strip() and item.strip() not in result:
            result.append(item.strip())
    return result


def _append_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _raw_value_and_ids(raw: Any) -> tuple[Any, list[str]]:
    if isinstance(raw, BaseModel):
        raw = raw.model_dump()
    if not isinstance(raw, Mapping):
        return raw, []
    ids = _evidence_ids(
        raw.get("evidence_id"),
        raw.get("evidence_ids", raw.get("input_evidence_ids")),
    )
    value = raw.get(
        "value",
        raw.get("numeric_value", raw.get("raw_result", raw.get("amount"))),
    )
    return value, ids


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _quantile(values: list[Decimal], probability: Decimal) -> Decimal:
    if len(values) == 1:
        return values[0]
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        position = Decimal(len(values) - 1) * probability
        lower_index = int(position.to_integral_value(rounding=ROUND_FLOOR))
        upper_index = min(lower_index + 1, len(values) - 1)
        fraction = position - Decimal(lower_index)
        return values[lower_index] + (
            values[upper_index] - values[lower_index]
        ) * fraction


def _percentile(values: list[Decimal], current: Decimal) -> Decimal:
    if len(values) == 1 or current <= values[0]:
        return Decimal("0")
    if current >= values[-1]:
        return Decimal("100")
    denominator = Decimal(len(values) - 1)
    for index in range(1, len(values)):
        lower = values[index - 1]
        upper = values[index]
        if current <= upper:
            if upper == lower:
                return Decimal(index) / denominator * Decimal("100")
            fraction = (current - lower) / (upper - lower)
            return (Decimal(index - 1) + fraction) / denominator * Decimal("100")
    return Decimal("100")


class HistoricalValuationTool(BaseTool):
    name: str = "historical_valuation_calculator"
    description: str = (
        "使用显式历史价格和 point-in-time 财务快照计算 P/E 历史统计；"
        "无法确认 Evidence 或日期时返回 unavailable，不使用前视数据。"
    )
    args_schema: Type[BaseModel] = HistoricalValuationToolInput
    result_schema: Type[BaseModel] = HistoricalValuationResult

    @staticmethod
    def _result(
        *,
        status: Literal["ok", "unavailable"],
        company_name: str | None,
        ticker: str | None,
        metric: str,
        reasons: list[str] | None = None,
        warnings: list[str] | None = None,
        **values: Any,
    ) -> HistoricalValuationResult:
        normalized_reasons = list(dict.fromkeys(reasons or []))
        normalized_warnings = list(dict.fromkeys(warnings or []))
        if status == "unavailable" and not normalized_warnings:
            normalized_warnings.append(
                "historical valuation unavailable: "
                + ", ".join(normalized_reasons or ["unknown_reason"])
            )
        return HistoricalValuationResult(
            status=status,
            company_name=company_name.strip() if company_name else None,
            ticker=ticker.strip().upper() if ticker else None,
            metric=metric,
            reasons=normalized_reasons,
            warnings=normalized_warnings,
            **values,
        )

    def _run(
        self,
        company_name: str | None = None,
        ticker: str | None = None,
        as_of: Any | None = None,
        metric: str = "pe_ratio",
        historical_prices: list[HistoricalPricePoint] | None = None,
        financial_snapshots: list[PointInTimeFinancialSnapshot] | None = None,
        **aliases: Any,
    ) -> HistoricalValuationResult:
        if historical_prices is None:
            historical_prices = aliases.get("prices") or aliases.get("price_series")
        if financial_snapshots is None:
            financial_snapshots = (
                aliases.get("snapshots")
                or aliases.get("point_in_time_snapshots")
                or aliases.get("point_in_time_financial_snapshots")
            )
        metric_name = str(metric or "pe_ratio").strip().lower()
        reasons: list[str] = []
        warnings: list[str] = []
        if metric_name != "pe_ratio":
            reasons.append("unsupported_metric")

        prices = historical_prices or []
        snapshots = financial_snapshots or []
        parsed_prices: list[tuple[date, Decimal, list[str]]] = []
        for point in prices:
            if not isinstance(point, HistoricalPricePoint):
                point = HistoricalPricePoint.model_validate(point)
            point_date = _as_date(point.date)
            if point_date is None:
                reasons.append("invalid_price_date")
            raw_price, nested_price_ids = _raw_value_and_ids(point.price)
            price = _as_decimal(raw_price)
            if price is None or price <= 0:
                reasons.append("invalid_price")
            ids = _evidence_ids(point.evidence_id, point.evidence_ids)
            _append_unique(ids, nested_price_ids)
            if not ids or not all(_valid_evidence_id(item) for item in ids):
                reasons.append("invalid_price_evidence_id")
            if point_date is not None and price is not None and price > 0:
                parsed_prices.append((point_date, price, ids))

        parsed_snapshots: list[tuple[date, Decimal, list[str]]] = []
        for snapshot in snapshots:
            if not isinstance(snapshot, PointInTimeFinancialSnapshot):
                snapshot = PointInTimeFinancialSnapshot.model_validate(snapshot)
            filed_at = _as_date(snapshot.filed_at)
            snapshot_date = filed_at or _as_date(snapshot.as_of)
            if snapshot_date is None:
                reasons.append("invalid_financial_date")
            snapshot_values: dict[str, Any] = {
                "eps": snapshot.eps,
                "diluted_eps": snapshot.diluted_eps,
                "earnings_per_share": snapshot.earnings_per_share,
                "net_income": snapshot.net_income,
                "shares_outstanding": snapshot.shares_outstanding,
                "value": snapshot.value,
            }
            snapshot_values.update(snapshot.facts)
            snapshot_values.update(snapshot.financials)
            if snapshot.model_extra:
                for key in ("facts", "financials", "metrics"):
                    extra_values = snapshot.model_extra.get(key)
                    if isinstance(extra_values, Mapping):
                        snapshot_values.update(extra_values)
            eps: Decimal | None = None
            nested_financial_ids: list[str] = []
            for key in (
                "eps",
                "diluted_eps",
                "earnings_per_share",
                "earnings_per_share_diluted",
                "metric_value",
                "value",
            ):
                if snapshot_values.get(key) is not None:
                    raw_eps, eps_ids = _raw_value_and_ids(snapshot_values[key])
                    eps = _as_decimal(raw_eps)
                    _append_unique(nested_financial_ids, eps_ids)
                    break
            if eps is None and not any(
                snapshot_values.get(key) is not None
                for key in ("eps", "diluted_eps", "earnings_per_share", "value")
            ):
                raw_net_income, net_income_ids = _raw_value_and_ids(
                    snapshot_values.get("net_income")
                )
                raw_shares, shares_ids = _raw_value_and_ids(
                    snapshot_values.get("shares_outstanding")
                )
                net_income = _as_decimal(raw_net_income)
                shares = _as_decimal(raw_shares)
                _append_unique(nested_financial_ids, net_income_ids)
                _append_unique(nested_financial_ids, shares_ids)
                if net_income is not None and shares is not None and shares > 0:
                    with localcontext() as context:
                        context.prec = 28
                        context.rounding = ROUND_HALF_EVEN
                        eps = net_income / shares
            if eps is None:
                reasons.append("invalid_financial_value")
            elif eps <= 0:
                reasons.append("non_positive_eps")
            ids = _evidence_ids(snapshot.evidence_id, snapshot.evidence_ids)
            _append_unique(ids, nested_financial_ids)
            if not ids or not all(_valid_evidence_id(item) for item in ids):
                reasons.append("invalid_financial_evidence_id")
            if snapshot_date is not None and eps is not None and eps > 0:
                parsed_snapshots.append((snapshot_date, eps, ids))

        requested_as_of = _as_date(as_of)
        if as_of is not None and requested_as_of is None:
            reasons.append("invalid_as_of_date")
        if reasons:
            return self._result(
                status="unavailable",
                company_name=company_name,
                ticker=ticker,
                metric=metric_name,
                reasons=reasons,
                warnings=warnings,
            )
        if not parsed_prices:
            return self._result(
                status="unavailable",
                company_name=company_name,
                ticker=ticker,
                metric=metric_name,
                reasons=["missing_historical_prices"],
                warnings=warnings,
            )
        if not parsed_snapshots:
            return self._result(
                status="unavailable",
                company_name=company_name,
                ticker=ticker,
                metric=metric_name,
                reasons=["missing_financial_snapshots"],
                warnings=warnings,
            )

        parsed_prices.sort(key=lambda item: item[0])
        analysis_date = requested_as_of or parsed_prices[-1][0]
        eligible_prices = [item for item in parsed_prices if item[0] <= analysis_date]
        if not eligible_prices:
            return self._result(
                status="unavailable",
                company_name=company_name,
                ticker=ticker,
                metric=metric_name,
                reasons=["no_price_on_or_before_as_of"],
                warnings=warnings,
            )
        if len(eligible_prices) != len(parsed_prices):
            warnings.append("future price observations excluded by as_of")

        monthly: dict[int, tuple[date, Decimal, list[str]]] = {}
        for point in eligible_prices:
            month_key = point[0].year * 12 + point[0].month
            monthly[month_key] = point
        latest_month = eligible_prices[-1][0].year * 12 + eligible_prices[-1][0].month
        first_month = latest_month - 59
        selected_prices = sorted(
            (
                point
                for month_key, point in monthly.items()
                if first_month <= month_key <= latest_month
            ),
            key=lambda item: item[0],
        )
        if len(selected_prices) < 60:
            return self._result(
                status="unavailable",
                company_name=company_name,
                ticker=ticker,
                metric=metric_name,
                history_count=len(selected_prices),
                reasons=["insufficient_history"],
                warnings=warnings,
            )

        parsed_snapshots.sort(key=lambda item: item[0])
        values: list[Decimal] = []
        selected_dates: list[str] = []
        input_ids: list[str] = []
        for price_date, price, price_ids in selected_prices:
            available = [item for item in parsed_snapshots if item[0] <= price_date]
            if not available:
                if any(item[0] > price_date for item in parsed_snapshots):
                    reasons.append("look_ahead")
                    warnings.append(
                        f"look-ahead snapshot excluded for price date {price_date.isoformat()}"
                    )
                else:
                    reasons.append("missing_financial_snapshot")
                continue
            snapshot_date, eps, snapshot_ids = available[-1]
            with localcontext() as context:
                context.prec = 28
                context.rounding = ROUND_HALF_EVEN
                values.append(price / eps)
            selected_dates.append(price_date.isoformat())
            for evidence_id in price_ids + snapshot_ids:
                if _valid_evidence_id(evidence_id) and evidence_id not in input_ids:
                    input_ids.append(evidence_id)

        if reasons or len(values) != 60:
            if len(values) != 60 and "insufficient_history" not in reasons:
                reasons.append("insufficient_history")
            return self._result(
                status="unavailable",
                company_name=company_name,
                ticker=ticker,
                metric=metric_name,
                history_count=len(values),
                selected_dates=selected_dates,
                input_evidence_ids=input_ids,
                reasons=reasons,
                warnings=warnings,
            )

        ordered = sorted(values)
        current_value = values[-1]
        with localcontext() as context:
            context.prec = 28
            context.rounding = ROUND_HALF_EVEN
            current_percentile = _percentile(ordered, current_value)
        if len(monthly) > 60:
            warnings.append("only the 60 latest monthly observations were used")
        return self._result(
            status="ok",
            company_name=company_name,
            ticker=ticker,
            metric=metric_name,
            current_value=_plain(current_value),
            five_year_median=_plain(_quantile(ordered, Decimal("0.5"))),
            percentile_25=_plain(_quantile(ordered, Decimal("0.25"))),
            percentile_75=_plain(_quantile(ordered, Decimal("0.75"))),
            current_percentile=_plain(current_percentile),
            history_count=len(values),
            selected_dates=selected_dates,
            input_evidence_ids=input_ids,
            reasons=[],
            warnings=warnings,
        )


__all__ = [
    "HistoricalPricePoint",
    "PointInTimeFinancialSnapshot",
    "HistoricalValuationToolInput",
    "HistoricalValuationResult",
    "HistoricalValuationTool",
    "HISTORICAL_VALUATION_CALCULATION_ID",
    "HistoricalValuationInput",
    "HistoricalFinancialSnapshot",
]

HistoricalValuationInput = HistoricalValuationToolInput
HistoricalFinancialSnapshot = PointInTimeFinancialSnapshot
