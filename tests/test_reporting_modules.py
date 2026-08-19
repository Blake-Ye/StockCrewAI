from __future__ import annotations

import base64
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import pytest
import re
from types import SimpleNamespace

from stockcrewai.reporting.context import build_report_context
from stockcrewai.reporting.renderer import (
    _SOURCE_METHOD_NOTE,
    _chart_caption_markdown,
    _risk_claim_markdown,
    build_deterministic_report_draft,
    build_narrative_context,
    render_validated_report,
)
from stockcrewai.reporting.validator import (
    REPORT_DRAFT_FIELDS,
    parse_report_draft,
    validate_rendered_report,
    validate_report_draft,
)
from stockcrewai.reporting.visuals import build_report_visuals


VALID_REPORT_DRAFT = json.dumps(
    {
        "execution_summary": "研究范围由已验证输入限定。",
        "company_quality": "公司质量叙述来自已验证 Claim。",
        "financial_trend": "财务趋势叙述来自已验证 Claim。",
        "current_valuation": "当前估值叙述由确定性数据支撑。",
        "historical_valuation": "历史估值叙述由确定性数据支撑。",
        "reverse_dcf": "反向 DCF 叙述由确定性数据支撑。",
        "key_risks": "主要风险叙述来自已验证 Claim。",
        "sources_and_method": "来源与方法由确定性流程提供。",
        "non_investment_disclaimer": "本文不构成投资建议。",
    },
    ensure_ascii=False,
)


def _canonical_context_inputs() -> dict[str, object]:
    return {
        "company": {"name": "Apple Inc.", "ticker": "AAPL"},
        "validated_claims": [
            {
                "claim_id": "claim_current_valuation",
                "category": "current_valuation",
                "statement": "市盈率为 999x。",
                "evidence_ids": ["ev_market_price", "ev_eps"],
                "calculation_ids": ["calc_pe_ratio"],
                "confidence": 0.9,
            }
        ],
        "deterministic_verdict": {"status": "ready"},
        "calculations": [
            {
                "calculation_id": "calc_operating_margin",
                "formula_id": "operating_margin",
                "display_result": "25.00%",
                "unit": "ratio",
                "period_basis": "FY",
                "status": "available",
                "validation_status": "valid",
                "input_evidence_ids": ["ev_revenue"],
            }
        ],
        "valuation": {
            "status": "ok",
            "readiness": "ready",
            "validation_status": "valid",
            "market_price": "100",
            "market_price_evidence_id": "ev_market_price",
            "price_timestamp": "2026-08-06T15:30:00Z",
            "currency": "USD",
            "source_reference": "market:test",
            "calculations": [
                {
                    "calculation_id": "calc_pe_ratio",
                    "formula_id": "pe_ratio",
                    "display_result": "25.00x",
                    "unit": "multiple",
                    "status": "available",
                    "validation_status": "valid",
                    "input_evidence_ids": ["ev_market_price", "ev_eps"],
                    "price_timestamp": "2026-08-06T15:30:00Z",
                    "source_reference": "market:test",
                }
            ],
        },
        "historical_valuation": {
            "status": "ok",
            "validation_status": "valid",
            "calculation_id": "calc_historical_pe",
            "metric": "pe_ratio",
            "current_value": "12",
            "five_year_median": "10",
            "current_percentile": "72.5",
            "selected_dates": ["2026-08-06"],
            "input_evidence_ids": ["ev_history", "ev_eps"],
        },
        "reverse_dcf": {
            "status": "ok",
            "validation_status": "valid",
            "calculation_id": "calc_reverse_dcf_growth",
            "base_fcf": "22000000000",
            "unit": "USD",
            "period_basis": "TTM",
            "implied_growth": "0.11",
            "input_evidence_ids": ["ev_market_price", "ev_fcf"],
        },
        "ttm": {
            "status": "ok",
            "metrics": [
                {
                    "metric_id": "free_cash_flow",
                    "calculation_id": "calc_free_cash_flow_ttm",
                    "raw_result": "22000000000",
                    "unit": "USD",
                    "period_basis": "TTM",
                    "status": "available",
                    "validation_status": "valid",
                    "input_evidence_ids": ["ev_fcf"],
                }
            ],
        },
        "source_metadata": {
            "facts": {
                "revenue": {
                    "evidence_id": "ev_revenue",
                    "period_end": "2025-12-31",
                    "source_reference": "sec:test-revenue",
                },
                "eps": {
                    "evidence_id": "ev_eps",
                    "period_end": "2025-12-31",
                    "source_reference": "sec:test-eps",
                },
                "fcf": {
                    "evidence_id": "ev_fcf",
                    "period_end": "2025-12-31",
                    "source_reference": "sec:test-fcf",
                },
            },
            "market_price": {
                "evidence_id": "ev_market_price",
                "price_timestamp": "2026-08-06T15:30:00Z",
                "source_reference": "market:test",
            },
            "historical_prices": [
                {
                    "evidence_id": "ev_history",
                    "as_of": "2026-08-06",
                    "source_reference": "market:test",
                }
            ],
        },
    }


def _apple_q3_context_inputs() -> dict[str, object]:
    source_reference = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
    current_duration = {
        "form": "10-Q",
        "fiscal_period": "Q3",
        "period_type": "duration",
        "period_start": "2025-09-28",
        "period_end": "2026-06-27",
    }
    prior_duration = {
        "form": "10-Q",
        "fiscal_period": "Q3",
        "period_type": "duration",
        "period_start": "2024-09-29",
        "period_end": "2025-06-28",
    }
    current_instant = {
        "form": "10-Q",
        "fiscal_period": "Q3",
        "period_type": "instant",
        "period_start": None,
        "period_end": "2026-06-27",
    }
    prior_instant = {
        "form": "10-Q",
        "fiscal_period": "Q3",
        "period_type": "instant",
        "period_start": None,
        "period_end": "2025-06-28",
    }
    evidence_specs = {
        "ev_aapl_revenue_2026_q3": current_duration,
        "ev_aapl_revenue_prior_2025_q3": prior_duration,
        "ev_aapl_operating_income_2026_q3": current_duration,
        "ev_aapl_net_income_2026_q3": current_duration,
        "ev_aapl_operating_cash_flow_2026_q3": current_duration,
        "ev_aapl_capex_2026_q3": current_duration,
        "ev_aapl_common_shares_outstanding_2026_q3": current_instant,
        "ev_aapl_shares_prior_2025_q3": prior_instant,
    }
    calculations = [
        ("revenue_growth", "16.15%", ["ev_aapl_revenue_2026_q3", "ev_aapl_revenue_prior_2025_q3"]),
        ("operating_margin", "33.60%", ["ev_aapl_operating_income_2026_q3", "ev_aapl_revenue_2026_q3"]),
        ("net_margin", "27.85%", ["ev_aapl_net_income_2026_q3", "ev_aapl_revenue_2026_q3"]),
        (
            "free_cash_flow_margin",
            "30.24%",
            [
                "ev_aapl_operating_cash_flow_2026_q3",
                "ev_aapl_capex_2026_q3",
                "ev_aapl_revenue_2026_q3",
            ],
        ),
        (
            "cash_conversion",
            "115.31%",
            ["ev_aapl_operating_cash_flow_2026_q3", "ev_aapl_net_income_2026_q3"],
        ),
        (
            "share_dilution",
            "-1.67%",
            [
                "ev_aapl_common_shares_outstanding_2026_q3",
                "ev_aapl_shares_prior_2025_q3",
            ],
        ),
    ]
    return {
        "company": {"name": "Apple Inc.", "ticker": "AAPL"},
        "validated_claims": [],
        "deterministic_verdict": {"status": "ready"},
        "calculations": [
            {
                "calculation_id": f"calc_{metric_id}",
                "formula_id": metric_id,
                "display_result": display_result,
                "unit": "ratio",
                "status": "available",
                "validation_status": "valid",
                "input_evidence_ids": evidence_ids,
                **(
                    {"adjustment_basis": "raw"}
                    if metric_id == "share_dilution"
                    else {}
                ),
            }
            for metric_id, display_result, evidence_ids in calculations
        ],
        "source_metadata": {
            "facts": {
                evidence_id: {
                    "evidence_id": evidence_id,
                    **metadata,
                    "period_basis": None,
                    "validation_status": "valid",
                    "source_reference": source_reference,
                }
                for evidence_id, metadata in evidence_specs.items()
            }
        },
    }


def _apple_q3_context_with_consistent_explicit_bases() -> dict[str, object]:
    inputs = deepcopy(_apple_q3_context_inputs())
    calculations = inputs["calculations"]
    source_metadata = inputs["source_metadata"]
    assert isinstance(calculations, list)
    assert isinstance(source_metadata, dict)
    facts = source_metadata["facts"]
    assert isinstance(facts, dict)

    expected_bases = {
        "revenue_growth": "YTD同比",
        "operating_margin": "YTD",
        "net_margin": "YTD",
        "free_cash_flow_margin": "YTD",
        "cash_conversion": "YTD",
        "share_dilution": "同比时点",
    }
    consistent_facts: dict[str, dict[str, object]] = {}
    consistent_calculations: list[dict[str, object]] = []
    for calculation in calculations:
        metric_id = str(calculation["formula_id"])
        basis = expected_bases[metric_id]
        evidence_ids = calculation["input_evidence_ids"]
        assert isinstance(evidence_ids, list)
        renamed_evidence_ids = []
        for index, evidence_id in enumerate(evidence_ids):
            original = facts[evidence_id]
            assert isinstance(original, dict)
            renamed_id = f"ev_consistent_{metric_id}_{index}"
            consistent_facts[renamed_id] = {
                **original,
                "evidence_id": renamed_id,
                "period_basis": basis,
            }
            renamed_evidence_ids.append(renamed_id)
        consistent_calculations.append(
            {
                **calculation,
                "period_basis": basis,
                "input_evidence_ids": renamed_evidence_ids,
            }
        )

    inputs["calculations"] = consistent_calculations
    inputs["source_metadata"] = {
        **source_metadata,
        "facts": consistent_facts,
    }
    return inputs


def _strict_lite_sections(report: str) -> dict[str, str]:
    matches = list(
        re.finditer(r"^## (?P<number>[0-9]+)\. [^\n]+$", report, re.MULTILINE)
    )
    assert [match.group("number") for match in matches] == [str(index) for index in range(10)]
    return {
        match.group("number"): report[
            match.end() : (matches[index + 1].start() if index + 1 < len(matches) else len(report))
        ]
        for index, match in enumerate(matches)
    }


def _reader_focused_inputs() -> dict[str, object]:
    inputs = _canonical_context_inputs()
    inputs["deterministic_verdict"] = {
        "status": "ready",
        "overall_rating": "expensive",
        "risk_level": "medium",
        "triggered_rules": ["high_valuation"],
    }
    financial_values = {
        "revenue_growth": "20.00%",
        "net_margin": "20.00%",
        "free_cash_flow_margin": "15.00%",
        "cash_conversion": "1.50",
        "share_dilution": "-2.00%",
    }
    inputs["calculations"] = [
        *inputs["calculations"],  # type: ignore[index]
        *[
            {
                "calculation_id": f"calc_{metric_id}",
                "formula_id": metric_id,
                    "display_result": display_value,
                    "unit": "ratio",
                    "period_basis": "FY",
                    "status": "available",
                    "validation_status": "valid",
                "input_evidence_ids": ["ev_revenue"],
                **(
                    {"adjustment_basis": "raw"}
                    if metric_id == "share_dilution"
                    else {}
                ),
            }
            for metric_id, display_value in financial_values.items()
        ],
    ]
    start = date(2021, 8, 31)
    series = [
        {
            "date": (start + timedelta(days=30 * index)).isoformat(),
            "pe_ratio": f"{15 + (index % 12) / 10:.2f}",
        }
        for index in range(60)
    ]
    inputs["historical_valuation"] = {
        **inputs["historical_valuation"],  # type: ignore[index]
        "series": series,
        "current_date": series[-1]["date"],
        "current_value": series[-1]["pe_ratio"],
        "percentile_25": "15.30",
        "five_year_median": "15.60",
        "percentile_75": "16.00",
    }
    inputs["ttm"] = {
        "status": "ok",
        "metrics": [
            {
                "metric_id": metric_id,
                "calculation_id": f"calc_{metric_id}_ttm",
                "raw_result": raw_result,
                "unit": "USD",
                "period_basis": "TTM",
                "status": "available",
                "validation_status": "valid",
                "input_evidence_ids": ["ev_revenue"],
            }
            for metric_id, raw_result in {
                "revenue": "100000000000",
                "operating_income": "25000000000",
                "net_income": "20000000000",
                "operating_cash_flow": "30000000000",
                "free_cash_flow": "22000000000",
            }.items()
        ],
    }
    inputs["annual_financial_history"] = _annual_financial_history()
    return inputs


def _financial_metrics() -> list[dict[str, object]]:
    values = {
        "revenue_growth": "0.20",
        "operating_margin": "0.25",
        "net_margin": "0.20",
        "free_cash_flow_margin": "0.15",
        "cash_conversion": "1.50",
        "share_dilution": "-0.02",
    }
    return [
        {
            "metric_id": metric_id,
            "display_value": value,
            "unit": "ratio",
            "status": "available",
            "validation_status": "valid",
            "calculation_id": f"calc_{metric_id}",
            "period_basis": "FY",
            "period_end": "2025-12-31",
            "as_of": "2025-12-31",
            "evidence_ids": [f"ev_{metric_id}"],
            **(
                {"adjustment_basis": "raw"}
                if metric_id == "share_dilution"
                else {}
            ),
        }
        for metric_id, value in values.items()
    ]


def _ttm_metrics() -> list[dict[str, object]]:
    values = {
        "revenue": "100000000000",
        "operating_income": "25000000000",
        "net_income": "20000000000",
        "operating_cash_flow": "30000000000",
        "free_cash_flow": "22000000000",
    }
    return [
        {
            "metric_id": metric_id,
            "raw_result": value,
            "unit": "USD",
            "period_basis": "TTM",
            "status": "available",
            "validation_status": "valid",
            "calculation_id": f"calc_{metric_id}_ttm",
            "input_evidence_ids": [f"ev_{metric_id}_ttm"],
        }
        for metric_id, value in values.items()
    ]


def _annual_financial_history() -> dict[str, object]:
    periods = []
    for fiscal_year in range(2021, 2026):
        value_index = fiscal_year - 2020
        periods.append(
            {
                "fiscal_year": fiscal_year,
                "period_start": f"{fiscal_year}-01-01",
                "period_end": f"{fiscal_year}-12-31",
                "filed_at": f"{fiscal_year + 1}-02-01",
                "period_basis": "FY",
                "currency": "USD",
                "revenue": str(value_index * 10_000_000_000),
                "net_income": str(value_index * 1_000_000_000),
                "operating_cash_flow": str(value_index * 2_000_000_000),
                "capex": str(value_index * 500_000_000),
                "free_cash_flow": str(value_index * 1_500_000_000),
                "evidence_ids": [
                    f"ev_revenue_{fiscal_year}",
                    f"ev_net_income_{fiscal_year}",
                    f"ev_operating_cash_flow_{fiscal_year}",
                    f"ev_capex_{fiscal_year}",
                ],
                "calculation_id": f"calc_annual_fcf_{fiscal_year}",
                "calculation_provenance": {
                    "formula": "free_cash_flow = operating_cash_flow - positive_capex",
                    "input_metric_ids": [
                        "operating_cash_flow",
                        "capex",
                    ],
                    "input_evidence_ids": [
                        f"ev_revenue_{fiscal_year}",
                        f"ev_net_income_{fiscal_year}",
                        f"ev_operating_cash_flow_{fiscal_year}",
                        f"ev_capex_{fiscal_year}",
                    ],
                },
                "validation_status": "valid",
            }
        )
    return {
        "status": "ok",
        "reason_code": None,
        "currency": "USD",
        "periods": periods,
        "validation_status": "valid",
    }


def _historical_payload() -> dict[str, object]:
    start = date(2021, 8, 31)
    series = [
        {
            "date": (start + timedelta(days=30 * index)).isoformat(),
            "pe_ratio": f"{15 + (index % 12) / 10:.2f}",
        }
        for index in range(60)
    ]
    return {
        "status": "ok",
        "validation_status": "valid",
        "series": series,
        "current_date": series[-1]["date"],
        "current_value": series[-1]["pe_ratio"],
        "percentile_25": "15.30",
        "five_year_median": "15.60",
        "percentile_75": "16.00",
    }


def _sha256_json(value: object) -> str:
    encoded = (
        value.encode()
        if isinstance(value, str)
        else json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    )
    return hashlib.sha256(encoded).hexdigest()


def test_context_is_json_safe_and_matches_fixed_fixture_hash() -> None:
    context = build_report_context(**_canonical_context_inputs())

    assert {metric["section"] for metric in context["metrics"]} == {
        "financial",
        "current_valuation",
        "historical_valuation",
        "reverse_dcf",
    }
    json.dumps(context, ensure_ascii=False, allow_nan=False)
    assert _sha256_json(context) == "ae3dc938001c68df0bca9ebf5054849afef999a3c4b8d171a791004b82e94c7d"


def test_report_metric_preserves_calculation_period_basis() -> None:
    inputs = _canonical_context_inputs()
    calculations = inputs["calculations"]
    assert isinstance(calculations, list)
    inputs["calculations"] = [
        {**calculations[0], "period_basis": "YTD"},
    ]

    context = build_report_context(**inputs)

    metric = next(
        metric for metric in context["metrics"] if metric["metric_id"] == "operating_margin"
    )
    assert metric["period_basis"] == "YTD"


def _context_with_evidence_period_bases(
    first_basis: str | None, second_basis: str | None
) -> dict[str, object]:
    inputs = _canonical_context_inputs()
    calculations = inputs["calculations"]
    source_metadata = inputs["source_metadata"]
    assert isinstance(calculations, list)
    assert isinstance(source_metadata, dict)
    facts = source_metadata["facts"]
    assert isinstance(facts, dict)
    inputs["calculations"] = [
        {
            key: value
            for key, value in calculations[0].items()
            if key != "period_basis"
        }
        | {
            "input_evidence_ids": ["ev_revenue", "ev_eps"],
        },
    ]
    for evidence_id, basis in (
        ("ev_revenue", first_basis),
        ("ev_eps", second_basis),
    ):
        evidence = next(
            evidence
            for evidence in facts.values()
            if evidence.get("evidence_id") == evidence_id
        )
        assert isinstance(evidence, dict)
        if basis is None:
            evidence.pop("period_basis", None)
        else:
            evidence["period_basis"] = basis
    return build_report_context(**inputs)


def test_report_metric_uses_common_evidence_period_basis() -> None:
    context = _context_with_evidence_period_bases("YTD", "YTD")

    metric = next(
        metric for metric in context["metrics"] if metric["metric_id"] == "operating_margin"
    )

    assert metric["period_basis"] == "YTD"


def test_report_metric_rejects_mixed_evidence_period_basis() -> None:
    context = _context_with_evidence_period_bases("FY", "TTM")

    metric = next(
        metric for metric in context["metrics"] if metric["metric_id"] == "operating_margin"
    )

    assert metric.get("period_basis") is None


def test_report_metric_rejects_missing_evidence_period_basis() -> None:
    context = _context_with_evidence_period_bases("YTD", None)

    metric = next(
        metric for metric in context["metrics"] if metric["metric_id"] == "operating_margin"
    )

    assert metric.get("period_basis") is None


def test_q3_calculation_period_conflict_fails_closed_and_suppresses_chart() -> None:
    inputs = _apple_q3_context_inputs()
    calculations = inputs["calculations"]
    assert isinstance(calculations, list)
    inputs["calculations"] = [
        {
            **calculation,
            **(
                {"period_basis": "FY"}
                if calculation["formula_id"] == "operating_margin"
                else {}
            ),
        }
        for calculation in calculations
    ]

    context = build_report_context(**inputs)
    metric = next(
        metric
        for metric in context["metrics"]
        if metric["metric_id"] == "operating_margin"
    )

    assert metric.get("period_basis") is None
    assert "financial_kpis" not in build_report_visuals(context=context)


def test_q3_evidence_period_conflict_fails_closed_and_suppresses_chart() -> None:
    inputs = _apple_q3_context_inputs()
    source_metadata = inputs["source_metadata"]
    assert isinstance(source_metadata, dict)
    facts = source_metadata["facts"]
    assert isinstance(facts, dict)
    for evidence_id in (
        "ev_aapl_operating_income_2026_q3",
        "ev_aapl_revenue_2026_q3",
    ):
        evidence = facts[evidence_id]
        assert isinstance(evidence, dict)
        evidence["period_basis"] = "FY"

    context = build_report_context(**inputs)
    metric = next(
        metric
        for metric in context["metrics"]
        if metric["metric_id"] == "operating_margin"
    )

    assert metric.get("period_basis") is None
    assert "financial_kpis" not in build_report_visuals(context=context)


def test_q3_calculation_evidence_and_inferred_periods_agree_for_chart() -> None:
    context = build_report_context(
        **_apple_q3_context_with_consistent_explicit_bases()
    )

    metric = next(
        metric
        for metric in context["metrics"]
        if metric["metric_id"] == "operating_margin"
    )

    assert metric["period_basis"] == "YTD"
    assert "financial_kpis" in build_report_visuals(context=context)


def test_q3_complete_structured_period_fields_still_infer_and_generate_chart() -> None:
    context = build_report_context(**_apple_q3_context_inputs())

    metric = next(
        metric
        for metric in context["metrics"]
        if metric["metric_id"] == "operating_margin"
    )

    assert metric["period_basis"] == "YTD"
    assert "financial_kpis" in build_report_visuals(context=context)


def test_real_apple_q3_context_restores_missing_financial_kpi_chart() -> None:
    context = build_report_context(**_apple_q3_context_inputs())

    visuals = build_report_visuals(context=context)

    assert "financial_kpis" in visuals


def test_real_apple_q3_context_restores_three_visuals_and_caption_basis() -> None:
    inputs = _apple_q3_context_inputs()
    complete_inputs = _reader_focused_inputs()
    inputs["annual_financial_history"] = complete_inputs["annual_financial_history"]
    inputs["historical_valuation"] = complete_inputs["historical_valuation"]

    context = build_report_context(**inputs)
    visuals = build_report_visuals(context=context)
    report = render_validated_report(
        context,
        parse_report_draft(VALID_REPORT_DRAFT),
    )

    assert set(visuals) == {
        "financial_kpis",
        "annual_financial_trend",
        "historical_pe",
    }
    assert report.count("data:image/png;base64,") == 3
    assert "期间：YTD同比 / YTD / 同比时点（2026-06-27）" in report
    assert "## 9. 非投资建议声明" in report
    assert "<!-- ## 9. 非投资建议声明 -->" not in report


def test_generate_report_preserves_standard_operating_period_fields() -> None:
    from stockcrewai.flow import ResearchFlow

    from unittest.mock import Mock, patch

    inputs = _apple_q3_context_inputs()
    source_metadata = inputs["source_metadata"]
    assert isinstance(source_metadata, dict)
    facts = source_metadata["facts"]
    assert isinstance(facts, dict)
    fact = facts["ev_aapl_revenue_2026_q3"]
    assert isinstance(fact, dict)

    flow = ResearchFlow()
    flow._validation_result = SimpleNamespace(status="valid")
    flow._edgar_result = SimpleNamespace(ttm_inputs={})
    flow._pipeline_state = {
        "company_name": "Apple Inc.",
        "ticker": "AAPL",
        "facts": {"revenue": {**fact, "evidence_id": "ev_aapl_revenue_2026_q3"}},
        "calculations": [],
    }
    flow._risk_input = {}

    captured: dict[str, object] = {}
    flow_module = __import__("stockcrewai.flow", fromlist=["build_report_context"])
    original_build_report_context = flow_module.build_report_context

    def capture_report_context(**kwargs: object) -> dict[str, object]:
        captured["source_metadata"] = kwargs["source_metadata"]
        return original_build_report_context(**kwargs)

    report_crew = Mock()
    report_crew.kickoff.return_value = SimpleNamespace(raw=VALID_REPORT_DRAFT)
    report_factory = Mock()
    report_factory.return_value.crew.return_value = report_crew
    with (
        patch.object(flow_module, "ReportCrew", report_factory),
        patch.object(
            flow_module,
            "build_report_context",
            side_effect=capture_report_context,
        ),
        patch.object(flow_module, "render_validated_report", return_value="# report"),
        patch.object(flow_module, "validate_rendered_report", return_value=(True, "")),
    ):
        flow.generate_report()

    generated_source_metadata = captured["source_metadata"]
    assert isinstance(generated_source_metadata, dict)
    generated_facts = generated_source_metadata["facts"]
    assert isinstance(generated_facts, dict)
    generated_fact = generated_facts["revenue"]
    assert isinstance(generated_fact, dict)
    assert generated_fact["fiscal_period"] == "Q3"
    assert generated_fact["period_type"] == "duration"


def test_normalized_financial_kpis_without_basis_are_unavailable_and_caption_is_insufficient() -> None:
    context = build_report_context(**_reader_focused_inputs())
    for metric in context["metrics"]:
        if metric["section"] == "financial":
            metric.pop("period_basis", None)

    visuals = build_report_visuals(context=context)
    caption = _chart_caption_markdown(
        context,
        "financial_kpis",
        {"financial_kpis": {"observations": ["收入同比保持正增长"]}},
    )

    assert "financial_kpis" not in visuals
    assert "期间：数据不足" in caption
    assert "截止：2025-12-31" in caption


def test_reverse_dcf_context_preserves_million_usd_display_units() -> None:
    inputs = _canonical_context_inputs()
    inputs["ttm"] = {
        "status": "ok",
        "metrics": [
            {
                "metric_id": "free_cash_flow",
                "calculation_id": "calc_free_cash_flow_ttm",
                "raw_result": "22000",
                "unit": "million USD",
                "period_basis": "TTM",
                "status": "available",
                "validation_status": "valid",
                "input_evidence_ids": ["ev_fcf"],
            }
        ],
    }
    inputs["reverse_dcf"] = {
        **inputs["reverse_dcf"],  # type: ignore[index]
        "base_fcf": "22",
        "base_fcf_unit": "billion USD",
        "unit": "billion USD",
        "period_basis": "TTM",
    }

    context = build_report_context(**inputs)

    ttm_fcf = next(
        metric
        for metric in context["ttm"]["metrics"]
        if metric["metric_id"] == "free_cash_flow"
    )
    assert ttm_fcf["raw_result"] == "22000"
    assert ttm_fcf["unit"] == "million USD"
    assert context["reverse_dcf"]["base_fcf"] == "22"
    assert context["reverse_dcf"]["base_fcf_unit"] == "billion USD"
    assert context["reverse_dcf"]["unit"] == "billion USD"


def test_reverse_dcf_without_base_fcf_does_not_project_implied_growth() -> None:
    inputs = _canonical_context_inputs()
    reverse_dcf = inputs["reverse_dcf"]
    assert isinstance(reverse_dcf, dict)
    inputs["reverse_dcf"] = {
        key: value
        for key, value in reverse_dcf.items()
        if key not in {"base_fcf", "base_fcf_unit", "unit", "period_basis"}
    }
    inputs["ttm"] = {
        "status": "ok",
        "metrics": [
            {
                "metric_id": "free_cash_flow",
                "calculation_id": "calc_free_cash_flow_ttm",
                "raw_result": "22000000000",
                "unit": "USD",
                "period_basis": "TTM",
                "status": "available",
                "validation_status": "valid",
                "input_evidence_ids": ["ev_fcf"],
            }
        ],
    }

    context = build_report_context(**inputs)

    assert context["reverse_dcf"] == {}
    assert not any(
        metric["metric_id"] == "reverse_dcf_implied_growth"
        for metric in context["metrics"]
    )


def test_missing_ttm_period_basis_is_rejected_and_cannot_drive_reverse_dcf() -> None:
    inputs = _canonical_context_inputs()
    inputs["ttm"] = {
        "status": "ok",
        "metrics": [
            {
                "metric_id": "free_cash_flow",
                "calculation_id": "calc_free_cash_flow_ttm",
                "raw_result": "22000",
                "unit": "million USD",
                "status": "available",
                "validation_status": "valid",
                "input_evidence_ids": ["ev_fcf"],
            }
        ],
    }
    inputs["reverse_dcf"] = {
        **inputs["reverse_dcf"],  # type: ignore[index]
        "base_fcf": "22",
        "base_fcf_unit": "billion USD",
        "unit": "billion USD",
        "period_basis": "TTM",
    }

    context = build_report_context(**inputs)

    assert context["ttm"]["metrics"] == []
    assert context["reverse_dcf"] == {}
    assert not any(
        metric["section"] == "reverse_dcf" for metric in context["metrics"]
    )


def test_report_context_keeps_ttm_fcf_and_rejects_ordinary_reverse_dcf_fcf() -> None:
    inputs = _canonical_context_inputs()
    source_metadata = inputs["source_metadata"]
    assert isinstance(source_metadata, dict)
    inputs["source_metadata"] = {
        **source_metadata,
        "facts": {
            **source_metadata["facts"],  # type: ignore[index]
            "ordinary_fcf": {
                "evidence_id": "ev_ordinary_fcf",
                "value": "10000000000",
                "unit": "USD",
                "period_basis": "FY",
                "period_end": "2025-12-31",
                "source_reference": "sec:test-ordinary-fcf",
            },
            "ttm_fcf": {
                "evidence_id": "ev_ttm_fcf",
                "value": "20000000000",
                "unit": "USD",
                "period_basis": "TTM",
                "period_end": "2026-06-30",
                "source_reference": "sec:test-ttm-fcf",
            },
        },
    }
    inputs["calculations"] = [
        *inputs["calculations"],  # type: ignore[index]
        {
            "calculation_id": "calc_ordinary_fcf",
            "formula_id": "free_cash_flow",
            "display_result": "10000000000",
            "unit": "USD",
            "status": "available",
            "validation_status": "valid",
            "input_evidence_ids": ["ev_ordinary_fcf"],
        },
    ]
    inputs["ttm"] = {
        "status": "ok",
        "metrics": [
            {
                "metric_id": "free_cash_flow",
                "calculation_id": "calc_free_cash_flow_ttm",
                "formula_id": "ttm_free_cash_flow",
                "raw_result": "20000000000",
                "unit": "USD",
                "period_basis": "TTM",
                "status": "available",
                "validation_status": "valid",
                "input_evidence_ids": ["ev_ttm_fcf"],
            }
        ],
    }
    inputs["reverse_dcf"] = {
        "status": "ok",
        "validation_status": "valid",
        "calculation_id": "calc_reverse_dcf_growth",
        "base_fcf": "20",
        "unit": "billion USD",
        "period_basis": "TTM",
        "implied_growth": "0.11",
        "input_evidence_ids": ["ev_ttm_fcf"],
    }

    context = build_report_context(**inputs)

    ttm_fcf = next(
        metric
        for metric in context["ttm"]["metrics"]
        if metric["metric_id"] == "free_cash_flow"
    )
    assert ttm_fcf["raw_result"] == "20000000000"
    assert ttm_fcf["period_basis"] == "TTM"
    assert context["reverse_dcf"]["base_fcf"] == "20"

    mismatched_inputs = {
        **inputs,
        "reverse_dcf": {
            **inputs["reverse_dcf"],  # type: ignore[index]
            "base_fcf": "10",
        },
    }
    with pytest.raises(ValueError, match="report_ttm_fcf_mismatch"):
        build_report_context(**mismatched_inputs)


def test_report_context_filters_fy_ytd_and_omits_reverse_base_without_canonical_ttm() -> None:
    inputs = _canonical_context_inputs()
    inputs["ttm"] = {
        "status": "ok",
        "metrics": [
            {
                "metric_id": "free_cash_flow",
                "calculation_id": "calc_free_cash_flow_fy",
                "raw_result": "10000000000",
                "unit": "USD",
                "period_basis": "FY",
                "status": "available",
                "validation_status": "valid",
                "input_evidence_ids": ["ev_fcf"],
            },
            {
                "metric_id": "free_cash_flow",
                "calculation_id": "calc_free_cash_flow_ytd",
                "raw_result": "15000000000",
                "unit": "USD",
                "period_basis": "YTD",
                "status": "available",
                "validation_status": "valid",
                "input_evidence_ids": ["ev_fcf"],
            },
            {
                "metric_id": "free_cash_flow",
                "calculation_id": "calc_free_cash_flow_ttm",
                "raw_result": "20000000000",
                "unit": "USD",
                "period_basis": "TTM",
                "status": "available",
                "validation_status": "valid",
                "input_evidence_ids": ["ev_fcf"],
            },
        ],
    }
    inputs["reverse_dcf"] = {
        **inputs["reverse_dcf"],  # type: ignore[index]
        "base_fcf": "20",
        "unit": "billion USD",
        "period_basis": "TTM",
        "input_evidence_ids": ["ev_fcf"],
    }

    context = build_report_context(**inputs)

    assert [
        (metric["metric_id"], metric["period_basis"])
        for metric in context["ttm"]["metrics"]
    ] == [("free_cash_flow", "TTM")]
    assert context["reverse_dcf"]["base_fcf"] == "20"

    inputs_without_ttm = deepcopy(inputs)
    inputs_without_ttm["ttm"]["metrics"] = inputs_without_ttm["ttm"]["metrics"][:2]  # type: ignore[index]
    context_without_ttm = build_report_context(**inputs_without_ttm)

    assert context_without_ttm["ttm"]["metrics"] == []
    assert context_without_ttm["reverse_dcf"] == {}
    assert not any(
        metric["section"] == "reverse_dcf"
        for metric in context_without_ttm["metrics"]
    )


def test_report_context_preserves_validated_historical_summary_values() -> None:
    inputs = _canonical_context_inputs()
    historical_valuation = inputs["historical_valuation"]
    assert isinstance(historical_valuation, dict)
    inputs["historical_valuation"] = {
        **historical_valuation,
        "series": [
            {"date": "2026-08-04", "pe_ratio": "10"},
            {"date": "2026-08-05", "pe_ratio": "20"},
            {"date": "2026-08-06", "pe_ratio": "30"},
        ],
        "current_date": "2026-08-06",
        "current_value": "999.01",
        "percentile_25": "888.02",
        "five_year_median": "777.03",
        "percentile_75": "666.04",
        "current_percentile": "55.05",
    }

    context = build_report_context(**inputs)

    historical_context = context["historical_valuation"]
    assert {
        key: historical_context[key]
        for key in (
            "current_value",
            "percentile_25",
            "five_year_median",
            "percentile_75",
            "current_percentile",
        )
    } == {
        "current_value": "999.01",
        "percentile_25": "888.02",
        "five_year_median": "777.03",
        "percentile_75": "666.04",
        "current_percentile": "55.05",
    }


def test_report_historical_current_pe_uses_realtime_date_not_series_month_start() -> None:
    inputs = _reader_focused_inputs()
    historical = inputs["historical_valuation"]
    assert isinstance(historical, dict)
    historical["series"][-1]["date"] = "2026-07-31"
    historical["current_date"] = "2026-08-12"
    historical["current_value"] = "40"

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    historical_section = _strict_lite_sections(report)["5"]

    assert "历史当前 P/E：40.00x（截至 2026-08-12" in historical_section
    assert "截至 2026-07-31" not in historical_section


def test_report_metric_projection_preserves_share_adjustment_basis() -> None:
    inputs = _canonical_context_inputs()
    source_metadata = inputs["source_metadata"]
    assert isinstance(source_metadata, dict)
    inputs["source_metadata"] = {
        **source_metadata,
        "facts": {
            **source_metadata["facts"],  # type: ignore[index]
            "shares_current": {
                "evidence_id": "ev_shares_current",
                "period_end": "2026-06-30",
                "source_reference": "sec:test-shares-current",
            },
            "shares_prior": {
                "evidence_id": "ev_shares_prior",
                "period_end": "2025-06-30",
                "source_reference": "sec:test-shares-prior",
            },
        },
    }
    inputs["calculations"] = [
        *inputs["calculations"],  # type: ignore[index]
        {
            "calculation_id": "calc_share_dilution",
            "formula_id": "share_dilution",
            "display_result": "-2.00%",
            "unit": "ratio",
            "status": "available",
            "validation_status": "valid",
            "adjustment_basis": "split_adjusted",
            "input_evidence_ids": ["ev_shares_current", "ev_shares_prior"],
        },
    ]

    context = build_report_context(**inputs)

    share_metric = next(
        metric for metric in context["metrics"] if metric["metric_id"] == "share_dilution"
    )
    assert share_metric["adjustment_basis"] == "split_adjusted"


def test_draft_validator_and_deterministic_fallback_preserve_contract() -> None:
    draft = build_deterministic_report_draft()
    assert parse_report_draft(draft) is draft
    assert set(draft.model_dump()) == {
        "execution_summary",
        "company_quality",
        "financial_trend",
        "current_valuation",
        "historical_valuation",
        "reverse_dcf",
        "key_risks",
        "sources_and_method",
        "non_investment_disclaimer",
    }

    invalid = VALID_REPORT_DRAFT.replace("公司质量叙述来自已验证 Claim。", "增长率为 42。")
    passed, code = validate_report_draft(SimpleNamespace(raw=invalid))
    assert passed is False
    assert code == "report_draft_forbidden_number"


def test_narrative_context_is_bounded_and_matches_fixed_fixture_hash() -> None:
    narrative = build_narrative_context(build_report_context(**_reader_focused_inputs()))

    encoded = json.dumps(narrative, ensure_ascii=False, separators=(",", ":"))
    assert len(encoded.encode()) <= 24 * 1024
    assert narrative["company"] == "Apple Inc."
    assert narrative["ticker"] == "AAPL"
    assert list(narrative["accepted_claim_summaries"]) == [
        "financial_quality",
        "financial_trend",
        "valuation",
        "risk",
    ]
    assert _sha256_json(narrative) == "1c31fc86b85fd2ba4e19ba20d658fdce737955d65a6def4e0a28becb7b82ea00"


def test_chart_context_exposes_number_free_observations() -> None:
    narrative = build_narrative_context(build_report_context(**_reader_focused_inputs()))
    chart_context = narrative["chart_context"]

    assert tuple(chart_context) == (
        "financial_kpis",
        "annual_financial_trend",
        "historical_pe",
    )
    assert chart_context["financial_kpis"]["available"] is True
    assert "收入同比保持正增长" in chart_context["financial_kpis"]["observations"]
    assert chart_context["annual_financial_trend"]["available"] is True
    assert chart_context["annual_financial_trend"]["observations"] == [
        "营业收入五年总体增长",
        "净利润五年总体增长",
        "自由现金流五年总体增长",
        "五年自由现金流全部为正",
        "最新自由现金流高于最新净利润",
    ]
    assert chart_context["historical_pe"]["available"] is True
    assert "当前市盈率高于五年中位数" in chart_context["historical_pe"]["observations"]
    for item in chart_context.values():
        assert tuple(item) == ("available", "observations")
    assert not re.search(r"[0-9]", json.dumps(chart_context, ensure_ascii=False))


def test_chart_context_marks_observations_unavailable_without_inputs() -> None:
    missing_financial = deepcopy(_reader_focused_inputs())
    calculations = missing_financial["calculations"]
    assert isinstance(calculations, list)
    missing_financial["calculations"] = [
        calculation
        for calculation in calculations
        if calculation.get("formula_id") != "revenue_growth"
    ]
    financial_chart_context = build_narrative_context(
        build_report_context(**missing_financial)
    )["chart_context"]
    assert financial_chart_context["financial_kpis"] == {
        "available": False,
        "observations": [],
    }

    missing_annual = deepcopy(_reader_focused_inputs())
    missing_annual.pop("annual_financial_history")
    annual_chart_context = build_narrative_context(
        build_report_context(**missing_annual)
    )["chart_context"]
    assert annual_chart_context["annual_financial_trend"] == {
        "available": False,
        "observations": [],
    }

    missing_historical = deepcopy(_reader_focused_inputs())
    historical = missing_historical["historical_valuation"]
    assert isinstance(historical, dict)
    historical["status"] = "unavailable"
    historical_chart_context = build_narrative_context(
        build_report_context(**missing_historical)
    )["chart_context"]
    assert historical_chart_context["historical_pe"] == {
        "available": False,
        "observations": [],
    }


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (("validation_status", "invalid"), ("period_basis", "TTM")),
)
def test_chart_context_requires_valid_annual_period(field: str, invalid_value: str) -> None:
    inputs = _reader_focused_inputs()
    annual = inputs["annual_financial_history"]
    assert isinstance(annual, dict)
    periods = annual["periods"]
    assert isinstance(periods, list)
    target = next(period for period in periods if period["fiscal_year"] == 2023)
    target[field] = invalid_value

    chart_context = build_narrative_context(build_report_context(**inputs))["chart_context"]

    assert chart_context["annual_financial_trend"] == {
        "available": False,
        "observations": [],
    }


def test_report_context_rejects_non_usd_annual_history() -> None:
    inputs = _reader_focused_inputs()
    annual = deepcopy(inputs["annual_financial_history"])
    assert isinstance(annual, dict)
    annual["currency"] = "EUR"
    periods = annual["periods"]
    assert isinstance(periods, list)
    for period in periods:
        period["currency"] = "EUR"
    inputs["annual_financial_history"] = annual

    context = build_report_context(**inputs)

    assert "annual_financial_history" not in context
    assert build_narrative_context(context)["chart_context"][
        "annual_financial_trend"
    ] == {"available": False, "observations": []}


def test_report_context_calculates_annual_cagr_and_expectation_gap() -> None:
    context = build_report_context(**_reader_focused_inputs())

    summary = context["annual_financial_summary"]
    assert summary == {
        "start_fiscal_year": 2021,
        "end_fiscal_year": 2025,
        "revenue_cagr": "49.53",
        "net_income_cagr": "49.53",
        "free_cash_flow_cagr": "49.53",
        "latest_fcf_direction": "up",
        "validation_status": "valid",
        "basis_note": (
            "CAGR 基于五个完整 FY 历史；反向 DCF 以 TTM FCF 为起点，"
            "二者口径不同，仅作方向比较，不是预测。"
        ),
        "expectation_gap_percentage_points": "-38.53",
    }
    assert all("%" not in summary[key] for key in (
        "revenue_cagr",
        "net_income_cagr",
        "free_cash_flow_cagr",
        "expectation_gap_percentage_points",
    ))


def test_report_context_marks_latest_fcf_down() -> None:
    inputs = _reader_focused_inputs()
    annual = inputs["annual_financial_history"]
    assert isinstance(annual, dict)
    periods = annual["periods"]
    assert isinstance(periods, list)
    periods[-1]["operating_cash_flow"] = "7500000000"
    periods[-1]["free_cash_flow"] = "5000000000"

    summary = build_report_context(**inputs)["annual_financial_summary"]

    assert summary["latest_fcf_direction"] == "down"


def test_report_context_uses_empty_annual_summary_without_valid_history() -> None:
    context = build_report_context(**_canonical_context_inputs())

    assert context["annual_financial_summary"] == {}


def test_financial_trend_rejects_unsupported_inference_terms_only() -> None:
    terms = ("不断提升", "持续扩张", "导致", "由于", "资本开支", "营运资金", "显著", "大幅")
    for term in terms:
        invalid = json.loads(VALID_REPORT_DRAFT)
        invalid["financial_trend"] = f"财务趋势包含{term}。"
        passed, code = validate_report_draft(
            SimpleNamespace(raw=json.dumps(invalid, ensure_ascii=False))
        )
        assert passed is False
        assert code == "report_draft_unsupported_inference"

        for field in REPORT_DRAFT_FIELDS:
            if field == "financial_trend":
                continue
            unaffected = json.loads(VALID_REPORT_DRAFT)
            unaffected[field] = (
                f"本文不构成投资建议，{term}。"
                if field == "non_investment_disclaimer"
                else f"该字段包含{term}。"
            )
            passed, _ = validate_report_draft(
                SimpleNamespace(raw=json.dumps(unaffected, ensure_ascii=False))
            )
            assert passed is True


def test_renderer_places_chart_reasoning_after_each_matching_chart() -> None:
    draft = build_deterministic_report_draft()
    draft.company_quality = (
        "我们可以看到公司质量关系。这说明经营表现需要结合现金流验证。"
        "由此判断公司质量需要结合已验证事实。"
    )
    draft.financial_trend = (
        "我们可以看到现金流关系。这说明利润获得现金支持。"
        "由此判断现金创造能力需要持续验证。"
    )
    draft.historical_valuation = (
        "我们可以看到估值关系。这说明市场预期较高。"
        "由此判断后续表现更依赖基本面兑现。"
    )

    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        draft,
    )
    chart_positions = [
        match.start() for match in re.finditer("data:image/png;base64,", report)
    ]
    assert len(chart_positions) == 3
    assert "现金流关系" not in report
    assert "公司质量关系" not in report
    assert "估值关系" not in report
    assert "整体增长但存在波动" in report
    assert "**图表推导：**" not in report


def test_renderer_keeps_chart_reasoning_fields_without_visuals(monkeypatch) -> None:
    monkeypatch.setattr(
        "stockcrewai.reporting.renderer.build_report_visuals",
        lambda **_: {},
    )
    draft = build_deterministic_report_draft()
    draft.company_quality = "我们可以看到公司质量关系。这说明经营表现需要结合现金流验证。由此判断公司质量需要结合已验证事实。"
    draft.financial_trend = "我们可以看到现金流关系。这说明利润获得现金支持。由此判断现金创造能力需要持续验证。"
    draft.historical_valuation = "我们可以看到估值关系。这说明市场预期较高。由此判断后续表现更依赖基本面兑现。"

    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        draft,
    )

    assert "根据上图" not in report
    assert "**图表推导：**" not in report
    assert draft.company_quality not in report
    assert draft.financial_trend not in report
    assert draft.historical_valuation not in report


def test_chart_reasoning_uses_data_interpretation_when_current_percentile_missing() -> None:
    inputs = _reader_focused_inputs()
    historical = inputs["historical_valuation"]
    assert isinstance(historical, dict)
    historical.pop("current_percentile")
    context = build_report_context(**inputs)

    chart_context = build_narrative_context(context)["chart_context"]
    assert chart_context["historical_pe"] == {
        "available": False,
        "observations": [],
    }

    report = render_validated_report(context, parse_report_draft(VALID_REPORT_DRAFT))
    historical_section = _strict_lite_sections(report)["5"]

    assert "data:image/png;base64," in historical_section
    assert "以下数据用于相对自身历史估值比较" in historical_section
    assert "重新评估条件" not in historical_section
    assert "根据上图" not in historical_section
    assert "图表推导" not in historical_section


def test_markdown_renderer_keeps_sections_terms_visuals_and_fixed_hash() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )

    assert [
        line for line in report.splitlines() if line.startswith("## ")
    ] == [
        "## 0. 封面与研究元数据",
        "## 1. 一页结论",
        "## 2. 公司与研究范围",
        "## 3. 最新经营状态",
        "## 4. 历史经营与财务质量",
        "## 5. 估值",
        "## 6. 主要风险与监控条件",
        "## 7. 综合判断与重新评估条件",
        "## 8. 数据来源、方法与技术附录",
        "## 9. 非投资建议声明",
    ]
    assert "- **相对自身历史估值：** 偏高" in report
    assert "- **市场隐含预期：** 低" in report
    assert "P/E（市盈率）" in report
    assert "FCF Yield（自由现金流收益率）" in report
    assert "TTM（过去十二个月）" in report
    assert "DCF（现金流折现）" in report
    assert report.count("data:image/png;base64,") == 3
    report_without_images = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "", report)
    assert "999" not in report_without_images
    assert "完整财年起止：FY2021（2021-01-01）至 FY2025（2025-12-31）" in report


def test_strict_lite_annual_financial_table_and_section_order() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    headings = [
        "## 0. 封面与研究元数据",
        "## 1. 一页结论",
        "## 2. 公司与研究范围",
        "## 3. 最新经营状态",
        "## 4. 历史经营与财务质量",
        "## 5. 估值",
        "## 6. 主要风险与监控条件",
        "## 7. 综合判断与重新评估条件",
        "## 8. 数据来源、方法与技术附录",
    ]

    assert [report.index(value) for value in headings] == sorted(
        report.index(value) for value in headings
    )
    chapter_four = _strict_lite_sections(report)["4"]
    assert "| 公司名称 |" in report
    assert "| 指标 | FY2021 | FY2022 | FY2023 | FY2024 | FY2025 |" in report
    for label in ("收入 CAGR", "净利润 CAGR", "FCF CAGR"):
        assert chapter_four.count(label) == 1
    assert "TTM 数据与完整财年数据期间不同" in report
    assert "status=ready" not in report.split("## 8. 数据来源", 1)[0]


def test_strict_lite_has_only_numbered_top_level_sections() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )

    assert [
        line for line in report.splitlines() if line.startswith("## ")
    ] == [
        "## 0. 封面与研究元数据",
        "## 1. 一页结论",
        "## 2. 公司与研究范围",
        "## 3. 最新经营状态",
        "## 4. 历史经营与财务质量",
        "## 5. 估值",
        "## 6. 主要风险与监控条件",
        "## 7. 综合判断与重新评估条件",
        "## 8. 数据来源、方法与技术附录",
        "## 9. 非投资建议声明",
    ]


def test_strict_lite_omits_missing_metadata_and_hides_empty_annual_rows() -> None:
    inputs = _reader_focused_inputs()
    company = inputs["company"]
    assert isinstance(company, dict)
    inputs["company"] = {"name": company["name"]}
    context = build_report_context(**inputs)
    annual = context["annual_financial_history"]
    assert isinstance(annual, dict)
    periods = annual["periods"]
    assert isinstance(periods, list)
    for period in periods:
        period.pop("net_income", None)
        period.pop("capex", None)
    periods[0].pop("revenue", None)

    report = render_validated_report(context, parse_report_draft(VALID_REPORT_DRAFT))
    metadata_section = report.split("## 0. 封面与研究元数据", 1)[1].split(
        "## 1. 一页结论", 1
    )[0]
    annual_section = report.split("## 4. 历史经营与财务质量", 1)[1].split(
        "## 5. 估值", 1
    )[0]

    assert "| 公司名称 | Apple Inc. |" in metadata_section
    assert "| 股票代码 |" not in metadata_section
    assert "| 研究期限 |" not in metadata_section
    assert "| 研究 Profile |" not in metadata_section
    assert "| 净利润 |" not in annual_section
    assert "| 资本开支 |" not in annual_section
    assert "| 营业收入 |" in annual_section
    assert "数据不足" in annual_section
    assert "不可用" not in annual_section


def test_strict_lite_separates_market_timestamp_and_period_bases() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    annual_section = report.split("## 4. 历史经营与财务质量", 1)[1].split(
        "## 5. 估值", 1
    )[0]
    latest_section = report.split("## 3. 最新经营状态", 1)[1].split(
        "## 4. 历史经营与财务质量", 1
    )[0]
    valuation_section = report.split("## 5. 估值", 1)[1].split(
        "## 6. 主要风险与监控条件", 1
    )[0]

    assert "| 指标 | FY2021 | FY2022 | FY2023 | FY2024 | FY2025 |" in annual_section
    assert "YTD" not in annual_section
    assert "财年年初至今累计（YTD）" in latest_section
    assert "TTM 财务规模（已验证）" in latest_section
    assert "市场价格" in valuation_section
    assert "截至 2026-08-06T15:30:00Z" in valuation_section


def test_strict_lite_content_stays_in_explicit_chapter_boundaries() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    sections = _strict_lite_sections(report)

    chapter_zero = sections["0"]
    assert "| 市场价格 |" in chapter_zero
    assert "2026-08-06T15:30:00Z" in chapter_zero

    chapter_one = sections["1"]
    assert chapter_one.count("- **") == 4

    chapter_two = sections["2"]
    assert "研究范围" in chapter_two
    for forbidden in ("图 1", "利润率", "TTM", "五年财务表", "FY2021"):
        assert forbidden not in chapter_two

    chapter_three = sections["3"]
    assert "财年年初至今累计（YTD）" in chapter_three
    assert "TTM 财务规模（已验证）" in chapter_three
    assert "图 1" in chapter_three
    assert "图 2" not in chapter_three

    chapter_four = sections["4"]
    assert "五年财务表" in chapter_four
    assert "完整财年起止" in chapter_four
    assert "图 2" in chapter_four
    for forbidden in ("YTD", "TTM", "图 1"):
        assert forbidden not in chapter_four

    chapter_five = sections["5"]
    assert "图 3" in chapter_five
    assert "反向 DCF" in chapter_five
    assert "重新评估条件" not in chapter_five

    chapter_seven = sections["7"]
    assert chapter_seven.count("### 重新评估条件") == 1
    assert report.count("### 重新评估条件") == 1
    assert "**图表推导：**" not in report


def test_markdown_renderer_puts_company_identity_in_title() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )

    assert report.startswith("# 投资研究报告：Apple Inc.（AAPL）\n")


def test_execution_summary_hides_audit_metadata_and_moves_it_to_method_section() -> None:
    inputs = _reader_focused_inputs()
    inputs["policy_context"] = {
        "profile": {
            "issuer_profile": "standard_operating",
            "security_profile": "common_stock",
            "reporting_profile": "us_gaap",
        },
        "coverage_level": "complete",
        "policy_version": "policy-v3",
    }

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    sections = _strict_lite_sections(report)
    execution_summary = sections["1"]
    method_section = sections["8"]

    assert "status=" not in execution_summary
    assert "Profile：" not in execution_summary
    assert "Policy version：" not in execution_summary
    assert "complete" not in execution_summary
    assert "high_valuation" not in execution_summary
    assert "### 方法与审计元数据" in method_section
    assert "ready" in method_section
    assert "standard_operating" in method_section
    assert "policy-v3" in method_section
    assert "high_valuation" in method_section


def test_financial_trend_uses_annual_trend_and_keeps_ttm_fcf_list() -> None:
    inputs = _reader_focused_inputs()
    source_metadata = inputs["source_metadata"]
    assert isinstance(source_metadata, dict)
    inputs["source_metadata"] = {
        **source_metadata,
        "facts": {
            **source_metadata["facts"],  # type: ignore[index]
            "ordinary_fcf": {
                "evidence_id": "ev_ordinary_fcf",
                "period_end": "2025-12-31",
                "source_reference": "sec:test-ordinary-fcf",
            },
        },
    }
    inputs["calculations"] = [
        *inputs["calculations"],  # type: ignore[index]
        {
            "calculation_id": "calc_ordinary_fcf",
            "formula_id": "free_cash_flow",
            "display_result": "10000000000",
            "unit": "USD",
            "status": "available",
            "validation_status": "valid",
            "input_evidence_ids": ["ev_ordinary_fcf"],
        },
    ]

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    financial_trend = _strict_lite_sections(report)["3"]

    assert "TTM 财务规模（已验证）" in financial_trend
    assert "自由现金流：220.00 亿美元" in financial_trend
    assert "营业利润：" not in financial_trend
    assert "100.00 亿美元" not in financial_trend


@pytest.mark.parametrize(
    ("raw_value", "unit"),
    (("22000000000", "USD"), ("22000", "million USD"), ("22", "billion USD")),
)
def test_renderer_formats_ttm_and_reverse_dcf_amounts_using_record_units(
    raw_value: str, unit: str
) -> None:
    inputs = _reader_focused_inputs()
    ttm = inputs["ttm"]
    reverse_dcf = inputs["reverse_dcf"]
    assert isinstance(ttm, dict)
    assert isinstance(reverse_dcf, dict)
    ttm_metrics = ttm["metrics"]
    assert isinstance(ttm_metrics, list)
    ttm["metrics"] = [
        {
            **metric,
            **(
                {"raw_result": raw_value, "unit": unit}
                if metric.get("metric_id") == "free_cash_flow"
                else {}
            ),
        }
        for metric in ttm_metrics
    ]
    inputs["reverse_dcf"] = {
        **reverse_dcf,
        "base_fcf": raw_value,
        "base_fcf_unit": unit,
        "unit": unit,
    }

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    sections = _strict_lite_sections(report)
    trend = sections["3"]
    reverse_section = sections["5"]

    assert "自由现金流：220.00 亿美元" in trend
    assert "| 基础自由现金流（TTM，模型起点） | 220.00 亿美元 |" in reverse_section
    assert "| 历史 FY FCF CAGR | 49.53% |" in reverse_section
    assert "| 隐含增长率 | 11.00% |" in reverse_section
    assert "| 差值（百分点） | -38.53 个百分点 |" in reverse_section
    assert "方向性对照" in reverse_section


def test_share_dilution_body_matches_visual_adjustment_basis_boundary() -> None:
    split_inputs = _reader_focused_inputs()
    split_calculations = split_inputs["calculations"]
    assert isinstance(split_calculations, list)
    split_inputs["calculations"] = [
        {
            **calculation,
            **(
                {"adjustment_basis": "split_adjusted"}
                if calculation.get("formula_id") == "share_dilution"
                else {}
            ),
        }
        for calculation in split_calculations
    ]

    split_report = render_validated_report(
        build_report_context(**split_inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    trend = _strict_lite_sections(split_report)["3"]
    assert "股份稀释率（拆分调整）：-2.00%" in trend

    unsupported_inputs = deepcopy(split_inputs)
    unsupported_calculations = unsupported_inputs["calculations"]
    assert isinstance(unsupported_calculations, list)
    unsupported_inputs["calculations"] = [
        {
            **calculation,
            **(
                {"adjustment_basis": "total_return_adjusted"}
                if calculation.get("formula_id") == "share_dilution"
                else {}
            ),
        }
        for calculation in unsupported_calculations
    ]

    unsupported_report = render_validated_report(
        build_report_context(**unsupported_inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    unsupported_trend = _strict_lite_sections(unsupported_report)["3"]
    assert "股份稀释率" not in unsupported_trend


def test_risk_sources_require_exact_sec_hostname() -> None:
    inputs = _reader_focused_inputs()
    claims = inputs["validated_claims"]
    source_metadata = inputs["source_metadata"]
    assert isinstance(claims, list)
    assert isinstance(source_metadata, dict)
    inputs["validated_claims"] = [
        *claims,
        {
            "claim_id": "claim_risk_valid_url",
            "category": "risk",
            "statement": "合法 SEC 来源风险",
            "evidence_ids": ["ev_risk_valid_url"],
            "calculation_ids": [],
            "confidence": 0.8,
        },
        {
            "claim_id": "claim_risk_spoofed_url",
            "category": "risk",
            "statement": "伪造 SEC 来源风险",
            "evidence_ids": ["ev_risk_spoofed_url"],
            "calculation_ids": [],
            "confidence": 0.8,
        },
    ]
    inputs["source_metadata"] = {
        **source_metadata,
        "risk_filings": [
            {
                "evidence_id": "ev_risk_valid_url",
                "source_reference": "https://www.sec.gov/Archives/valid.txt",
            },
            {
                "evidence_id": "ev_risk_spoofed_url",
                "source_reference": "https://sec.gov.evil.example/Archives/fake.txt",
            },
        ],
    }

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    risk_section = _strict_lite_sections(report)["6"]

    assert "来源：https://www.sec.gov/Archives/valid.txt" in risk_section
    assert "sec.gov.evil.example" not in risk_section
    assert "伪造 SEC 来源风险（来源：" not in risk_section


def test_report_title_is_single_line_plain_text_for_company_identity() -> None:
    inputs = _reader_focused_inputs()
    inputs["company"] = {
        "name": "Acme\n# *Holdings* [A]",
        "ticker": "AC\nME`_",
    }

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )

    assert report.splitlines()[0] == "# 投资研究报告：Acme Holdings A（AC ME）"
    assert "\n# *Holdings*" not in report


def test_execution_summary_explains_verified_valuation_relationships() -> None:
    inputs = _reader_focused_inputs()
    historical = inputs["historical_valuation"]
    assert isinstance(historical, dict)
    inputs["historical_valuation"] = {**historical, "current_percentile": "72.5"}

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    execution_summary = _strict_lite_sections(report)["1"]

    assert execution_summary.count("- **") == 4
    assert "- **经营质量：** 强" in execution_summary
    assert "- **相对自身历史估值：** 偏高" in execution_summary
    assert "- **市场隐含预期：** 低" in execution_summary
    assert "- **研究动作：** 加入观察名单并跟踪关键指标" in execution_summary


def test_report_uses_compact_reader_facing_copy_without_duplicate_audit_prose() -> None:
    """防止报告重新退化成重复的系统日志式文案。"""
    inputs = _reader_focused_inputs()
    historical = inputs["historical_valuation"]
    assert isinstance(historical, dict)
    inputs["historical_valuation"] = {**historical, "current_percentile": "72.5"}

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    execution_summary = _strict_lite_sections(report)["1"]

    assert "- **经营质量：** 强" in execution_summary
    assert "- **相对自身历史估值：** 偏高" in execution_summary
    assert "- **市场隐含预期：** 低" in execution_summary
    assert "- **研究动作：** 加入观察名单并跟踪关键指标" in execution_summary
    assert execution_summary.count("- **") == 4
    assert "总体判断：" not in execution_summary
    assert "结论仅基于已验证数据和 Claim" not in execution_summary
    assert "数字已由规范化指标展示。" not in report
    assert report.count("## 9. 非投资建议声明") == 1
    assert "本文不构成任何投资建议。" not in report


def test_summary_turns_verified_valuation_into_observation_conditions() -> None:
    """摘要应回答估值位置及何时重新评估，而非重复宽泛标签。"""
    inputs = _reader_focused_inputs()
    historical = inputs["historical_valuation"]
    assert isinstance(historical, dict)
    inputs["historical_valuation"] = {**historical, "current_percentile": "72.5"}

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    summary = _strict_lite_sections(report)["1"]

    assert summary.count("- **") == 4
    assert "相对自身历史估值" in summary
    assert "研究动作" in summary
    assert "当前 P/E" not in summary
    assert "重新评估条件" not in summary


def test_execution_summary_omits_unavailable_valuation_relationships() -> None:
    inputs = deepcopy(_reader_focused_inputs())
    historical = inputs["historical_valuation"]
    reverse_dcf = inputs["reverse_dcf"]
    assert isinstance(historical, dict)
    assert isinstance(reverse_dcf, dict)
    inputs["historical_valuation"] = {
        key: value
        for key, value in historical.items()
        if key not in {"current_value", "five_year_median", "current_percentile"}
    }
    inputs["reverse_dcf"] = {
        key: value for key, value in reverse_dcf.items() if key != "implied_growth"
    }

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    execution_summary = _strict_lite_sections(report)["1"]

    assert "当前 P/E" not in execution_summary
    assert "隐含增长率" not in execution_summary


def test_risks_are_capped_and_visible_claims_show_existing_sec_sources() -> None:
    inputs = _reader_focused_inputs()
    claims = inputs["validated_claims"]
    assert isinstance(claims, list)
    risk_claims = [
        {
            "claim_id": f"claim_risk_{index}",
            "category": "risk",
            "statement": (
                f"关税风险陈述 {index}" if index == 1 else f"风险陈述 {index}"
            ),
            "evidence_ids": [f"ev_risk_{index}"],
            "calculation_ids": [],
            "confidence": 0.8,
        }
        for index in range(1, 8)
    ]
    inputs["validated_claims"] = [*claims, *risk_claims]
    source_metadata = inputs["source_metadata"]
    assert isinstance(source_metadata, dict)
    inputs["source_metadata"] = {
        **source_metadata,
        "risk_filings": [
            {
                "evidence_id": f"ev_risk_{index}",
                "source_reference": f"sec:test-risk-{index}",
            }
            for index in range(1, 8)
        ],
    }

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    risk_section = _strict_lite_sections(report)["6"]
    main_risks, appendix = risk_section.split("### 风险附录", 1)

    assert "主要风险叙述来自已验证 Claim。" not in risk_section
    for index in range(1, 4):
        assert f"风险陈述 {index}" in main_risks
    for index in range(4, 8):
        assert f"风险陈述 {index}" not in main_risks
        assert f"风险陈述 {index}" in appendix
    for index in range(1, 8):
        assert f"来源：sec:test-risk-{index}" in risk_section
    assert "影响路径：成本上升可能压低毛利率并推高库存压力" in risk_section
    assert "观察项：毛利率、库存周转与关税披露" in risk_section
    for index in range(2, 8):
        assert "影响路径：需结合SEC风险更新判断财务影响" in risk_section
        assert "观察项：SEC风险更新和对应财务指标" in risk_section


@pytest.mark.parametrize(
    ("statement", "expected_path"),
    [
        ("蜂窝网络运营商/信用风险", "需结合SEC风险更新判断财务影响"),
        ("AI 网络安全风险", "事件与监管要求可能增加安全及合规费用"),
        ("AI 算力投入风险", "投入需求可能推高资本开支并影响毛利率"),
        ("宏观经济与供应链风险", "需求变化可能影响收入增长与利润率"),
        ("关税与贸易限制风险", "成本上升可能压低毛利率并推高库存压力"),
        ("关键组件、有限来源与供应中断风险", "成本上升可能压低毛利率并推高库存压力"),
    ],
)
def test_risk_impact_paths_use_specific_keyword_priority(
    statement: str, expected_path: str
) -> None:
    markdown = _risk_claim_markdown(
        [{"category": "risk", "statement": statement}],
    )

    assert f"影响路径：{expected_path}" in markdown


def test_risks_filter_non_displayable_claims_before_main_cap() -> None:
    inputs = _reader_focused_inputs()
    claims = inputs["validated_claims"]
    assert isinstance(claims, list)
    risk_claims = [
        {
            "claim_id": "claim_risk_numeric",
            "category": "risk",
            "statement": "风险数字为 12%。",
            "evidence_ids": ["ev_risk_numeric"],
            "calculation_ids": ["calc_risk_numeric"],
            "confidence": 0.8,
        },
        *[
            {
                "claim_id": f"claim_risk_text_{index}",
                "category": "risk",
                "statement": f"可展示风险陈述 {index}",
                "evidence_ids": [f"ev_risk_text_{index}"],
                "calculation_ids": [],
                "confidence": 0.8,
            }
            for index in range(1, 7)
        ],
    ]
    inputs["validated_claims"] = [*claims, *risk_claims]
    source_metadata = inputs["source_metadata"]
    assert isinstance(source_metadata, dict)
    inputs["source_metadata"] = {
        **source_metadata,
        "risk_filings": [
            {
                "evidence_id": evidence_id,
                "source_reference": f"sec:test-{evidence_id}",
            }
            for evidence_id in [
                "ev_risk_numeric",
                *(f"ev_risk_text_{index}" for index in range(1, 7)),
            ]
        ],
    }

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    risk_section = _strict_lite_sections(report)["6"]
    main_risks, appendix = risk_section.split("### 风险附录", 1)

    assert "风险数字为 12%。" not in risk_section
    for index in range(1, 4):
        assert f"可展示风险陈述 {index}" in main_risks
    for index in range(4, 7):
        assert f"可展示风险陈述 {index}" not in main_risks
        assert f"可展示风险陈述 {index}" in appendix
    assert "<details>" in appendix
    assert "<summary>展开查看其余风险" in appendix


def test_context_summary_uses_deterministic_conclusion_not_draft_summary() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    execution_summary = _strict_lite_sections(report)["1"]

    assert "研究范围由已验证输入限定。" not in execution_summary
    assert "结论仅基于已验证数据和 Claim；缺失输入不作推断。" not in execution_summary
    assert execution_summary.count("- **") == 4
    assert "总体判断" not in execution_summary
    assert "风险等级" not in execution_summary
    assert "行动参考" not in execution_summary
    assert "status=" not in execution_summary
    assert "Profile：" not in execution_summary
    assert "Policy version：" not in execution_summary


def test_legacy_summary_moves_status_and_rule_to_method_section() -> None:
    inputs = _canonical_context_inputs()
    verdict = {
        **inputs["deterministic_verdict"],  # type: ignore[index]
        "overall_rating": "expensive",
        "risk_level": "medium",
        "triggered_rules": ["high_valuation"],
    }

    report = render_validated_report(
        inputs["validated_claims"],
        verdict,
        inputs["valuation"],
        inputs["historical_valuation"],
        inputs["reverse_dcf"],
        inputs["source_metadata"],
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    execution_summary = report.split("## 公司质量", 1)[0]
    method_section = report.split("## 数据来源与方法", 1)[1].split(
        "## 非投资建议声明", 1
    )[0]

    assert "status=" not in execution_summary
    assert "触发规则：" not in execution_summary
    assert "研究范围由已验证输入限定。" not in execution_summary
    assert "结论仅基于已验证数据和 Claim；缺失输入不作推断。" not in execution_summary
    assert execution_summary.count("- **") == 4
    assert "经营质量：** 数据不足" in execution_summary
    assert "市场隐含预期：** 数据不足" in execution_summary
    assert "研究动作：** 等待补充证据" in execution_summary
    assert "### 方法与审计元数据" in method_section
    assert "status=ready" in method_section
    assert "触发规则代码：high_valuation" in method_section
    assert "市场隐含预期" in method_section


def test_legacy_summary_omits_unavailable_valuation_relationships() -> None:
    inputs = _canonical_context_inputs()
    historical = inputs["historical_valuation"]
    reverse_dcf = inputs["reverse_dcf"]
    assert isinstance(historical, dict)
    assert isinstance(reverse_dcf, dict)
    inputs["historical_valuation"] = {
        key: value
        for key, value in historical.items()
        if key not in {"current_value", "five_year_median", "current_percentile"}
    }
    inputs["reverse_dcf"] = {
        key: value for key, value in reverse_dcf.items() if key != "implied_growth"
    }

    report = render_validated_report(
        inputs["validated_claims"],
        inputs["deterministic_verdict"],
        inputs["valuation"],
        inputs["historical_valuation"],
        inputs["reverse_dcf"],
        inputs["source_metadata"],
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    execution_summary = report.split("## 公司质量", 1)[0]

    assert "当前 P/E" not in execution_summary
    assert "隐含增长率" not in execution_summary


def test_first_chart_guidance_describes_three_semantic_panels() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )

    assert "三个面板分别展示" in report
    assert "增长与资本配置" in report
    assert "盈利能力" in report
    assert "现金流质量" in report


def test_visuals_keep_three_keys_png_data_uris_without_pixel_snapshots() -> None:
    visuals = build_report_visuals(
        financial_metrics=_financial_metrics(),
        ttm_metrics=_ttm_metrics(),
        annual_financial_history=_annual_financial_history(),
        historical_payload=_historical_payload(),
    )

    assert set(visuals) == {
        "financial_kpis",
        "annual_financial_trend",
        "historical_pe",
    }
    for uri in visuals.values():
        assert uri.startswith("data:image/png;base64,")
        raw = base64.b64decode(uri.split(",", 1)[1])
        assert raw.startswith(b"\x89PNG\r\n\x1a\n")


def test_strict_lite_chart_captions_are_standardized_and_complete() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )

    assert report.count("data:image/png;base64,") == 3
    assert "图 1：最新经营质量" in report
    assert "图 2：五年核心财务趋势指数" in report
    assert "基期=100" in report
    assert "图 3：五年历史 P/E" in report
    assert "研究问题：" in report
    assert "限制与反证：" in report
    assert "期间：FY（as_of=2025-12-31）" in report
    assert "单位：百分比（%）" in report
    assert "来源：sec:test-revenue" in report
    assert "截止：2025-12-31" in report
    assert "观察：收入同比保持正增长" in report
    assert "投资含义：用于判断经营质量是否值得继续跟踪" in report
    assert "限制与反证：指标为已验证期间的口径比较" in report
    assert "最新可用财务期间" not in report


def test_strict_lite_charts_follow_caption_order() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )

    assert report.index("图 1：最新经营质量") < report.index(
        "图 2：五年核心财务趋势指数"
    ) < report.index("图 3：五年历史 P/E")


def test_rendered_report_validator_allows_advice_only_in_disclaimer() -> None:
    report = "确定性状态：status=ready\n## 公司质量\n公司事实。\n## 非投资建议声明\n本文不构成买入建议。\n"

    assert validate_rendered_report(report, "ready")[0] is True
    numbered = report.replace("## 非投资建议声明", "## 9. 非投资建议声明")
    assert validate_rendered_report(numbered, "ready")[0] is True
    comment_only = numbered.replace(
        "## 9. 非投资建议声明", "<!-- ## 9. 非投资建议声明 -->"
    )
    assert validate_rendered_report(comment_only, "ready")[0] is False
    contaminated = report.replace("公司事实。", "公司建议买入。")
    assert validate_rendered_report(contaminated, "ready")[0] is False


def test_execution_summary_keeps_only_four_deterministic_conclusions() -> None:
    inputs = _reader_focused_inputs()
    claims = inputs["validated_claims"]
    assert isinstance(claims, list)
    inputs["validated_claims"] = [
        *claims,
        {
            "claim_id": "claim_verified_risk",
            "category": "risk",
            "statement": "申报文件已识别经营风险。",
            "evidence_ids": ["ev_revenue"],
            "calculation_ids": [],
            "confidence": 0.9,
        },
    ]

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    summary = _strict_lite_sections(report)["1"]

    assert summary.count("- **") == 4
    assert "风险状态：" not in summary
    assert "风险等级：中等风险" not in report
    assert "风险水平：中等风险" not in summary


def test_execution_summary_does_not_invent_risk_level_without_risk_claim() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    summary = _strict_lite_sections(report)["1"]

    assert "风险等级：中等风险" not in report
    assert "风险水平：中等风险" not in summary
    assert "风险状态：" not in summary


@pytest.mark.parametrize(
    ("overall_rating", "expected_status"),
    (
        ("attractive", "估值吸引"),
        ("reasonable", "估值合理"),
        ("watchlist", "关注风险"),
        ("expensive", "估值偏贵"),
    ),
)
def test_research_status_comes_only_from_verdict_rating(
    overall_rating: str, expected_status: str
) -> None:
    inputs = _reader_focused_inputs()
    inputs["deterministic_verdict"] = {
        **inputs["deterministic_verdict"],  # type: ignore[index]
        "overall_rating": overall_rating,
    }

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    summary = _strict_lite_sections(report)["1"]

    assert summary.count("- **") == 4
    assert "- **研究动作：** 加入观察名单并跟踪关键指标" in summary
    assert expected_status not in summary


def test_unknown_verdict_rating_does_not_guess_research_status() -> None:
    inputs = _reader_focused_inputs()
    inputs["deterministic_verdict"] = {
        **inputs["deterministic_verdict"],  # type: ignore[index]
        "overall_rating": "not_a_rating",
    }

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    summary = _strict_lite_sections(report)["1"]

    assert summary.count("- **") == 4
    assert "研究动作" in summary


def test_reverse_dcf_summary_uses_model_years_and_cagr_language() -> None:
    inputs = _reader_focused_inputs()
    reverse_dcf = inputs["reverse_dcf"]
    assert isinstance(reverse_dcf, dict)
    inputs["reverse_dcf"] = {**reverse_dcf, "forecast_years": 10}

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    sections = _strict_lite_sections(report)
    summary = sections["1"]
    reverse_section = sections["5"]

    assert summary.count("- **") == 4
    assert "未来 10 年自由现金流年复合增长要求" in reverse_section
    assert "11.00%" in reverse_section
    assert "TTM 自由现金流增长接近" not in report
    assert "下一期" not in report


def test_reverse_dcf_omits_missing_model_year() -> None:
    inputs = _reader_focused_inputs()
    reverse_dcf = inputs["reverse_dcf"]
    assert isinstance(reverse_dcf, dict)
    inputs["reverse_dcf"] = {
        key: value for key, value in reverse_dcf.items() if key != "forecast_years"
    }

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    reverse_section = _strict_lite_sections(report)["5"]

    assert "预测年数 | 不可用 年" not in reverse_section


def test_summary_distinguishes_request_horizon_from_dcf_horizon() -> None:
    inputs = _reader_focused_inputs()
    company = inputs["company"]
    reverse_dcf = inputs["reverse_dcf"]
    assert isinstance(company, dict)
    assert isinstance(reverse_dcf, dict)
    inputs["company"] = {**company, "horizon": "3 年"}
    inputs["reverse_dcf"] = {**reverse_dcf, "forecast_years": 10}

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    summary = _strict_lite_sections(report)["1"]

    assert summary.count("- **") == 4
    assert "用户关注期限是 3 年" not in summary
    assert "10 年反向 DCF 只是长期估值参照，不是该请求期限的预测" not in summary


def test_first_financial_chart_has_ytd_comparison_and_ttm_basis_note() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )

    assert "截至 2025-12-31 的财年年初至今累计（YTD）" in report
    assert "股份变化是同比时点比较" in report
    assert "后文 TTM 是最近十二个月" in report
    assert "九个月" not in report


def test_sources_method_states_quarterly_data_may_be_unaudited() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    method_section = _strict_lite_sections(report)["8"]

    assert "SEC 申报中的年度及季度数据" in method_section
    assert "已验证计算和市场数据" in method_section
    assert "季度数据可能未经审计" in method_section
    assert "季度数据经审计" not in method_section


def test_sources_method_ignores_llm_audit_claim() -> None:
    draft_payload = json.loads(VALID_REPORT_DRAFT)
    draft_payload["sources_and_method"] = "本报告使用经审计财务数据。"
    draft = parse_report_draft(json.dumps(draft_payload, ensure_ascii=False))

    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        draft,
    )
    method_section = _strict_lite_sections(report)["8"]

    assert "本报告使用经审计财务数据" not in method_section
    assert "经审计财务数据" not in method_section
    assert _SOURCE_METHOD_NOTE in method_section


def test_strict_lite_risk_monitoring_table_caps_main_rows_and_escapes_cells() -> None:
    inputs = _reader_focused_inputs()
    claims = inputs["validated_claims"]
    source_metadata = inputs["source_metadata"]
    assert isinstance(claims, list)
    assert isinstance(source_metadata, dict)
    risk_claims = [
        {
            "claim_id": f"claim_strict_risk_{index}",
            "category": "risk",
            "statement": statement,
            "evidence_ids": [f"ev_strict_risk_{index}"],
            "calculation_ids": [],
            "confidence": 0.8,
        }
        for index, statement in enumerate(
            (
                "风险一 | 第一行\n第二行",
                "风险二",
                "风险三",
                "风险四",
            ),
            start=1,
        )
    ]
    inputs["validated_claims"] = [*claims, *risk_claims]
    inputs["source_metadata"] = {
        **source_metadata,
        "risk_filings": [
            {
                "evidence_id": f"ev_strict_risk_{index}",
                "source_reference": (
                    f"https://www.sec.gov/Archives/edgar/data/1/risk-{index}.html"
                ),
            }
            for index in range(1, 5)
        ],
    }

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    risk_section = report.split("## 6. 主要风险与监控条件", 1)[1].split(
        "## 7. 综合判断", 1
    )[0]
    main_risks, appendix = risk_section.split("### 风险附录", 1)

    assert "| 风险 | 影响路径 | 监控指标 | 来源 |" in risk_section
    assert risk_section.count("<tr>") == 0
    assert "风险一 \\| 第一行<br>第二行" in main_risks
    assert "风险二" in main_risks
    assert "风险三" in main_risks
    assert "风险四" not in main_risks
    assert "风险四" in appendix
    assert "https://www.sec.gov/Archives/edgar/data/1/risk-1.html" in main_risks


def test_strict_lite_conclusion_uses_four_blocks_and_keeps_advice_outside_body() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )

    assert "## 7. 综合判断与重新评估条件" in report
    assert "## 8. 数据来源、方法与技术附录" in report
    assert "## 9. 非投资建议声明" in report
    conclusion = report.split("## 7. 综合判断与重新评估条件", 1)[1].split(
        "## 8. 数据来源、方法与技术附录", 1
    )[0]
    assert "### 已验证事实" in conclusion
    assert "### 确定性比较" in conclusion
    assert "### 确定性判断" in conclusion
    assert "### 重新评估条件" in conclusion
    assert "### 方法与审计元数据" not in conclusion
    assert "### 判断规则" not in conclusion

    report_body = report.split("## 9. 非投资建议声明", 1)[0]
    for advice in ("买入", "卖出", "持有"):
        assert advice not in report_body
