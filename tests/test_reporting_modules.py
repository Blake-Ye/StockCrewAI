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
    build_deterministic_report_draft,
    build_narrative_context,
    render_validated_report,
)
from stockcrewai.reporting.validator import (
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
            "evidence_ids": [f"ev_{metric_id}"],
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
    assert _sha256_json(context) == "a81ff33edee2f7208e21f601f131d95060ca1875ba196dc9a4a50f43d8791128"


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
    assert _sha256_json(narrative) == "e25f06b4b11cd5316357866f3e515a1daf3a7fdc8f5991da8b0d9c35665f7116"


def test_chart_context_exposes_number_free_observations() -> None:
    narrative = build_narrative_context(build_report_context(**_reader_focused_inputs()))
    chart_context = narrative["chart_context"]

    assert tuple(chart_context) == ("financial_kpis", "ttm_scale", "historical_pe")
    assert chart_context["financial_kpis"]["available"] is True
    assert "收入同比保持正增长" in chart_context["financial_kpis"]["observations"]
    assert chart_context["ttm_scale"]["available"] is True
    assert "经营现金流高于净利润" in chart_context["ttm_scale"]["observations"]
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

    missing_ttm = deepcopy(_reader_focused_inputs())
    ttm = missing_ttm["ttm"]
    assert isinstance(ttm, dict)
    ttm_metrics = ttm["metrics"]
    assert isinstance(ttm_metrics, list)
    ttm["metrics"] = [
        metric for metric in ttm_metrics if metric.get("metric_id") != "operating_cash_flow"
    ]
    ttm_chart_context = build_narrative_context(
        build_report_context(**missing_ttm)
    )["chart_context"]
    assert ttm_chart_context["ttm_scale"] == {
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
    (("validation_status", "invalid"), ("period_basis", "FY")),
)
def test_chart_context_requires_valid_ttm_period(field: str, invalid_value: str) -> None:
    inputs = _reader_focused_inputs()
    ttm = inputs["ttm"]
    assert isinstance(ttm, dict)
    ttm_metrics = ttm["metrics"]
    assert isinstance(ttm_metrics, list)
    target = next(metric for metric in ttm_metrics if metric["metric_id"] == "net_income")
    target[field] = invalid_value

    chart_context = build_narrative_context(build_report_context(**inputs))["chart_context"]

    assert chart_context["ttm_scale"] == {
        "available": False,
        "observations": [],
    }


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
    assert chart_positions[0] < report.index("公司质量关系")
    assert chart_positions[1] < report.index("现金流关系")
    assert chart_positions[2] < report.index("估值关系")
    execution_summary = report.split("## 公司质量", 1)[0]
    assert "data:image/png;base64," not in execution_summary
    for marker in ("公司质量关系", "现金流关系", "估值关系"):
        assert report.count(marker) == 1
    for draft_text in (
        draft.company_quality,
        draft.financial_trend,
        draft.historical_valuation,
    ):
        assert f"**图表推导：** 根据上图及其对应的已验证数据，{draft_text}" in report


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
    for draft_text in (
        draft.company_quality,
        draft.financial_trend,
        draft.historical_valuation,
    ):
        assert f"**数据解读：** 根据已验证数据，{draft_text}" in report


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
    historical_section = report.split("## 历史估值", 1)[1].split("## 反向 DCF", 1)[0]

    assert "data:image/png;base64," in historical_section
    assert "**数据解读：** 根据已验证数据，" in historical_section
    assert "根据上图" not in historical_section
    assert "图表推导" not in historical_section


def test_markdown_renderer_keeps_sections_terms_visuals_and_fixed_hash() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )

    for heading in (
        "执行摘要",
        "公司质量",
        "财务趋势",
        "当前估值",
        "历史估值",
        "反向 DCF",
        "主要风险",
        "数据来源与方法",
        "非投资建议声明",
    ):
        assert f"## {heading}" in report
    assert "- **结论：** 当前估值高于过去五年中位水平" in report
    assert "P/E（市盈率）" in report
    assert "FCF Yield（自由现金流收益率）" in report
    assert "TTM（过去十二个月）" in report
    assert "DCF（现金流折现）" in report
    assert report.count("data:image/png;base64,") == 3
    report_without_images = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "", report)
    assert "999" not in report_without_images
    assert _sha256_json(report) == "a318e1e00d650c42b30217693667574bbbcf7bf540d6d35bf7043281a0e552ac"


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
    execution_summary = report.split("## 公司质量", 1)[0]
    method_section = report.split("## 数据来源与方法", 1)[1].split(
        "## 非投资建议声明", 1
    )[0]

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


def test_financial_trend_uses_ttm_fcf_instead_of_ordinary_period_fcf() -> None:
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
    financial_trend = report.split("## 财务趋势", 1)[1].split("## 当前估值", 1)[0]

    assert "TTM 财务规模（已验证）" in financial_trend
    assert "自由现金流：220.00 亿美元" in financial_trend
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
    trend = report.split("## 财务趋势", 1)[1].split("## 当前估值", 1)[0]
    reverse_section = report.split("## 反向 DCF", 1)[1].split("## 主要风险", 1)[0]

    assert "自由现金流：220.00 亿美元" in trend
    assert "| 基础自由现金流（TTM） | 220.00 亿美元 |" in reverse_section


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
    trend = split_report.split("## 财务趋势", 1)[1].split("## 当前估值", 1)[0]
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
    unsupported_trend = unsupported_report.split("## 财务趋势", 1)[1].split(
        "## 当前估值", 1
    )[0]
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
    risk_section = report.split("## 主要风险", 1)[1].split(
        "## 数据来源与方法", 1
    )[0]

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
    execution_summary = report.split("## 公司质量", 1)[0]

    assert "当前 P/E" in execution_summary
    assert "16.10x" in execution_summary
    assert "15.60x" in execution_summary
    assert "高于" in execution_summary
    assert "72.50%" in execution_summary
    assert "反向 DCF" in execution_summary
    assert "11.00%" in execution_summary


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
    execution_summary = report.split("## 公司质量", 1)[0]

    assert "- **结论：** 当前估值高于过去五年中位水平" in execution_summary
    assert "- **风险水平：** 中等风险" not in execution_summary
    assert "- **研究状态：** 估值偏贵" in execution_summary
    assert (
        "当前 P/E 为 16.10x，高于五年中位数 15.60x，"
        "处于过去五年估值样本的 72.50% 分位。"
    ) in execution_summary
    assert "反向 DCF 显示，自由现金流年复合增长要求约为 11.00%。" in execution_summary
    assert "总体判断：" not in execution_summary
    assert "结论仅基于已验证数据和 Claim" not in execution_summary
    assert "数字已由规范化指标展示。" not in report
    assert report.count("## 非投资建议声明") == 1
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
    summary = report.split("## 公司质量", 1)[0]

    assert "- **结论：** 当前估值高于过去五年中位水平，估值并不便宜。" in summary
    assert "- **研究状态：** 估值偏贵" in summary
    assert "- **重新评估条件：**" in summary
    assert "P/E 回落至 15.60x 附近" in summary
    assert "TTM 自由现金流增长接近 11.00%" not in summary
    assert "- **结论：** 估值偏贵" not in summary


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
    execution_summary = report.split("## 公司质量", 1)[0]

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
            "statement": f"风险陈述 {index}",
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
    risk_section = report.split("## 主要风险", 1)[1].split(
        "## 数据来源与方法", 1
    )[0]
    main_risks, appendix = risk_section.split("### 风险附录", 1)

    for index in range(1, 6):
        assert f"风险陈述 {index}" in main_risks
    for index in range(6, 8):
        assert f"风险陈述 {index}" not in main_risks
        assert f"风险陈述 {index}" in appendix
    for index in range(1, 8):
        assert f"来源：sec:test-risk-{index}" in risk_section


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
    risk_section = report.split("## 主要风险", 1)[1].split(
        "## 数据来源与方法", 1
    )[0]
    main_risks, appendix = risk_section.split("### 风险附录", 1)

    assert "风险数字为 12%。" not in risk_section
    for index in range(1, 6):
        assert f"可展示风险陈述 {index}" in main_risks
    assert "可展示风险陈述 6" not in main_risks
    assert "可展示风险陈述 6" in appendix
    assert "<details>" in appendix
    assert "<summary>展开查看其余风险" in appendix


def test_context_summary_uses_deterministic_conclusion_not_draft_summary() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    execution_summary = report.split("## 公司质量", 1)[0]

    assert "研究范围由已验证输入限定。" not in execution_summary
    assert "结论仅基于已验证数据和 Claim；缺失输入不作推断。" not in execution_summary
    assert "- **关键数据：**" in execution_summary


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
    assert "当前 P/E 为 12.00x，高于五年中位数 10.00x" in execution_summary
    assert "处于过去五年估值样本的 72.50% 分位" in execution_summary
    assert "反向 DCF 显示，自由现金流年复合增长要求约为 11.00%。" in execution_summary
    assert "### 方法与审计元数据" in method_section
    assert "status=ready" in method_section
    assert "触发规则代码：high_valuation" in method_section


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


def test_visuals_keep_three_keys_png_data_uris_and_hashes() -> None:
    visuals = build_report_visuals(
        financial_metrics=_financial_metrics(),
        ttm_metrics=_ttm_metrics(),
        historical_payload=_historical_payload(),
    )

    assert set(visuals) == {"financial_kpis", "ttm_scale", "historical_pe"}
    hashes = {}
    for key, uri in visuals.items():
        assert uri.startswith("data:image/png;base64,")
        raw = base64.b64decode(uri.split(",", 1)[1])
        assert raw.startswith(b"\x89PNG\r\n\x1a\n")
        hashes[key] = hashlib.sha256(raw).hexdigest()
    assert hashes == {
        "financial_kpis": "ba7e964c7b332946a288789e002448f14085d854c7936223169471648d38b8cc",
        "ttm_scale": "82305a87783b8c81366bceacb561f029645b28539e52977d2da788f0eabd88b3",
        "historical_pe": "560abfcefb47e995b17facef3ce9913776b18327b0bb5b41820d6199eacafd33",
    }


def test_rendered_report_validator_allows_advice_only_in_disclaimer() -> None:
    report = "确定性状态：status=ready\n## 公司质量\n公司事实。\n## 非投资建议声明\n本文不构成买入建议。\n"

    assert validate_rendered_report(report, "ready")[0] is True
    contaminated = report.replace("公司事实。", "公司建议买入。")
    assert validate_rendered_report(contaminated, "ready")[0] is False


def test_execution_summary_reports_verified_risk_without_unquantified_level() -> None:
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
    summary = report.split("## 公司质量", 1)[0]

    assert "风险状态：已识别风险，但尚未建立量化评分。" in summary
    assert "风险等级：中等风险" not in report
    assert "风险水平：中等风险" not in summary


def test_execution_summary_does_not_invent_risk_level_without_risk_claim() -> None:
    report = render_validated_report(
        build_report_context(**_reader_focused_inputs()),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    summary = report.split("## 公司质量", 1)[0]

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
    summary = report.split("## 公司质量", 1)[0]

    assert f"- **研究状态：** {expected_status}" in summary


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
    summary = report.split("## 公司质量", 1)[0]

    assert "- **研究状态：**" not in summary


def test_reverse_dcf_summary_uses_model_years_and_cagr_language() -> None:
    inputs = _reader_focused_inputs()
    reverse_dcf = inputs["reverse_dcf"]
    assert isinstance(reverse_dcf, dict)
    inputs["reverse_dcf"] = {**reverse_dcf, "forecast_years": 10}

    report = render_validated_report(
        build_report_context(**inputs),
        parse_report_draft(VALID_REPORT_DRAFT),
    )
    summary = report.split("## 公司质量", 1)[0]

    assert "未来 10 年自由现金流年复合增长要求" in summary
    assert "11.00%" in summary
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
    reverse_section = report.split("## 反向 DCF", 1)[1].split("## 主要风险", 1)[0]

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
    summary = report.split("## 公司质量", 1)[0]

    assert "用户关注期限是 3 年" in summary
    assert "10 年反向 DCF 只是长期估值参照，不是该请求期限的预测" in summary


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
    method_section = report.split("## 数据来源与方法", 1)[1].split(
        "## 非投资建议声明", 1
    )[0]

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
    method_section = report.split("## 数据来源与方法", 1)[1].split(
        "## 非投资建议声明", 1
    )[0]

    assert "本报告使用经审计财务数据" not in method_section
    assert "经审计财务数据" not in method_section
    assert _SOURCE_METHOD_NOTE in method_section
