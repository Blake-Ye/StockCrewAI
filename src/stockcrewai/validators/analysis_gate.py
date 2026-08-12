from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from stockcrewai.models.policy import GateResult, PolicyDecision
from stockcrewai.models.profile import (
    CoverageLevel,
    IssuerProfile,
    ProfileResult,
    ReportingProfile,
    SecurityProfile,
)
from stockcrewai.pipelines.metric_registry import policy_version_for_profile


def _reason_codes(fixed_code: str, decisions: Sequence[PolicyDecision]) -> list[str]:
    codes = [fixed_code]
    for decision in decisions:
        if decision.reason_code not in codes:
            codes.append(decision.reason_code)
    return codes


def _is_sic_classified_nonordinary(profile: ProfileResult) -> bool:
    """判断是否由 SIC 明确识别为当前普通公司主线之外的类别。

    ``ProfileRegistry`` 已经把 SIC 映射成银行、保险、REIT、公用事业或
    商品生产商等 Profile。只有带有 ``profile_classified_from_sic`` 证据时
    才在这里触发范围阻断，避免影响旧的显式 Profile 单元测试和适配器。
    """
    return (
        profile.issuer_profile is not IssuerProfile.STANDARD_OPERATING
        and "profile_classified_from_sic" in profile.reason_codes
    )


def evaluate_analysis_gate(
    profile: ProfileResult,
    decisions: Sequence[PolicyDecision],
) -> GateResult:
    """根据类别范围和指标决策返回确定性的 Analysis Gate。

    SIC 类别阻断优先于指标证据阻断。这样银行不会先进入 NIM/ROA 等
    指标 Gate 再显示 ``missing_required_evidence``，而是直接说明当前
    主线不支持该行业类别。
    """
    blocking_decisions = [
        decision
        for decision in decisions
        if decision.status in {"unavailable", "invalid"} and decision.blocking
    ]
    non_blocking_decisions = [
        decision
        for decision in decisions
        if not decision.blocking or decision.status == "not_applicable"
    ]

    if _is_sic_classified_nonordinary(profile):
        return GateResult(
            status="unsupported",
            coverage_level=profile.coverage_level,
            # 范围 Gate 已经截断主线，不把下游 Profile 指标缺失冒充成阻断原因。
            blocking_decisions=[],
            non_blocking_decisions=[],
            reason_codes=["unsupported_category_sic"],
            policy_version=policy_version_for_profile(profile),
        )

    status: Literal["ready", "blocked", "evidence_only", "unsupported"]
    if profile.coverage_level is CoverageLevel.UNSUPPORTED_SECURITY:
        status = "unsupported"
        fixed_reason_code = "unsupported_security"
    elif (
        profile.reporting_profile is ReportingProfile.FOREIGN_PRIVATE_ISSUER_IFRS
        and profile.issuer_profile is IssuerProfile.HOLDING_COMPANY
    ):
        status = "evidence_only"
        fixed_reason_code = "foreign_profile_evidence_only"
    elif (
        profile.reporting_profile is ReportingProfile.FOREIGN_PRIVATE_ISSUER_IFRS
        and (
            profile.issuer_profile is IssuerProfile.UNKNOWN
            or profile.security_profile is SecurityProfile.UNKNOWN
        )
    ):
        status = "evidence_only"
        fixed_reason_code = "foreign_profile_incomplete"
    elif (
        profile.security_profile in {SecurityProfile.ADR, SecurityProfile.SPAC}
        and profile.reporting_profile is not ReportingProfile.FOREIGN_PRIVATE_ISSUER_IFRS
    ):
        status = "evidence_only"
        fixed_reason_code = "security_profile_policy_unavailable"
    elif profile.coverage_level is CoverageLevel.EVIDENCE_ONLY:
        status = "evidence_only"
        fixed_reason_code = "evidence_only_coverage"
    elif blocking_decisions:
        status = "blocked"
        fixed_reason_code = "analysis_blocked"
    else:
        status = "ready"
        fixed_reason_code = "analysis_ready"

    return GateResult(
        status=status,
        coverage_level=profile.coverage_level,
        blocking_decisions=blocking_decisions,
        non_blocking_decisions=non_blocking_decisions,
        reason_codes=_reason_codes(fixed_reason_code, decisions),
        policy_version=policy_version_for_profile(profile),
    )


__all__ = ["evaluate_analysis_gate"]
