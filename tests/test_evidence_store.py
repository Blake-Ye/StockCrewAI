from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from stockcrewai.services.evidence_store import EvidenceStore


RUN_ID = "run-2026-08-10-a"
OTHER_RUN_ID = "run-2026-08-10-b"


def _records() -> dict[str, Any]:
    return {
        "evidence": [
            {
                "evidence_id": "ev_revenue_2024",
                "run_id": RUN_ID,
                "metric_id": "revenue",
                "period": "FY2024",
                "as_of": datetime(2026, 8, 10, tzinfo=timezone.utc),
                "filed_at": date(2025, 2, 1),
                "period_start": date(2024, 1, 1),
                "period_end": date(2024, 12, 31),
                "source_reference": "fixture:sec/revenue/2024",
                "value": Decimal("90.00"),
                "validation_status": "valid",
            },
            {
                "evidence_id": "ev_revenue_2025",
                "run_id": RUN_ID,
                "metric_id": "revenue",
                "period": "FY2025",
                "as_of": "2026-08-10T00:00:00Z",
                "filed_at": "2026-02-01",
                "period_start": "2025-01-01",
                "period_end": "2025-12-31",
                "source": "fixture:sec/revenue/2025",
                "value": "100.00",
                "validation_status": "valid",
            },
            {
                "evidence_id": "ev_revenue_unvalidated",
                "run_id": RUN_ID,
                "metric_id": "revenue",
                "period": "FY2026",
                "as_of": "2026-08-10T00:00:00Z",
                "period_end": "2026-06-30",
                "source_reference": "fixture:sec/revenue/2026",
                "value": "110.00",
                "validation_status": "unvalidated",
            },
            {
                "evidence_id": "ev_other_run",
                "run_id": OTHER_RUN_ID,
                "metric_id": "revenue",
                "period": "FY2025",
                "as_of": "2026-08-10T00:00:00Z",
                "period_end": "2025-12-31",
                "source_reference": "fixture:other-run/secret-revenue",
                "value": "999.00",
                "validation_status": "valid",
            },
            {
                "evidence_id": "ev_not_allowlisted",
                "run_id": RUN_ID,
                "metric_id": "cash",
                "period": "FY2025",
                "as_of": "2026-08-10T00:00:00Z",
                "period_end": "2025-12-31",
                "source_reference": "fixture:sec/cash/out-of-scope",
                "value": "10.00",
                "validation_status": "valid",
            },
        ],
        "calculations": [
            {
                "calculation_id": "calc_margin_2025",
                "run_id": RUN_ID,
                "as_of": "2026-08-10T00:00:00Z",
                "period": "FY2025",
                "source_reference": "fixture:calculation/margin/2025",
                "result": "0.25",
                "validation_status": "valid",
            },
            {
                "calculation_id": "calc_margin_unvalidated",
                "run_id": RUN_ID,
                "as_of": "2026-08-10T00:00:00Z",
                "period": "FY2026",
                "source_reference": "fixture:calculation/margin/2026",
                "result": "0.30",
                "validation_status": "unvalidated",
            },
            {
                "calculation_id": "calc_not_allowlisted",
                "run_id": RUN_ID,
                "as_of": "2026-08-10T00:00:00Z",
                "period": "FY2025",
                "source_reference": "fixture:calculation/secret",
                "result": "123",
                "validation_status": "valid",
            },
            {
                "calculation_id": "calc_other_run",
                "run_id": OTHER_RUN_ID,
                "as_of": "2026-08-10T00:00:00Z",
                "period": "FY2025",
                "source_reference": "fixture:other-run/secret-calculation",
                "result": "999",
                "validation_status": "valid",
            },
        ],
        "filing_sections": [
            {
                "section_id": "section_risk_10k",
                "run_id": RUN_ID,
                "form": "10-K",
                "filed_at": "2026-02-01",
                "period_end": "2025-12-31",
                "source_reference": "fixture:sec/10-k/2025",
                "section_title": "Item 1A Risk Factors",
                "text": "供应链中断可能影响产品交付。",
                "validation_status": "valid",
            },
            {
                "section_id": "section_risk_10q",
                "run_id": RUN_ID,
                "form": "10-Q",
                "filed_at": "2026-05-01",
                "period_end": "2026-03-31",
                "source_reference": "fixture:sec/10-q/2026q1",
                "section_title": "Risk Factor Updates",
                "text": "客户集中度风险保持不变。",
                "validation_status": "valid",
            },
            {
                "section_id": "section_unvalidated",
                "run_id": RUN_ID,
                "form": "10-K",
                "filed_at": "2026-02-01",
                "period_end": "2025-12-31",
                "source_reference": "fixture:sec/10-k/unvalidated",
                "section_title": "Item 1A Risk Factors",
                "text": "未经验证的文本。",
                "validation_status": "unvalidated",
            },
        ],
        "quant": [
            {
                "factor_id": "quality_roe",
                "run_id": RUN_ID,
                "as_of": "2026-08-10T00:00:00Z",
                "period": "2025-12-31",
                "source_reference": "fixture:quant/quality_roe",
                "raw_value": "0.25",
                "validation_status": "valid",
            },
            {
                "factor_id": "value_fcf_yield_unvalidated",
                "run_id": RUN_ID,
                "as_of": "2026-08-10T00:00:00Z",
                "period": "2025-12-31",
                "source_reference": "fixture:quant/value_fcf_yield",
                "raw_value": "0.10",
                "validation_status": "unvalidated",
            },
        ],
        "allowlist": {
            "evidence_ids": [
                "ev_revenue_2024",
                "ev_revenue_2025",
                "ev_revenue_unvalidated",
            ],
            "calculation_ids": ["calc_margin_2025", "calc_margin_unvalidated"],
            "filing_section_ids": ["section_risk_10k", "section_risk_10q", "section_unvalidated"],
            "factor_ids": ["quality_roe", "value_fcf_yield_unvalidated"],
        },
    }


def _store(records: dict[str, Any] | None = None) -> EvidenceStore:
    return EvidenceStore(records if records is not None else _records(), run_id=RUN_ID)


def test_store_returns_only_allowlisted_validated_evidence_with_stable_limit() -> None:
    store = _store()

    result = store.query_validated_evidence(
        metric_ids=["revenue"], periods=["FY2024", "FY2025"], limit=1
    )

    assert result["status"] == "ok"
    assert result["reason_code"] == "ok"
    assert [record["evidence_id"] for record in result["records"]] == [
        "ev_revenue_2024"
    ]
    record = result["records"][0]
    assert record["source_reference"] == "fixture:sec/revenue/2024"
    assert record["as_of"] == "2026-08-10T00:00:00Z"
    assert record["filed_at"] == "2025-02-01"
    assert record["period_end"] == "2024-12-31"
    assert record["validation_status"] == "valid"
    json.dumps(result, allow_nan=False)

    empty_period_filter = store.query_validated_evidence(
        metric_ids=["revenue"], periods=[], limit=2
    )
    assert [record["evidence_id"] for record in empty_period_filter["records"]] == [
        "ev_revenue_2024",
        "ev_revenue_2025",
    ]


def test_store_does_not_return_partial_results_when_one_metric_filter_is_unknown() -> None:
    result = _store().query_validated_evidence(
        metric_ids=["revenue", "metric_not_in_this_run"], periods=None, limit=10
    )

    assert result == {
        "status": "error",
        "reason_code": "evidence_metric_unknown",
        "records": [],
    }


def test_store_rejects_unknown_other_run_and_non_allowlisted_ids_without_leaking_records() -> None:
    store = _store()

    unknown = store.get_validated_calculations(["calc_missing"])
    other_run = store.get_validated_calculations(["calc_other_run"])
    not_allowlisted = store.get_validated_calculations(["calc_not_allowlisted"])

    assert unknown == {
        "status": "error",
        "reason_code": "calculation_id_unknown",
        "records": [],
    }
    assert other_run == {
        "status": "error",
        "reason_code": "calculation_id_run_mismatch",
        "records": [],
    }
    assert not_allowlisted == {
        "status": "error",
        "reason_code": "calculation_id_not_allowlisted",
        "records": [],
    }
    assert "secret" not in json.dumps(other_run, ensure_ascii=False)


def test_store_never_returns_unvalidated_records() -> None:
    store = _store()

    evidence = store.query_validated_evidence(
        metric_ids=["revenue"], periods=["FY2026"], limit=10
    )
    calculations = store.get_validated_calculations(["calc_margin_unvalidated"])
    quant = store.get_quant_summary(["value_fcf_yield_unvalidated"])
    filing = store.search_validated_filing_sections("未经验证", forms=["10-K"], limit=10)

    assert evidence["records"] == []
    assert evidence["reason_code"] == "evidence_not_validated"
    assert calculations == {
        "status": "error",
        "reason_code": "calculation_not_validated",
        "records": [],
    }
    assert quant == {
        "status": "error",
        "reason_code": "quant_not_validated",
        "records": [],
    }
    assert filing["records"] == []
    assert filing["reason_code"] == "filing_not_validated"


def test_store_searches_filings_by_query_and_form_with_deterministic_limit() -> None:
    store = _store()

    result = store.search_validated_filing_sections("Risk", forms=["10-k", "10-Q"], limit=1)

    assert result["status"] == "ok"
    assert [record["section_id"] for record in result["records"]] == [
        "section_risk_10k"
    ]
    record = result["records"][0]
    assert record["form"] == "10-K"
    assert record["source_reference"] == "fixture:sec/10-k/2025"
    assert record["filed_at"] == "2026-02-01"
    assert record["period_end"] == "2025-12-31"
    assert record["validation_status"] == "valid"
    json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_store_returns_quant_summary_and_validates_limit() -> None:
    store = _store()

    result = store.get_quant_summary(["quality_roe"])
    zero = store.query_validated_evidence(metric_ids=["revenue"], periods=None, limit=0)
    negative = store.query_validated_evidence(metric_ids=None, periods=None, limit=-1)
    too_large = store.query_validated_evidence(metric_ids=None, periods=None, limit=101)

    assert result["status"] == "ok"
    assert result["records"][0]["factor_id"] == "quality_roe"
    assert result["records"][0]["as_of"] == "2026-08-10T00:00:00Z"
    assert result["records"][0]["source_reference"] == "fixture:quant/quality_roe"
    assert result["records"][0]["validation_status"] == "valid"
    assert zero == {"status": "ok", "reason_code": "ok", "records": []}
    assert negative == {
        "status": "error",
        "reason_code": "limit_negative",
        "records": [],
    }
    assert too_large == {
        "status": "error",
        "reason_code": "limit_too_large",
        "records": [],
    }


def test_store_copies_input_and_returns_copies_without_network_or_file_writes() -> None:
    records = _records()
    store = _store(records)

    with patch("urllib.request.urlopen", side_effect=AssertionError("network access")), patch.object(
        Path, "write_text", side_effect=AssertionError("file write")
    ):
        first = store.query_validated_evidence(metric_ids=["revenue"], periods=None, limit=10)

    records["evidence"][0]["source_reference"] = "tampered"
    records["evidence"].append(
        {
            "evidence_id": "ev_injected_after_init",
            "run_id": RUN_ID,
            "metric_id": "revenue",
            "period": "FY2099",
            "as_of": "2099-01-01T00:00:00Z",
            "source_reference": "fixture:tampered",
            "validation_status": "valid",
        }
    )
    first["records"][0]["source_reference"] = "tampered-output"

    second = store.query_validated_evidence(metric_ids=["revenue"], periods=None, limit=10)

    assert second["records"][0]["source_reference"] == "fixture:sec/revenue/2024"
    assert "ev_injected_after_init" not in {
        record["evidence_id"] for record in second["records"]
    }


def test_store_requires_provenance_fields_instead_of_fabricating_them() -> None:
    records = _records()
    records["calculations"].append(
        {
            "calculation_id": "calc_missing_source",
            "run_id": RUN_ID,
            "as_of": "2026-08-10T00:00:00Z",
            "period": "FY2025",
            "result": "1",
            "validation_status": "valid",
        }
    )
    records["allowlist"]["calculation_ids"].append("calc_missing_source")

    with pytest.raises(ValueError, match="source_reference"):
        EvidenceStore(records, run_id=RUN_ID)
