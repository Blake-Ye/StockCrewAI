"""估值阶段的确定性输入、policy 和 Claim 构建函数。"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from stockcrewai.models.evidence import ClaimRecord
from stockcrewai.tools.historical_valuation_tool import (
    HISTORICAL_VALUATION_CALCULATION_ID,
)
from stockcrewai.tools.reverse_dcf_tool import REVERSE_DCF_CALCULATION_ID
from stockcrewai.tools.valuation_tool import VALUATION_FORMULAS
from stockcrewai.tools.verdict_tool import DeterministicVerdictTool


_CURRENT_VALUATION_CALCULATION_IDS = frozenset(
    f"calc_{formula_id}" for formula_id in VALUATION_FORMULAS
)
_VALUATION_CALCULATION_REGISTRY = frozenset(
    {
        *_CURRENT_VALUATION_CALCULATION_IDS,
        HISTORICAL_VALUATION_CALCULATION_ID,
        REVERSE_DCF_CALCULATION_ID,
    }
)


def _json_safe(value: Any) -> Any:
    """递归把模型、日期、Decimal 和容器转换为 JSON-safe 值。"""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (date, datetime, Decimal)):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return str(value)
    return value


def _valuation_facts(validated_state: dict[str, Any]) -> dict[str, Any]:
    """补充估值工具所需的已验证自由现金流事实。"""
    facts = dict(validated_state.get("facts", {}))
    for legacy_key in ("diluted_eps", "earnings_per_share_diluted", "current_fcf"):
        facts.pop(legacy_key, None)

    ttm_payload = validated_state.get("ttm", {})
    ttm_metrics = (
        ttm_payload.get("metrics", [])
        if isinstance(ttm_payload, Mapping)
        else []
    )
    if isinstance(ttm_metrics, Mapping):
        ttm_metrics = list(ttm_metrics.values())
    if isinstance(ttm_metrics, list):
        for metric in ttm_metrics:
            if not isinstance(metric, Mapping):
                continue
            metric_id = metric.get("metric_id")
            if metric_id not in {"diluted_eps", "free_cash_flow"}:
                continue
            if metric.get("status") != "available":
                continue
            payload = {
                "raw_result": metric.get("raw_result"),
                "unit": metric.get("unit"),
                "period_basis": metric.get("period_basis") or "TTM",
                "validation_status": metric.get("validation_status"),
                "input_evidence_ids": metric.get("input_evidence_ids", []),
            }
            if metric_id == "diluted_eps":
                facts["diluted_eps"] = payload
            else:
                facts["current_fcf"] = payload

    for calculation in validated_state.get("calculations", []):
        if (
            calculation.get("formula_id") == "free_cash_flow"
            and calculation.get("raw_result") is not None
            and "current_fcf" not in facts
        ):
            fcf_fact = {
                "raw_result": calculation["raw_result"],
                "evidence_ids": calculation.get("input_evidence_ids", []),
                "period_basis": calculation.get("period_basis"),
                "validation_status": calculation.get("validation_status"),
            }
            input_evidence_ids = set(calculation.get("input_evidence_ids", []))
            source_units = {
                fact.get("unit")
                for fact in facts.values()
                if isinstance(fact, Mapping)
                and fact.get("evidence_id") in input_evidence_ids
                and fact.get("unit")
            }
            if len(source_units) == 1:
                fcf_fact["unit"] = next(iter(source_units))
            if (
                fcf_fact.get("period_basis") == "TTM"
                and fcf_fact.get("validation_status") == "valid"
            ):
                facts["current_fcf"] = fcf_fact
    return facts


def _valuation_analysis_input(
    state: dict[str, Any],
    valuation: dict[str, Any],
    historical_valuation: dict[str, Any],
    reverse_dcf: dict[str, Any],
    trusted_evidence_ids: set[str] | None = None,
) -> dict[str, Any]:
    """构造确定性估值 Claim 构建器使用的已验证估值输入包。"""
    state_evidence_ids = {
        item
        for item in state.get("validated_evidence_ids", [])
        if isinstance(item, str) and item
    }
    if trusted_evidence_ids is None:
        trusted_ids = state_evidence_ids
    elif isinstance(trusted_evidence_ids, (set, list, tuple)):
        trusted_ids = {
            item for item in trusted_evidence_ids if isinstance(item, str) and item
        }
    else:
        trusted_ids = set()

    def referenced_evidence_ids(payload: Any) -> set[str]:
        if not isinstance(payload, Mapping):
            return set()
        referenced: set[str] = set()
        for key in (
            "market_price_evidence_id",
            "price_evidence_id",
            "input_evidence_ids",
            "evidence_ids",
        ):
            values = payload.get(key)
            if isinstance(values, str):
                values = [values]
            if isinstance(values, list):
                referenced.update(
                    item for item in values if isinstance(item, str) and item
                )
        calculations = payload.get("calculations", [])
        if isinstance(calculations, list):
            for calculation in calculations:
                if not isinstance(calculation, Mapping):
                    continue
                values = calculation.get("input_evidence_ids", [])
                if isinstance(values, list):
                    referenced.update(
                        item for item in values if isinstance(item, str) and item
                    )
        return referenced

    referenced_ids = set().union(
        *(
            referenced_evidence_ids(payload)
            for payload in (valuation, historical_valuation, reverse_dcf)
        )
    )
    evidence_ids = trusted_ids & referenced_ids
    calculation_ids = {
        item
        for item in state.get("validated_calculation_ids", [])
        if isinstance(item, str) and item
    }
    calculation_ids.update(_VALUATION_CALCULATION_REGISTRY)
    return {
        "company_name": state.get("company_name"),
        "ticker": state.get("ticker"),
        "facts": _json_safe(_valuation_facts(state)),
        "calculations": _json_safe(state.get("calculations", [])),
        "valuation_result": _json_safe(valuation),
        "historical_valuation_result": _json_safe(historical_valuation),
        "reverse_dcf_result": _json_safe(reverse_dcf),
        "validated_evidence_ids": sorted(evidence_ids),
        "validated_calculation_ids": sorted(calculation_ids),
        "policy_context": _json_safe(state.get("policy_context", {})),
    }


def build_deterministic_valuation_claims(
    valuation_input: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """从已验证估值 payload 按固定域顺序生成 Claims。"""
    if not isinstance(valuation_input, Mapping):
        return []

    validated_evidence_ids = valuation_input.get("validated_evidence_ids")
    validated_calculation_ids = valuation_input.get("validated_calculation_ids")
    if not (
        isinstance(validated_evidence_ids, list)
        and isinstance(validated_calculation_ids, list)
        and all(
            isinstance(identifier, str) and identifier.strip()
            for identifier in (*validated_evidence_ids, *validated_calculation_ids)
        )
    ):
        return []
    evidence_allowlist = set(validated_evidence_ids)
    calculation_allowlist = set(validated_calculation_ids)
    policy_context = valuation_input.get("policy_context")
    policy_decisions = (
        policy_context.get("policy_decisions")
        if isinstance(policy_context, Mapping)
        else None
    )
    if not isinstance(policy_decisions, list):
        policy_decisions = []
    not_applicable_metrics = {
        decision.get("metric_id")
        for decision in policy_decisions
        if isinstance(decision, Mapping)
        and decision.get("status") == "not_applicable"
        and isinstance(decision.get("metric_id"), str)
    }

    def evidence_ids_from(value: Any) -> list[str] | None:
        if not isinstance(value, list) or not value:
            return None
        result: list[str] = []
        for identifier in value:
            if (
                not isinstance(identifier, str)
                or not identifier.strip()
                or identifier not in evidence_allowlist
            ):
                return None
            if identifier not in result:
                result.append(identifier)
        return result or None

    claim_specs: list[tuple[str, str, str, list[str], list[str]]] = []
    current_result = valuation_input.get("valuation_result")
    current_calculation_ids: list[str] = []
    current_evidence_ids: list[str] = []
    current_calculations = (
        current_result.get("calculations")
        if isinstance(current_result, Mapping)
        else None
    )
    if (
        isinstance(current_result, Mapping)
        and current_result.get("readiness") == "ready"
        and current_result.get("validation_status") == "valid"
        and isinstance(current_calculations, list)
    ):
        for calculation in current_calculations:
            if not isinstance(calculation, Mapping):
                continue
            if calculation.get("formula_id") in not_applicable_metrics:
                continue
            calculation_id = calculation.get("calculation_id")
            input_evidence_ids = evidence_ids_from(calculation.get("input_evidence_ids"))
            if not (
                calculation.get("status") == "available"
                and calculation.get("validation_status") == "valid"
                and isinstance(calculation_id, str)
                and calculation_id.strip()
                and calculation_id in calculation_allowlist
                and input_evidence_ids
            ):
                continue
            if calculation_id not in current_calculation_ids:
                current_calculation_ids.append(calculation_id)
            for evidence_id in input_evidence_ids:
                if evidence_id not in current_evidence_ids:
                    current_evidence_ids.append(evidence_id)

    if current_calculation_ids and current_evidence_ids:
        claim_specs.append(
            (
                "claim_current_valuation",
                "current_valuation",
                "当前估值结果由已验证计算及输入证据支持。",
                current_evidence_ids,
                current_calculation_ids,
            )
        )

    def auxiliary_ids(result: Any) -> tuple[list[str], list[str]] | None:
        if not isinstance(result, Mapping):
            return None
        calculation_id = result.get("calculation_id")
        evidence_ids = evidence_ids_from(result.get("input_evidence_ids"))
        if not (
            isinstance(calculation_id, str)
            and calculation_id.strip()
            and calculation_id in calculation_allowlist
            and evidence_ids
        ):
            return None
        return [calculation_id], evidence_ids

    historical_result = valuation_input.get("historical_valuation_result")
    if (
        isinstance(historical_result, Mapping)
        and historical_result.get("status") == "ok"
        and historical_result.get("validation_status") == "valid"
        and "historical_valuation" not in not_applicable_metrics
    ):
        historical_ids = auxiliary_ids(historical_result)
        if historical_ids is not None:
            claim_specs.append(
                (
                    "claim_historical_valuation",
                    "historical_valuation",
                    "历史估值结果由已验证计算及输入证据支持。",
                    historical_ids[1],
                    historical_ids[0],
                )
            )

    def reason_codes(result: Mapping[str, Any]) -> set[str]:
        values: list[str] = []
        for value in (result.get("reason_code"), result.get("reasons")):
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                values.extend(
                    item.strip()
                    for item in value
                    if isinstance(item, str) and item.strip()
                )
        return set(values)

    reverse_dcf_result = valuation_input.get("reverse_dcf_result")
    reverse_dcf_not_applicable_reasons = {
        "invalid_fcf",
        "ttm_fcf_required",
        "policy_not_applicable",
    }
    if (
        isinstance(reverse_dcf_result, Mapping)
        and reverse_dcf_result.get("status") == "ok"
        and reverse_dcf_result.get("validation_status") == "valid"
        and "reverse_dcf" not in not_applicable_metrics
        and not (reason_codes(reverse_dcf_result) & reverse_dcf_not_applicable_reasons)
    ):
        reverse_dcf_ids = auxiliary_ids(reverse_dcf_result)
        if reverse_dcf_ids is not None:
            claim_specs.append(
                (
                    "claim_reverse_dcf",
                    "reverse_dcf",
                    "反向 DCF 结果由已验证计算及输入证据支持。",
                    reverse_dcf_ids[1],
                    reverse_dcf_ids[0],
                )
            )
    return [
        ClaimRecord(
            claim_id=claim_id,
            category=category,
            statement=statement,
            evidence_ids=evidence_ids,
            calculation_ids=calculation_ids,
            confidence=1.0,
        ).model_dump(mode="json")
        for claim_id, category, statement, evidence_ids, calculation_ids in claim_specs
    ]


_REVERSE_DCF_APPLICABILITY_REASONS = frozenset(
    {
        "invalid_fcf",
        "negative_fcf",
        "negative_eps",
        "policy_not_applicable",
        "ttm_fcf_required",
    }
)
_REVERSE_DCF_POLICY_FIELDS = (
    "issuer_type",
    "company_type",
    "industry",
    "industry_name",
    "sector",
)
_REVERSE_DCF_NON_APPLICABLE_GROUPS = {
    "bank": {"bank", "banking", "commercialbank", "financialinstitution", "银行"},
    "reit": {"reit", "realestateinvestmenttrust", "房地产投资信托"},
}


def _reverse_dcf_reason_codes(reverse_dcf: Mapping[str, Any]) -> list[str]:
    """读取反向 DCF 工具的机器原因码。"""
    values: list[str] = []
    for value in (reverse_dcf.get("reason_code"), reverse_dcf.get("reasons", [])):
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            values.extend(
                item.strip() for item in value if isinstance(item, str) and item.strip()
            )
    return list(dict.fromkeys(values))


def _policy_token(value: Any) -> str:
    return re.sub(r"[\s_\-/]+", "", value.strip().lower()) if isinstance(value, str) else ""


def _numeric_policy_value(payload: Any, keys: tuple[str, ...]) -> Decimal | None:
    if not isinstance(payload, Mapping):
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, Mapping):
            if value.get("validation_status") not in (None, "valid"):
                continue
            value = next(
                (
                    value.get(name)
                    for name in ("value", "raw_result", "numeric_value")
                    if value.get(name) is not None
                ),
                None,
            )
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
        if parsed.is_finite():
            return parsed
    return None


def _reverse_dcf_policy_reason(
    state: Mapping[str, Any], reverse_dcf: Mapping[str, Any]
) -> str | None:
    """只从结构化 policy、issuer/industry 和已验证数值判断不适用。"""
    for key in ("reverse_dcf_applicability", "reverse_dcf_policy"):
        policy = state.get(key)
        if isinstance(policy, Mapping):
            status = _policy_token(policy.get("status") or policy.get("applicability"))
            if status == "notapplicable" or policy.get("applicable") is False:
                return str(policy.get("reason_code") or "policy_not_applicable")
        elif _policy_token(policy) == "notapplicable":
            return "policy_not_applicable"

    for field in ("is_bank", "is_financial_institution", "is_reit"):
        if state.get(field) is True:
            return f"{field}_policy"
    for field in _REVERSE_DCF_POLICY_FIELDS:
        token = _policy_token(state.get(field))
        for policy_name, aliases in _REVERSE_DCF_NON_APPLICABLE_GROUPS.items():
            if token in aliases:
                return f"{field}_{policy_name}"

    facts = state.get("facts")
    fcf = _numeric_policy_value(facts, ("current_fcf", "free_cash_flow", "fcf"))
    fcf = fcf if fcf is not None else _numeric_policy_value(reverse_dcf, ("base_fcf",))
    if fcf is not None and fcf <= 0:
        return "negative_fcf"
    eps = _numeric_policy_value(
        facts, ("diluted_eps", "earnings_per_share_diluted", "ttm_eps")
    )
    return "negative_eps" if eps is not None and eps <= 0 else None


def _current_valuation_gate(valuation: Mapping[str, Any]) -> dict[str, Any]:
    """接受完整估值，或至少一个带有效 Evidence 的确定性指标。"""
    fully_ready = (
        valuation.get("readiness") == "ready"
        and valuation.get("validation_status") == "valid"
    )
    audited_metrics: list[str] = []
    for calculation in valuation.get("calculations", []):
        if not isinstance(calculation, Mapping):
            continue
        formula_id = calculation.get("formula_id")
        evidence_ids = calculation.get("input_evidence_ids")
        result = next(
            (
                calculation.get(key)
                for key in ("raw_result", "normalized_result", "display_result")
                if calculation.get(key) not in (None, "")
            ),
            None,
        )
        if (
            formula_id in VALUATION_FORMULAS
            and calculation.get("calculation_id") == f"calc_{formula_id}"
            and calculation.get("status") == "available"
            and calculation.get("validation_status") == "valid"
            and isinstance(evidence_ids, list)
            and evidence_ids
            and all(isinstance(item, str) and item.strip() for item in evidence_ids)
            and result is not None
        ):
            audited_metrics.append(formula_id)
    status = "ready" if fully_ready else "partial" if audited_metrics else "required"
    return {"status": status, "audited_metrics": audited_metrics}


def _deterministic_verdict(
    *,
    validation_status: str = "unavailable",
    valuation: Mapping[str, Any] | None = None,
    historical_valuation: Mapping[str, Any] | None = None,
    reverse_dcf: Mapping[str, Any] | None = None,
    risk_input: Mapping[str, Any] | None = None,
    policy_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """调用确定性 Verdict 工具生成 JSON-safe 决策状态。"""
    result = DeterministicVerdictTool().run(
        validation_status=validation_status,
        valuation=dict(valuation or {}),
        historical_valuation=dict(historical_valuation or {}),
        reverse_dcf=dict(reverse_dcf or {}),
        risk_input=dict(risk_input or {}),
        policy_context=dict(policy_context or {}),
    )
    return _json_safe(result)


def _reverse_dcf_inputs(
    state: Mapping[str, Any], valuation: Mapping[str, Any]
) -> dict[str, Any]:
    """构造反向 DCF 工具需要的价格、自由现金流和股数输入。"""
    valuation_facts = _valuation_facts(dict(state))
    price = valuation.get("market_price")
    price_evidence_id = valuation.get("market_price_evidence_id")
    market_price = (
        {"value": price, "evidence_id": price_evidence_id}
        if price is not None and price_evidence_id
        else None
    )
    fcf = valuation_facts.get("current_fcf")
    shares = next(
        (
            valuation_facts.get(fact_id)
            for fact_id in ("common_shares_outstanding", "shares_current")
            if valuation_facts.get(fact_id) is not None
        ),
        None,
    )
    return {
        "market_price": market_price,
        "fcf": fcf,
        "shares_outstanding": shares,
    }


__all__ = [
    "_current_valuation_gate",
    "_deterministic_verdict",
    "_json_safe",
    "_numeric_policy_value",
    "_policy_token",
    "_reverse_dcf_inputs",
    "_reverse_dcf_policy_reason",
    "_reverse_dcf_reason_codes",
    "_valuation_analysis_input",
    "_valuation_facts",
    "build_deterministic_valuation_claims",
]
