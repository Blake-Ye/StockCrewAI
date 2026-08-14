import base64
from datetime import date, timedelta
import importlib
from io import BytesIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image


def _financial_metrics():
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
            **({"adjustment_basis": "raw"} if metric_id == "share_dilution" else {}),
        }
        for metric_id, value in values.items()
    ]


def _ttm_metrics():
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


def _annual_financial_history():
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


def _historical_payload():
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


_VISUAL_KEYS = {"financial_kpis", "annual_financial_trend", "historical_pe"}
_PNG_PREFIX = "data:image/png;base64,"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ReportVisualsTests(unittest.TestCase):
    def _builder(self):
        module = importlib.import_module("stockcrewai.reporting.visuals")
        builder = getattr(module, "build_report_visuals", None)
        self.assertIsNotNone(builder, "必须提供 reporting.visuals.build_report_visuals")
        return builder

    def test_financial_kpis_use_independent_panels_and_keep_labels_inside(self):
        module = importlib.import_module("stockcrewai.reporting.visuals")
        values = {
            "revenue_growth": 975.00,
            "operating_margin": 33.60,
            "net_margin": 27.85,
            "free_cash_flow_margin": 30.24,
            "cash_conversion": 115.31,
            "share_dilution": -1.67,
        }
        records = {
            metric_id: {
                "display_value": f"{value:.2f}%",
                "unit": "percentage",
                "status": "available",
                "validation_status": "valid",
                "calculation_id": f"calc_{metric_id}",
                "evidence_ids": [f"ev_{metric_id}"],
                "period_basis": "FY",
                "period_end": "2025-12-31",
                "as_of": "2025-12-31",
                **(
                    {"adjustment_basis": "split_adjusted"}
                    if metric_id == "share_dilution"
                    else {}
                ),
            }
            for metric_id, value in values.items()
        }
        rendered = {}

        def inspect_png_uri(draw, *, size, **kwargs):
            figure, axes = module.plt.subplots(figsize=size, dpi=120)
            try:
                draw(axes)
                figure.canvas.draw()
                rendered["axes"] = list(figure.axes)
                rendered["renderer"] = figure.canvas.get_renderer()
                return "captured"
            finally:
                module.plt.close(figure)

        with patch.object(module, "_png_uri", side_effect=inspect_png_uri):
            self.assertEqual(module._financial_kpi_png(records), "captured")

        axes = rendered["axes"]
        self.assertEqual(len(axes), 3)
        self.assertEqual(
            [axis.get_title() for axis in axes],
            ["增长与资本配置", "盈利能力", "现金流质量"],
        )
        self.assertNotEqual(axes[0].get_xlim(), axes[1].get_xlim())
        self.assertNotEqual(axes[1].get_xlim(), axes[2].get_xlim())
        self.assertGreater(axes[0].get_xlim()[1], 975.0)
        self.assertGreater(axes[2].get_xlim()[1], 115.31)
        self.assertTrue(
            any(
                line.get_xdata()[0] == 100
                for line in axes[2].lines
                if len(line.get_xdata())
            )
        )

        renderer = rendered["renderer"]
        for axis in axes:
            axes_bbox = axis.bbox
            for text, bar in zip(axis.texts, axis.patches):
                text_extent = text.get_window_extent(renderer)
                bar_extent = bar.get_window_extent(renderer)
                with self.subTest(label=text.get_text()):
                    self.assertGreaterEqual(text_extent.x0, axes_bbox.x0)
                    self.assertLessEqual(text_extent.x1, axes_bbox.x1)
                    if bar.get_width() >= 0:
                        self.assertGreater(text_extent.x0, bar_extent.x1)
                    else:
                        self.assertLess(text_extent.x1, bar_extent.x0)

        growth_labels = [text.get_text() for text in axes[0].get_yticklabels()]
        self.assertIn("股份变化（拆分调整）", growth_labels)

    def test_financial_kpis_reject_invalid_share_adjustment_basis(self):
        module = importlib.import_module("stockcrewai.reporting.visuals")
        values = {
            "revenue_growth": 20.0,
            "operating_margin": 25.0,
            "net_margin": 20.0,
            "free_cash_flow_margin": 15.0,
            "cash_conversion": 150.0,
            "share_dilution": -2.0,
        }
        records = {
            metric_id: {
                "display_value": f"{value:.2f}%",
            "unit": "percentage",
            "status": "available",
            "validation_status": "valid",
            "calculation_id": f"calc_{metric_id}",
            "evidence_ids": [f"ev_{metric_id}"],
            "period_basis": "FY",
            "period_end": "2025-12-31",
            "as_of": "2025-12-31",
            **(
                    {"adjustment_basis": "total_return_adjusted"}
                    if metric_id == "share_dilution"
                    else {}
                ),
            }
            for metric_id, value in values.items()
        }
        self.assertIsNone(module._financial_kpi_png(records))

    def test_financial_kpis_omit_mixed_period_basis(self):
        builder = self._builder()
        financial_metrics = _financial_metrics()
        for record in financial_metrics:
            record.update(
                section="financial",
                period_basis="YTD",
                period_end="2025-12-31",
                as_of="2025-12-31",
            )
        next(
            record
            for record in financial_metrics
            if record["metric_id"] == "cash_conversion"
        )["period_basis"] = "TTM"

        visuals = builder(financial_metrics=financial_metrics)

        self.assertNotIn("financial_kpis", visuals)

    def test_financial_kpis_generate_when_semantic_bases_share_as_of(self):
        builder = self._builder()
        financial_metrics = _financial_metrics()
        for record in financial_metrics:
            record.update(
                section="financial",
                period_end="2026-06-27",
                as_of="2026-06-27",
            )
            record["period_basis"] = {
                "revenue_growth": "YTD同比",
                "share_dilution": "同比时点",
            }.get(record["metric_id"], "YTD")

        visuals = builder(financial_metrics=financial_metrics)

        self.assertIn("financial_kpis", visuals)

    def test_financial_kpis_omit_different_as_of(self):
        builder = self._builder()
        financial_metrics = _financial_metrics()
        for record in financial_metrics:
            record.update(
                section="financial",
                period_basis="YTD",
                period_end="2026-06-27",
                as_of="2026-06-27",
            )
        next(
            record
            for record in financial_metrics
            if record["metric_id"] == "cash_conversion"
        )["as_of"] = "2026-06-26"

        visuals = builder(financial_metrics=financial_metrics)

        self.assertNotIn("financial_kpis", visuals)

    def test_normalized_financial_kpis_require_explicit_period_basis(self):
        builder = self._builder()
        financial_metrics = _financial_metrics()
        for record in financial_metrics:
            record["section"] = "financial"
            record.pop("period_basis")

        visuals = builder(financial_metrics=financial_metrics)

        self.assertNotIn("financial_kpis", visuals)

    def test_financial_kpis_omit_missing_period_basis_or_period_metadata(self):
        builder = self._builder()
        financial_metrics = _financial_metrics()
        for record in financial_metrics:
            record.update(
                period_basis="FY",
                period_end="2025-12-31",
                as_of="2025-12-31",
            )
        for missing_fields in (("period_basis",), ("period_end", "as_of")):
            with self.subTest(missing_fields=missing_fields):
                records = [record.copy() for record in financial_metrics]
                target = next(
                    record
                    for record in records
                    if record["metric_id"] == "cash_conversion"
                )
                for field in missing_fields:
                    target.pop(field)

                visuals = builder(financial_metrics=records)

                self.assertNotIn("financial_kpis", visuals)

    def test_historical_pe_uses_validated_upstream_summary_values(self):
        module = importlib.import_module("stockcrewai.reporting.visuals")
        payload = _historical_payload()
        payload.update(
            {
                "current_value": payload["series"][-1]["pe_ratio"],
                "percentile_25": "101.01",
                "five_year_median": "202.02",
                "percentile_75": "303.03",
            }
        )
        rendered = {}

        def inspect_png_uri(draw, *, size, **kwargs):
            figure, axes = module.plt.subplots(figsize=size, dpi=120)
            try:
                draw(axes)
                figure.canvas.draw()
                rendered["axes"] = list(figure.axes)
                return "captured"
            finally:
                module.plt.close(figure)

        with patch.object(module, "_png_uri", side_effect=inspect_png_uri):
            self.assertEqual(module._historical_pe_png(payload), "captured")

        reference_lines = [
            line
            for line in rendered["axes"][0].lines
            if line.get_linestyle() == "--"
        ]
        self.assertEqual(
            [line.get_ydata()[0] for line in reference_lines],
            [101.01, 202.02, 303.03],
        )

    def test_historical_pe_accepts_matching_high_precision_current_value(self):
        """防止 Decimal 转 float 后的精度损失误删真实历史估值图。"""
        module = importlib.import_module("stockcrewai.reporting.visuals")
        payload = _historical_payload()
        precise_value = "34.83715582331386467889908257"
        payload["series"][-1]["pe_ratio"] = precise_value
        payload["current_value"] = precise_value

        with patch.object(module, "_png_uri", return_value="captured"):
            self.assertEqual(module._historical_pe_png(payload), "captured")

    def test_historical_pe_data_uri_stays_below_markdown_preview_limit(self):
        """第三张图必须低于常见的 64 KiB Data URI 预览限制。"""
        module = importlib.import_module("stockcrewai.reporting.visuals")

        uri = module._historical_pe_png(_historical_payload())

        self.assertIsNotNone(uri)
        self.assertLess(len(uri), 64 * 1024)

    def test_historical_pe_keeps_realtime_point_separate_from_complete_series(self):
        module = importlib.import_module("stockcrewai.reporting.visuals")
        payload = _historical_payload()
        payload["current_date"] = "2026-08-12"
        payload["current_value"] = "999.99"
        rendered = {}

        def inspect_png_uri(draw, *, size, **kwargs):
            figure, axes = module.plt.subplots(figsize=size, dpi=120)
            try:
                draw(axes)
                figure.canvas.draw()
                rendered["axes"] = list(figure.axes)
                return "captured"
            finally:
                module.plt.close(figure)

        with patch.object(module, "_png_uri", side_effect=inspect_png_uri):
            self.assertEqual(module._historical_pe_png(payload), "captured")

        axes = rendered["axes"][0]
        line = next(line for line in axes.lines if line.get_linestyle() == "-")
        self.assertEqual(len(line.get_xdata()), 60)
        self.assertEqual(len(axes.collections), 1)
        self.assertEqual(list(axes.collections[0].get_offsets()[0]), [60.0, 999.99])

    def test_builds_three_deterministic_png_data_uris_from_verified_inputs(self):
        builder = self._builder()

        visuals = builder(
            financial_metrics=_financial_metrics(),
            ttm_metrics=_ttm_metrics(),
            annual_financial_history=_annual_financial_history(),
            historical_payload=_historical_payload(),
        )

        self.assertEqual(
            set(visuals), _VISUAL_KEYS
        )
        for uri in visuals.values():
            self.assertTrue(uri.startswith("data:image/png;base64,"))
            self.assertTrue(
                base64.b64decode(uri.split(",", 1)[1]).startswith(b"\x89PNG\r\n\x1a\n")
            )

        self.assertEqual(
            visuals,
            builder(
                financial_metrics=_financial_metrics(),
                ttm_metrics=_ttm_metrics(),
                annual_financial_history=_annual_financial_history(),
                historical_payload=_historical_payload(),
            ),
        )

    def test_annual_trend_normalizes_series_on_one_shared_index_axis(self):
        module = importlib.import_module("stockcrewai.reporting.visuals")
        annual = _annual_financial_history()
        periods = annual["periods"]
        expected_values = {
            "revenue": [100.0, 120.0, 180.0, 160.0, 250.0],
            "net_income": [100.0, 80.0, 120.0, 150.0, 200.0],
            "free_cash_flow": [100.0, 150.0, 120.0, 160.0, 200.0],
        }
        for period, values in zip(
            periods,
            zip(
                expected_values["revenue"],
                expected_values["net_income"],
                expected_values["free_cash_flow"],
            ),
        ):
            period["revenue"] = str(int(values[0] * 1_000_000_000))
            period["net_income"] = str(int(values[1] * 10_000_000))
            period["free_cash_flow"] = str(int(values[2] * 5_000_000))
        rendered = {}

        def inspect_png_uri(draw, *, size, **kwargs):
            figure, axes = module.plt.subplots(figsize=size, dpi=120)
            try:
                draw(axes)
                figure.canvas.draw()
                rendered["axes"] = list(figure.axes)
                return "captured"
            finally:
                module.plt.close(figure)

        with patch.object(module, "_png_uri", side_effect=inspect_png_uri):
            self.assertEqual(
                module._annual_financial_trend_png(annual),
                "captured",
            )

        axes = rendered["axes"]
        self.assertEqual(len(axes), 1)
        axis = axes[0]
        self.assertEqual(axis.get_ylabel(), "指数（首个财年=100）")
        data_lines = [line for line in axis.lines if len(line.get_ydata()) == 5]
        self.assertEqual(len(data_lines), 3)
        self.assertEqual(
            [list(line.get_ydata()) for line in data_lines],
            [
                expected_values["revenue"],
                expected_values["net_income"],
                expected_values["free_cash_flow"],
            ],
        )
        self.assertEqual(
            [text.get_text() for text in axis.get_legend().get_texts()],
            ["营业收入", "净利润", "自由现金流"],
        )

    def test_annual_trend_omits_chart_when_first_year_value_is_nonpositive_or_invalid(
        self,
    ):
        builder = self._builder()

        for first_value in ("0", "not-a-number"):
            with self.subTest(first_value=first_value):
                annual = _annual_financial_history()
                annual["periods"][0]["net_income"] = first_value

                visuals = builder(annual_financial_history=annual)

                self.assertNotIn("annual_financial_trend", visuals)

    def _assert_png_uris(self, visuals):
        for key, uri in visuals.items():
            with self.subTest(key=key):
                self.assertTrue(uri.startswith(_PNG_PREFIX))
                encoded = uri.split(",", 1)[1]
                payload = base64.b64decode(encoded, validate=True)
                self.assertTrue(payload.startswith(_PNG_SIGNATURE))
                with Image.open(BytesIO(payload)) as image:
                    image.load()
                    self.assertGreaterEqual(image.width, 720)
                    self.assertGreaterEqual(image.height, 360)
                    ratio = image.width / image.height
                    self.assertGreaterEqual(ratio, 1.2)
                    self.assertLessEqual(ratio, 3.5)

    def test_missing_input_omits_only_the_corresponding_chart(self):
        builder = self._builder()

        visuals = builder(
            financial_metrics=_financial_metrics(),
            ttm_metrics=_ttm_metrics(),
        )

        self.assertEqual(set(visuals), {"financial_kpis"})

    def test_missing_verification_fields_omit_all_charts(self):
        builder = self._builder()
        financial_metrics = _financial_metrics()
        ttm_metrics = _ttm_metrics()
        historical_payload = _historical_payload()
        annual_financial_history = _annual_financial_history()

        for records in (financial_metrics, ttm_metrics):
            for record in records:
                record.pop("status")
                record.pop("validation_status")
        historical_payload.pop("status")
        historical_payload.pop("validation_status")
        annual_financial_history.pop("status")
        annual_financial_history.pop("validation_status")

        self.assertEqual(
            builder(
                financial_metrics=financial_metrics,
                ttm_metrics=ttm_metrics,
                annual_financial_history=annual_financial_history,
                historical_payload=historical_payload,
            ),
            {},
        )

    def test_historical_pe_requires_top_level_verification_fields(self):
        builder = self._builder()

        for field in ("status", "validation_status"):
            with self.subTest(field=field):
                historical_payload = _historical_payload()
                historical_payload.pop(field)

                visuals = builder(
                    financial_metrics=_financial_metrics(),
                    ttm_metrics=_ttm_metrics(),
                    annual_financial_history=_annual_financial_history(),
                    historical_payload=historical_payload,
                )

                self.assertEqual(
                    set(visuals), {"financial_kpis", "annual_financial_trend"}
                )

    def test_matplotlib_uses_agg_and_writable_project_temp_config(self):
        self._builder()
        import matplotlib

        self.assertEqual(matplotlib.get_backend().lower(), "agg")
        config_dir = Path(os.environ["MPLCONFIGDIR"])
        self.assertEqual(config_dir.parent, Path(tempfile.gettempdir()))
        self.assertTrue(config_dir.name.startswith("stockcrewai"))
        self.assertTrue(os.access(config_dir, os.W_OK))

    def test_amount_conversion_uses_actual_usd_billions(self):
        module = importlib.import_module("stockcrewai.reporting.visuals")
        converter = getattr(module, "_amount_in_billion_usd")

        self.assertAlmostEqual(
            converter({"raw_result": "466823000000", "unit": "USD"}),
            466.823,
            places=3,
        )
        self.assertAlmostEqual(
            converter({"raw_result": "466823", "unit": "million USD"}),
            466.823,
            places=3,
        )
        self.assertAlmostEqual(
            converter({"raw_result": "4668.23", "unit": "亿美元"}),
            466.823,
            places=3,
        )

    def test_annual_trend_rejects_non_usd_currency(self):
        builder = self._builder()
        annual = _annual_financial_history()
        annual["currency"] = "EUR"
        for period in annual["periods"]:
            period["currency"] = "EUR"

        visuals = builder(
            financial_metrics=_financial_metrics(),
            annual_financial_history=annual,
            historical_payload=_historical_payload(),
        )

        self.assertNotIn("annual_financial_trend", visuals)

    def test_chart_labels_and_historical_ticks_are_reader_friendly(self):
        module = importlib.import_module("stockcrewai.reporting.visuals")

        self.assertEqual(module._FINANCIAL_KPI_TITLE, "财务质量指标（已验证数据）")
        self.assertEqual(
            module._ANNUAL_TREND_TITLE,
            "近五年核心财务趋势（已验证完整财年）",
        )

        points = [
            (
                date(2021 + index // 12, index % 12 + 1, 1).isoformat(),
                float(index),
            )
            for index in range(60)
        ]
        indices, labels = module._historical_tick_data(points)
        self.assertEqual(indices, [0, 12, 24, 36, 48, 59])
        self.assertEqual(
            labels,
            ["2021-01", "2022-01", "2023-01", "2024-01", "2025-01", "2025-12"],
        )
