from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from typing import Any, Literal, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, model_validator


_EVIDENCE_ID = re.compile(r"ev_[A-Za-z0-9][A-Za-z0-9_.:-]*")
REVERSE_DCF_CALCULATION_ID = "calc_reverse_dcf_growth"
_SCENARIOS = (
    (Decimal("0.08"), Decimal("0.02")),
    (Decimal("0.09"), Decimal("0.025")),
    (Decimal("0.10"), Decimal("0.03")),
)
_FORECAST_YEARS = 10
_GROWTH_LOW = Decimal("-0.99")
_GROWTH_HIGH = Decimal("1")
_SOLVER_TOLERANCE = Decimal("1E-18")
_MAX_ITERATIONS = 128


def _copy_alias(payload: dict[str, Any], target: str, aliases: tuple[str, ...]) -> None:
    if payload.get(target) is not None:
        return
    for alias in aliases:
        if payload.get(alias) is not None:
            payload[target] = payload[alias]
            return


class ReverseDCFInput(BaseModel):
    """反向 DCF 的显式市场、FCF proxy 和股份输入。"""

    model_config = ConfigDict(extra="allow")

    company_name: str | None = None
    ticker: str | None = None
    market_price: Any | None = None
    price_evidence_id: Any | None = None
    fcf: Any | None = None
    fcf_evidence_id: Any | None = None
    base_fcf: Any | None = None
    fcf_proxy: Any | None = None
    current_fcf: Any | None = None
    free_cash_flow: Any | None = None
    operating_cash_flow: Any | None = None
    operating_cash_flow_evidence_id: Any | None = None
    capital_expenditure: Any | None = None
    capex: Any | None = None
    capital_expenditure_evidence_id: Any | None = None
    shares_outstanding: Any | None = None
    shares_evidence_id: Any | None = None
    price_timestamp: Any | None = None
    forecast_years: Any = _FORECAST_YEARS
    facts: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def accept_common_aliases(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        _copy_alias(payload, "market_price", ("price", "current_price"))
        _copy_alias(
            payload,
            "fcf",
            ("base_fcf", "fcf_proxy", "current_fcf", "free_cash_flow"),
        )
        _copy_alias(
            payload,
            "shares_outstanding",
            ("shares", "common_shares_outstanding", "shares_current"),
        )
        _copy_alias(payload, "price_evidence_id", ("price_evidence",))
        _copy_alias(payload, "fcf_evidence_id", ("free_cash_flow_evidence_id",))
        _copy_alias(payload, "shares_evidence_id", ("shares_outstanding_evidence_id",))
        return payload


ReverseDCFToolInput = ReverseDCFInput


class ReverseDCFScenario(BaseModel):
    discount_rate: str
    terminal_growth: str
    implied_growth: str | None = None
    iteration_count: int = 0
    residual: str | None = None
    convergence_status: Literal["converged", "not_converged", "unavailable"]
    equity_value: str | None = None


class ReverseDCFResult(BaseModel):
    status: Literal["ok", "unavailable"]
    calculation_id: str = REVERSE_DCF_CALCULATION_ID
    company_name: str | None = None
    ticker: str | None = None
    base_fcf: str | None = None
    equity_value: str | None = None
    market_price: str | None = None
    shares_outstanding: str | None = None
    forecast_years: int = _FORECAST_YEARS
    discount_rate: str | None = None
    terminal_growth: str | None = None
    implied_growth: str | None = None
    iteration_count: int = 0
    residual: str | None = None
    convergence_status: Literal["converged", "not_converged", "unavailable"] = (
        "unavailable"
    )
    scenario_matrix: list[ReverseDCFScenario] = Field(default_factory=list)
    input_evidence_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _as_int(value: Any) -> int | None:
    decimal_value = _as_decimal(value)
    if decimal_value is None or decimal_value != decimal_value.to_integral_value():
        return None
    return int(decimal_value)


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _valid_evidence_id(value: Any) -> bool:
    return isinstance(value, str) and _EVIDENCE_ID.fullmatch(value.strip()) is not None


def _raw_value_and_ids(raw: Any) -> tuple[Any, list[str]]:
    if isinstance(raw, BaseModel):
        raw = raw.model_dump()
    if not isinstance(raw, Mapping):
        return raw, []
    ids: list[str] = []
    for key in ("evidence_id", "evidence_ids", "input_evidence_ids"):
        value = raw.get(key)
        if value is None:
            continue
        ids.extend(value if isinstance(value, list) else [value])
    result_ids: list[str] = []
    for item in ids:
        if isinstance(item, str) and item.strip() and item.strip() not in result_ids:
            result_ids.append(item.strip())
    value = raw.get(
        "value",
        raw.get("numeric_value", raw.get("raw_result", raw.get("amount"))),
    )
    return value, result_ids


def _first_value(values: tuple[Any, ...], facts: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for value in values:
        if value is not None:
            return value
    for key in keys:
        if facts.get(key) is not None:
            return facts[key]
    return None


def _append_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _dcf_value(
    base_fcf: Decimal,
    growth: Decimal,
    discount_rate: Decimal,
    terminal_growth: Decimal,
    forecast_years: int,
) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        one = Decimal("1")
        present_value = Decimal("0")
        for year in range(1, forecast_years + 1):
            forecast_fcf = base_fcf * (one + growth) ** year
            present_value += forecast_fcf / (one + discount_rate) ** year
        terminal_fcf = base_fcf * (one + growth) ** forecast_years
        terminal_value = terminal_fcf * (one + terminal_growth) / (
            discount_rate - terminal_growth
        )
        return present_value + terminal_value / (one + discount_rate) ** forecast_years


def _solve_growth(
    base_fcf: Decimal,
    equity_value: Decimal,
    discount_rate: Decimal,
    terminal_growth: Decimal,
    forecast_years: int,
) -> tuple[Decimal | None, int, Decimal | None, Literal["converged", "not_converged"]]:
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        low = _GROWTH_LOW
        high = _GROWTH_HIGH
        low_residual = _dcf_value(
            base_fcf, low, discount_rate, terminal_growth, forecast_years
        ) - equity_value
        high_residual = _dcf_value(
            base_fcf, high, discount_rate, terminal_growth, forecast_years
        ) - equity_value
        if low_residual > 0 or high_residual < 0:
            boundary = low if abs(low_residual) < abs(high_residual) else high
            return boundary, 0, (
                low_residual if boundary == low else high_residual
            ), "not_converged"
        for iteration in range(1, _MAX_ITERATIONS + 1):
            midpoint = (low + high) / Decimal("2")
            residual = _dcf_value(
                base_fcf,
                midpoint,
                discount_rate,
                terminal_growth,
                forecast_years,
            ) - equity_value
            if abs(residual) <= _SOLVER_TOLERANCE or high - low <= _SOLVER_TOLERANCE:
                return midpoint, iteration, residual, "converged"
            if residual < 0:
                low = midpoint
            else:
                high = midpoint
        midpoint = (low + high) / Decimal("2")
        residual = _dcf_value(
            base_fcf,
            midpoint,
            discount_rate,
            terminal_growth,
            forecast_years,
        ) - equity_value
        return midpoint, _MAX_ITERATIONS, residual, "not_converged"


class ReverseDCFTool(BaseTool):
    name: str = "reverse_dcf_calculator"
    description: str = (
        "使用价格、股份和 FCF proxy，以固定 10 年期和三种折现/永续增长场景"
        "用 Decimal 二分法求隐含增长率；输入不足时返回 unavailable。"
    )
    args_schema: Type[BaseModel] = ReverseDCFInput
    result_schema: Type[BaseModel] = ReverseDCFResult

    @staticmethod
    def _unavailable(
        *,
        company_name: str | None,
        ticker: str | None,
        reasons: list[str],
        warnings: list[str],
        input_evidence_ids: list[str],
        market_price: Decimal | None = None,
        shares_outstanding: Decimal | None = None,
        base_fcf: Decimal | None = None,
    ) -> ReverseDCFResult:
        if not warnings:
            warnings = ["reverse DCF unavailable: " + ", ".join(dict.fromkeys(reasons))]
        return ReverseDCFResult(
            status="unavailable",
            company_name=company_name.strip() if company_name else None,
            ticker=ticker.strip().upper() if ticker else None,
            market_price=_plain(market_price) if market_price is not None else None,
            shares_outstanding=(
                _plain(shares_outstanding) if shares_outstanding is not None else None
            ),
            base_fcf=_plain(base_fcf) if base_fcf is not None else None,
            reasons=list(dict.fromkeys(reasons)),
            warnings=list(dict.fromkeys(warnings)),
            input_evidence_ids=input_evidence_ids,
        )

    def _run(
        self,
        company_name: str | None = None,
        ticker: str | None = None,
        market_price: Any | None = None,
        price_evidence_id: Any | None = None,
        fcf: Any | None = None,
        fcf_evidence_id: Any | None = None,
        base_fcf: Any | None = None,
        fcf_proxy: Any | None = None,
        current_fcf: Any | None = None,
        free_cash_flow: Any | None = None,
        operating_cash_flow: Any | None = None,
        operating_cash_flow_evidence_id: Any | None = None,
        capital_expenditure: Any | None = None,
        capex: Any | None = None,
        capital_expenditure_evidence_id: Any | None = None,
        shares_outstanding: Any | None = None,
        shares_evidence_id: Any | None = None,
        price_timestamp: Any | None = None,
        forecast_years: Any = _FORECAST_YEARS,
        facts: dict[str, Any] | None = None,
        **aliases: Any,
    ) -> ReverseDCFResult:
        if market_price is None:
            market_price = aliases.get("price") or aliases.get("current_price")
        if fcf is None:
            fcf = aliases.get("fcf_proxy") or aliases.get("free_cash_flow")
        if shares_outstanding is None:
            shares_outstanding = (
                aliases.get("shares")
                or aliases.get("common_shares_outstanding")
                or aliases.get("shares_current")
            )
        del price_timestamp
        fact_map: Mapping[str, Any] = facts or {}
        reasons: list[str] = []
        warnings: list[str] = []
        input_ids: list[str] = []

        raw_price = _first_value(
            (market_price,), fact_map, ("market_price", "price", "current_price")
        )
        price_value, price_ids = _raw_value_and_ids(raw_price)
        if price_evidence_id is not None:
            price_ids.extend(
                item
                for item in (
                    price_evidence_id
                    if isinstance(price_evidence_id, list)
                    else [price_evidence_id]
                )
                if item not in price_ids
            )
        price = _as_decimal(price_value)
        valid_price_ids = [item for item in price_ids if _valid_evidence_id(item)]
        if price is None or price <= 0:
            reasons.append("invalid_price")
        if not price_ids:
            reasons.append("missing_price_evidence_id")
        elif len(valid_price_ids) != len(price_ids):
            reasons.append("invalid_price_evidence_id")
        _append_unique(input_ids, valid_price_ids)

        raw_shares = _first_value(
            (shares_outstanding,),
            fact_map,
            ("shares_outstanding", "common_shares_outstanding", "shares_current", "shares"),
        )
        shares_value, shares_ids = _raw_value_and_ids(raw_shares)
        if shares_evidence_id is not None:
            shares_ids.extend(
                item
                for item in (
                    shares_evidence_id
                    if isinstance(shares_evidence_id, list)
                    else [shares_evidence_id]
                )
                if item not in shares_ids
            )
        shares = _as_decimal(shares_value)
        valid_shares_ids = [item for item in shares_ids if _valid_evidence_id(item)]
        if shares is None or shares <= 0:
            reasons.append("invalid_shares_outstanding")
        if not shares_ids:
            reasons.append("missing_shares_evidence_id")
        elif len(valid_shares_ids) != len(shares_ids):
            reasons.append("invalid_shares_evidence_id")
        direct_fcf = _first_value(
            (fcf, base_fcf, fcf_proxy, current_fcf, free_cash_flow),
            fact_map,
            ("fcf", "base_fcf", "fcf_proxy", "current_fcf", "free_cash_flow"),
        )
        raw_direct_fcf, direct_fcf_ids = _raw_value_and_ids(direct_fcf)
        if fcf_evidence_id is not None:
            direct_fcf_ids.extend(
                item
                for item in (
                    fcf_evidence_id
                    if isinstance(fcf_evidence_id, list)
                    else [fcf_evidence_id]
                )
                if item not in direct_fcf_ids
            )

        fcf_value: Decimal | None = None
        valid_fcf_ids: list[str] = []
        if direct_fcf is not None:
            fcf_value = _as_decimal(raw_direct_fcf)
            valid_fcf_ids = [item for item in direct_fcf_ids if _valid_evidence_id(item)]
            if fcf_value is None or fcf_value <= 0:
                reasons.append("invalid_fcf")
            if not direct_fcf_ids:
                reasons.append("missing_fcf_evidence_id")
            elif len(valid_fcf_ids) != len(direct_fcf_ids):
                reasons.append("invalid_fcf_evidence_id")
        else:
            raw_ocf = _first_value(
                (operating_cash_flow,),
                fact_map,
                ("operating_cash_flow", "ocf"),
            )
            raw_capex = _first_value(
                (capital_expenditure, capex),
                fact_map,
                ("capital_expenditure", "capex"),
            )
            ocf_value, ocf_ids = _raw_value_and_ids(raw_ocf)
            capex_value, capex_ids = _raw_value_and_ids(raw_capex)
            if operating_cash_flow_evidence_id is not None:
                ocf_ids.append(str(operating_cash_flow_evidence_id))
            if capital_expenditure_evidence_id is not None:
                capex_ids.append(str(capital_expenditure_evidence_id))
            ocf = _as_decimal(ocf_value)
            capex_decimal = _as_decimal(capex_value)
            valid_ocf_ids = [item for item in ocf_ids if _valid_evidence_id(item)]
            valid_capex_ids = [item for item in capex_ids if _valid_evidence_id(item)]
            if ocf is None:
                reasons.append("invalid_operating_cash_flow")
            if capex_decimal is None or capex_decimal < 0:
                reasons.append("invalid_capital_expenditure")
            if not ocf_ids:
                reasons.append("missing_operating_cash_flow_evidence_id")
            elif len(valid_ocf_ids) != len(ocf_ids):
                reasons.append("invalid_operating_cash_flow_evidence_id")
            if not capex_ids:
                reasons.append("missing_capital_expenditure_evidence_id")
            elif len(valid_capex_ids) != len(capex_ids):
                reasons.append("invalid_capital_expenditure_evidence_id")
            if ocf is not None and capex_decimal is not None and capex_decimal >= 0:
                with localcontext() as context:
                    context.prec = 28
                    context.rounding = ROUND_HALF_EVEN
                    fcf_value = ocf - capex_decimal
            valid_fcf_ids = []
            _append_unique(valid_fcf_ids, valid_ocf_ids)
            _append_unique(valid_fcf_ids, valid_capex_ids)
            if fcf_value is not None and fcf_value <= 0:
                reasons.append("invalid_fcf")

        _append_unique(input_ids, valid_fcf_ids)
        _append_unique(input_ids, valid_shares_ids)
        if fcf_value is not None and fcf_value <= 0 and "invalid_fcf" not in reasons:
            reasons.append("invalid_fcf")

        years = _as_int(forecast_years)
        if years != _FORECAST_YEARS:
            reasons.append("forecast_years_must_be_10")

        if reasons:
            return self._unavailable(
                company_name=company_name,
                ticker=ticker,
                reasons=reasons,
                warnings=warnings,
                input_evidence_ids=input_ids,
                market_price=price,
                shares_outstanding=shares,
                base_fcf=fcf_value,
            )

        assert price is not None
        assert shares is not None
        assert fcf_value is not None
        with localcontext() as context:
            context.prec = 28
            context.rounding = ROUND_HALF_EVEN
            equity_value = price * shares

        matrix: list[ReverseDCFScenario] = []
        for discount_rate, terminal_growth in _SCENARIOS:
            implied_growth, iteration_count, residual, convergence_status = _solve_growth(
                fcf_value,
                equity_value,
                discount_rate,
                terminal_growth,
                _FORECAST_YEARS,
            )
            matrix.append(
                ReverseDCFScenario(
                    discount_rate=_plain(discount_rate),
                    terminal_growth=_plain(terminal_growth),
                    implied_growth=(
                        _plain(implied_growth) if implied_growth is not None else None
                    ),
                    iteration_count=iteration_count,
                    residual=_plain(residual) if residual is not None else None,
                    convergence_status=convergence_status,
                    equity_value=_plain(equity_value),
                )
            )

        base = matrix[1]
        if base.convergence_status != "converged":
            warnings.append("base scenario did not converge within the deterministic bounds")
            return ReverseDCFResult(
                status="unavailable",
                company_name=company_name.strip() if company_name else None,
                ticker=ticker.strip().upper() if ticker else None,
                base_fcf=_plain(fcf_value),
                equity_value=_plain(equity_value),
                market_price=_plain(price),
                shares_outstanding=_plain(shares),
                forecast_years=_FORECAST_YEARS,
                discount_rate=base.discount_rate,
                terminal_growth=base.terminal_growth,
                implied_growth=base.implied_growth,
                iteration_count=base.iteration_count,
                residual=base.residual,
                convergence_status=base.convergence_status,
                scenario_matrix=matrix,
                input_evidence_ids=input_ids,
                reasons=["no_converged_base_scenario"],
                warnings=warnings,
            )
        return ReverseDCFResult(
            status="ok",
            company_name=company_name.strip() if company_name else None,
            ticker=ticker.strip().upper() if ticker else None,
            base_fcf=_plain(fcf_value),
            equity_value=_plain(equity_value),
            market_price=_plain(price),
            shares_outstanding=_plain(shares),
            forecast_years=_FORECAST_YEARS,
            discount_rate=base.discount_rate,
            terminal_growth=base.terminal_growth,
            implied_growth=base.implied_growth,
            iteration_count=base.iteration_count,
            residual=base.residual,
            convergence_status=base.convergence_status,
            scenario_matrix=matrix,
            input_evidence_ids=input_ids,
            reasons=[],
            warnings=warnings,
        )


__all__ = [
    "ReverseDCFInput",
    "ReverseDCFToolInput",
    "ReverseDCFScenario",
    "ReverseDCFResult",
    "ReverseDCFTool",
    "REVERSE_DCF_CALCULATION_ID",
]
