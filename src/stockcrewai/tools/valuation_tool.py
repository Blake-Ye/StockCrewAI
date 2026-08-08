from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_EVEN
from typing import Any, Literal, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


VALUATION_FORMULAS = (
    "market_capitalization",
    "pe_ratio",
    "fcf_yield",
)

_FACT_ALIASES = {
    "common_shares_outstanding": (
        "common_shares_outstanding",
        "shares_current",
    ),
    "diluted_eps": (
        "diluted_eps",
        "earnings_per_share_diluted",
    ),
    "current_fcf": (
        "current_fcf",
        "free_cash_flow",
        "free_cash_flow_current",
    ),
}


class ValuationToolInput(BaseModel):
    company_name: str | None = Field(default=None, description="公司名称")
    ticker: str | None = Field(default=None, description="股票代码")
    market_price: Any | None = Field(default=None, description="调用方提供的市场价格")
    price_timestamp: str | None = Field(
        default=None,
        description="市场价格的时间戳",
    )
    currency: str | None = Field(default=None, description="市场价格和估值的货币")
    source_reference: str | None = Field(
        default=None,
        description="市场价格来源引用",
    )
    facts: dict[str, Any] = Field(
        default_factory=dict,
        description="带 Evidence ID 的结构化财务 facts",
    )


class ValuationCalculation(BaseModel):
    calculation_id: str
    formula_id: str
    formula_version: str = "v1"
    input_evidence_ids: list[str] = Field(default_factory=list)
    raw_inputs: dict[str, str] = Field(default_factory=dict)
    raw_result: str | None = None
    normalized_result: str | None = None
    display_result: str | None = None
    unit: str | None = None
    status: Literal["available", "unavailable"]
    validation_status: Literal["unvalidated", "valid", "invalid"] = "unvalidated"
    market_price: str | None = None
    market_price_evidence_id: str | None = None
    price_timestamp: str | None = None
    currency: str | None = None
    source_reference: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @property
    def metric_id(self) -> str:
        return self.formula_id

    @property
    def price_currency(self) -> str | None:
        return self.currency

    @property
    def price_source_reference(self) -> str | None:
        return self.source_reference

    @property
    def price_evidence_id(self) -> str | None:
        return self.market_price_evidence_id


class ValuationResult(BaseModel):
    status: Literal["ok", "partial", "not_ready"]
    readiness: Literal["ready", "not_ready"]
    readiness_reasons: list[str] = Field(default_factory=list)
    company_name: str | None = None
    ticker: str | None = None
    market_price: str | None = None
    market_price_evidence_id: str | None = None
    price_timestamp: str | None = None
    currency: str | None = None
    source_reference: str | None = None
    calculations: list[ValuationCalculation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def price_evidence_id(self) -> str | None:
        return self.market_price_evidence_id


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("数值缺失或类型不支持")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("数值格式无效") from exc
    if not result.is_finite():
        raise ValueError("数值必须是有限值")
    return result


def _plain(value: Decimal) -> str:
    return format(value, "f")


def _scientific(value: Decimal) -> str:
    return format(value, ".5E")


def _multiply(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        return left * right


def _divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        return numerator / denominator


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text.strip() else None


def _valid_timestamp(value: Any) -> str | None:
    text = _as_text(value)
    if text is None or ("T" not in text and " " not in text):
        return None
    parse_value = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(parse_value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalized_unit(value: Any) -> str | None:
    text = _as_text(value)
    return text.upper().replace(" ", "").replace("_PER_", "/") if text else None


def _unit_check(
    fact_name: str,
    unit: str | None,
    expected: Literal["shares", "currency", "currency_per_share"],
    currency: str | None,
) -> tuple[bool, str | None, str]:
    normalized = _normalized_unit(unit)
    if expected == "shares":
        if normalized in {"SHARE", "SHARES"}:
            return True, None, ""
        return (
            False,
            f"{fact_name}_unit",
            f"{fact_name} unit 缺失或不受支持，必须为 shares",
        )

    if currency is None or normalized is None:
        return (
            False,
            f"{fact_name}_unit",
            f"{fact_name} unit 缺失或不受支持，无法匹配价格币种",
        )

    if expected == "currency":
        if normalized == currency:
            return True, None, ""
        if re.fullmatch(r"[A-Z][A-Z0-9]*", normalized):
            return (
                False,
                f"{fact_name}_currency_mismatch",
                f"{fact_name} unit {unit} 与价格币种 {currency} 不匹配",
            )
        return (
            False,
            f"{fact_name}_unit",
            f"{fact_name} unit 不受支持，必须匹配价格币种 {currency}",
        )

    match = re.fullmatch(
        r"([A-Z][A-Z0-9]*)(?:/|PER)SHARES?",
        normalized,
    )
    if match is None:
        return (
            False,
            f"{fact_name}_unit",
            f"{fact_name} unit 不受支持，必须为 {currency}/share",
        )
    if match.group(1) != currency:
        return (
            False,
            f"{fact_name}_currency_mismatch",
            f"{fact_name} unit {unit} 与价格币种 {currency} 不匹配",
        )
    return True, None, ""


def _unique_ids(*groups: list[str]) -> list[str]:
    result: list[str] = []
    for group in groups:
        for evidence_id in group:
            if evidence_id and evidence_id not in result:
                result.append(evidence_id)
    return result


def _has_financial_evidence(
    evidence_ids: list[str], market_price_evidence_id: str | None
) -> bool:
    return any(
        evidence_id and evidence_id != market_price_evidence_id
        for evidence_id in evidence_ids
    )


def _market_price_evidence_id(
    ticker: str | None,
    market_price: str | None,
    price_timestamp: str | None,
    currency: str | None,
    source_reference: str | None,
) -> str | None:
    if not all(
        (
            ticker,
            market_price,
            price_timestamp,
            currency,
            source_reference,
        )
    ):
        return None
    payload = json.dumps(
        {
            "currency": currency,
            "market_price": market_price,
            "price_timestamp": price_timestamp,
            "source_reference": source_reference,
            "ticker": ticker,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return f"ev_market_price_{ticker}_{digest}"


def _raw_value_and_evidence(raw: Any) -> tuple[Any, list[str]]:
    if isinstance(raw, BaseModel):
        raw = raw.model_dump()
    if not isinstance(raw, dict):
        return raw, []

    evidence_ids: list[str] = []
    if raw.get("evidence_id"):
        evidence_ids.append(str(raw["evidence_id"]))
    for key in ("evidence_ids", "input_evidence_ids"):
        ids = raw.get(key, [])
        if isinstance(ids, str):
            ids = [ids]
        if isinstance(ids, list):
            evidence_ids.extend(str(item) for item in ids if item)
    value = raw.get("value", raw.get("numeric_value", raw.get("raw_result")))
    return value, _unique_ids(evidence_ids)


def _fact(
    facts: dict[str, Any], canonical_name: str
) -> tuple[Decimal | None, list[str], str | None, str | None, str | None]:
    for fact_name in _FACT_ALIASES[canonical_name]:
        if fact_name not in facts:
            continue
        raw_fact = facts[fact_name]
        if isinstance(raw_fact, BaseModel):
            raw_fact = raw_fact.model_dump()
        unit = raw_fact.get("unit") if isinstance(raw_fact, dict) else None
        raw_value, evidence_ids = _raw_value_and_evidence(raw_fact)
        if raw_value is None:
            return None, evidence_ids, fact_name, _as_text(unit), "缺少数值"
        try:
            return _as_decimal(raw_value), evidence_ids, fact_name, _as_text(unit), None
        except ValueError as exc:
            return None, evidence_ids, fact_name, _as_text(unit), str(exc)
    return None, [], None, None, "缺少输入"


class ValuationTool(BaseTool):
    name: str = "deterministic_valuation_calculator"
    description: str = (
        "使用调用方提供的、有时间戳和来源的市场价格，基于结构化 facts 计算"
        " market capitalization、P/E 和 FCF yield；不会生成投资 verdict。"
    )
    args_schema: Type[BaseModel] = ValuationToolInput
    result_schema: Type[BaseModel] = ValuationResult

    @staticmethod
    def _unavailable(
        formula_id: str,
        evidence_ids: list[str],
        raw_inputs: dict[str, str],
        warning: str,
        market_price: str | None,
        market_price_evidence_id: str | None,
        price_timestamp: str | None,
        currency: str | None,
        source_reference: str | None,
    ) -> ValuationCalculation:
        return ValuationCalculation(
            calculation_id=f"calc_{formula_id}",
            formula_id=formula_id,
            input_evidence_ids=evidence_ids,
            raw_inputs=raw_inputs,
            status="unavailable",
            market_price=market_price,
            market_price_evidence_id=market_price_evidence_id,
            price_timestamp=price_timestamp,
            currency=currency,
            source_reference=source_reference,
            warnings=[warning],
        )

    @staticmethod
    def _available(
        formula_id: str,
        result: Decimal,
        unit: str,
        evidence_ids: list[str],
        raw_inputs: dict[str, str],
        market_price: str,
        market_price_evidence_id: str | None,
        price_timestamp: str,
        currency: str,
        source_reference: str,
        financial_evidence_complete: bool,
    ) -> ValuationCalculation:
        if unit == "ratio":
            display_result = f"{result * 100:.2f}%"
        elif unit == "multiple":
            display_result = f"{result:.2f}x"
        else:
            display_result = _plain(result)
        warnings: list[str] = []
        if not financial_evidence_complete:
            warnings.append("至少一个财务输入缺少 Evidence ID")
        if market_price_evidence_id is None:
            warnings.append("市场价格缺少完整 provenance 或 Evidence ID")
        validation_status: Literal["unvalidated", "valid", "invalid"] = (
            "valid"
            if market_price_evidence_id is not None
            and financial_evidence_complete
            else "unvalidated"
        )
        return ValuationCalculation(
            calculation_id=f"calc_{formula_id}",
            formula_id=formula_id,
            input_evidence_ids=evidence_ids,
            raw_inputs=raw_inputs,
            raw_result=_plain(result),
            normalized_result=_scientific(result),
            display_result=display_result,
            unit=unit,
            status="available",
            validation_status=validation_status,
            market_price=market_price,
            market_price_evidence_id=market_price_evidence_id,
            price_timestamp=price_timestamp,
            currency=currency,
            source_reference=source_reference,
            warnings=warnings,
        )

    def _run(
        self,
        company_name: str | None = None,
        ticker: str | None = None,
        market_price: Any | None = None,
        price_timestamp: str | None = None,
        currency: str | None = None,
        source_reference: str | None = None,
        facts: dict[str, Any] | None = None,
    ) -> ValuationResult:
        facts = facts or {}
        normalized_ticker = _as_text(ticker)
        if normalized_ticker is not None:
            normalized_ticker = normalized_ticker.upper()
        timestamp = _valid_timestamp(price_timestamp)
        price_currency = _as_text(currency)
        if price_currency is not None:
            price_currency = price_currency.upper()
        price_source = _as_text(source_reference)
        price: Decimal | None = None
        try:
            price_value, _ = _raw_value_and_evidence(market_price)
            if price_value is not None:
                price = _as_decimal(price_value)
        except ValueError:
            price = None
        market_price_text = _plain(price) if price is not None else None

        price_reasons: list[str] = []
        if price is None or price <= 0:
            price_reasons.append("market_price")
        if timestamp is None:
            price_reasons.append("price_timestamp")
        if price_currency is None:
            price_reasons.append("currency")
        if price_source is None:
            price_reasons.append("source_reference")
        price_ready = not price_reasons
        market_price_evidence_id = (
            _market_price_evidence_id(
                normalized_ticker,
                market_price_text,
                timestamp,
                price_currency,
                price_source,
            )
            if price_ready
            else None
        )
        market_evidence_ids = (
            [market_price_evidence_id] if market_price_evidence_id else []
        )

        shares, share_ids, _, share_unit, share_problem = _fact(
            facts, "common_shares_outstanding"
        )
        eps, eps_ids, _, eps_unit, eps_problem = _fact(facts, "diluted_eps")
        fcf, fcf_ids, _, fcf_unit, fcf_problem = _fact(facts, "current_fcf")
        shares_unit_ok, shares_unit_reason, shares_unit_warning = _unit_check(
            "common_shares_outstanding", share_unit, "shares", price_currency
        )
        eps_unit_ok, eps_unit_reason, eps_unit_warning = _unit_check(
            "diluted_eps", eps_unit, "currency_per_share", price_currency
        )
        fcf_unit_ok, fcf_unit_reason, fcf_unit_warning = _unit_check(
            "current_fcf", fcf_unit, "currency", price_currency
        )
        share_inputs = (
            {"common_shares_outstanding": _plain(shares)}
            if shares is not None
            else {}
        )
        eps_inputs = {"diluted_eps": _plain(eps)} if eps is not None else {}
        fcf_inputs = {"current_fcf": _plain(fcf)} if fcf is not None else {}

        calculations: list[ValuationCalculation] = []
        readiness_reasons = list(price_reasons)
        if shares is None:
            readiness_reasons.append("common_shares_outstanding")
        elif shares <= 0:
            readiness_reasons.append("common_shares_outstanding_positive")
        if shares_unit_reason:
            readiness_reasons.append(shares_unit_reason)
        if eps is None:
            readiness_reasons.append("diluted_eps")
        elif eps <= 0:
            readiness_reasons.append("diluted_eps_positive")
        if eps_unit_reason:
            readiness_reasons.append(eps_unit_reason)
        if fcf is None:
            readiness_reasons.append("current_fcf")
        if fcf_unit_reason:
            readiness_reasons.append(fcf_unit_reason)
        warnings: list[str] = []

        shares_ready = shares is not None and shares > 0 and shares_unit_ok
        eps_ready = eps is not None and eps > 0 and eps_unit_ok
        fcf_ready = fcf is not None and fcf_unit_ok
        share_warning = (
            f"缺少 common_shares_outstanding：{share_problem}"
            if shares is None
            else "common_shares_outstanding 必须为正数"
            if shares <= 0
            else shares_unit_warning
        )
        eps_warning = (
            f"缺少 diluted_eps：{eps_problem}"
            if eps is None
            else "diluted_eps 必须为正数才能计算 P/E"
            if eps <= 0
            else eps_unit_warning
        )
        fcf_warning = (
            f"缺少 current_fcf：{fcf_problem}"
            if fcf is None
            else fcf_unit_warning
        )

        provenance = {
            "market_price": market_price_text,
            "market_price_evidence_id": market_price_evidence_id,
            "price_timestamp": timestamp,
            "currency": price_currency,
            "source_reference": price_source,
        }
        if not price_ready:
            warning = "估值未就绪：缺少或无效的市场价格来源信息"
            calculations.extend(
                [
                    self._unavailable(
                        "market_capitalization",
                        _unique_ids(market_evidence_ids, share_ids),
                        share_inputs,
                        warning,
                        **provenance,
                    ),
                    self._unavailable(
                        "pe_ratio",
                        _unique_ids(market_evidence_ids, eps_ids),
                        eps_inputs,
                        warning,
                        **provenance,
                    ),
                    self._unavailable(
                        "fcf_yield",
                        _unique_ids(market_evidence_ids, fcf_ids, share_ids),
                        {**fcf_inputs, **share_inputs},
                        warning,
                        **provenance,
                    ),
                ]
            )
            warnings.append(warning)
        else:
            assert price is not None
            price_text = market_price_text or _plain(price)
            if not shares_ready:
                calculations.append(
                    self._unavailable(
                        "market_capitalization",
                        _unique_ids(market_evidence_ids, share_ids),
                        share_inputs,
                        share_warning,
                        **provenance,
                    )
                )
                calculations.append(
                    self._unavailable(
                        "fcf_yield",
                        _unique_ids(market_evidence_ids, fcf_ids, share_ids),
                        {**fcf_inputs, **share_inputs},
                        share_warning,
                        **provenance,
                    )
                )
            else:
                assert shares is not None
                market_cap = _multiply(price, shares)
                calculations.append(
                    self._available(
                        "market_capitalization",
                        market_cap,
                        "currency",
                        _unique_ids(market_evidence_ids, share_ids),
                        {
                            "market_price": price_text,
                            "common_shares_outstanding": _plain(shares),
                        },
                        price_text,
                        market_price_evidence_id,
                        timestamp or "",
                        price_currency or "",
                        price_source or "",
                        _has_financial_evidence(share_ids, market_price_evidence_id),
                    )
                )
                if not fcf_ready:
                    calculations.append(
                        self._unavailable(
                            "fcf_yield",
                            _unique_ids(market_evidence_ids, fcf_ids, share_ids),
                            {**fcf_inputs, **share_inputs},
                            fcf_warning,
                            **provenance,
                        )
                    )
                else:
                    assert fcf is not None
                    calculations.append(
                        self._available(
                            "fcf_yield",
                            _divide(fcf, market_cap),
                            "ratio",
                            _unique_ids(market_evidence_ids, fcf_ids, share_ids),
                            {
                                "current_fcf": _plain(fcf),
                                "market_price": price_text,
                                **share_inputs,
                            },
                            price_text,
                            market_price_evidence_id,
                            timestamp or "",
                            price_currency or "",
                            price_source or "",
                            _has_financial_evidence(
                                fcf_ids, market_price_evidence_id
                            )
                            and _has_financial_evidence(
                                share_ids, market_price_evidence_id
                            ),
                        )
                    )

            if not eps_ready:
                calculations.append(
                    self._unavailable(
                        "pe_ratio",
                        _unique_ids(market_evidence_ids, eps_ids),
                        eps_inputs,
                        eps_warning,
                        **provenance,
                    )
                )
            else:
                assert eps is not None
                calculations.append(
                    self._available(
                        "pe_ratio",
                        _divide(price, eps),
                        "multiple",
                        _unique_ids(market_evidence_ids, eps_ids),
                        {
                            "market_price": price_text,
                            "diluted_eps": _plain(eps),
                        },
                        price_text,
                        market_price_evidence_id,
                        timestamp or "",
                        price_currency or "",
                        price_source or "",
                        _has_financial_evidence(eps_ids, market_price_evidence_id),
                    )
                )

        by_formula = {item.formula_id: item for item in calculations}
        calculations = [by_formula[formula_id] for formula_id in VALUATION_FORMULAS]
        if any(
            item.status == "available" and item.validation_status != "valid"
            for item in calculations
        ):
            readiness_reasons.append("valuation_provenance")
        readiness_reasons = list(dict.fromkeys(readiness_reasons))
        readiness: Literal["ready", "not_ready"] = (
            "ready" if not readiness_reasons else "not_ready"
        )
        available_count = sum(item.status == "available" for item in calculations)
        status: Literal["ok", "partial", "not_ready"]
        if readiness == "ready":
            status = "ok"
        elif not price_ready or available_count == 0:
            status = "not_ready"
        else:
            status = "partial"
        for item in calculations:
            warnings.extend(item.warnings)
        return ValuationResult(
            status=status,
            readiness=readiness,
            readiness_reasons=readiness_reasons,
            company_name=company_name.strip() if company_name else None,
            ticker=normalized_ticker,
            market_price=market_price_text,
            market_price_evidence_id=market_price_evidence_id,
            price_timestamp=timestamp,
            currency=price_currency,
            source_reference=price_source,
            calculations=calculations,
            warnings=list(dict.fromkeys(warnings)),
        )
