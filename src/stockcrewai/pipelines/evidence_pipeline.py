"""Evidence 边界的确定性适配函数。"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError

from stockcrewai.models.evidence import (
    EvidenceRecord,
    MarketPriceRecord,
    ValidationStatus,
)
from stockcrewai.models.policy import GateResult, PolicyDecision
from stockcrewai.models.profile import (
    IssuerProfile,
    ProfileResult,
)
from stockcrewai.pipelines.metric_registry import (
    POLICY_VERSION,
    evaluate_policy_decisions,
    policy_version_for_profile,
    resolve_metric_policies,
)
from stockcrewai.pipelines.profile_registry import classify_profiles
from stockcrewai.tools.edgar_tool import EdgarError, EdgarResult
from stockcrewai.tools.validation_tool import sync_validation_status
from stockcrewai.validators.analysis_gate import evaluate_analysis_gate


_COMMON_STOCK_FACT_METRIC_IDS = frozenset(
    {
        "common_shares_outstanding",
        "common_stock_shares_outstanding",
        "diluted_shares",
        "diluted_weighted_average_shares",
        "shares_outstanding",
    }
)
_COMMON_STOCK_FACT_XBRL_TAGS = frozenset(
    {
        "commonstocksharesoutstanding",
        "entitycommonstocksharesoutstanding",
        "weightedaveragenumberofdilutedsharesoutstanding",
    }
)


def _calculation_facts(edgar_result: EdgarResult) -> dict[str, Any]:
    """为计算器补充稳定别名，但保留原始 Evidence。"""
    facts: dict[str, Any] = dict(edgar_result.facts)
    if "revenue" in facts and "revenue_current" not in facts:
        facts["revenue_current"] = facts["revenue"]
    if "common_shares_outstanding" in facts and "shares_current" not in facts:
        facts["shares_current"] = facts["common_shares_outstanding"]
    return facts


def _edgar_error(
    code: str,
    message: str,
    company_name: str | None = None,
    ticker: str | None = None,
) -> EdgarResult:
    """构造稳定 reason code 的 EDGAR 错误结果。"""
    return EdgarResult(
        status="error",
        input_company_name=company_name,
        input_ticker=ticker,
        errors=[EdgarError(code=code, message=message)],
    )


def _ttm_unavailable(
    company_name: str | None,
    ticker: str | None,
    reason_code: str,
) -> dict[str, Any]:
    """构造不会伪造数据的 TTM unavailable 结果。"""
    return {
        "status": "unavailable",
        "company_name": company_name,
        "ticker": ticker,
        "metrics": [],
        "warnings": [],
        "reason_code": reason_code,
    }


def validate_ttm_evidence(
    ttm_inputs: Mapping[str, Mapping[str, Any]] | None,
    *,
    company_name: str | None,
    ticker: str | None,
    validation_tool: Any,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """验证 TTM Evidence，并将状态投影回 metric -> role -> fact。"""
    raw_inputs = ttm_inputs if isinstance(ttm_inputs, Mapping) else {}
    flattened: dict[str, Any] = {}
    locations: dict[str, tuple[str, str]] = {}
    for metric_id, by_role in raw_inputs.items():
        if not isinstance(by_role, Mapping):
            continue
        for role, raw_fact in by_role.items():
            metric_key = str(metric_id)
            role_key = str(role)
            fact_key = f"{metric_key}:{role_key}"
            flattened[fact_key] = raw_fact
            locations[fact_key] = (metric_key, role_key)

    fact_keys = list(flattened)
    if not flattened:
        return _json_safe(raw_inputs), {
            "status": "unavailable",
            "validated": False,
            "validated_evidence_ids": [],
            "validated_calculation_ids": [],
            "fact_keys": fact_keys,
            "reason_code": "ttm_evidence_missing",
        }

    try:
        validation_result = validation_tool.run(
            company_name=company_name,
            ticker=ticker,
            facts=flattened,
            calculations=[],
        )
        synced = sync_validation_status(flattened, [], validation_result)
        projected: dict[str, dict[str, Any]] = {}
        for fact_key, raw_fact in synced["facts"].items():
            metric_key, role_key = locations[fact_key]
            projected.setdefault(metric_key, {})[role_key] = raw_fact
        diagnostic = _json_safe(validation_result)
        if not isinstance(diagnostic, dict):
            diagnostic = {
                "status": "unavailable",
                "validated": False,
                "validated_evidence_ids": [],
                "validated_calculation_ids": [],
            }
        diagnostic["fact_keys"] = fact_keys
        diagnostic["fact_count"] = len(flattened)
        return projected, diagnostic
    except Exception as exc:
        return _json_safe(raw_inputs), {
            "status": "unavailable",
            "validated": False,
            "validated_evidence_ids": [],
            "validated_calculation_ids": [],
            "fact_keys": fact_keys,
            "fact_count": len(flattened),
            "issues": [
                {
                    "code": "ttm_validation_error",
                    "severity": "error",
                    "field": "ttm_inputs",
                    "message": f"TTM Evidence 验证失败：{type(exc).__name__}",
                }
            ],
        }


def _json_safe(value: Any) -> Any:
    """递归转换为可由标准 JSON 编码器处理的值。"""
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


def _synchronized_outputs(
    edgar_result: EdgarResult,
    calculation_result: Any,
    validation_result: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """将批量验证状态同步到事实和计算结果。"""
    synced = sync_validation_status(
        edgar_result.facts,
        calculation_result.calculations,
        validation_result,
    )
    edgar_output = _json_safe(edgar_result)
    calculation_output = _json_safe(calculation_result)
    if isinstance(edgar_output, dict):
        edgar_output["facts"] = synced["facts"]
    if isinstance(calculation_output, dict):
        calculation_output["calculations"] = synced["calculations"]
    return edgar_output, calculation_output


def _validated_state(
    edgar_result: EdgarResult,
    calculation_result: Any,
    validation_result: Any,
) -> dict[str, Any]:
    """筛出验证器白名单中的事实、计算和可读取 filing。"""
    validated_evidence_ids = list(validation_result.validated_evidence_ids)
    validated_calculation_ids = list(validation_result.validated_calculation_ids)
    evidence_ids = set(validated_evidence_ids)
    calculation_ids = set(validated_calculation_ids)

    facts: dict[str, Any] = {}
    for fact_id, raw_fact in edgar_result.facts.items():
        payload = _json_safe(raw_fact)
        if isinstance(payload, dict) and payload.get("evidence_id") in evidence_ids:
            facts[fact_id] = payload

    calculations: list[dict[str, Any]] = []
    for raw_calculation in calculation_result.calculations:
        payload = _json_safe(raw_calculation)
        if (
            isinstance(payload, dict)
            and payload.get("calculation_id") in calculation_ids
        ):
            calculations.append(payload)

    filings: list[dict[str, Any]] = []
    for raw_filing in edgar_result.filings:
        payload = _json_safe(raw_filing)
        if (
            isinstance(payload, dict)
            and payload.get("evidence_id")
            and payload.get("form")
            and payload.get("source_reference")
            and payload.get("text")
            and payload.get("text_retrieval_status") == "available"
        ):
            filings.append(payload)

    return {
        "company_name": edgar_result.company_name,
        "ticker": edgar_result.ticker,
        "validated_evidence_ids": validated_evidence_ids,
        "validated_calculation_ids": validated_calculation_ids,
        "validated_filing_ids": [filing["evidence_id"] for filing in filings],
        "facts": facts,
        "calculations": calculations,
        "filings": filings,
    }


def _profile_metadata_from_legacy(
    profile: Mapping[str, Any] | None,
    source_metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """把旧 profile 字段映射为 registry 的显式输入，不猜测缺失字段。"""
    metadata = dict(source_metadata or {})
    legacy = dict(profile or {})
    issuer = legacy.get("issuer_profile", legacy.get("issuer_type"))
    security = legacy.get("security_profile", legacy.get("security_type"))
    reporting = legacy.get("reporting_profile")
    issuer_aliases = {
        "operating_company": "standard_operating",
        "operating": "standard_operating",
    }
    reporting_aliases = {"us_sec": "domestic_us_gaap", "us_gaap": "domestic_us_gaap"}
    if issuer is not None:
        metadata["sec_registrant_profile"] = issuer_aliases.get(str(issuer).lower(), issuer)
    if security is not None:
        metadata["sec_security_profile"] = security
    if reporting is not None:
        metadata["sec_reporting_profile"] = reporting_aliases.get(
            str(reporting).lower(), reporting
        )
    evidence_ids = legacy.get("classification_evidence_ids")
    if isinstance(evidence_ids, Sequence) and not isinstance(
        evidence_ids, (str, bytes, bytearray)
    ):
        metadata["classification_evidence_ids"] = list(evidence_ids)
    return metadata


def _profile_result(
    profile: Mapping[str, Any] | ProfileResult | None,
    source_metadata: Mapping[str, Any] | None,
) -> ProfileResult:
    if isinstance(profile, ProfileResult):
        return profile
    if isinstance(profile, Mapping) and profile:
        try:
            return ProfileResult.model_validate(profile)
        except ValidationError:
            pass
    return classify_profiles(_profile_metadata_from_legacy(profile, source_metadata))


def profile_metadata_from_edgar(edgar_result: Any) -> dict[str, Any]:
    """提取 SEC/filing/security 元数据供 deterministic profile registry 使用。"""
    payload = _json_safe(edgar_result)
    if not isinstance(payload, Mapping):
        return {}
    metadata = {
        key: payload[key]
        for key in (
            "sec_registrant_profile",
            "sec_reporting_profile",
            "sec_security_profile",
            "sic",
            "has_revenue",
            "is_foreign_private_issuer",
            "is_investment_company",
            "security_type",
            "security_class",
            "recent_listing",
            "listing_age_days",
        )
        if key in payload
    }
    filings = payload.get("filings", [])
    if isinstance(filings, list):
        metadata["filing_forms"] = [
            filing.get("form")
            for filing in filings
            if isinstance(filing, Mapping) and filing.get("form")
        ]
        metadata["filing_envelopes"] = [
            {
                key: filing.get(key)
                for key in (
                    "evidence_id",
                    "form",
                    "filed_at",
                    "period_end",
                    "accession_number",
                    "source_reference",
                )
                if filing.get(key) is not None
            }
            for filing in filings
            if isinstance(filing, Mapping)
            and filing.get("form") in {"20-F", "6-K"}
        ]
    taxonomy: list[str] = []
    facts = payload.get("facts", {})
    common_stock_fact_present = False
    if isinstance(facts, Mapping):
        for fact_id, fact in facts.items():
            if not isinstance(fact, Mapping):
                continue
            if isinstance(fact.get("taxonomy"), str):
                taxonomy.append(fact["taxonomy"])
            metric_id = str(fact.get("metric_id") or fact_id).casefold()
            xbrl_tag = str(fact.get("xbrl_tag") or "").casefold().rsplit(":", 1)[-1]
            unit = str(fact.get("unit") or "").casefold()
            if (
                (metric_id in _COMMON_STOCK_FACT_METRIC_IDS
                 or xbrl_tag in _COMMON_STOCK_FACT_XBRL_TAGS)
                and (not unit or unit in {"share", "shares"})
            ):
                common_stock_fact_present = True
    metadata["taxonomy"] = taxonomy
    if common_stock_fact_present:
        metadata.setdefault("security_type", "common_stock")
        metadata.setdefault("security_class", "common_stock")
    evidence_ids = (
        [
            fact.get("evidence_id")
            for fact in facts.values()
            if isinstance(fact, Mapping) and isinstance(fact.get("evidence_id"), str)
        ]
        if isinstance(facts, Mapping)
        else []
    )
    if isinstance(filings, list):
        evidence_ids.extend(
            filing.get("evidence_id")
            for filing in filings
            if isinstance(filing, Mapping) and isinstance(filing.get("evidence_id"), str)
        )
    metadata["classification_evidence_ids"] = [item for item in evidence_ids if item]
    return metadata


def _policy_evidence_records(
    facts: Mapping[str, Any] | None,
    evidence_ids: Sequence[str] = (),
) -> list[Any]:
    records: list[Any] = []
    seen: set[str] = set()
    for raw_fact in (facts or {}).values():
        if not isinstance(raw_fact, Mapping):
            continue
        evidence_id = raw_fact.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id or evidence_id in seen:
            continue
        status = raw_fact.get("validation_status")
        try:
            status = ValidationStatus(status)
        except ValueError:
            status = ValidationStatus.UNVALIDATED
        records.append(SimpleNamespace(evidence_id=evidence_id, validation_status=status))
        seen.add(evidence_id)
    for evidence_id in evidence_ids:
        if isinstance(evidence_id, str) and evidence_id and evidence_id not in seen:
            records.append(
                SimpleNamespace(
                    evidence_id=evidence_id,
                    validation_status=ValidationStatus.VALID,
                )
            )
            seen.add(evidence_id)
    return records


def _policy_calculation_records(
    calculations: Any,
    policies: Sequence[Any],
) -> list[Any]:
    if isinstance(calculations, Mapping):
        calculations = calculations.get("calculations", [])
    if not isinstance(calculations, Sequence) or isinstance(
        calculations, (str, bytes, bytearray)
    ):
        return []
    formula_ids = {policy.metric_id: policy.formula_id for policy in policies}
    records: list[Any] = []
    for raw_calculation in calculations:
        if not isinstance(raw_calculation, Mapping):
            continue
        calculation_id = raw_calculation.get("calculation_id")
        input_evidence_ids = raw_calculation.get("input_evidence_ids", [])
        formula_id = raw_calculation.get("formula_id")
        if (
            not isinstance(calculation_id, str)
            or not calculation_id
            or not isinstance(input_evidence_ids, list)
            or not input_evidence_ids
            or not isinstance(formula_id, str)
        ):
            continue
        formula_id = formula_ids.get(formula_id, formula_id)
        if not formula_id.endswith(":v1"):
            continue
        result = next(
            (
                raw_calculation.get(key)
                for key in ("result", "raw_result", "normalized_result", "display_result")
                if raw_calculation.get(key) not in (None, "")
            ),
            None,
        )
        if result is not None:
            try:
                result = Decimal(str(result))
            except (InvalidOperation, TypeError, ValueError):
                result = None
        try:
            status = ValidationStatus(raw_calculation.get("validation_status", "unvalidated"))
        except ValueError:
            status = ValidationStatus.UNVALIDATED
        records.append(
            SimpleNamespace(
                calculation_id=calculation_id,
                formula_id=formula_id,
                input_evidence_ids=[
                    item for item in input_evidence_ids if isinstance(item, str)
                ],
                result=result,
                validation_status=status,
            )
        )
    return records


def _profile_policy_gate(
    profile: ProfileResult,
    decisions: Sequence[PolicyDecision],
) -> GateResult:
    """调用 WP02 的确定性 Analysis Gate；不从自然语言或 limitation 推断。"""
    return evaluate_analysis_gate(profile, decisions)


def build_profile_policy_context(
    *,
    profile: Mapping[str, Any] | ProfileResult | None = None,
    source_metadata: Mapping[str, Any] | None = None,
    facts: Mapping[str, Any] | None = None,
    calculations: Any = None,
    additional_evidence_ids: Sequence[str] = (),
    additional_calculations: Any = None,
    evidence_records: Sequence[EvidenceRecord] = (),
    market_price_records: Sequence[MarketPriceRecord] = (),
) -> dict[str, Any]:
    """构造 Flow 内唯一 JSON-safe 的 Profile/Policy/Gate 共享上下文。"""
    profile_result = _profile_result(profile, source_metadata)
    policies = resolve_metric_policies(profile_result)

    if profile_result.issuer_profile is not IssuerProfile.STANDARD_OPERATING:
        gate = _profile_policy_gate(profile_result, ())
        return _json_safe(
            {
                "profile": profile_result.model_dump(mode="json"),
                "coverage_level": profile_result.coverage_level.value,
                "profile_registry_version": profile_result.registry_version,
                "policies": [],
                "policy_decisions": [],
                "policy_version": policy_version_for_profile(profile_result),
                "gate": gate.model_dump(mode="json"),
            }
        )








    calculation_values: list[Any] = []
    for value in (calculations, additional_calculations):
        if isinstance(value, Mapping):
            value = value.get("calculations", [])
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            calculation_values.extend(value)
    evidence = _policy_evidence_records(facts, additional_evidence_ids)
    policy_records = _policy_calculation_records(calculation_values, policies)
    decisions = evaluate_policy_decisions(policies, evidence, policy_records)
    gate = _profile_policy_gate(profile_result, decisions)
    return _json_safe(
        {
            "profile": profile_result.model_dump(mode="json"),
            "coverage_level": profile_result.coverage_level.value,
            "profile_registry_version": profile_result.registry_version,
            "policies": [policy.model_dump(mode="json") for policy in policies],
            "policy_decisions": [decision.model_dump(mode="json") for decision in decisions],
            "policy_version": POLICY_VERSION,
            "gate": gate.model_dump(mode="json"),
        }
    )


def _market_price_kwargs(market_price_data: Any) -> dict[str, Any]:
    """把市场价格对象规范化为估值工具的关键字参数。"""
    if market_price_data is None:
        return {}
    if hasattr(market_price_data, "model_dump"):
        market_price_data = market_price_data.model_dump(mode="json")
    if not isinstance(market_price_data, Mapping):
        return {"market_price": market_price_data}
    return {
        "market_price": market_price_data.get("market_price", market_price_data.get("price")),
        "price_timestamp": market_price_data.get(
            "price_timestamp", market_price_data.get("timestamp")
        ),
        "currency": market_price_data.get("currency"),
        "source_reference": market_price_data.get(
            "source_reference", market_price_data.get("source")
        ),
    }


def _historical_prices(market_price_data: Any) -> list[dict[str, Any]]:
    """从市场价格结果中提取普通字典形式的历史价格记录。"""
    if hasattr(market_price_data, "model_dump"):
        market_price_data = market_price_data.model_dump(mode="json")
    if not isinstance(market_price_data, Mapping):
        return []
    prices = market_price_data.get("historical_prices", [])
    return (
        [dict(item) for item in prices if isinstance(item, Mapping)]
        if isinstance(prices, list)
        else []
    )


def _historical_financial_snapshots(edgar_result: EdgarResult) -> list[dict[str, Any]]:
    """从 EDGAR 结果提取普通字典形式的历史财务快照。"""
    snapshots = getattr(edgar_result, "historical_financial_snapshots", [])
    return (
        [dict(item) for item in snapshots if isinstance(item, Mapping)]
        if isinstance(snapshots, list)
        else []
    )


def _with_validation_status(
    result: Any,
    *,
    allowed_evidence_ids: set[str],
    base_valid: bool,
) -> dict[str, Any]:
    """按照统一证据白名单为辅助估值结果补充验证状态。"""
    payload = _json_safe(result)
    if not isinstance(payload, dict):
        return {"status": "unavailable", "validation_status": "unvalidated"}
    input_ids = payload.get("input_evidence_ids", [])
    input_ids_are_valid = isinstance(input_ids, list) and all(
        isinstance(item, str) and item in allowed_evidence_ids for item in input_ids
    )
    payload["validation_status"] = (
        "valid"
        if (
            base_valid
            and payload.get("status") == "ok"
            and bool(input_ids)
            and input_ids_are_valid
        )
        else "unvalidated"
    )
    return payload


__all__ = [
    "_calculation_facts",
    "_edgar_error",
    "_historical_financial_snapshots",
    "_historical_prices",
    "_json_safe",
    "_market_price_kwargs",
    "_profile_policy_gate",
    "_profile_result",
    "_policy_calculation_records",
    "_policy_evidence_records",
    "_synchronized_outputs",
    "_ttm_unavailable",
    "_validated_state",
    "_with_validation_status",
    "build_profile_policy_context",
    "profile_metadata_from_edgar",
    "sync_validation_status",
    "validate_ttm_evidence",
]
