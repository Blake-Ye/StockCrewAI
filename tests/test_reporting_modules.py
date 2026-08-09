from __future__ import annotations

import base64
from datetime import date, timedelta
import hashlib
import json
import re
from types import SimpleNamespace

from stockcrewai.reporting.context import build_report_context
from stockcrewai.reporting.renderer import (
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
            "implied_growth": "0.11",
            "input_evidence_ids": ["ev_market_price", "ev_fcf"],
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
    }
    inputs["ttm"] = {
        "status": "ok",
        "metrics": [
            {
                "metric_id": metric_id,
                "calculation_id": f"calc_{metric_id}_ttm",
                "raw_result": raw_result,
                "unit": "USD",
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
    assert _sha256_json(context) == "15a73aa2d0a9d726f6fc2af2f16ec0993d6d4254ffd00e77fd04bdf8a58abb91"


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
    assert _sha256_json(narrative) == "90a8257dc23fb22622ea652f50e4f63cd560ee9872527ade997249b3a6f4fd06"


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
    assert "总体判断：估值偏贵" in report
    assert "P/E（市盈率）" in report
    assert "FCF Yield（自由现金流收益率）" in report
    assert "TTM（过去十二个月）" in report
    assert "DCF（现金流折现）" in report
    assert report.count("data:image/png;base64,") == 3
    report_without_images = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "", report)
    assert "999" not in report_without_images
    assert _sha256_json(report) == "0031e06c12489977736cf4e9bc61c4dae4b6f05acc763303ca58fa19dc620e28"


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
        "financial_kpis": "9f68282d2f87c3e3b064e78a26da0fd2aed9dd76f0ab5bb7f1b02ba237038175",
        "ttm_scale": "82305a87783b8c81366bceacb561f029645b28539e52977d2da788f0eabd88b3",
        "historical_pe": "46b35af391ddee0f01a2193ba444d8acac5a9f1924b2744e51313e6715b283cb",
    }


def test_rendered_report_validator_allows_advice_only_in_disclaimer() -> None:
    report = "确定性状态：status=ready\n## 公司质量\n公司事实。\n## 非投资建议声明\n本文不构成买入建议。\n"

    assert validate_rendered_report(report, "ready")[0] is True
    contaminated = report.replace("公司事实。", "公司建议买入。")
    assert validate_rendered_report(contaminated, "ready")[0] is False
