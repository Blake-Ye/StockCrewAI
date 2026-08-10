from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from stockcrewai.models.profile import CoverageLevel
from stockcrewai.models.quant import QuantResearchPacket
from stockcrewai.reporting.context import build_report_context
from stockcrewai.reporting.renderer import (
    build_deterministic_report_draft,
    render_validated_report,
)
from stockcrewai.reporting.validator import validate_report_draft


FACTOR_ARTIFACT_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BACKTEST_ARTIFACT_ID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
EXPECTED_QUANT_REPORT_FRAGMENTS = (
    "AAPL",
    "2/10",
    "88.89%",
    "12.34%",
    "-21.00%",
    "15.00%",
    "180.00%",
    "10 bps",
    "SPY",
    "coverage=partial",
    "survivorship_bias_known=true",
    FACTOR_ARTIFACT_ID,
    BACKTEST_ARTIFACT_ID,
)


@pytest.fixture
def quant_packet() -> QuantResearchPacket:
    return QuantResearchPacket(
        as_of=datetime(2026, 8, 10, tzinfo=timezone.utc),
        universe_id="offline-universe",
        strategy_version="quant-strategy-v1",
        coverage=CoverageLevel.PARTIAL,
        factor_summary={
            "snapshot_count": Decimal("10"),
            "observation_count": Decimal("170"),
        },
        ranking_summary={
            "target_ticker": "AAPL",
            "peer_group": "standard_operating:technology",
            "score": Decimal("0.7000"),
            "rank": Decimal("2"),
            "peer_count": Decimal("10"),
            "industry_percentile": Decimal("0.8889"),
            "target_available_factor_count": Decimal("17"),
            "target_rank_status": "available",
            "target_rank_reason_code": "scored",
        },
        backtest_summary={
            "complete_period_count": Decimal("60"),
            "net_cost_bps": Decimal("10"),
            "strategy_cagr": Decimal("0.1234"),
            "strategy_cagr_status": "available",
            "strategy_cagr_reason_code": "computed",
            "strategy_max_drawdown": Decimal("-0.2100"),
            "strategy_max_drawdown_status": "available",
            "strategy_max_drawdown_reason_code": "computed",
            "average_turnover": Decimal("0.1500"),
            "average_turnover_status": "available",
            "average_turnover_reason_code": "computed",
            "annualized_turnover": Decimal("1.8000"),
            "annualized_turnover_status": "available",
            "annualized_turnover_reason_code": "computed",
        },
        benchmark_summary={
            "spy_cagr": Decimal("0.0800"),
            "spy_max_drawdown": Decimal("-0.1800"),
            "universe_cagr": Decimal("0.1000"),
            "universe_max_drawdown": Decimal("-0.2000"),
        },
        data_quality={
            "factor_snapshot_count": Decimal("10"),
            "factor_observation_count": Decimal("170"),
            "complete_period_count": Decimal("60"),
            "period_count": Decimal("61"),
            "survivorship_bias_known": True,
        },
        limitations=["survivorship_bias_known"],
        artifact_ids=[FACTOR_ARTIFACT_ID, BACKTEST_ARTIFACT_ID],
    )


def _context_inputs() -> dict[str, Any]:
    return {
        "company": {"name": "Apple Inc.", "ticker": "AAPL"},
        "validated_claims": [
            {
                "claim_id": "claim_quality",
                "category": "financial_quality",
                "statement": "公司质量来自已验证证据。",
                "evidence_ids": ["evidence_quality"],
                "calculation_ids": ["calculation_quality"],
                "confidence": "high",
            }
        ],
        "deterministic_verdict": {
            "status": "ready",
            "overall_rating": "reasonable",
            "risk_level": "medium",
            "triggered_rules": [],
        },
    }


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _contains_float(value: Any) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_contains_float(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_float(child) for child in value)
    return False


def _quant_section(report: str) -> str:
    assert "## 量化旁证" in report
    return report.split("## 量化旁证", 1)[1].split("\n## ", 1)[0]


def test_quant_packet_context_matches_json_dump_and_keeps_decimal_strings(
    quant_packet: QuantResearchPacket,
) -> None:
    context = build_report_context(**_context_inputs(), quant_packet=quant_packet)

    assert context["quant"]["status"] == "available"
    assert context["quant"]["reason_code"] == "quant_packet_validated"
    assert context["quant"]["packet"] == quant_packet.model_dump(mode="json")
    assert not _contains_float(context["quant"])
    assert context["quant"]["packet"]["ranking_summary"]["score"] == "0.7000"
    json.dumps(context["quant"], ensure_ascii=False, allow_nan=False)


def test_quant_packet_rendering_uses_fixture_literals(
    quant_packet: QuantResearchPacket,
) -> None:
    context = build_report_context(**_context_inputs(), quant_packet=quant_packet)
    report = render_validated_report(
        report_context=context,
        report_draft=build_deterministic_report_draft(),
    )

    quant_section = _quant_section(report)
    for fragment in EXPECTED_QUANT_REPORT_FRAGMENTS:
        assert fragment in quant_section


@pytest.mark.parametrize(
    "forged_narrative",
    ("量化旁证为99%。", "伪造 strategy CAGR 为99.99%。"),
)
def test_report_draft_numbers_are_rejected_before_quant_rendering(
    forged_narrative: str,
) -> None:
    payload = build_deterministic_report_draft().model_dump()
    payload["execution_summary"] = forged_narrative

    passed, error = validate_report_draft(
        SimpleNamespace(raw=json.dumps(payload, ensure_ascii=False))
    )

    assert passed is False
    assert "report_draft_forbidden_number" in str(error)


def test_quant_sidecar_does_not_change_claims_metrics_verdict_or_verdict_hash(
    quant_packet: QuantResearchPacket,
) -> None:
    without_quant = build_report_context(**_context_inputs())
    with_quant = build_report_context(**_context_inputs(), quant_packet=quant_packet)

    assert with_quant["claims"] == without_quant["claims"]
    assert with_quant["metrics"] == without_quant["metrics"]
    assert all(
        metric.get("section") != "quant"
        and not str(metric.get("metric_id", "")).startswith("quant")
        for metric in with_quant["metrics"]
    )
    assert "quant" not in with_quant["verdict"]
    assert _canonical_json_sha256(with_quant["verdict"]) == _canonical_json_sha256(
        without_quant["verdict"]
    )


def test_explicit_missing_quant_packet_is_unavailable_without_fake_values() -> None:
    context = build_report_context(**_context_inputs(), quant_packet=None)

    assert context["quant"] == {
        "status": "unavailable",
        "reason_code": "quant_packet_missing",
        "packet": None,
    }
    report = render_validated_report(
        report_context=context,
        report_draft=build_deterministic_report_draft(),
    )
    quant_section = _quant_section(report)

    assert "不可用" in quant_section
    assert "quant_packet_missing" in quant_section
    assert "0%" not in quant_section
    assert "0.00%" not in quant_section
    assert FACTOR_ARTIFACT_ID not in quant_section
    assert BACKTEST_ARTIFACT_ID not in quant_section
    assert "artifact_ids=[]" not in quant_section
    assert "artifact_ids：[]" not in quant_section


def test_partial_quant_packet_keeps_unavailable_cagr_state_without_zero_fill(
    quant_packet: QuantResearchPacket,
) -> None:
    partial_packet = quant_packet.model_copy(deep=True)
    partial_packet.backtest_summary.update(
        {
            "strategy_cagr": None,
            "strategy_cagr_status": "unavailable",
            "strategy_cagr_reason_code": "missing_history",
        }
    )
    context = build_report_context(**_context_inputs(), quant_packet=partial_packet)
    report = render_validated_report(
        report_context=context,
        report_draft=build_deterministic_report_draft(),
    )
    quant_section = _quant_section(report)

    for fragment in (
        "strategy_cagr",
        "unavailable",
        "missing_history",
        "-21.00%",
        "15.00%",
        "180.00%",
        "10 bps",
    ):
        assert fragment in quant_section
    assert "0%" not in quant_section
    assert "0.00%" not in quant_section


def test_omitting_quant_packet_preserves_legacy_context_and_report() -> None:
    context = build_report_context(**_context_inputs())

    assert "quant" not in context
    report = render_validated_report(
        report_context=context,
        report_draft=build_deterministic_report_draft(),
    )
    assert "## 量化旁证" not in report
