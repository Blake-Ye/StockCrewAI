from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from stockcrewai.tools.edgar_tool import EdgarFact


TTM_ROLES = ("latest_fy", "current_ytd", "prior_ytd")
SUPPORTED_TTM_METRICS = (
    "revenue",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "capex",
)


class TTMBuilderToolInput(BaseModel):
    company_name: str | None = None
    ticker: str | None = None
    metric_inputs: dict[str, dict[str, EdgarFact]] = Field(default_factory=dict)


class TTMMetricResult(BaseModel):
    metric_id: str
    calculation_id: str
    formula_id: str
    formula_version: str = "v1"
    input_evidence_ids: list[str] = Field(default_factory=list)
    raw_inputs: dict[str, str] = Field(default_factory=dict)
    raw_result: str | None = None
    unit: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    status: Literal["available", "unavailable"]
    validation_status: Literal["unvalidated", "valid"] = "unvalidated"
    reasons: list[str] = Field(default_factory=list)


class TTMResult(BaseModel):
    status: Literal["ok", "partial"]
    company_name: str | None = None
    ticker: str | None = None
    metrics: list[TTMMetricResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("invalid_value")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid_value") from exc
    if not parsed.is_finite():
        raise ValueError("invalid_value")
    return parsed


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _unit(value: str | None) -> str | None:
    return value.strip().upper().replace(" ", "") if value else None


def _periods_match(facts: dict[str, EdgarFact]) -> bool:
    latest = facts["latest_fy"]
    current = facts["current_ytd"]
    prior = facts["prior_ytd"]
    if any(fact.period_type != "duration" for fact in facts.values()):
        return False
    if str(latest.fiscal_period).upper() != "FY":
        return False
    current_fiscal_period = str(current.fiscal_period).upper()
    prior_fiscal_period = str(prior.fiscal_period).upper()
    if (
        current_fiscal_period != prior_fiscal_period
        or current_fiscal_period not in {"Q1", "Q2", "Q3"}
    ):
        return False
    if (
        current.fiscal_year != latest.fiscal_year + 1
        or prior.fiscal_year != latest.fiscal_year
    ):
        return False
    current_start = _date(current.period_start)
    current_end = _date(current.period_end)
    prior_start = _date(prior.period_start)
    prior_end = _date(prior.period_end)
    if not all((current_start, current_end, prior_start, prior_end)):
        return False
    if current_start > current_end or prior_start > prior_end:
        return False
    current_duration = current_end - current_start
    prior_duration = prior_end - prior_start
    start_shift = current_start - prior_start
    end_shift = current_end - prior_end
    if (
        abs(current_duration - prior_duration) > timedelta(days=7)
        or not all(
            timedelta(days=364) <= shift <= timedelta(days=371)
            for shift in (start_shift, end_shift)
        )
    ):
        return False
    return True


class TTMBuilderTool(BaseTool):
    name: str = "deterministic_ttm_builder"
    description: str = (
        "使用三段已验证的 SEC 流量 Evidence 构建 TTM；不对 EPS、股票数量或时点值加总。"
    )
    args_schema: Type[BaseModel] = TTMBuilderToolInput

    @staticmethod
    def _ids(facts: dict[str, EdgarFact]) -> list[str]:
        return [facts[role].evidence_id for role in TTM_ROLES if facts[role].evidence_id]

    @staticmethod
    def _base_result(metric_id: str, **kwargs: Any) -> TTMMetricResult:
        return TTMMetricResult(
            metric_id=metric_id,
            calculation_id=f"calc_{metric_id}_ttm",
            formula_id=f"ttm_{metric_id}",
            **kwargs,
        )

    def _build_metric(
        self,
        metric_id: str,
        raw_by_role: dict[str, EdgarFact],
    ) -> tuple[TTMMetricResult, Decimal | None]:
        if metric_id not in SUPPORTED_TTM_METRICS:
            return self._base_result(
                metric_id,
                status="unavailable",
                reasons=["unsupported_metric"],
            ), None
        try:
            facts = {
                role: EdgarFact.model_validate(raw_by_role[role])
                for role in raw_by_role
            }
        except Exception:
            return self._base_result(
                metric_id,
                status="unavailable",
                reasons=["invalid_evidence"],
            ), None
        ids = [
            facts[role].evidence_id
            for role in TTM_ROLES
            if role in facts and facts[role].evidence_id
        ]
        raw_inputs: dict[str, str] = {}
        values: dict[str, Decimal] = {}
        reasons: list[str] = []
        if set(facts) - set(TTM_ROLES):
            reasons.append("unsupported_role")
        if any(role not in facts for role in TTM_ROLES):
            reasons.append("missing_input")
        if len(ids) != len(set(ids)):
            reasons.append("duplicate_evidence_id")
        if any(facts.get(role) and facts[role].validation_status != "valid" for role in TTM_ROLES):
            reasons.append("invalid_evidence")
        for role in TTM_ROLES:
            if role not in facts:
                continue
            fact = facts[role]
            if not all(
                getattr(fact, field)
                for field in (
                    "evidence_id",
                    "value",
                    "unit",
                    "period_type",
                    "period",
                    "period_start",
                    "period_end",
                    "fiscal_year",
                    "fiscal_period",
                    "source_reference",
                )
            ):
                reasons.append("missing_metadata")
            try:
                values[role] = _decimal(fact.value)
                raw_inputs[role] = format(values[role], "f")
            except ValueError:
                reasons.append("invalid_value")
        if facts and all(role in facts for role in TTM_ROLES):
            units = {_unit(facts[role].unit) for role in TTM_ROLES}
            if len(units) != 1 or None in units:
                reasons.append("unit_mismatch")
            if not _periods_match(facts):
                reasons.append("period_mismatch")
        if metric_id == "capex" and any(
            values.get(role, Decimal("0")) < 0 for role in TTM_ROLES
        ):
            reasons.append("capex_sign")
        reasons = list(dict.fromkeys(reasons))
        unit = facts.get("latest_fy").unit if facts.get("latest_fy") else None
        period_start = None
        period_end = None
        if not reasons:
            prior_end = _date(facts["prior_ytd"].period_end)
            current_end = _date(facts["current_ytd"].period_end)
            period_start = (prior_end + timedelta(days=1)).isoformat()
            period_end = current_end.isoformat()
            value = values["latest_fy"] + values["current_ytd"] - values["prior_ytd"]
            return self._base_result(
                metric_id,
                input_evidence_ids=ids,
                raw_inputs=raw_inputs,
                raw_result=format(value, "f"),
                unit=unit,
                period_start=period_start,
                period_end=period_end,
                status="available",
                validation_status="valid",
            ), value
        return self._base_result(
            metric_id,
            input_evidence_ids=ids,
            raw_inputs=raw_inputs,
            unit=unit,
            status="unavailable",
            reasons=reasons,
        ), None

    def _run(
        self,
        company_name: str | None = None,
        ticker: str | None = None,
        metric_inputs: dict[str, dict[str, EdgarFact]] | None = None,
    ) -> TTMResult:
        metric_inputs = metric_inputs or {}
        metric_ids = [
            metric_id for metric_id in SUPPORTED_TTM_METRICS if metric_id in metric_inputs
        ]
        metric_ids.extend(sorted(set(metric_inputs) - set(metric_ids)))
        metrics: list[TTMMetricResult] = []
        values: dict[str, Decimal] = {}
        for metric_id in metric_ids:
            metric, value = self._build_metric(metric_id, metric_inputs[metric_id])
            metrics.append(metric)
            if value is not None:
                values[metric_id] = value

        if "operating_cash_flow" in metric_inputs or "capex" in metric_inputs:
            ocf = next((item for item in metrics if item.metric_id == "operating_cash_flow"), None)
            capex = next((item for item in metrics if item.metric_id == "capex"), None)
            reasons = []
            if ocf is None or capex is None:
                reasons.append("missing_input")
            if ocf and ocf.status != "available":
                reasons.extend(ocf.reasons)
            if capex and capex.status != "available":
                reasons.extend(capex.reasons)
            reasons = list(dict.fromkeys(reasons))
            if not reasons:
                if ocf.unit != capex.unit or ocf.period_start != capex.period_start or ocf.period_end != capex.period_end:
                    reasons.append("period_mismatch")
            if not reasons:
                result = values["operating_cash_flow"] - values["capex"]
                ids = list(dict.fromkeys(ocf.input_evidence_ids + capex.input_evidence_ids))
                metrics.append(
                    self._base_result(
                        "free_cash_flow",
                        input_evidence_ids=ids,
                        raw_inputs={
                            "operating_cash_flow": ocf.raw_result,
                            "capex": capex.raw_result,
                        },
                        raw_result=format(result, "f"),
                        unit=ocf.unit,
                        period_start=ocf.period_start,
                        period_end=ocf.period_end,
                        status="available",
                        validation_status="valid",
                    )
                )
            else:
                metrics.append(
                    self._base_result(
                        "free_cash_flow",
                        status="unavailable",
                        reasons=reasons,
                    )
                )
        warnings = [
            f"{metric.metric_id}:{reason}"
            for metric in metrics
            for reason in metric.reasons
        ]
        return TTMResult(
            status="ok" if metrics and all(metric.status == "available" for metric in metrics) else "partial",
            company_name=company_name,
            ticker=ticker.upper() if ticker else None,
            metrics=metrics,
            warnings=warnings,
        )
