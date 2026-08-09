from __future__ import annotations

import ast
import inspect
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from stockcrewai.tools.filing_section_search_tool import (
    FilingSectionSearchTool,
    FilingSectionSearchToolInput,
)
from stockcrewai.tools.quant_summary_tool import (
    QuantSummaryTool,
    QuantSummaryToolInput,
)
from stockcrewai.tools.validated_calculation_tool import (
    ValidatedCalculationTool,
    ValidatedCalculationToolInput,
)
from stockcrewai.tools.validated_evidence_tool import (
    ValidatedEvidenceTool,
    ValidatedEvidenceToolInput,
)


class FakeEvidenceStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.responses: dict[str, Any] = {}

    def query_validated_evidence(
        self, metric_ids: list[str], periods: list[str], limit: int
    ) -> Any:
        self.calls.append(("query_validated_evidence", (metric_ids, periods, limit)))
        return self.responses["query_validated_evidence"]

    def get_validated_calculations(self, calculation_ids: list[str]) -> Any:
        self.calls.append(("get_validated_calculations", (calculation_ids,)))
        return self.responses["get_validated_calculations"]

    def search_validated_filing_sections(
        self, query: str, forms: list[str], limit: int
    ) -> Any:
        self.calls.append(("search_validated_filing_sections", (query, forms, limit)))
        return self.responses["search_validated_filing_sections"]

    def get_quant_summary(self, factor_ids: list[str]) -> Any:
        self.calls.append(("get_quant_summary", (factor_ids,)))
        return self.responses["get_quant_summary"]


def _success_responses() -> dict[str, Any]:
    as_of = datetime(2026, 8, 10, 1, 2, 3, tzinfo=timezone.utc)
    return {
        "query_validated_evidence": {
            "status": "ok",
            "records": [
                {
                    "evidence_id": "ev_revenue_2025",
                    "source": "fixture:revenue",
                    "as_of": as_of,
                    "validation_status": "valid",
                    "value": Decimal("123.45"),
                }
            ],
        },
        "get_validated_calculations": {
            "status": "ok",
            "records": [
                {
                    "calculation_id": "calc_margin_2025",
                    "source": "fixture:margin",
                    "as_of": as_of,
                    "validation_status": "valid",
                    "result": Decimal("0.25"),
                }
            ],
        },
        "search_validated_filing_sections": {
            "status": "ok",
            "records": [
                {
                    "evidence_id": "ev_filing_10k_2025",
                    "source": "fixture:10-k",
                    "filed_at": date(2026, 2, 1),
                    "validation_status": "valid",
                    "content_role": "data",
                }
            ],
        },
        "get_quant_summary": {
            "status": "ok",
            "records": [
                {
                    "factor_id": "quality_roe",
                    "source": "fixture:quant",
                    "as_of": as_of,
                    "validation_status": "valid",
                    "normalized_value": Decimal("0.75"),
                }
            ],
        },
    }


TOOL_CASES = (
    (
        ValidatedEvidenceTool,
        ValidatedEvidenceToolInput,
        "query_validated_evidence",
        {"metric_ids": [" revenue "], "periods": ["FY2025"], "limit": 3},
        ("query_validated_evidence", (["revenue"], ["FY2025"], 3)),
    ),
    (
        ValidatedCalculationTool,
        ValidatedCalculationToolInput,
        "get_validated_calculations",
        {"calculation_ids": [" calc_margin_2025 "]},
        ("get_validated_calculations", (["calc_margin_2025"],)),
    ),
    (
        FilingSectionSearchTool,
        FilingSectionSearchToolInput,
        "search_validated_filing_sections",
        {"query": " risk factors ", "forms": [" 10-K "], "limit": 2},
        ("search_validated_filing_sections", ("risk factors", ["10-K"], 2)),
    ),
    (
        QuantSummaryTool,
        QuantSummaryToolInput,
        "get_quant_summary",
        {"factor_ids": [" quality_roe "]},
        ("get_quant_summary", (["quality_roe"],)),
    ),
)


@pytest.mark.parametrize(
    ("tool_cls", "schema", "method_name", "kwargs", "expected_call"), TOOL_CASES
)
def test_tool_schema_validates_and_delegates_once(
    tool_cls: type[Any],
    schema: type[Any],
    method_name: str,
    kwargs: dict[str, Any],
    expected_call: tuple[str, tuple[Any, ...]],
) -> None:
    store = FakeEvidenceStore()
    store.responses = _success_responses()
    tool = tool_cls(evidence_store=store)

    assert tool.args_schema is schema
    result = tool.run(**kwargs)

    assert store.calls == [expected_call]
    assert result["status"] == store.responses[method_name]["status"]


@pytest.mark.parametrize("tool_cls", [case[0] for case in TOOL_CASES])
def test_tools_require_explicit_evidence_store(tool_cls: type[Any]) -> None:
    with pytest.raises(TypeError):
        tool_cls()


@pytest.mark.parametrize(
    ("schema", "valid", "invalid"),
    [
        (
            ValidatedEvidenceToolInput,
            {"metric_ids": ["revenue"], "periods": [], "limit": 1},
            {"metric_ids": [" "], "periods": [], "limit": 0},
        ),
        (
            ValidatedCalculationToolInput,
            {"calculation_ids": ["calc_margin"]},
            {"calculation_ids": [" "]},
        ),
        (
            FilingSectionSearchToolInput,
            {"query": "risk", "forms": [], "limit": 1},
            {"query": " ", "forms": [], "limit": 1},
        ),
        (
            QuantSummaryToolInput,
            {"factor_ids": ["quality_roe"]},
            {"factor_ids": []},
        ),
    ],
)
def test_args_schemas_have_deterministic_validation(
    schema: type[Any], valid: dict[str, Any], invalid: dict[str, Any]
) -> None:
    parsed = schema.model_validate(valid)
    assert parsed.model_dump()

    with pytest.raises(ValidationError):
        schema.model_validate(invalid)

    for field in schema.model_fields.values():
        assert field.description


@pytest.mark.parametrize("tool_cls", [case[0] for case in TOOL_CASES])
@pytest.mark.parametrize("reason_code", ["unknown_id", "result_not_in_allowlist"])
def test_unknown_and_allowlist_results_preserve_stable_reason_code(
    tool_cls: type[Any], reason_code: str
) -> None:
    store = FakeEvidenceStore()
    store.responses = {
        method_name: {
            "status": "rejected",
            "reason_code": reason_code,
            "records": [],
        }
        for _, _, method_name, _, _ in TOOL_CASES
    }
    tool_case = next(case for case in TOOL_CASES if case[0] is tool_cls)
    tool = tool_cls(evidence_store=store)

    result = tool.run(**tool_case[3])

    assert result["status"] == "rejected"
    assert result["reason_code"] == reason_code
    assert store.calls == [tool_case[4]]


@pytest.mark.parametrize("tool_cls", [case[0] for case in TOOL_CASES])
def test_success_results_are_json_safe_and_keep_audit_fields(tool_cls: type[Any]) -> None:
    store = FakeEvidenceStore()
    store.responses = _success_responses()
    tool_case = next(case for case in TOOL_CASES if case[0] is tool_cls)
    tool = tool_cls(evidence_store=store)

    result = tool.run(**tool_case[3])
    encoded = json.dumps(result, allow_nan=False)

    assert encoded
    item = result["records"][0]
    assert any(field in item for field in ("evidence_id", "calculation_id", "factor_id"))
    assert item["source"]
    assert item.get("as_of") or item.get("filed_at")
    assert item["validation_status"] == "valid"
    assert isinstance(item.get("as_of", item.get("filed_at")), str)


def test_adapters_do_not_import_network_or_calculation_modules() -> None:
    modules = [
        "stockcrewai.tools.validated_evidence_tool",
        "stockcrewai.tools.validated_calculation_tool",
        "stockcrewai.tools.filing_section_search_tool",
        "stockcrewai.tools.quant_summary_tool",
    ]
    forbidden_modules = {"requests", "httpx", "urllib", "edgar", "yfinance"}
    for module_name in modules:
        module = __import__(module_name, fromlist=["*"])
        tree = ast.parse(inspect.getsource(module))
        imported_names = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_names.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        assert imported_names.isdisjoint(forbidden_modules)
