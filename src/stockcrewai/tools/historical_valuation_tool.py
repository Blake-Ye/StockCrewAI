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
HISTORICAL_VALUATION_REQUIRED_MONTHS = 60


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
    period_basis: Any | None = None
    ttm_eps: Any | None = None
    eps: Any | None = None
    diluted_eps: Any | None = None
    earnings_per_share: Any | None = None
    net_income: Any | None = None
    shares_outstanding: Any | None = None
    value: Any | None = None
    evidence_id: Any | None = None
    evidence_ids: Any | None = None
    financial_evidence_ids: Any | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    financials: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def accept_common_aliases(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        _copy_alias(payload, "as_of", ("snapshot_date", "available_at", "date"))
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
    status: Literal["ok", "unavailable", "not_applicable"]
    calculation_id: str = HISTORICAL_VALUATION_CALCULATION_ID
    company_name: str | None = None
    ticker: str | None = None
    metric: str = "pe_ratio"
    period_basis: Literal["TTM"] | None = None
    current_value: str | None = None
    current_date: str | None = None
    series: list[dict[str, Any]] = Field(default_factory=list)
    five_year_median: str | None = None
    percentile_25: str | None = None
    percentile_75: str | None = None
    current_percentile: str | None = None
    history_count: int = 0
    selected_dates: list[str] = Field(default_factory=list)
    input_evidence_ids: list[str] = Field(default_factory=list)
    available_months: int = 0
    required_months: int = HISTORICAL_VALUATION_REQUIRED_MONTHS
    applicability_reason: str | None = None
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
        raw.get(
            "evidence_ids",
            raw.get("financial_evidence_ids", raw.get("input_evidence_ids")),
        ),
    )
    value = raw.get(
        "value",
        raw.get(
            "ttm_eps",
            raw.get("numeric_value", raw.get("raw_result", raw.get("amount"))),
        ),
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
        "历史样本不足时返回 not_applicable；无法确认 Evidence 或日期时返回 "
        "unavailable，不使用前视数据。"
    )
    args_schema: Type[BaseModel] = HistoricalValuationToolInput
    result_schema: Type[BaseModel] = HistoricalValuationResult

    @staticmethod
    def _result(
        *,
        status: Literal["ok", "unavailable", "not_applicable"],
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
        if status != "ok":
            values["series"] = []
            values["current_date"] = None
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
            if filed_at is None:
                reasons.append("historical_ttm_eps_required")
                continue
            snapshot_values: dict[str, Any] = {
                "period_basis": snapshot.period_basis,
                "ttm_eps": snapshot.ttm_eps,
                "financial_evidence_ids": snapshot.financial_evidence_ids,
            }
            snapshot_values.update(snapshot.facts)
            snapshot_values.update(snapshot.financials)
            if snapshot.model_extra:
                for key in ("facts", "financials", "metrics"):
                    extra_values = snapshot.model_extra.get(key)
                    if isinstance(extra_values, Mapping):
                        snapshot_values.update(extra_values)
            period_basis = str(snapshot_values.get("period_basis") or "").strip().upper()
            raw_eps = snapshot_values.get("ttm_eps")
            eps_ids: list[str] = []
            if isinstance(raw_eps, Mapping) or isinstance(raw_eps, BaseModel):
                raw_eps, nested_ids = _raw_value_and_ids(raw_eps)
                _append_unique(eps_ids, nested_ids)
            eps = _as_decimal(raw_eps)
            if period_basis != "TTM" or eps is None or eps <= 0:
                reasons.append("historical_ttm_eps_required")
                continue
            ids = _evidence_ids(snapshot.evidence_id, snapshot.evidence_ids)
            _append_unique(ids, _evidence_ids(snapshot.financial_evidence_ids))
            _append_unique(ids, eps_ids)
            for key in ("financial_evidence_ids", "evidence_ids"):
                _append_unique(ids, _evidence_ids(snapshot_values.get(key)))
            if not ids or not all(_valid_evidence_id(item) for item in ids):
                reasons.append("invalid_financial_evidence_id")
            if ids and all(_valid_evidence_id(item) for item in ids):
                parsed_snapshots.append((filed_at, eps, ids))

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
        first_month = latest_month - (HISTORICAL_VALUATION_REQUIRED_MONTHS - 1)
        selected_prices = sorted(
            (
                point
                for month_key, point in monthly.items()
                if first_month <= month_key <= latest_month
            ),
            key=lambda item: item[0],
        )
        if len(selected_prices) < HISTORICAL_VALUATION_REQUIRED_MONTHS:
            return self._result(
                status="not_applicable",
                company_name=company_name,
                ticker=ticker,
                metric=metric_name,
                history_count=len(selected_prices),
                available_months=len(selected_prices),
                required_months=HISTORICAL_VALUATION_REQUIRED_MONTHS,
                applicability_reason="insufficient_history",
                reasons=["insufficient_history"],
                warnings=warnings,
            )

        if not parsed_snapshots:
            return self._result(
                status="unavailable",
                company_name=company_name,
                ticker=ticker,
                metric=metric_name,
                available_months=len(selected_prices),
                required_months=HISTORICAL_VALUATION_REQUIRED_MONTHS,
                reasons=["missing_financial_snapshots"],
                warnings=warnings,
            )

        parsed_snapshots.sort(key=lambda item: item[0])
        values: list[Decimal] = []
        series: list[dict[str, str]] = []
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
                pe_ratio = price / eps
            values.append(pe_ratio)
            selected_date = price_date.isoformat()
            selected_dates.append(selected_date)
            series.append(
                {
                    "date": selected_date,
                    "ttm_eps": _plain(eps),
                    "pe_ratio": _plain(pe_ratio),
                    "financial_evidence_ids": list(snapshot_ids),
                }
            )
            for evidence_id in price_ids + snapshot_ids:
                if _valid_evidence_id(evidence_id) and evidence_id not in input_ids:
                    input_ids.append(evidence_id)

        if reasons or len(values) != HISTORICAL_VALUATION_REQUIRED_MONTHS:
            if (
                len(values) != HISTORICAL_VALUATION_REQUIRED_MONTHS
                and "insufficient_history" not in reasons
            ):
                reasons.append("insufficient_history")
            return self._result(
                status="unavailable",
                company_name=company_name,
                ticker=ticker,
                metric=metric_name,
                history_count=len(values),
                available_months=len(values),
                required_months=HISTORICAL_VALUATION_REQUIRED_MONTHS,
                selected_dates=selected_dates,
                input_evidence_ids=input_ids,
                series=[],
                current_date=None,
                reasons=reasons,
                warnings=warnings,
            )

        ordered = sorted(values)
        current_value = values[-1]
        with localcontext() as context:
            context.prec = 28
            context.rounding = ROUND_HALF_EVEN
            current_percentile = _percentile(ordered, current_value)
        if len(monthly) > HISTORICAL_VALUATION_REQUIRED_MONTHS:
            warnings.append("only the 60 latest monthly observations were used")
        return self._result(
            status="ok",
            company_name=company_name,
            ticker=ticker,
            metric=metric_name,
            period_basis="TTM",
            current_value=series[-1]["pe_ratio"],
            current_date=series[-1]["date"],
            series=series,
            five_year_median=_plain(_quantile(ordered, Decimal("0.5"))),
            percentile_25=_plain(_quantile(ordered, Decimal("0.25"))),
            percentile_75=_plain(_quantile(ordered, Decimal("0.75"))),
            current_percentile=_plain(current_percentile),
            history_count=len(values),
            available_months=len(values),
            required_months=HISTORICAL_VALUATION_REQUIRED_MONTHS,
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
    "HISTORICAL_VALUATION_REQUIRED_MONTHS",
    "HistoricalValuationInput",
    "HistoricalFinancialSnapshot",
]

HistoricalValuationInput = HistoricalValuationToolInput
HistoricalFinancialSnapshot = PointInTimeFinancialSnapshot
