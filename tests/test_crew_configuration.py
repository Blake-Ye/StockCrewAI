import json
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


VALID_REPORT = "# 投资研究报告\n\n确定性状态：status=ready\n\n本文不构成投资建议。"
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


def _valid_parser_payload():
    return {
        "company_mention": "苹果公司",
        "company_name_guess": "Apple Inc.",
        "ticker_guess": "AAPL",
        "exchange_guess": "NASDAQ",
        "request_type": "investment_research",
        "investment_horizon": "3 年",
        "requested_focus": ["financial_quality", "valuation", "risk"],
        "language": "zh-CN",
        "confidence": 0.95,
    }


class RecordingCrew:
    def __init__(self, raw=None, task_raws=None):
        self.raw = raw
        self.task_raws = task_raws
        self.inputs = None
        self.kickoff_calls = 0

    def kickoff(self, *, inputs):
        self.kickoff_calls += 1
        self.inputs = inputs
        if self.task_raws is not None:
            return SimpleNamespace(
                raw=self.raw,
                # AnalysisCrew now returns only financial/risk Agent tasks;
                # valuation is appended by the Flow's deterministic builder.
                tasks_output=[
                    SimpleNamespace(raw=raw) for raw in self.task_raws[:2]
                ],
            )
        return SimpleNamespace(raw=self.raw)


def _valid_analysis_outputs():
    return [
        json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "claim_financial_quality",
                        "category": "financial_quality",
                        "statement": "财务质量稳定。",
                        "evidence_ids": ["ev_revenue"],
                        "calculation_ids": ["calc_margin"],
                        "confidence": 0.9,
                    },
                    {
                        "claim_id": "claim_financial_trend",
                        "category": "financial_trend",
                        "statement": "财务趋势可验证。",
                        "evidence_ids": ["ev_revenue"],
                        "calculation_ids": ["calc_margin"],
                        "confidence": 0.8,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "claim_risk",
                        "category": "risk",
                        "statement": "申报文本包含供应链风险。",
                        "evidence_ids": ["ev_filing"],
                        "calculation_ids": [],
                        "confidence": 0.8,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "claim_current_valuation",
                        "category": "current_valuation",
                        "statement": "当前估值输入可验证。",
                        "evidence_ids": ["ev_market_price"],
                        "calculation_ids": ["calc_pe"],
                        "confidence": 0.8,
                    },
                    {
                        "claim_id": "claim_historical_valuation",
                        "category": "historical_valuation",
                        "statement": "历史估值输入可验证。",
                        "evidence_ids": ["ev_history"],
                        "calculation_ids": ["calc_margin"],
                        "confidence": 0.8,
                    },
                    {
                        "claim_id": "claim_reverse_dcf",
                        "category": "reverse_dcf",
                        "statement": "反向 DCF 输入可验证。",
                        "evidence_ids": ["ev_market_price"],
                        "calculation_ids": ["calc_margin"],
                        "confidence": 0.8,
                    }
                ]
            },
            ensure_ascii=False,
        ),
    ]


def _valid_pipeline_fakes():
    from stockcrewai.tools.calculator_tool import CalculationBatch, CalculationResult
    from stockcrewai.tools.edgar_tool import (
        EdgarFact,
        EdgarFilingEvidence,
        EdgarResult,
        EdgarRiskEligibility,
        EdgarRiskSection,
    )
    from stockcrewai.tools.validation_tool import ValidationResult

    parser_result = SimpleNamespace(
        raw=json.dumps(
            _valid_parser_payload(),
            ensure_ascii=False,
        )
    )
    filing = EdgarFilingEvidence(
        evidence_id="ev_filing",
        cik="0000320193",
        form="10-K",
        filed_at="2026-01-01",
        period_end="2025-12-31",
        accession_number="0000320193-26-000001",
        source_reference="sec:test-filing",
        text_source_reference="sec:test-filing-text",
        text="Item 1A Risk Factors\n供应链风险\nItem 1B",
        risk_sections=[
            EdgarRiskSection(
                section_type="10k_item_1a",
                section_title="Item 1A. Risk Factors",
                text="供应链风险",
                complete=True,
            )
        ],
        risk_eligibility=EdgarRiskEligibility(
            evidence_id="ev_filing",
            eligibility="eligible",
            reason_code="eligible_item_1a",
            source_reference="sec:test-filing",
            evidence_kind="item_1a",
            section_title="Item 1A. Risk Factors",
            filed_at="2026-01-01",
        ),
        text_retrieval_status="available",
        text_truncated=False,
    )
    edgar_tool = Mock()
    edgar_tool.run.return_value = EdgarResult(
        status="ok",
        company_name="Apple Inc.",
        ticker="AAPL",
        cik="0000320193",
        facts={
            "revenue": EdgarFact(
                metric_id="revenue",
                evidence_id="ev_revenue",
                value="100",
                period_end="2025-12-31",
                source_reference="sec:test-revenue",
            )
        },
        ttm_inputs={
            "revenue": {
                "latest_fy": EdgarFact(
                    metric_id="revenue",
                    evidence_id="ev_revenue_latest_fy",
                    value="100",
                    unit="USD",
                    period_type="duration",
                    period="FY",
                    period_start="2025-01-01",
                    period_end="2025-12-31",
                    fiscal_year=2025,
                    fiscal_period="FY",
                    source_reference="sec:test-revenue-latest-fy",
                ),
                "current_ytd": EdgarFact(
                    metric_id="revenue",
                    evidence_id="ev_revenue_current_ytd",
                    value="75",
                    unit="USD",
                    period_type="duration",
                    period="Q3",
                    period_start="2026-01-01",
                    period_end="2026-09-30",
                    fiscal_year=2026,
                    fiscal_period="Q3",
                    source_reference="sec:test-revenue-current-ytd",
                ),
                "prior_ytd": EdgarFact(
                    metric_id="revenue",
                    evidence_id="ev_revenue_prior_ytd",
                    value="70",
                    unit="USD",
                    period_type="duration",
                    period="Q3",
                    period_start="2025-01-01",
                    period_end="2025-09-30",
                    fiscal_year=2025,
                    fiscal_period="Q3",
                    source_reference="sec:test-revenue-prior-ytd",
                ),
            }
        },
        filings=[filing],
    )
    calculator_tool = Mock()
    calculator_tool.run.return_value = CalculationBatch(
        status="ok",
        company_name="Apple Inc.",
        ticker="AAPL",
        calculations=[
            CalculationResult(
                calculation_id="calc_margin",
                formula_id="operating_margin",
                input_evidence_ids=["ev_revenue"],
                raw_inputs={"revenue_current": "100"},
                raw_result="0.25",
                status="available",
            )
        ],
    )
    validation_tool = Mock()
    validation_tool.run.return_value = ValidationResult(
        status="valid",
        validated=True,
        company_name="Apple Inc.",
        ticker="AAPL",
        validated_evidence_ids=["ev_revenue"],
        validated_calculation_ids=["calc_margin"],
    )
    market_price_data = {
        "status": "ok",
        "ticker": "AAPL",
        "market_price": "100",
        "market_price_evidence_id": "ev_market_price",
        "price_timestamp": "2026-08-06T15:30:00Z",
        "currency": "USD",
        "source_reference": "market:test",
        "historical_prices": [
            {
                "date": "2025-08-06",
                "price": "90",
                "evidence_id": "ev_history",
            }
        ],
    }
    valuation_tool = Mock()
    valuation_tool.run.return_value = {
        "status": "ok",
        "readiness": "ready",
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
                "input_evidence_ids": ["ev_revenue", "ev_market_price"],
                "price_timestamp": "2026-08-06T15:30:00Z",
                "source_reference": "market:test",
                "status": "available",
                "validation_status": "valid",
            }
        ],
    }
    historical_valuation_tool = Mock()
    historical_valuation_tool.run.return_value = {
        "status": "ok",
        "calculation_id": "calc_historical_pe",
        "current_value": "12",
        "five_year_median": "10",
        "current_percentile": "72.5",
        "selected_dates": ["2026-08-06"],
        "input_evidence_ids": ["ev_history"],
    }
    reverse_dcf_tool = Mock()
    reverse_dcf_tool.run.return_value = {
        "status": "ok",
        "calculation_id": "calc_reverse_dcf_growth",
        "implied_growth": "0.11",
        "input_evidence_ids": ["ev_market_price", "ev_revenue"],
    }
    ttm_builder_tool = Mock()
    ttm_builder_tool.run.return_value = {
        "status": "unavailable",
        "company_name": "Apple Inc.",
        "ticker": "AAPL",
        "metrics": [],
        "warnings": [],
    }
    return parser_result, {
        "edgar_tool": edgar_tool,
        "calculator_tool": calculator_tool,
        "validation_tool": validation_tool,
        "valuation_tool": valuation_tool,
        "market_price_data": market_price_data,
        "historical_valuation_tool": historical_valuation_tool,
        "reverse_dcf_tool": reverse_dcf_tool,
        "ttm_builder_tool": ttm_builder_tool,
    }


def _run_valid_pipeline(
    analysis_crew, report_crew, verdict_value=None
):
    from stockcrewai.main import run_research

    parser_result, tools = _valid_pipeline_fakes()
    if verdict_value is None:
        verdict_value = {"status": "ready"}
    with (
        patch(
            "stockcrewai.pipeline_support.run_request_parser",
            return_value=parser_result,
        ),
        patch(
            "stockcrewai.pipeline_support._deterministic_verdict",
            return_value=verdict_value,
        ) as verdict,
    ):
        result = run_research(
            "分析苹果公司未来 3 年投资价值",
            analysis_crew=analysis_crew,
            report_crew=report_crew,
            **tools,
        )
    return result, verdict


class AnalysisOutputTests(unittest.TestCase):
    def test_deterministic_verdict_uses_defined_policy_when_data_is_incomplete(self):
        from stockcrewai.main import _deterministic_verdict

        verdict = _deterministic_verdict(
            validation_status="valid",
            valuation={"readiness": "not_ready"},
            historical_valuation={"status": "unavailable"},
            reverse_dcf={"status": "unavailable"},
            risk_input={"status": "available"},
        )

        self.assertEqual(verdict["status"], "insufficient_data")
        self.assertTrue(verdict["policy_defined"])
        self.assertEqual(verdict["overall_rating"], "insufficient_data")

    def test_input_requirements_accept_explicit_investment_horizon(self):
        from stockcrewai.main import _input_requirements

        result = _input_requirements({"investment_horizon": "3 年"})

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["provided"], {"investment_horizon": "3 年"})

    def test_filter_rejects_unknown_claim_fields(self):
        from stockcrewai.main import _filter_analysis_claims

        outputs = _valid_analysis_outputs()
        first = json.loads(outputs[0])
        first["claims"][0]["untrusted"] = "must not reach report"
        output = SimpleNamespace(
            tasks_output=[
                SimpleNamespace(raw=json.dumps(first, ensure_ascii=False)),
                SimpleNamespace(raw=outputs[1]),
                SimpleNamespace(raw=outputs[2]),
            ]
        )

        claims, required_data = _filter_analysis_claims(
            output,
            ["ev_revenue"],
            ["ev_filing"],
            ["ev_market_price", "ev_history"],
            ["calc_margin", "calc_pe"],
        )

        self.assertEqual(claims, [])
        self.assertEqual(required_data, ["analysis_output_invalid"])

    def test_filter_rejects_unparseable_output_with_gate_code(self):
        from stockcrewai.main import _filter_analysis_claims

        outputs = _valid_analysis_outputs()
        claims, required_data = _filter_analysis_claims(
            SimpleNamespace(
                tasks_output=[
                    SimpleNamespace(raw="this is not JSON"),
                    SimpleNamespace(raw=outputs[1]),
                    SimpleNamespace(raw=outputs[2]),
                ]
            ),
            ["ev_revenue"],
            ["ev_filing"],
            ["ev_market_price", "ev_history"],
            ["calc_margin", "calc_pe"],
        )

        self.assertEqual(claims, [])
        self.assertEqual(required_data, ["analysis_output_invalid"])

    def test_filter_aggregates_all_analysis_task_outputs(self):
        from stockcrewai.main import _filter_analysis_claims

        output = SimpleNamespace(
            tasks_output=[SimpleNamespace(raw=raw) for raw in _valid_analysis_outputs()]
        )

        claims, required_data = _filter_analysis_claims(
            output,
            ["ev_revenue"],
            ["ev_filing"],
            ["ev_market_price", "ev_history"],
            ["calc_margin", "calc_pe"],
        )

        self.assertEqual(
            [claim["claim_id"] for claim in claims],
            [
                "claim_financial_quality",
                "claim_financial_trend",
                "claim_risk",
                "claim_current_valuation",
                "claim_historical_valuation",
                "claim_reverse_dcf",
            ],
        )
        self.assertEqual(required_data, [])

    def test_filter_rejects_structured_non_claim_payload(self):
        from stockcrewai.main import _filter_analysis_claims

        outputs = _valid_analysis_outputs()
        output = SimpleNamespace(
            tasks_output=[
                SimpleNamespace(
                    raw=json.dumps(
                        {"status": "not_ready", "reason": "unavailable"}
                    )
                ),
                SimpleNamespace(raw=outputs[1]),
                SimpleNamespace(raw=outputs[2]),
            ]
        )

        claims, required_data = _filter_analysis_claims(
            output,
            ["ev_revenue"],
            ["ev_filing"],
            ["ev_market_price", "ev_history"],
            ["calc_margin", "calc_pe"],
        )

        self.assertEqual(claims, [])
        self.assertEqual(required_data, ["analysis_output_invalid"])

    def test_claim_gate_rejects_risk_calculation_ids_with_domain_reason(self):
        from stockcrewai.main import _filter_analysis_claims_with_diagnostics

        outputs = _valid_analysis_outputs()
        risk_payload = json.loads(outputs[1])
        risk_payload["claims"][0]["calculation_ids"] = ["calc_forbidden"]
        result = SimpleNamespace(
            tasks_output=[
                SimpleNamespace(raw=outputs[0]),
                SimpleNamespace(raw=json.dumps(risk_payload, ensure_ascii=False)),
                SimpleNamespace(raw=outputs[2]),
            ]
        )

        claims, required_data, diagnostics = _filter_analysis_claims_with_diagnostics(
            result,
            ["ev_revenue"],
            ["ev_filing"],
            ["ev_market_price", "ev_history"],
            ["calc_margin", "calc_pe"],
        )

        self.assertEqual(claims, [])
        self.assertEqual(required_data, ["analysis_output_invalid"])
        self.assertEqual(diagnostics["domain"], "risk")
        self.assertEqual(diagnostics["reason_code"], "calculation_ids_invalid")

    def test_claim_gate_rejects_valuation_claim_without_calculation_id(self):
        from stockcrewai.main import _filter_analysis_claims_with_diagnostics

        outputs = _valid_analysis_outputs()
        valuation_payload = json.loads(outputs[2])
        valuation_payload["claims"][0]["calculation_ids"] = []
        result = SimpleNamespace(
            tasks_output=[
                SimpleNamespace(raw=outputs[0]),
                SimpleNamespace(raw=outputs[1]),
                SimpleNamespace(
                    raw=json.dumps(valuation_payload, ensure_ascii=False)
                ),
            ]
        )

        claims, required_data, diagnostics = _filter_analysis_claims_with_diagnostics(
            result,
            ["ev_revenue"],
            ["ev_filing"],
            ["ev_market_price", "ev_history"],
            ["calc_margin", "calc_pe"],
        )

        self.assertEqual(claims, [])
        self.assertEqual(required_data, ["analysis_output_invalid"])
        self.assertEqual(diagnostics["domain"], "valuation")
        self.assertEqual(diagnostics["reason_code"], "calculation_ids_invalid")

    def test_claim_gate_rejects_financial_claim_with_unknown_evidence_id(self):
        from stockcrewai.main import _filter_analysis_claims_with_diagnostics

        outputs = _valid_analysis_outputs()
        financial_payload = json.loads(outputs[0])
        financial_payload["claims"][0]["evidence_ids"] = ["ev_unknown"]
        result = SimpleNamespace(
            tasks_output=[
                SimpleNamespace(
                    raw=json.dumps(financial_payload, ensure_ascii=False)
                ),
                SimpleNamespace(raw=outputs[1]),
                SimpleNamespace(raw=outputs[2]),
            ]
        )

        claims, required_data, diagnostics = _filter_analysis_claims_with_diagnostics(
            result,
            ["ev_revenue"],
            ["ev_filing"],
            ["ev_market_price", "ev_history"],
            ["calc_margin", "calc_pe"],
        )

        self.assertEqual(claims, [])
        self.assertEqual(required_data, ["analysis_output_invalid"])
        self.assertEqual(diagnostics["domain"], "financial")
        self.assertEqual(diagnostics["reason_code"], "evidence_ids_invalid")

    def test_research_exposes_typed_auxiliary_results_and_defined_verdict(self):
        from stockcrewai.main import run_research
        from stockcrewai.tools.calculator_tool import CalculationBatch
        from stockcrewai.tools.edgar_tool import EdgarResult
        from stockcrewai.tools.validation_tool import ValidationResult

        parser_result = SimpleNamespace(
            raw=json.dumps(_valid_parser_payload(), ensure_ascii=False)
        )
        edgar_tool = Mock()
        edgar_tool.run.return_value = EdgarResult(
            status="error",
            company_name="Apple Inc.",
            ticker="AAPL",
        )
        calculator_tool = Mock()
        calculator_tool.run.return_value = CalculationBatch(
            status="error", company_name="Apple Inc.", ticker="AAPL"
        )
        validation_tool = Mock()
        validation_tool.run.return_value = ValidationResult(
            status="invalid",
            validated=False,
            company_name="Apple Inc.",
            ticker="AAPL",
        )
        market_price_tool = Mock()
        market_price_tool.run.return_value = {
            "status": "unavailable",
            "ticker": "AAPL",
            "market_price": None,
            "price_timestamp": None,
            "currency": None,
            "source_reference": "https://finance.yahoo.com/quote/AAPL",
        }

        with patch(
            "stockcrewai.pipeline_support.run_request_parser",
            return_value=parser_result,
        ):
            result = run_research(
                "分析苹果公司",
                edgar_tool=edgar_tool,
                calculator_tool=calculator_tool,
                validation_tool=validation_tool,
                market_price_tool=market_price_tool,
            )

        self.assertEqual(result["historical_valuation"]["status"], "unavailable")
        self.assertEqual(result["reverse_dcf"]["status"], "unavailable")
        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "financial_evidence_and_calculations_required",
            result["required_data"],
        )
        self.assertIsNone(result["analysis"])
        self.assertIsNone(result["report"])
        self.assertNotIn("limitations", result)

    def test_validated_state_includes_available_filing_evidence(self):
        from stockcrewai.main import _validated_state
        from stockcrewai.tools.calculator_tool import CalculationBatch
        from stockcrewai.tools.edgar_tool import EdgarFilingEvidence, EdgarResult
        from stockcrewai.tools.validation_tool import ValidationResult

        filing = EdgarFilingEvidence(
            evidence_id="ev_filing_10k_001",
            cik="0000320193",
            form="10-K",
            filed_at="2026-01-01",
            accession_number="0000320193-26-000001",
            source_reference="https://www.sec.gov/Archives/filing.htm",
            text_source_reference="https://www.sec.gov/Archives/filing.htm",
            text="Item 1A Risk Factors",
            text_retrieval_status="available",
        )
        state = _validated_state(
            EdgarResult(
                status="ok",
                company_name="Apple Inc.",
                ticker="AAPL",
                filings=[filing],
            ),
            CalculationBatch(status="ok", company_name="Apple Inc.", ticker="AAPL"),
            ValidationResult(
                status="valid",
                validated=True,
                company_name="Apple Inc.",
                ticker="AAPL",
            ),
        )

        self.assertEqual(state["validated_filing_ids"], ["ev_filing_10k_001"])
        self.assertEqual(state["filings"][0]["evidence_id"], "ev_filing_10k_001")


class EntrypointTests(unittest.TestCase):
    def test_run_research_uses_native_flow_and_preserves_injections(self):
        """验证旧入口只启动 Flow，并完整保留离线注入和 JSON-safe 返回契约。"""
        from stockcrewai.main import run_research

        parser_result, tools = _valid_pipeline_fakes()
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)
        flow = Mock()
        flow.kickoff.return_value = {
            "status": "ok",
            "decimal_value": Decimal("1.25"),
        }

        with (
            patch(
                "stockcrewai.pipeline_support.run_request_parser",
                return_value=parser_result,
            ),
            patch(
                "stockcrewai.main.ResearchFlow",
                create=True,
                return_value=flow,
            ) as flow_factory,
        ):
            result = run_research(
                "分析苹果公司未来 3 年投资价值",
                analysis_crew=analysis_crew,
                report_crew=report_crew,
                **tools,
            )

        flow_factory.assert_called_once_with(
            edgar_tool=tools["edgar_tool"],
            calculator_tool=tools["calculator_tool"],
            validation_tool=tools["validation_tool"],
            valuation_tool=tools["valuation_tool"],
            market_price_data=tools["market_price_data"],
            market_price_tool=tools.get("market_price_tool"),
            analysis_crew=analysis_crew,
            report_crew=report_crew,
            historical_valuation_tool=tools["historical_valuation_tool"],
            reverse_dcf_tool=tools["reverse_dcf_tool"],
            ttm_builder_tool=tools["ttm_builder_tool"],
        )
        flow.kickoff.assert_called_once_with(
            inputs={"request": "分析苹果公司未来 3 年投资价值"}
        )
        self.assertEqual(
            result,
            {"status": "ok", "decimal_value": "1.25"},
        )

    def test_runtime_disables_crewai_task_output_persistence(self):
        from crewai.crew import Crew

        from stockcrewai.main import _configure_crewai_runtime

        private_attribute = Crew.__private_attributes__["_task_output_handler"]
        original_factory = private_attribute.default_factory
        try:
            _configure_crewai_runtime()
            handler = private_attribute.default_factory()
            self.assertTrue(getattr(handler, "persistent", False) is False)
            self.assertEqual(handler.load(), [])
        finally:
            private_attribute.default_factory = original_factory

    def test_console_entrypoint_accepts_request_from_environment(self):
        from stockcrewai.main import main

        request = "分析苹果公司未来 3 年投资价值"
        with patch.dict(os.environ, {"STOCKCREWAI_REQUEST": request}), patch(
            "stockcrewai.main.sys.argv", ["kickoff"]
        ), patch(
            "stockcrewai.main.run_research", return_value={"status": "ok"}
        ) as run_research:
            main()

        run_research.assert_called_once_with(request)

    def test_console_entrypoint_prints_result_and_returns_none(self):
        from stockcrewai.main import main

        with patch(
            "stockcrewai.main.run_research",
            return_value={"status": "ok"},
        ) as run_research:
            with patch("builtins.print") as print_output:
                result = main("分析苹果公司")

        self.assertIsNone(result)
        run_research.assert_called_once_with("分析苹果公司")
        print_output.assert_called_once_with(
            json.dumps({"status": "ok"}, ensure_ascii=False, indent=2)
        )

    def test_uv_console_script_uses_safe_entrypoint(self):
        import tomllib

        project_root = Path(__file__).resolve().parents[1]
        with (project_root / "pyproject.toml").open("rb") as project_file:
            project_config = tomllib.load(project_file)

        self.assertEqual(
            project_config["project"]["scripts"]["kickoff"],
            "stockcrewai.main:kickoff",
        )


class EdgarIntegrationTests(unittest.TestCase):
    def test_research_route_passes_parser_identity_to_edgar_tool(self):
        from stockcrewai.main import run_research
        from stockcrewai.tools.edgar_tool import EdgarResult

        parser_result = SimpleNamespace(
            raw=json.dumps(_valid_parser_payload(), ensure_ascii=False)
        )
        edgar_tool = Mock()
        edgar_tool.run.return_value = EdgarResult(
            status="ok",
            company_name="Apple Inc.",
            ticker="AAPL",
            cik="0000320193",
        )
        market_price_tool = Mock()
        market_price_tool.run.return_value = {
            "status": "unavailable",
            "ticker": "AAPL",
            "market_price": None,
            "price_timestamp": None,
            "currency": None,
            "source_reference": "https://finance.yahoo.com/quote/AAPL",
        }

        with patch(
            "stockcrewai.pipeline_support.run_request_parser",
            return_value=parser_result,
        ):
            result = run_research(
                "分析苹果公司",
                edgar_tool=edgar_tool,
                market_price_tool=market_price_tool,
                analysis_crew=RecordingCrew("claims"),
                report_crew=RecordingCrew("report"),
            )

        edgar_tool.run.assert_called_once_with(
            company_name="Apple Inc.",
            ticker="AAPL",
            include_filing_text=True,
        )
        self.assertEqual(result["parsed_request"]["ticker_guess"], "AAPL")
        self.assertEqual(result["edgar"]["status"], "ok")

    def test_research_route_calculates_and_validates_edgar_facts(self):
        from stockcrewai.main import run_research
        from stockcrewai.tools.edgar_tool import EdgarFact, EdgarResult

        def fact(metric_id, value):
            return EdgarFact(
                metric_id=metric_id,
                evidence_id=f"ev_{metric_id}",
                value=str(value),
                source_reference="https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
            )

        parser_result = SimpleNamespace(
            raw=json.dumps(_valid_parser_payload(), ensure_ascii=False)
        )
        edgar_tool = Mock()
        edgar_tool.run.return_value = EdgarResult(
            status="partial",
            company_name="Apple Inc.",
            ticker="AAPL",
            cik="0000320193",
            facts={
                "revenue": fact("revenue", 100),
                "operating_income": fact("operating_income", 25),
                "net_income": fact("net_income", 20),
                "operating_cash_flow": fact("operating_cash_flow", 30),
                "capex": fact("capex", 10),
                "cash_and_equivalents": fact("cash_and_equivalents", 40),
                "short_term_investments": fact("short_term_investments", 5),
                "short_term_debt": fact("short_term_debt", 3),
                "long_term_debt": fact("long_term_debt", 15),
                "stockholders_equity": fact("stockholders_equity", 60),
                "total_current_assets": fact("total_current_assets", 80),
                "total_current_liabilities": fact("total_current_liabilities", 40),
                "common_shares_outstanding": fact("common_shares_outstanding", 10),
            },
        )
        market_price_tool = Mock()
        market_price_tool.run.return_value = {
            "status": "unavailable",
            "ticker": "AAPL",
            "market_price": None,
            "price_timestamp": None,
            "currency": None,
            "source_reference": "https://finance.yahoo.com/quote/AAPL",
        }

        with patch(
            "stockcrewai.pipeline_support.run_request_parser",
            return_value=parser_result,
        ):
            result = run_research(
                "分析苹果公司",
                edgar_tool=edgar_tool,
                market_price_tool=market_price_tool,
                analysis_crew=RecordingCrew("claims"),
                report_crew=RecordingCrew("report"),
            )

        calculations = result["calculations"]
        by_id = {item["formula_id"]: item for item in calculations["calculations"]}
        self.assertEqual(calculations["status"], "partial")
        self.assertEqual(by_id["operating_margin"]["display_result"], "25.00%")
        self.assertEqual(by_id["free_cash_flow"]["raw_result"], "20")
        self.assertEqual(by_id["revenue_growth"]["status"], "unavailable")
        self.assertEqual(result["validation"]["status"], "valid")
        self.assertIn("ev_revenue", by_id["operating_margin"]["input_evidence_ids"])

    def test_research_runs_reverse_dcf_when_current_inputs_are_validated(self):
        from stockcrewai.main import run_research
        from stockcrewai.tools.calculator_tool import CalculationBatch, CalculationResult
        from stockcrewai.tools.edgar_tool import EdgarFact, EdgarResult
        from stockcrewai.tools.validation_tool import ValidationResult

        parser_result = SimpleNamespace(
            raw=json.dumps(_valid_parser_payload(), ensure_ascii=False)
        )

        def fact(metric_id, value, unit):
            return EdgarFact(
                metric_id=metric_id,
                evidence_id=f"ev_{metric_id}",
                value=str(value),
                unit=unit,
                source_reference=f"sec:test-{metric_id}",
            )

        edgar_tool = Mock()
        edgar_tool.run.return_value = EdgarResult(
            status="partial",
            company_name="Apple Inc.",
            ticker="AAPL",
            facts={
                "operating_cash_flow": fact("operating_cash_flow", 30, "USD"),
                "capex": fact("capex", 10, "USD"),
                "common_shares_outstanding": fact(
                    "common_shares_outstanding", 10, "shares"
                ),
                "earnings_per_share_diluted": fact(
                    "earnings_per_share_diluted", 2, "USD/share"
                ),
            },
        )
        calculator_tool = Mock()
        calculator_tool.run.return_value = CalculationBatch(
            status="partial",
            company_name="Apple Inc.",
            ticker="AAPL",
            calculations=[
                CalculationResult(
                    calculation_id="calc_free_cash_flow",
                    formula_id="free_cash_flow",
                    input_evidence_ids=["ev_operating_cash_flow", "ev_capex"],
                    raw_inputs={"operating_cash_flow": "30", "capex": "10"},
                    raw_result="20",
                    status="available",
                )
            ],
        )
        validation_tool = Mock()
        validation_tool.run.return_value = ValidationResult(
            status="valid",
            validated=True,
            company_name="Apple Inc.",
            ticker="AAPL",
            validated_evidence_ids=[
                "ev_operating_cash_flow",
                "ev_capex",
                "ev_common_shares_outstanding",
                "ev_earnings_per_share_diluted",
            ],
            validated_calculation_ids=["calc_free_cash_flow"],
        )
        market_price_tool = Mock()
        market_price_tool.run.return_value = {
            "status": "ok",
            "ticker": "AAPL",
            "market_price": "100",
            "price_timestamp": "2026-08-06T15:30:00Z",
            "currency": "USD",
            "source_reference": "https://finance.yahoo.com/quote/AAPL",
            "historical_prices": [
                {
                    "date": "2026-08-31",
                    "price": "100",
                    "evidence_id": "ev_market_price_history_AAPL_2026-08",
                }
            ],
        }
        historical_valuation_tool = Mock()
        historical_valuation_tool.run.return_value = {
            "status": "ok",
            "current_value": "12",
            "five_year_median": "10",
            "input_evidence_ids": ["ev_market_price_history_AAPL_2026-08"],
        }
        ttm_builder_tool = Mock()
        ttm_builder_tool.run.return_value = {
            "status": "ok",
            "company_name": "Apple Inc.",
            "ticker": "AAPL",
            "metrics": [
                {
                    "metric_id": "diluted_eps",
                    "calculation_id": "calc_diluted_eps_ttm",
                    "formula_id": "ttm_diluted_eps",
                    "raw_result": "2",
                    "unit": "USD/share",
                    "period_basis": "TTM",
                    "input_evidence_ids": ["ev_earnings_per_share_diluted"],
                    "status": "available",
                    "validation_status": "valid",
                },
                {
                    "metric_id": "free_cash_flow",
                    "calculation_id": "calc_free_cash_flow_ttm",
                    "formula_id": "ttm_free_cash_flow",
                    "raw_result": "20",
                    "unit": "USD",
                    "period_basis": "TTM",
                    "input_evidence_ids": ["ev_operating_cash_flow", "ev_capex"],
                    "status": "available",
                    "validation_status": "valid",
                },
            ],
            "warnings": [],
        }

        with patch(
            "stockcrewai.pipeline_support.run_request_parser",
            return_value=parser_result,
        ):
            result = run_research(
                "分析苹果公司未来 3 年投资价值",
                edgar_tool=edgar_tool,
                calculator_tool=calculator_tool,
                validation_tool=validation_tool,
                market_price_tool=market_price_tool,
                analysis_crew=RecordingCrew("[]"),
                report_crew=RecordingCrew("report"),
                historical_valuation_tool=historical_valuation_tool,
                ttm_builder_tool=ttm_builder_tool,
            )

        self.assertEqual(result["input_requirements"]["status"], "ready")
        self.assertEqual(result["valuation"]["readiness"], "ready")
        self.assertEqual(result["valuation"]["validation_status"], "valid")
        self.assertEqual(result["reverse_dcf"]["status"], "ok")
        self.assertEqual(result["reverse_dcf"]["validation_status"], "valid")
        self.assertEqual(result["reverse_dcf"]["forecast_years"], 10)
        self.assertEqual(result["historical_valuation"]["validation_status"], "valid")
        historical_valuation_tool.run.assert_called_once_with(
            company_name="Apple Inc.",
            ticker="AAPL",
            historical_prices=market_price_tool.run.return_value["historical_prices"],
            financial_snapshots=[],
            as_of="2026-08-06",
        )

    def test_validated_state_runs_analysis_and_report_with_unavailable_risk_and_price(self):
        from stockcrewai.main import run_research
        from stockcrewai.tools.calculator_tool import CalculationBatch, CalculationResult
        from stockcrewai.tools.edgar_tool import EdgarFact, EdgarResult
        from stockcrewai.tools.validation_tool import ValidationResult

        parser_result = SimpleNamespace(
            raw=json.dumps(_valid_parser_payload(), ensure_ascii=False)
        )
        edgar_tool = Mock()
        edgar_tool.run.return_value = EdgarResult(
            status="partial",
            company_name="Apple Inc.",
            ticker="AAPL",
            facts={
                "revenue": EdgarFact(
                    metric_id="revenue",
                    evidence_id="ev_revenue",
                    value="100",
                    source_reference="sec:test-revenue",
                ),
                "unvalidated_fact": EdgarFact(
                    metric_id="unvalidated_fact",
                    evidence_id="ev_unvalidated",
                    value="999",
                    source_reference="sec:test-unvalidated",
                ),
            },
        )
        calculation_result = CalculationBatch(
            status="partial",
            company_name="Apple Inc.",
            ticker="AAPL",
            calculations=[
                CalculationResult(
                    calculation_id="calc_operating_margin",
                    formula_id="operating_margin",
                    input_evidence_ids=["ev_revenue"],
                    raw_inputs={"revenue_current": "100"},
                    raw_result="0.25",
                    status="available",
                ),
                CalculationResult(
                    calculation_id="calc_unvalidated",
                    formula_id="net_margin",
                    raw_result="0.20",
                    status="available",
                ),
            ],
        )
        calculator_tool = Mock()
        calculator_tool.run.return_value = calculation_result
        validation_tool = Mock()
        validation_tool.run.return_value = ValidationResult(
            status="valid",
            validated=True,
            company_name="Apple Inc.",
            ticker="AAPL",
            validated_evidence_ids=["ev_revenue"],
            validated_calculation_ids=["calc_operating_margin"],
            checked_calculation_ids=["calc_operating_margin", "calc_unvalidated"],
        )
        market_price_tool = Mock()
        market_price_tool.run.return_value = {
            "status": "ok",
            "ticker": "AAPL",
            "market_price": "200",
            "price_timestamp": "2026-08-06T15:30:00Z",
            "currency": "USD",
            "source_reference": "https://finance.yahoo.com/quote/AAPL",
        }
        analysis_crew = RecordingCrew(
            json.dumps(
                {
                    "claims": [
                        {
                            "claim_id": "claim_margin",
                            "category": "financial_quality",
                            "statement": "营业利润率为 25%。",
                            "evidence_ids": ["ev_revenue"],
                            "calculation_ids": ["calc_operating_margin"],
                            "confidence": 0.9,
                        },
                        {
                            "claim_id": "claim_rejected",
                            "category": "financial_quality",
                            "statement": "这条引用了未验证证据。",
                            "evidence_ids": ["ev_unvalidated"],
                            "calculation_ids": [],
                            "confidence": 0.5,
                        },
                        "not a claim object",
                    ]
                },
                ensure_ascii=False,
            )
        )
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)

        with patch(
            "stockcrewai.pipeline_support.run_request_parser",
            return_value=parser_result,
        ):
            result = run_research(
                "分析苹果公司",
                edgar_tool=edgar_tool,
                calculator_tool=calculator_tool,
                validation_tool=validation_tool,
                market_price_tool=market_price_tool,
                analysis_crew=analysis_crew,
                report_crew=report_crew,
            )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("risk_evidence_missing", result["required_data"])
        self.assertEqual(analysis_crew.kickoff_calls, 0)
        self.assertEqual(report_crew.kickoff_calls, 0)
        self.assertIsNone(result["analysis"])
        self.assertIsNone(result["report"])
        self.assertNotIn("limitations", result)

    def test_invalid_validation_skips_analysis_and_report(self):
        from stockcrewai.main import run_research
        from stockcrewai.tools.calculator_tool import CalculationBatch
        from stockcrewai.tools.edgar_tool import EdgarResult
        from stockcrewai.tools.validation_tool import ValidationResult

        parser_result = SimpleNamespace(
            raw=json.dumps(_valid_parser_payload(), ensure_ascii=False)
        )
        edgar_tool = Mock()
        edgar_tool.run.return_value = EdgarResult(
            status="error",
            company_name="Apple Inc.",
            ticker="AAPL",
        )
        calculator_tool = Mock()
        calculator_tool.run.return_value = CalculationBatch(
            status="error",
            company_name="Apple Inc.",
            ticker="AAPL",
        )
        validation_tool = Mock()
        validation_tool.run.return_value = ValidationResult(
            status="invalid",
            validated=False,
            company_name="Apple Inc.",
            ticker="AAPL",
        )
        market_price_tool = Mock()
        market_price_tool.run.return_value = {
            "status": "ok",
            "ticker": "AAPL",
            "market_price": "200",
            "price_timestamp": "2026-08-06T15:30:00Z",
            "currency": "USD",
            "source_reference": "https://finance.yahoo.com/quote/AAPL",
        }
        analysis_crew = RecordingCrew("must not run")
        report_crew = RecordingCrew("must not run")

        with patch(
            "stockcrewai.pipeline_support.run_request_parser",
            return_value=parser_result,
        ):
            result = run_research(
                "分析苹果公司",
                edgar_tool=edgar_tool,
                calculator_tool=calculator_tool,
                validation_tool=validation_tool,
                market_price_tool=market_price_tool,
                analysis_crew=analysis_crew,
                report_crew=report_crew,
            )

        self.assertEqual(analysis_crew.kickoff_calls, 0)
        self.assertEqual(report_crew.kickoff_calls, 0)
        self.assertIsNone(result["analysis"])
        self.assertIsNone(result["report"])
        self.assertEqual(result["valuation"]["status"], "not_ready")
        self.assertEqual(result["market_price_data"], market_price_tool.run.return_value)
        market_price_tool.run.assert_called_once_with(ticker="AAPL")
        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "financial_evidence_and_calculations_required",
            result["required_data"],
        )
        self.assertNotIn("limitations", result)


class AnalysisGateTests(unittest.TestCase):
    def test_valuation_allowlist_uses_fixed_calculation_registry(self):
        from stockcrewai.pipeline_support import _valuation_analysis_input

        payload = _valuation_analysis_input(
            {
                "company_name": "Apple Inc.",
                "ticker": "AAPL",
                "facts": {},
                "calculations": [],
                "validated_evidence_ids": [],
                "validated_calculation_ids": [],
            },
            {"calculations": []},
            {
                "status": "ok",
                "calculation_id": "calc_historical_pe",
                "input_evidence_ids": ["ev_history"],
            },
            {
                "status": "ok",
                "calculation_id": "calc_reverse_dcf_growth",
                "input_evidence_ids": ["ev_market_price"],
            },
        )

        self.assertEqual(
            payload["validated_calculation_ids"],
            [
                "calc_fcf_yield",
                "calc_historical_pe",
                "calc_market_capitalization",
                "calc_pe_ratio",
                "calc_reverse_dcf_growth",
            ],
        )

    def test_analysis_crew_receives_only_role_scoped_inputs(self):
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)

        result, verdict = _run_valid_pipeline(analysis_crew, report_crew)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            set(analysis_crew.inputs),
            {
                "financial_analysis_input",
                "risk_analysis_input",
            },
        )
        self.assertNotIn("validated_state", analysis_crew.inputs)
        self.assertNotIn("risk_input", analysis_crew.inputs)
        self.assertNotIn("valuation_result", analysis_crew.inputs)
        self.assertNotIn(
            "risk_sections",
            analysis_crew.inputs["financial_analysis_input"],
        )
        self.assertNotIn(
            "facts",
            analysis_crew.inputs["risk_analysis_input"],
        )
        self.assertEqual(analysis_crew.kickoff_calls, 1)
        self.assertEqual(report_crew.kickoff_calls, 1)
        verdict.assert_called_once()

    def test_claim_gate_risk_claims_supply_auditable_verdict_risk_input(self):
        """Claim Gate 通过的风险 Claim 应成为 Verdict 的确定性风险输入。"""
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)

        result, verdict = _run_valid_pipeline(analysis_crew, report_crew)

        self.assertEqual(result["status"], "ok")
        verdict.assert_called_once()
        risk_input = verdict.call_args.kwargs["risk_input"]
        self.assertEqual(risk_input["status"], "available")
        self.assertEqual(risk_input.get("risk_level"), "medium")
        self.assertEqual(risk_input.get("claim_ids"), ["claim_risk"])
        self.assertEqual(risk_input.get("evidence_ids"), ["ev_filing"])
        self.assertEqual(risk_input.get("policy_version"), "risk_claim_presence_v1")
        json.dumps(risk_input, ensure_ascii=False, allow_nan=False)

    def test_missing_risk_claim_uses_deterministic_disclosure_fallback(self):
        """风险 Claim 两次为空时应使用已验证披露事实继续通过 Gate。"""
        outputs = _valid_analysis_outputs()
        analysis_crew = RecordingCrew(
            task_raws=[outputs[0], json.dumps({"claims": []}), outputs[2]]
        )
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)

        result, verdict = _run_valid_pipeline(analysis_crew, report_crew)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(analysis_crew.kickoff_calls, 2)
        self.assertEqual(report_crew.kickoff_calls, 1)
        verdict.assert_called_once()
        self.assertIn(
            "claim_risk_disclosure_ev_filing",
            {claim["claim_id"] for claim in result["analysis"]},
        )

    def test_missing_risk_sections_blocks_before_analysis_verdict_and_report(self):
        from stockcrewai.main import run_research

        parser_result, tools = _valid_pipeline_fakes()
        tools["edgar_tool"].run.return_value.filings[0].risk_sections = []
        analysis_crew = RecordingCrew("must not run")
        report_crew = RecordingCrew("must not run")
        with (
            patch(
                "stockcrewai.pipeline_support.run_request_parser",
                return_value=parser_result,
            ),
            patch("stockcrewai.pipeline_support._deterministic_verdict") as verdict,
        ):
            result = run_research(
                "分析苹果公司未来 3 年投资价值",
                analysis_crew=analysis_crew,
                report_crew=report_crew,
                **tools,
            )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stage"], "analysis")
        self.assertEqual(result["required_data"], ["risk_evidence_missing"])
        self.assertIsNone(result["analysis"])
        self.assertIsNone(result["report"])
        self.assertEqual(analysis_crew.kickoff_calls, 0)
        self.assertEqual(report_crew.kickoff_calls, 0)
        verdict.assert_not_called()
        self.assertNotIn("analysis_diagnostics", result)
        self._assert_compact_blocked(result)

    def test_invalid_analysis_json_blocks_after_analysis_without_verdict_or_report(
        self,
    ):
        analysis_crew = RecordingCrew(
            task_raws=["not JSON", *_valid_analysis_outputs()[1:]]
        )
        report_crew = RecordingCrew("must not run")

        result, verdict = _run_valid_pipeline(analysis_crew, report_crew)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["required_data"], ["analysis_output_invalid"])
        self.assertEqual(analysis_crew.kickoff_calls, 1)
        self.assertEqual(report_crew.kickoff_calls, 0)
        verdict.assert_not_called()
        self._assert_compact_blocked(result)

    def test_invalid_financial_json_preserves_analysis_diagnostics_and_raw_outputs(
        self,
    ):
        raw_outputs = ["not JSON", *_valid_analysis_outputs()[1:]]
        analysis_crew = RecordingCrew(task_raws=raw_outputs)
        report_crew = RecordingCrew("must not run")

        result, verdict = _run_valid_pipeline(analysis_crew, report_crew)

        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["analysis"])
        self.assertIsNone(result["report"])
        self.assertEqual(result["required_data"], ["analysis_output_invalid"])
        self.assertEqual(result["analysis_diagnostics"]["domain"], "financial")
        self.assertEqual(
            result["analysis_diagnostics"]["reason_code"], "raw_json_invalid"
        )
        diagnostic_outputs = result["analysis_diagnostics"]["raw_task_outputs"]
        self.assertEqual(diagnostic_outputs["financial"], raw_outputs[0])
        self.assertEqual(diagnostic_outputs["risk"], raw_outputs[1])
        valuation_claims = json.loads(diagnostic_outputs["valuation"])["claims"]
        self.assertEqual(
            [claim["category"] for claim in valuation_claims],
            ["current_valuation", "historical_valuation", "reverse_dcf"],
        )
        self.assertEqual(analysis_crew.kickoff_calls, 1)
        self.assertEqual(report_crew.kickoff_calls, 0)
        verdict.assert_not_called()

    def test_risk_claim_with_non_filing_evidence_exposes_evidence_diagnostic(self):
        outputs = _valid_analysis_outputs()
        risk_payload = json.loads(outputs[1])
        risk_payload["claims"][0]["evidence_ids"] = ["ev_revenue"]
        raw_outputs = [
            outputs[0],
            json.dumps(risk_payload, ensure_ascii=False),
            outputs[2],
        ]
        analysis_crew = RecordingCrew(task_raws=raw_outputs)
        report_crew = RecordingCrew("must not run")

        result, verdict = _run_valid_pipeline(analysis_crew, report_crew)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["required_data"], ["analysis_output_invalid"])
        self.assertEqual(result["analysis_diagnostics"]["domain"], "risk")
        self.assertEqual(
            result["analysis_diagnostics"]["reason_code"], "evidence_ids_invalid"
        )
        self.assertEqual(
            result["analysis_diagnostics"]["raw_task_outputs"]["risk"],
            raw_outputs[1],
        )
        self.assertEqual(analysis_crew.kickoff_calls, 1)
        self.assertEqual(report_crew.kickoff_calls, 0)
        verdict.assert_not_called()

    def test_analysis_diagnostics_redacts_secrets_and_preserves_domain_order(self):
        raw_outputs = [
            "DEEPSEEK_API_KEY=top-secret env-secret-value",
            *_valid_analysis_outputs()[1:],
        ]
        analysis_crew = RecordingCrew(task_raws=raw_outputs)
        report_crew = RecordingCrew("must not run")

        with patch.dict(
            os.environ,
            {"TEST_DIAGNOSTIC_SECRET": "env-secret-value"},
        ):
            result, _ = _run_valid_pipeline(analysis_crew, report_crew)

        diagnostics = result["analysis_diagnostics"]
        serialized = json.dumps(diagnostics, ensure_ascii=False)
        self.assertNotIn("top-secret", serialized)
        self.assertNotIn("env-secret-value", serialized)
        self.assertIn("[REDACTED]", serialized)
        self.assertEqual(
            list(diagnostics["raw_task_outputs"]),
            ["financial", "risk", "valuation"],
        )
        self.assertTrue(
            any("\u4e00" <= character <= "\u9fff" for character in diagnostics["reason"])
        )

    def test_analysis_diagnostics_redacts_common_explicit_secret_fields(self):
        raw_outputs = [
            (
                'Key=plain-key "key": "quoted-key" '
                "token=plain-token secret: plain-secret "
                "password=plain-password api_key=plain-api-key"
            ),
            *_valid_analysis_outputs()[1:],
        ]
        analysis_crew = RecordingCrew(task_raws=raw_outputs)
        report_crew = RecordingCrew("must not run")

        result, _ = _run_valid_pipeline(analysis_crew, report_crew)

        serialized = json.dumps(result["analysis_diagnostics"], ensure_ascii=False)
        for secret in (
            "plain-key",
            "quoted-key",
            "plain-token",
            "plain-secret",
            "plain-password",
            "plain-api-key",
        ):
            self.assertNotIn(secret, serialized)
        self.assertEqual(serialized.count("[REDACTED]"), 6)

    def test_risk_claim_cannot_use_financial_evidence(self):
        outputs = _valid_analysis_outputs()
        risk_payload = json.loads(outputs[1])
        risk_payload["claims"][0]["evidence_ids"] = ["ev_revenue"]
        analysis_crew = RecordingCrew(
            task_raws=[
                outputs[0],
                json.dumps(risk_payload, ensure_ascii=False),
                outputs[2],
            ]
        )
        report_crew = RecordingCrew("must not run")

        result, verdict = _run_valid_pipeline(analysis_crew, report_crew)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["required_data"], ["analysis_output_invalid"])
        self.assertEqual(analysis_crew.kickoff_calls, 1)
        self.assertEqual(report_crew.kickoff_calls, 0)
        verdict.assert_not_called()
        self._assert_compact_blocked(result)

    def test_empty_claims_blocks_with_domain_required_data_after_analysis(self):
        outputs = _valid_analysis_outputs()
        analysis_crew = RecordingCrew(
            task_raws=[json.dumps({"claims": []}), *outputs[1:]]
        )
        report_crew = RecordingCrew("must not run")

        result, verdict = _run_valid_pipeline(analysis_crew, report_crew)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["required_data"], ["financial_analysis_claims_required"]
        )
        self.assertEqual(analysis_crew.kickoff_calls, 2)
        self.assertEqual(report_crew.kickoff_calls, 0)
        verdict.assert_not_called()
        self._assert_compact_blocked(result)

    def test_complete_claims_call_verdict_then_report_without_limitations(self):
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)

        result, verdict = _run_valid_pipeline(analysis_crew, report_crew)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["analysis"][0]["category"], "financial_quality")
        self.assertIn("## 执行摘要", result["report"])
        self.assertIn("确定性状态：status=ready", result["report"])
        self.assertIn("财务质量稳定。", result["report"])
        self.assertIn("本文不构成任何投资建议。", result["report"])
        self.assertNotIn("limitations", result)
        self.assertEqual(set(report_crew.inputs), {"narrative_context"})
        self.assertEqual(
            report_crew.inputs["narrative_context"]["verdict"]["status"], "ready"
        )
        self.assertGreater(report_crew.inputs["narrative_context"]["counts"]["metrics"], 0)
        self.assertIn("25.00x", result["report"])
        self.assertNotIn("analysis notice", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("rejected", json.dumps(result, ensure_ascii=False))
        self.assertEqual(analysis_crew.kickoff_calls, 1)
        self.assertEqual(report_crew.kickoff_calls, 1)
        verdict.assert_called_once()

    def test_report_draft_parse_failure_blocks_without_fallback(self):
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        payload = json.loads(VALID_REPORT_DRAFT)
        payload["execution_summary"] = "确定性状态：status=ready。"
        report_crew = RecordingCrew(json.dumps(payload, ensure_ascii=False))

        result, verdict = _run_valid_pipeline(
            analysis_crew,
            report_crew,
            verdict_value={"status": "ready"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stage"], "report")
        self.assertIsNone(result["report"])
        self.assertEqual(result["required_data"], ["report_draft_schema_invalid"])
        self.assertEqual(result["analysis_diagnostics"]["domain"], "report")
        self.assertEqual(
            result["analysis_diagnostics"]["reason_code"],
            "report_draft_schema_invalid",
        )
        self.assertEqual(report_crew.kickoff_calls, 1)
        verdict.assert_called_once()

    def test_report_renderer_preserves_insufficient_data_status(self):
        analysis_crew = RecordingCrew(task_raws=_valid_analysis_outputs())
        report_crew = RecordingCrew(VALID_REPORT_DRAFT)

        result, _ = _run_valid_pipeline(
            analysis_crew,
            report_crew,
            verdict_value={"status": "insufficient_data"},
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["verdict"]["status"], "insufficient_data")
        self.assertIn("确定性状态：status=insufficient_data", result["report"])
        self.assertNotIn("status=ready", result["report"])

    def test_report_task_has_no_limitations_placeholder(self):
        from stockcrewai.crews.report.crew import ReportCrew

        configured_crew = CrewConfigurationTests()._build_crew(ReportCrew)

        self.assertNotIn("{limitations}", configured_crew.tasks[0].description)
        self.assertNotIn("已知局限性", configured_crew.tasks[0].description)

    def _assert_compact_blocked(self, result):
        self.assertNotIn("limitations", result)
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("analysis notice", serialized)
        self.assertNotIn("rejected", serialized)


class RequestParserContractTests(unittest.TestCase):
    def test_parser_guardrail_rejects_missing_extra_wrong_focus_and_bad_confidence(self):
        from stockcrewai.crews.request_parser.crew import (
            validate_parsed_request_output,
        )

        cases = {}
        missing = _valid_parser_payload()
        missing.pop("language")
        cases["missing field"] = missing
        extra = _valid_parser_payload()
        extra["cik"] = "0000320193"
        cases["extra field"] = extra
        wrong_focus = _valid_parser_payload()
        wrong_focus["requested_focus"] = "valuation"
        cases["requested_focus type"] = wrong_focus
        bad_confidence = _valid_parser_payload()
        bad_confidence["confidence"] = 1.1
        cases["confidence range"] = bad_confidence

        for reason, payload in cases.items():
            with self.subTest(reason=reason):
                passed, _ = validate_parsed_request_output(
                    SimpleNamespace(raw=json.dumps(payload, ensure_ascii=False))
                )
                self.assertFalse(passed)

    def test_parser_guardrail_accepts_exact_nine_field_payload(self):
        from stockcrewai.crews.request_parser.crew import (
            validate_parsed_request_output,
        )

        output = SimpleNamespace(
            raw=json.dumps(_valid_parser_payload(), ensure_ascii=False)
        )
        passed, message = validate_parsed_request_output(output)

        self.assertTrue(passed)
        self.assertEqual(message, output.raw)

    def test_parser_task_has_local_guardrail_and_exact_contract_prompt(self):
        from stockcrewai.crews.request_parser.crew import RequestParserCrew

        task = RequestParserCrew().parse_investment_request_task()
        self.assertIsNotNone(task.guardrail)
        self.assertEqual(task.guardrail_max_retries, 2)
        description = RequestParserCrew().tasks_config[
            "parse_investment_request_task"
        ]["description"]
        for field in _valid_parser_payload():
            self.assertIn(field, description)
        self.assertIn("禁止额外字段", description)
        self.assertIn("JSON", description)

    def test_parser_payload_gate_rejects_shape_and_type_drift(self):
        from stockcrewai.pipeline_support import _parser_payload

        for reason, payload in (
            ("missing", {key: value for key, value in _valid_parser_payload().items() if key != "language"}),
            ("extra", {**_valid_parser_payload(), "cik": "0000320193"}),
            ("focus", {**_valid_parser_payload(), "requested_focus": "valuation"}),
            ("confidence", {**_valid_parser_payload(), "confidence": -0.1}),
        ):
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError):
                    _parser_payload(SimpleNamespace(json_dict=payload, raw=""))

    def test_valid_parser_payload_gate_preserves_all_nine_fields(self):
        from stockcrewai.pipeline_support import _parser_payload

        payload = _parser_payload(
            SimpleNamespace(
                json_dict=None,
                raw=json.dumps(_valid_parser_payload(), ensure_ascii=False),
            )
        )

        self.assertEqual(set(payload), set(_valid_parser_payload()))
        self.assertEqual(payload["requested_focus"], ["financial_quality", "valuation", "risk"])


class ReportContractTests(unittest.TestCase):
    def _canonical_context_inputs(self):
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

    def _reader_focused_context_inputs(self):
        inputs = self._canonical_context_inputs()
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
        inputs["calculations"].extend(
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
        )
        start = date(2021, 8, 31)
        series = [
            {
                "date": (start + timedelta(days=30 * index)).isoformat(),
                "pe_ratio": f"{15 + (index % 12) / 10:.2f}",
            }
            for index in range(60)
        ]
        inputs["historical_valuation"]["series"] = series
        inputs["historical_valuation"]["current_date"] = series[-1]["date"]
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

    def test_historical_metric_prefers_current_date_over_selected_dates(self):
        from stockcrewai.crews.report.crew import build_report_context

        inputs = self._canonical_context_inputs()
        inputs["historical_valuation"]["selected_dates"] = [
            "2021-01-31",
            "2026-07-31",
        ]
        inputs["historical_valuation"]["current_date"] = "2026-08-31"

        context = build_report_context(**inputs)
        historical_metrics = [
            metric
            for metric in context["metrics"]
            if metric["section"] == "historical_valuation"
        ]

        self.assertTrue(historical_metrics)
        self.assertEqual(
            {metric["as_of"] for metric in historical_metrics},
            {"2026-08-31"},
        )

    def test_historical_metric_falls_back_to_last_selected_date(self):
        from stockcrewai.crews.report.crew import build_report_context

        inputs = self._canonical_context_inputs()
        inputs["historical_valuation"]["selected_dates"] = [
            "2021-01-31",
            "2026-07-31",
        ]

        context = build_report_context(**inputs)
        historical_metrics = [
            metric
            for metric in context["metrics"]
            if metric["section"] == "historical_valuation"
        ]

        self.assertTrue(historical_metrics)
        self.assertEqual(
            {metric["as_of"] for metric in historical_metrics},
            {"2026-07-31"},
        )

    def test_reader_focused_renderer_injects_verdict_terms_actions_and_visuals(self):
        from stockcrewai.crews.report.crew import (
            build_report_context,
            parse_report_draft,
            render_validated_report,
        )

        inputs = self._reader_focused_context_inputs()
        context = build_report_context(**inputs)
        self.assertEqual(len(context["ttm"]["metrics"]), 5)
        report = render_validated_report(
            context,
            parse_report_draft(VALID_REPORT_DRAFT),
        )

        self.assertIn("总体判断：估值偏贵", report)
        self.assertIn("风险等级：中等风险", report)
        self.assertIn("触发规则：估值偏高规则触发", report)
        self.assertIn("行动参考：等待更高安全边际", report)
        self.assertIn("P/E（市盈率）", report)
        self.assertIn("FCF Yield（自由现金流收益率）", report)
        self.assertIn("TTM（过去十二个月）", report)
        self.assertIn("DCF（现金流折现）", report)
        self.assertIn("反向 DCF（由市场价格倒推隐含增长）", report)
        self.assertIn("读图：柱子高于 0 表示增长/利润率为正；股份变化为负表示股份减少。", report)
        self.assertIn("读图：所有柱子都使用最近十二个月口径，单位为十亿美元，便于比较规模而不是比较利润率。", report)
        self.assertIn("读图：曲线高于中位数表示当前 TTM P/E 高于自身历史常态；最新点用于定位当前估值。", report)
        self.assertEqual(report.count("data:image/png;base64,"), 3)
        self.assertNotIn("无已验证 Claim。", report)
        self.assertNotRegex(report, r"Decimal\(['\"]")
        report_body = report.split("## 非投资建议声明", 1)[0]
        for forbidden in ("买入", "卖出", "持有"):
            self.assertNotIn(forbidden, report_body)

        quality_start = report.index("## 公司质量")
        trend_start = report.index("## 财务趋势")
        quality = report[quality_start:trend_start]
        trend_start = report.index("## 财务趋势")
        valuation_start = report.index("## 当前估值")
        trend = report[trend_start:valuation_start]
        self.assertIn("营业利润率：25.00%", quality)
        self.assertNotIn("营业收入同比增长：20.00%", quality)
        self.assertIn("营业收入同比增长：20.00%", trend)
        self.assertNotIn("营业利润率：25.00%", trend)

    def test_historical_pe_percentiles_render_as_multiples(self):
        from stockcrewai.crews.report.crew import (
            build_report_context,
            parse_report_draft,
            render_validated_report,
        )

        inputs = self._canonical_context_inputs()
        inputs["historical_valuation"].update(
            {"percentile_25": "68.90", "percentile_75": "132.12"}
        )
        report = render_validated_report(
            build_report_context(**inputs),
            parse_report_draft(VALID_REPORT_DRAFT),
        )

        self.assertIn("历史 P/E 二十五分位：68.90x", report)
        self.assertNotIn("历史 P/E 二十五分位：68.90%", report)
        self.assertIn("历史 P/E 七十五分位：132.12x", report)
        self.assertNotIn("历史 P/E 七十五分位：132.12%", report)
        self.assertIn("当前历史百分位：72.50%", report)

    def test_renderer_formats_long_multiple_suffix_to_two_decimals(self):
        from stockcrewai.crews.report.crew import (
            build_report_context,
            parse_report_draft,
            render_validated_report,
        )

        inputs = self._canonical_context_inputs()
        inputs["historical_valuation"]["current_value"] = "1.234567890123456789x"

        report = render_validated_report(
            build_report_context(**inputs),
            parse_report_draft(VALID_REPORT_DRAFT),
        )

        self.assertIn("历史当前 P/E：1.23x", report)
        self.assertNotIn("1.234567890123456789x", report)

    def test_report_context_normalizes_all_metric_sections_and_is_json_safe(self):
        from stockcrewai.crews.report.crew import build_report_context

        inputs = self._canonical_context_inputs()
        context = build_report_context(**inputs)

        self.assertEqual(
            {metric["section"] for metric in context["metrics"]},
            {"financial", "current_valuation", "historical_valuation", "reverse_dcf"},
        )
        required = {
            "metric_id",
            "display_value",
            "unit",
            "as_of",
            "source_reference",
            "evidence_ids",
            "calculation_id",
        }
        for metric in context["metrics"]:
            self.assertTrue(required.issubset(metric))
            self.assertTrue(metric["metric_id"])
            self.assertTrue(metric["display_value"])
            self.assertTrue(metric["unit"])
            self.assertTrue(metric["as_of"])
            self.assertTrue(metric["source_reference"])
            self.assertTrue(metric["evidence_ids"])
            if metric["metric_id"] != "market_price":
                self.assertTrue(metric["calculation_id"])
        json.dumps(context, ensure_ascii=False, allow_nan=False)

    def test_report_context_skips_unvalidated_untraceable_metrics(self):
        from stockcrewai.crews.report.crew import build_report_context

        inputs = self._canonical_context_inputs()
        inputs["calculations"] = [
            {
                "calculation_id": "calc_missing_validation",
                "formula_id": "net_margin",
                "display_result": "10.00%",
                "unit": "ratio",
                "status": "available",
                "input_evidence_ids": ["ev_revenue"],
            },
            {
                "formula_id": "free_cash_flow",
                "display_result": "20",
                "unit": "USD",
                "status": "available",
                "validation_status": "valid",
                "input_evidence_ids": ["ev_fcf"],
            },
        ]
        inputs["valuation"]["calculations"][0]["source_reference"] = None
        inputs["historical_valuation"]["input_evidence_ids"] = []
        inputs["reverse_dcf"]["calculation_id"] = None

        context = build_report_context(**inputs)

        self.assertEqual(context["metrics"], [])

    def test_narrative_context_bounds_nvda_claims_and_preserves_raw_counts(self):
        from stockcrewai.crews.report.crew import (
            build_narrative_context,
            build_report_context,
        )

        inputs = self._canonical_context_inputs()
        inputs["company"] = {
            "name": "NVIDIA Corporation",
            "ticker": "NVDA",
            "horizon": "五年",
        }
        categories = (
            ["financial_quality"] * 4
            + ["financial_trend"] * 4
            + ["current_valuation"] * 3
            + ["historical_valuation"] * 2
            + ["reverse_dcf"] * 2
            + ["risk"] * 2
        )
        inputs["validated_claims"] = [
            {
                "claim_id": f"claim_nvda_{index}",
                "category": category,
                "statement": f"{category} 的已验证叙述。" + "长" * 1800,
                "evidence_ids": ["ev_revenue"],
                "calculation_ids": [],
                "confidence": 0.9,
            }
            for index, category in enumerate(categories)
        ]
        inputs["source_metadata"] = {
            "facts": {
                "raw": {
                    "evidence_id": "ev_raw",
                    "source_reference": "sec:full-source-list",
                    "text": "SEC raw evidence that must not enter narrative context",
                }
            },
            "historical_prices": [{"date": str(index)} for index in range(500)],
            "rejected_claims": [{"statement": "rejected claim must not enter"}],
        }

        context = build_report_context(**inputs)
        narrative = build_narrative_context(context)
        encoded = json.dumps(narrative, ensure_ascii=False, separators=(",", ":"))

        self.assertLessEqual(len(encoded.encode("utf-8")), 24 * 1024)
        self.assertEqual(narrative["company"], "NVIDIA Corporation")
        self.assertEqual(narrative["ticker"], "NVDA")
        self.assertEqual(narrative["horizon"], "五年")
        self.assertEqual(
            list(narrative["accepted_claim_summaries"]),
            ["financial_quality", "financial_trend", "valuation", "risk"],
        )
        self.assertEqual(narrative["counts"]["claims"], 17)
        self.assertEqual(narrative["counts"]["accepted_claims"], 17)
        self.assertEqual(
            [
                len(narrative["accepted_claim_summaries"][key])
                for key in narrative["accepted_claim_summaries"]
            ],
            [4, 4, 7, 2],
        )
        self.assertNotIn("SEC raw evidence", encoded)
        self.assertNotIn("full-source-list", encoded)
        self.assertNotIn("rejected claim", encoded)
        self.assertNotIn("claim_nvda_", encoded)
        self.assertEqual(
            narrative["available_sections"],
            [
                "company_quality",
                "financial_trend",
                "current_valuation",
                "historical_valuation",
                "reverse_dcf",
                "key_risks",
            ],
        )

    def test_renderer_uses_canonical_metric_not_conflicting_claim_number(self):
        from stockcrewai.crews.report.crew import (
            build_report_context,
            parse_report_draft,
            render_validated_report,
        )

        inputs = self._canonical_context_inputs()
        context = build_report_context(**inputs)
        report = render_validated_report(
            context,
            parse_report_draft(VALID_REPORT_DRAFT),
        )

        self.assertIn("25.00x", report)
        self.assertNotIn("999", report)
        self.assertIn("market:test", report)

    def test_renderer_skips_numeric_calculated_claims_but_preserves_risk_and_text(self):
        from stockcrewai.crews.report.crew import (
            build_report_context,
            parse_report_draft,
            render_validated_report,
        )

        inputs = self._canonical_context_inputs()
        inputs["validated_claims"].extend(
            [
                {
                    "claim_id": "claim_numeric_financial_quality",
                    "category": "financial_quality",
                    "statement": "营业利润率为 25.00%。",
                    "evidence_ids": ["ev_revenue"],
                    "calculation_ids": ["calc_operating_margin"],
                    "confidence": 0.9,
                },
                {
                    "claim_id": "claim_numeric_risk",
                    "category": "risk",
                    "statement": "截至 2025 年，风险事件涉及 3 票。",
                    "evidence_ids": ["ev_filing"],
                    "calculation_ids": [],
                    "confidence": 0.8,
                },
                {
                    "claim_id": "claim_text_only",
                    "category": "financial_quality",
                    "statement": "公司治理稳定。",
                    "evidence_ids": ["ev_revenue"],
                    "calculation_ids": ["calc_operating_margin"],
                    "confidence": 0.8,
                },
            ]
        )
        report = render_validated_report(
            build_report_context(**inputs),
            parse_report_draft(VALID_REPORT_DRAFT),
        )

        self.assertNotIn("相应指标", report)
        self.assertNotIn("营业利润率为 25.00%", report)
        self.assertIn("营业利润率：25.00%", report)
        self.assertIn("25.00x", report)
        self.assertNotIn("999", report)
        self.assertIn("截至 2025 年，风险事件涉及 3 票。", report)
        self.assertIn("公司治理稳定。", report)

    def test_renderer_rejects_context_metric_missing_required_provenance(self):
        from stockcrewai.crews.report.crew import parse_report_draft, render_validated_report

        context = {
            "company": {"name": "Apple Inc.", "ticker": "AAPL"},
            "claims": [],
            "verdict_status": "ready",
            "metrics": [
                {
                    "metric_id": "pe_ratio",
                    "display_value": "25.00x",
                    "unit": "multiple",
                    "as_of": "2026-08-06",
                    "source_reference": "",
                    "evidence_ids": ["ev_market_price"],
                    "calculation_id": "calc_pe_ratio",
                }
            ],
            "source_metadata": {},
        }
        with self.assertRaises(ValueError):
            render_validated_report(context, parse_report_draft(VALID_REPORT_DRAFT))

    def test_report_draft_rejects_missing_extra_numeric_and_forbidden_fields(self):
        from stockcrewai.crews.report.crew import validate_report_draft

        cases = {}
        missing = json.loads(VALID_REPORT_DRAFT)
        missing.pop("financial_trend")
        cases["missing field"] = json.dumps(missing, ensure_ascii=False)
        extra = json.loads(VALID_REPORT_DRAFT)
        extra["status"] = "ready"
        cases["extra field"] = json.dumps(extra, ensure_ascii=False)
        cases["small decimal"] = VALID_REPORT_DRAFT.replace(
            "公司质量叙述来自已验证 Claim。", "增长率为 0.00002。"
        )
        cases["free number"] = VALID_REPORT_DRAFT.replace(
            "公司质量叙述来自已验证 Claim。", "自由数字 42。"
        )
        cases["code fence"] = VALID_REPORT_DRAFT.replace(
            "公司质量叙述来自已验证 Claim。", "```文字```"
        )
        cases["advice"] = VALID_REPORT_DRAFT.replace(
            "公司质量叙述来自已验证 Claim。", "建议买入。"
        )
        cases["claim id"] = VALID_REPORT_DRAFT.replace(
            "公司质量叙述来自已验证 Claim。", "claim_forged 不得出现。"
        )

        for reason, raw in cases.items():
            with self.subTest(reason=reason):
                passed, _ = validate_report_draft(SimpleNamespace(raw=raw))
                self.assertFalse(passed)

    def test_report_draft_rejects_llm_investment_conclusions(self):
        from stockcrewai.crews.report.crew import validate_report_draft

        for conclusion in (
            "公司具备较强投资价值。",
            "该公司值得投资。",
            "当前估值偏贵。",
            "当前估值便宜。",
            "市场可能高估该公司。",
            "市场可能低估该公司。",
            "该公司具有安全边际。",
            "公司前景乐观。",
            "公司前景悲观。",
        ):
            with self.subTest(conclusion=conclusion):
                raw = VALID_REPORT_DRAFT.replace(
                    "公司质量叙述来自已验证 Claim。", conclusion
                )
                passed, _ = validate_report_draft(SimpleNamespace(raw=raw))
                self.assertFalse(passed)

    def test_report_draft_validates_disclaimer_separately_from_other_fields(self):
        from stockcrewai.crews.report.crew import REPORT_DRAFT_FIELDS, parse_report_draft

        disclaimer = "本报告不构成买入、卖出、持有或其他投资建议。"
        payload = json.loads(VALID_REPORT_DRAFT)
        payload["non_investment_disclaimer"] = disclaimer
        parsed = parse_report_draft(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(parsed.non_investment_disclaimer, disclaimer)

        for field in REPORT_DRAFT_FIELDS[:-1]:
            candidate = dict(payload)
            candidate[field] = disclaimer
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    parse_report_draft(json.dumps(candidate, ensure_ascii=False))

        ordinary_description = dict(payload)
        ordinary_description["non_investment_disclaimer"] = "本文介绍公司业务。"
        with self.assertRaises(ValueError):
            parse_report_draft(json.dumps(ordinary_description, ensure_ascii=False))

        for forbidden in (
            "免责声明包含 42。",
            "```免责声明```",
            "免责声明包含评级。",
            "免责声明包含 claim_forged。",
            "免责声明包含 status=ready。",
        ):
            candidate = dict(payload)
            candidate["non_investment_disclaimer"] = forbidden
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(ValueError):
                    parse_report_draft(json.dumps(candidate, ensure_ascii=False))

    def test_renderer_allows_advice_terms_only_in_disclaimer_section(self):
        from stockcrewai.crews.report.crew import (
            parse_report_draft,
            render_validated_report,
            validate_rendered_report,
        )

        disclaimer = "本报告不构成买入、卖出、持有或其他投资建议。"
        payload = json.loads(VALID_REPORT_DRAFT)
        payload["non_investment_disclaimer"] = disclaimer
        report = render_validated_report(
            [],
            {"status": "ready"},
            {},
            {},
            {},
            {},
            parse_report_draft(json.dumps(payload, ensure_ascii=False)),
        )

        self.assertIn("## 非投资建议声明", report)
        self.assertIn(disclaimer, report)
        contaminated = report.replace(
            "公司质量叙述来自已验证 Claim。", disclaimer, 1
        )
        passed, _ = validate_rendered_report(contaminated, "ready")
        self.assertFalse(passed)

    def test_report_draft_accepts_only_one_valid_json_object(self):
        from stockcrewai.crews.report.crew import ReportDraft, validate_report_draft

        output = SimpleNamespace(raw=VALID_REPORT_DRAFT)
        passed, message = validate_report_draft(output)

        self.assertTrue(passed)
        self.assertEqual(message, VALID_REPORT_DRAFT)
        self.assertEqual(ReportDraft.model_config["extra"], "forbid")

        duplicate = VALID_REPORT_DRAFT + VALID_REPORT_DRAFT
        passed, _ = validate_report_draft(SimpleNamespace(raw=duplicate))
        self.assertFalse(passed)

    def test_report_draft_guardrail_accepts_structured_pydantic_output(self):
        from stockcrewai.crews.report.crew import (
            ReportDraft,
            parse_report_draft,
            validate_report_draft,
        )

        draft = ReportDraft.model_validate(json.loads(VALID_REPORT_DRAFT))
        output = SimpleNamespace(
            pydantic=draft,
            raw="this is not JSON; the structured output is authoritative",
        )

        self.assertIs(parse_report_draft(output), draft)
        passed, validated = validate_report_draft(output)
        self.assertTrue(passed)
        self.assertIs(validated, draft)

    def test_report_draft_parser_rejects_non_object_and_duplicate_keys(self):
        from stockcrewai.crews.report.crew import parse_report_draft

        with self.assertRaises(ValueError):
            parse_report_draft("[]")
        with self.assertRaises(ValueError):
            parse_report_draft('{"execution_summary":"a","execution_summary":"b"}')

    def test_deterministic_report_draft_has_fixed_safe_contract(self):
        from stockcrewai.crews.report.crew import (
            REPORT_DRAFT_FIELDS,
            build_deterministic_report_draft,
            parse_report_draft,
        )

        draft = build_deterministic_report_draft()
        self.assertEqual(set(draft.model_dump()), set(REPORT_DRAFT_FIELDS))
        self.assertEqual(parse_report_draft(draft), draft)
        for field in REPORT_DRAFT_FIELDS[:-1]:
            value = getattr(draft, field)
            self.assertNotRegex(value, r"[0-9]")
            self.assertNotRegex(value, r"claim_[A-Za-z0-9_-]+")
            self.assertNotIn("买入", value)
            self.assertNotIn("卖出", value)
            self.assertNotIn("评级", value)
        self.assertIn("不构成", draft.non_investment_disclaimer)
        self.assertIn("投资建议", draft.non_investment_disclaimer)

    def test_report_task_has_local_guardrail_and_retries(self):
        from stockcrewai.crews.report.crew import (
            ReportCrew,
            validate_report_draft,
        )

        task = ReportCrew().generate_validated_report_task()
        self.assertIs(task.guardrail, validate_report_draft)
        self.assertEqual(task.guardrail_max_retries, 2)
        self.assertIsNone(task.output_pydantic)
        self.assertIsNone(task.output_json)

    def test_report_prompt_forbids_new_numbers_claims_and_advice(self):
        from stockcrewai.crews.report.crew import ReportCrew

        crew = ReportCrew()
        prompt = crew.tasks_config["generate_validated_report_task"]["description"]
        for phrase in (
            "narrative_context",
            "唯一 JSON",
            "不得输出数字",
            "不得输出 Claim ID",
            "不得输出评级",
            "买入",
            "卖出",
            "持有",
            "Python Renderer",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, prompt)
        for phrase in (
            "validated_claims：",
            "deterministic_verdict：",
            "valuation：",
            "historical_valuation：",
            "reverse_dcf：",
            "source_metadata：",
        ):
            with self.subTest(removed_phrase=phrase):
                self.assertNotIn(phrase, prompt)

    def test_report_prompt_contains_valid_nine_field_json_example(self):
        from stockcrewai.crews.report.crew import ReportCrew, parse_report_draft

        prompt = ReportCrew().tasks_config["generate_validated_report_task"][
            "description"
        ]
        marker = "JSON 格式示例"
        self.assertIn(marker, prompt)
        example_start = prompt.index("{", prompt.index(marker))
        example_end = prompt.index("}", example_start) + 1
        example = json.loads(prompt[example_start:example_end])
        expected_fields = (
            "execution_summary",
            "company_quality",
            "financial_trend",
            "current_valuation",
            "historical_valuation",
            "reverse_dcf",
            "key_risks",
            "sources_and_method",
            "non_investment_disclaimer",
        )

        self.assertEqual(tuple(example), expected_fields)
        self.assertEqual(len(example), 9)
        for field in expected_fields[:-1]:
            value = example[field]
            with self.subTest(field=field):
                self.assertIsInstance(value, str)
                self.assertTrue(value.strip())
                self.assertNotRegex(value, r"[0-9]")
                for forbidden in (
                    "评级",
                    "买入",
                    "卖出",
                    "持有",
                    "增持",
                    "减持",
                    "推荐",
                    "投资建议",
                    "投资推荐",
                    "买卖建议",
                    "status",
                    "确定性状态",
                ):
                    self.assertNotIn(forbidden, value)

        disclaimer = example["non_investment_disclaimer"]
        self.assertNotRegex(disclaimer, r"[0-9]")
        self.assertRegex(disclaimer, r"不构成|不提供|不代表")
        self.assertRegex(disclaimer, r"投资建议|投资推荐|买卖建议")
        parse_report_draft(json.dumps(example, ensure_ascii=False))

    def test_renderer_only_accepts_validated_claims_and_injects_verified_values(self):
        from stockcrewai.crews.report.crew import parse_report_draft, render_validated_report

        claims = [
            {
                "claim_id": "claim_financial_quality",
                "category": "financial_quality",
                "statement": "财务质量稳定。",
                "evidence_ids": ["ev_revenue"],
                "calculation_ids": ["calc_margin"],
                "confidence": 0.9,
            }
        ]
        draft = parse_report_draft(VALID_REPORT_DRAFT)
        report = render_validated_report(
            claims,
            {"status": "insufficient_data", "overall_rating": "不得渲染"},
            {"market_price": "100", "source_reference": "market:test"},
            {"current_percentile": "12", "source_reference": "history:test"},
            {"implied_growth": "0.25", "source_reference": "dcf:test"},
            {"facts": {"revenue": {"source_reference": "sec:test"}}},
            draft,
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
            self.assertIn(f"## {heading}", report)
        self.assertIn("确定性状态：status=insufficient_data", report)
        self.assertIn("财务质量稳定。", report)
        self.assertIn('"market_price":"100"', report)
        self.assertIn("sec:test", report)
        self.assertIn("本文不构成任何投资建议。", report)
        self.assertNotIn("不得渲染", report)
        self.assertLess(report.index("## 执行摘要"), report.index("## 公司质量"))
        self.assertLess(report.index("## 公司质量"), report.index("## 财务趋势"))

        with self.assertRaises(ValueError):
            render_validated_report(
                "raw agent markdown",
                {"status": "ready"},
                {},
                {},
                {},
                {},
                draft,
            )

        rejected_claim = {**claims[0], "rejected": True}
        with self.assertRaises(ValueError):
            render_validated_report(
                [rejected_claim],
                {"status": "ready"},
                {},
                {},
                {},
                {},
                draft,
            )

class CrewConfigurationTests(unittest.TestCase):
    def _deepseek_environment(self):
        return patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "test-deepseek-key",
                "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
            },
        )

    def _assert_deepseek_agent(self, agent, planning_enabled=False):
        self.assertEqual(agent.llm.provider, "deepseek")
        self.assertEqual(agent.llm.model, "deepseek-v4-flash")
        self.assertEqual(agent.llm.api_key, "test-deepseek-key")
        if planning_enabled:
            self.assertEqual(agent.planning_config.reasoning_effort, "medium")
            self.assertEqual(agent.planning_config.max_attempts, 1)
        else:
            self.assertIsNone(agent.planning_config)

    def _build_crew(self, crew_factory):
        with TemporaryDirectory(prefix="stockcrewai-crewai-", dir="/private/tmp") as storage_dir:
            with self._deepseek_environment(), patch(
                "crewai.memory.storage.kickoff_task_outputs_storage.db_storage_path",
                return_value=storage_dir,
            ):
                return crew_factory().crew()

    def test_request_parser_crew_has_one_deepseek_agent_and_bound_task(self):
        from stockcrewai.crews.request_parser.crew import RequestParserCrew

        configured_crew = self._build_crew(RequestParserCrew)

        self.assertEqual(len(configured_crew.agents), 1)
        self.assertEqual(len(configured_crew.tasks), 1)
        self._assert_deepseek_agent(configured_crew.agents[0])
        self.assertIs(configured_crew.tasks[0].agent, configured_crew.agents[0])

    def test_analysis_crew_has_two_deepseek_agents_and_bound_tasks(self):
        from stockcrewai.crews.analysis.crew import AnalysisCrew

        configured_crew = self._build_crew(AnalysisCrew)

        self.assertEqual(len(configured_crew.agents), 2)
        self.assertEqual(len(configured_crew.tasks), 2)
        for agent, task in zip(configured_crew.agents, configured_crew.tasks):
            self._assert_deepseek_agent(agent)
            self.assertIs(task.agent, agent)

        for agent in configured_crew.agents:
            self.assertEqual(agent.tools, [])

    def test_report_crew_keeps_crewai_agent_variable_mapping(self):
        from stockcrewai.crews.report.crew import ReportCrew

        report_crew = ReportCrew()

        self.assertNotIn("map_all_agent_variables", report_crew.__dict__)
        self.assertIs(
            getattr(report_crew.map_all_agent_variables, "__func__", None),
            ReportCrew.map_all_agent_variables,
        )
        report_crew.map_all_agent_variables()

    def test_report_crew_has_one_deepseek_agent_and_bound_task(self):
        from stockcrewai.crews.report.crew import ReportCrew

        configured_crew = self._build_crew(ReportCrew)

        self.assertEqual(len(configured_crew.agents), 1)
        self.assertEqual(len(configured_crew.tasks), 1)
        self._assert_deepseek_agent(configured_crew.agents[0])
        self.assertEqual(
            f"{configured_crew.agents[0].llm.provider}/"
            f"{configured_crew.agents[0].llm.model}",
            "deepseek/deepseek-v4-flash",
        )
        self.assertEqual(
            configured_crew.agents[0].llm.response_format,
            {"type": "json_object"},
        )
        self.assertIs(configured_crew.tasks[0].agent, configured_crew.agents[0])
        self.assertEqual(configured_crew.agents[0].tools, [])


if __name__ == "__main__":
    unittest.main()
