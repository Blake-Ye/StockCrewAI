from __future__ import annotations

import base64
import hashlib
from contextlib import ExitStack
from io import BytesIO
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from PIL import Image
import pytest

from stockcrewai.models.profile import CoverageLevel
from stockcrewai.models.quant import QuantResearchPacket
from stockcrewai.reporting.context import build_report_context
from stockcrewai.reporting.renderer import (
    build_deterministic_report_draft,
    render_validated_report,
)
from stockcrewai.reporting.validator import validate_report_draft
from tests.test_crew_configuration import (
    VALID_REPORT_DRAFT,
    RecordingCrew,
    _valid_analysis_outputs,
)
from tests.test_main_flow import _flow_dependencies, _offline_flow_patches, _run_flow


FACTOR_ARTIFACT_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BACKTEST_ARTIFACT_ID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
RANKING_EVIDENCE_ID = "evidence-ranking"
RANKING_CALCULATION_ID = "calculation-ranking"
_FLOW_UNSET = object()
_FLOW_VERDICT = {
    "status": "ready",
    "overall_rating": "reasonable",
    "risk_level": "medium",
    "triggered_rules": [],
}
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


def _quant_field_provenance() -> dict[str, dict[str, list[str]]]:
    field_provenance = {
        field_path: {"artifact_ids": [FACTOR_ARTIFACT_ID]}
        for field_path in (
            "ranking_summary.rank",
            "ranking_summary.peer_count",
            "ranking_summary.industry_percentile",
            "ranking_summary.score",
        )
    }
    field_provenance.update(
        {
            field_path: {"artifact_ids": [BACKTEST_ARTIFACT_ID]}
            for field_path in (
                "backtest_summary.strategy_cagr",
                "backtest_summary.strategy_max_drawdown",
                "backtest_summary.average_turnover",
                "backtest_summary.annualized_turnover",
                "backtest_summary.net_cost_bps",
                "benchmark_summary.spy_cagr",
                "benchmark_summary.spy_max_drawdown",
                "benchmark_summary.universe_cagr",
                "benchmark_summary.universe_max_drawdown",
                "data_quality.complete_period_count",
                "data_quality.period_count",
            )
        }
    )
    field_provenance["ranking_summary.rank"].update(
        {
            "evidence_ids": [RANKING_EVIDENCE_ID],
            "calculation_ids": [RANKING_CALCULATION_ID],
        }
    )
    return field_provenance


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
        field_provenance=_quant_field_provenance(),
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


def _new_flow(*, quant_packet: Any = _FLOW_UNSET):
    from stockcrewai.flow import ResearchFlow

    parser_result, dependencies = _flow_dependencies()
    flow_kwargs = {
        **dependencies,
        "analysis_crew": RecordingCrew(task_raws=_valid_analysis_outputs()),
        "report_crew": RecordingCrew(VALID_REPORT_DRAFT),
    }
    if quant_packet is not _FLOW_UNSET:
        flow_kwargs["quant_packet"] = quant_packet
    return parser_result, ResearchFlow(**flow_kwargs)


def _run_quant_flow(*, quant_packet: Any = _FLOW_UNSET):
    import stockcrewai.pipeline_support as pipeline_support
    import stockcrewai.flow as flow_module

    parser_result, flow = _new_flow(quant_packet=quant_packet)
    captured_context_kwargs: dict[str, Any] = {}
    verdict_call = None
    original_build_report_context = flow_module.build_report_context

    def capture_report_context(**kwargs: Any) -> dict[str, Any]:
        captured_context_kwargs.update(kwargs)
        return original_build_report_context(**kwargs)

    with ExitStack() as stack:
        stack.enter_context(_offline_flow_patches(parser_result))
        verdict_call = stack.enter_context(
            patch.object(
                pipeline_support,
                "_deterministic_verdict",
                return_value=_FLOW_VERDICT,
            )
        )
        stack.enter_context(
            patch.object(
                flow_module,
                "build_report_context",
                side_effect=capture_report_context,
            )
        )
        result = _run_flow(flow)

    assert verdict_call is not None
    return result, flow, captured_context_kwargs, verdict_call.call_args.kwargs


def _nested_mapping_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            nested
            for child in value.values()
            for nested in _nested_mapping_keys(child)
        }
    if isinstance(value, list):
        return {nested for child in value for nested in _nested_mapping_keys(child)}
    return set()


def _png_uris(report: str) -> list[str]:
    return re.findall(r"data:image/png;base64,[A-Za-z0-9+/=]+", report)


def _png_files(root: Path) -> set[Path]:
    return {path.resolve() for path in root.rglob("*.png")}


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


def test_quant_packet_rendering_includes_field_level_provenance(
    quant_packet: QuantResearchPacket,
) -> None:
    context = build_report_context(**_context_inputs(), quant_packet=quant_packet)
    report = render_validated_report(
        report_context=context,
        report_draft=build_deterministic_report_draft(),
    )

    quant_section = _quant_section(report)
    assert "字段级追溯" in quant_section
    for field_path in quant_packet.field_provenance:
        assert field_path in quant_section
    assert FACTOR_ARTIFACT_ID in quant_section
    assert BACKTEST_ARTIFACT_ID in quant_section
    assert RANKING_EVIDENCE_ID in quant_section
    assert RANKING_CALCULATION_ID in quant_section


@pytest.mark.parametrize("provenance_mode", ("missing", "empty"))
def test_missing_quant_field_provenance_is_unavailable_without_numbers(
    quant_packet: QuantResearchPacket,
    provenance_mode: str,
) -> None:
    packet_payload = quant_packet.model_dump(mode="python")
    if provenance_mode == "missing":
        packet_payload.pop("field_provenance")
    else:
        packet_payload["field_provenance"] = {}

    context = build_report_context(**_context_inputs(), quant_packet=packet_payload)
    assert context["quant"] == {
        "status": "unavailable",
        "reason_code": "quant_field_provenance_missing",
        "packet": None,
    }

    report = render_validated_report(
        report_context=context,
        report_draft=build_deterministic_report_draft(),
    )
    quant_section = _quant_section(report)
    assert "quant_field_provenance_missing" in quant_section
    for fragment in ("2/10", "88.89%", "12.34%", "-21.00%", "10 bps"):
        assert fragment not in quant_section
    assert FACTOR_ARTIFACT_ID not in quant_section
    assert BACKTEST_ARTIFACT_ID not in quant_section


def test_empty_quant_field_provenance_artifact_ids_are_unavailable(
    quant_packet: QuantResearchPacket,
) -> None:
    packet_payload = quant_packet.model_dump(mode="python")
    packet_payload["field_provenance"]["ranking_summary.rank"]["artifact_ids"] = []

    context = build_report_context(**_context_inputs(), quant_packet=packet_payload)
    assert context["quant"] == {
        "status": "unavailable",
        "reason_code": "quant_field_provenance_missing",
        "packet": None,
    }
    report = render_validated_report(
        report_context=context,
        report_draft=build_deterministic_report_draft(),
    )
    quant_section = _quant_section(report)
    assert "2/10" not in quant_section
    assert FACTOR_ARTIFACT_ID not in quant_section


def test_quant_packet_rendering_embeds_exactly_three_quant_png_data_uris(
    quant_packet: QuantResearchPacket,
) -> None:
    context = build_report_context(**_context_inputs(), quant_packet=quant_packet)
    report = render_validated_report(
        report_context=context,
        report_draft=build_deterministic_report_draft(),
    )

    quant_section = _quant_section(report)
    assert quant_section.count("data:image/png;base64,") == 3


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


@pytest.mark.parametrize("quant_mode", ("explicit_missing", "omitted"))
def test_missing_or_omitted_quant_has_no_quant_png_data_uris(
    quant_mode: str,
) -> None:
    if quant_mode == "explicit_missing":
        context = build_report_context(**_context_inputs(), quant_packet=None)
    else:
        context = build_report_context(**_context_inputs())

    report = render_validated_report(
        report_context=context,
        report_draft=build_deterministic_report_draft(),
    )

    if quant_mode == "explicit_missing":
        assert "data:image/png;base64," not in _quant_section(report)
    else:
        assert "## 量化旁证" not in report


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
    strategy_cagr_line = next(
        line
        for line in quant_section.splitlines()
        if line.startswith("- strategy_cagr：")
    )
    assert "0.00%" not in strategy_cagr_line
    assert "strategy_cagr：0%" not in strategy_cagr_line


def test_omitting_quant_packet_preserves_legacy_context_and_report() -> None:
    context = build_report_context(**_context_inputs())

    assert "quant" not in context
    report = render_validated_report(
        report_context=context,
        report_draft=build_deterministic_report_draft(),
    )
    assert "## 量化旁证" not in report


def test_flow_keeps_quant_packet_private_and_state_json_safe(
    quant_packet: QuantResearchPacket,
) -> None:
    _, flow = _new_flow(quant_packet=quant_packet)

    assert flow._quant_packet is quant_packet
    state_payload = flow.state.model_dump(mode="json")
    assert state_payload["quant"] == {}
    assert "quant_packet" not in state_payload
    json.dumps(state_payload, ensure_ascii=False, allow_nan=False)


def test_flow_injects_valid_quant_packet_before_report(
    quant_packet: QuantResearchPacket,
) -> None:
    result, _, context_kwargs, _ = _run_quant_flow(quant_packet=quant_packet)

    assert result["status"] == "ok"
    assert result["required_data"] == []
    assert context_kwargs["quant_packet"] is quant_packet
    assert result["quant"] == {
        "status": "available",
        "reason_code": "quant_packet_validated",
        "packet": quant_packet.model_dump(mode="json"),
    }
    assert "## 量化旁证" in result["report"]
    assert result["report"].count("data:image/png;base64,") == 3
    json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_flow_missing_quant_packet_is_typed_unavailable_without_blocking() -> None:
    result, _, context_kwargs, _ = _run_quant_flow()

    assert result["status"] == "ok"
    assert result["stage"] == "report"
    assert result["required_data"] == []
    assert context_kwargs["quant_packet"] is None
    assert result["quant"] == {
        "status": "unavailable",
        "reason_code": "quant_packet_missing",
        "packet": None,
    }
    assert "## 量化旁证" in result["report"]
    assert "quant_packet_missing" in result["report"]


def test_flow_invalid_quant_packet_is_typed_unavailable_without_blocking() -> None:
    invalid_packet = {"coverage": "not-a-coverage"}
    result, _, context_kwargs, _ = _run_quant_flow(quant_packet=invalid_packet)

    assert result["status"] == "ok"
    assert result["stage"] == "report"
    assert result["required_data"] == []
    assert context_kwargs["quant_packet"] == invalid_packet
    assert result["quant"] == {
        "status": "unavailable",
        "reason_code": "quant_packet_invalid",
        "packet": None,
    }
    assert "## 量化旁证" in result["report"]
    assert "quant_packet_invalid" in result["report"]


def test_flow_partial_or_evidence_only_quant_does_not_block_report(
    quant_packet: QuantResearchPacket,
) -> None:
    for coverage in (CoverageLevel.PARTIAL, CoverageLevel.EVIDENCE_ONLY):
        packet = quant_packet.model_copy(deep=True, update={"coverage": coverage})
        result, _, _, _ = _run_quant_flow(quant_packet=packet)

        assert result["status"] == "ok"
        assert result["stage"] == "report"
        assert result["required_data"] == []
        assert result["quant"]["status"] == "available"
        assert result["quant"]["packet"]["coverage"] == coverage.value
        assert "## 量化旁证" in result["report"]


def test_flow_quant_sidecar_does_not_change_verdict_inputs_or_hash(
    quant_packet: QuantResearchPacket,
) -> None:
    without_quant, _, _, verdict_kwargs_without = _run_quant_flow()
    with_quant, _, _, verdict_kwargs_with = _run_quant_flow(quant_packet=quant_packet)

    assert _canonical_json_sha256(verdict_kwargs_without) == _canonical_json_sha256(
        verdict_kwargs_with
    )
    assert _canonical_json_sha256(without_quant["verdict"]) == _canonical_json_sha256(
        with_quant["verdict"]
    )
    assert not {"quant", "quant_packet"} & _nested_mapping_keys(verdict_kwargs_without)
    assert not {"quant", "quant_packet"} & _nested_mapping_keys(verdict_kwargs_with)
    assert "## 量化旁证" in with_quant["report"]
    assert with_quant["report"].count("data:image/png;base64,") == 3


def test_flow_quant_report_leaves_no_png_files_in_workspace(
    quant_packet: QuantResearchPacket,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    repository_pngs_before = _png_files(repository_root)
    tmp_pngs_before = _png_files(tmp_path)
    monkeypatch.chdir(tmp_path)

    result, _, _, _ = _run_quant_flow(quant_packet=quant_packet)

    assert result["status"] == "ok"
    uris = _png_uris(result["report"])
    assert len(uris) == 3
    for uri in uris:
        payload = base64.b64decode(uri.split(",", 1)[1], validate=True)
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        with Image.open(BytesIO(payload)) as image:
            image.verify()
    assert _png_files(repository_root) == repository_pngs_before
    assert _png_files(tmp_path) == tmp_pngs_before
