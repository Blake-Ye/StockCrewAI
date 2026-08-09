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
            "evidence_ids": [f"ev_{metric_id}"],
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
    }


class ReportVisualsTests(unittest.TestCase):
    def _builder(self):
        try:
            module = importlib.import_module("stockcrewai.report_visuals")
        except ImportError:
            module = None
        self.assertIsNotNone(module, "report_visuals 模块必须存在")
        builder = getattr(module, "build_report_visuals", None)
        self.assertIsNotNone(builder, "必须提供 build_report_visuals")
        return builder

    def test_financial_kpi_percentage_labels_render_outside_bars(self):
        module = importlib.import_module("stockcrewai.report_visuals")
        values = {
            "revenue_growth": 16.15,
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
            }
            for metric_id, value in values.items()
        }
        rendered = {}

        def inspect_png_uri(draw, *, size):
            figure, axes = module.plt.subplots(figsize=size, dpi=120)
            try:
                draw(axes)
                figure.canvas.draw()
                renderer = figure.canvas.get_renderer()
                percentage_texts = [
                    text for text in axes.texts if text.get_text().endswith("%")
                ]
                rendered["labels"] = [
                    text.get_text() for text in percentage_texts
                ]
                rendered["texts"] = percentage_texts
                rendered["extents"] = [
                    text.get_window_extent(renderer) for text in percentage_texts
                ]
                rendered["bars"] = [
                    bar.get_window_extent(renderer) for bar in axes.patches
                ]
                rendered["axes_bbox"] = axes.bbox
                rendered["zero_x"] = axes.transData.transform((0, 0))[0]
                return "captured"
            finally:
                module.plt.close(figure)

        with patch.object(module, "_png_uri", side_effect=inspect_png_uri):
            self.assertEqual(module._financial_kpi_png(records), "captured")

        self.assertEqual(len(rendered["labels"]), 6)
        self.assertEqual(
            rendered["labels"],
            ["16.15%", "33.60%", "27.85%", "30.24%", "115.31%", "-1.67%"],
        )
        axes_bbox = rendered["axes_bbox"]
        for label, extent in zip(rendered["labels"], rendered["extents"]):
            with self.subTest(label=label):
                self.assertGreaterEqual(extent.x0, axes_bbox.x0)
                self.assertLessEqual(extent.x1, axes_bbox.x1)

        by_label = {
            label: (text, extent, bar)
            for label, text, extent, bar in zip(
                rendered["labels"],
                rendered["texts"],
                rendered["extents"],
                rendered["bars"],
            )
        }

        maximum_text, maximum_extent, maximum_bar = by_label["115.31%"]
        self.assertGreater(maximum_extent.x0, maximum_bar.x1)
        self.assertGreaterEqual(
            axes_bbox.x1 - maximum_extent.x1,
            axes_bbox.width * 0.05,
        )
        self.assertEqual(maximum_text.get_color(), "black")

        negative_text, negative_extent, _ = by_label["-1.67%"]
        self.assertGreater(negative_extent.x0, rendered["zero_x"])
        self.assertNotEqual(negative_text.get_color(), "white")

        for label in ("16.15%", "33.60%", "27.85%", "30.24%"):
            _, extent, bar = by_label[label]
            self.assertGreater(extent.x0, bar.x1)

    def test_financial_kpi_axis_padding_follows_real_data_range(self):
        module = importlib.import_module("stockcrewai.report_visuals")

        def render(values):
            records = {
                metric_id: {
                    "display_value": f"{value:.2f}%",
                    "unit": "percentage",
                }
                for metric_id, value in values.items()
            }
            uri = module._financial_kpi_png(records)
            self.assertIsNotNone(uri)
            image = Image.open(
                BytesIO(base64.b64decode(uri.split(",", 1)[1]))
            ).convert("RGB")

            black = (0, 0, 0)
            gray = (85, 85, 85)
            blue = (53, 104, 168)
            axis_columns = [
                x
                for x in range(image.width)
                if sum(
                    image.getpixel((x, y)) == black
                    for y in range(image.height)
                )
                > image.height // 2
            ]
            self.assertGreaterEqual(len(axis_columns), 2)
            axes_left, axes_right = min(axis_columns), max(axis_columns)
            zero_columns = [
                x
                for x in range(image.width)
                if sum(
                    image.getpixel((x, y)) == gray
                    for y in range(image.height)
                )
                > image.height // 2
            ]
            self.assertEqual(len(zero_columns), 1)

            blue_rows = [
                y
                for y in range(image.height)
                if any(
                    image.getpixel((x, y)) == blue
                    for x in range(image.width)
                )
            ]
            row_groups = []
            group_start = previous_row = blue_rows[0]
            for row in blue_rows[1:]:
                if row != previous_row + 1:
                    row_groups.append((group_start, previous_row))
                    group_start = row
                previous_row = row
            row_groups.append((group_start, previous_row))
            self.assertEqual(len(row_groups), len(module._FINANCIAL_KPI_IDS))

            negative_left = min(
                x
                for y in range(row_groups[-1][0], row_groups[-1][1] + 1)
                for x in range(image.width)
                if image.getpixel((x, y)) == blue
            )
            axis_width = axes_right - axes_left
            return {
                "zero_fraction": (zero_columns[0] - axes_left) / axis_width,
                "negative_left_fraction": (negative_left - axes_left)
                / axis_width,
            }

        negative_values = {
            "revenue_growth": 10.0,
            "operating_margin": 20.0,
            "net_margin": 30.0,
            "free_cash_flow_margin": 40.0,
            "cash_conversion": 50.0,
            "share_dilution": -5.0,
        }
        negative_chart = render(negative_values)
        expected_negative_xlim = (-16.0, 61.0)
        expected_negative_zero_fraction = (
            -expected_negative_xlim[0]
            / (expected_negative_xlim[1] - expected_negative_xlim[0])
        )
        self.assertAlmostEqual(
            negative_chart["zero_fraction"],
            expected_negative_zero_fraction,
            delta=0.01,
        )
        self.assertGreaterEqual(negative_chart["negative_left_fraction"], 0.14)

        positive_values = {**negative_values, "share_dilution": 5.0}
        positive_chart = render(positive_values)
        expected_positive_xlim = (-5.0, 60.0)
        expected_positive_zero_fraction = (
            -expected_positive_xlim[0]
            / (expected_positive_xlim[1] - expected_positive_xlim[0])
        )
        self.assertAlmostEqual(
            positive_chart["zero_fraction"],
            expected_positive_zero_fraction,
            delta=0.01,
        )
        self.assertLess(
            positive_chart["zero_fraction"], negative_chart["zero_fraction"]
        )

    def test_builds_three_deterministic_png_data_uris_from_verified_inputs(self):
        builder = self._builder()

        visuals = builder(
            financial_metrics=_financial_metrics(),
            ttm_metrics=_ttm_metrics(),
            historical_payload=_historical_payload(),
        )

        self.assertEqual(
            set(visuals), {"financial_kpis", "ttm_scale", "historical_pe"}
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
                historical_payload=_historical_payload(),
            ),
        )

    def test_missing_input_omits_only_the_corresponding_chart(self):
        builder = self._builder()

        visuals = builder(
            financial_metrics=_financial_metrics(),
            ttm_metrics=_ttm_metrics(),
        )

        self.assertEqual(set(visuals), {"financial_kpis", "ttm_scale"})

    def test_missing_verification_fields_omit_all_charts(self):
        builder = self._builder()
        financial_metrics = _financial_metrics()
        ttm_metrics = _ttm_metrics()
        historical_payload = _historical_payload()

        for records in (financial_metrics, ttm_metrics):
            for record in records:
                record.pop("status")
                record.pop("validation_status")
        historical_payload.pop("status")
        historical_payload.pop("validation_status")

        self.assertEqual(
            builder(
                financial_metrics=financial_metrics,
                ttm_metrics=ttm_metrics,
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
                    historical_payload=historical_payload,
                )

                self.assertEqual(set(visuals), {"financial_kpis", "ttm_scale"})

    def test_matplotlib_uses_agg_and_writable_project_temp_config(self):
        self._builder()
        import matplotlib

        self.assertEqual(matplotlib.get_backend().lower(), "agg")
        config_dir = Path(os.environ["MPLCONFIGDIR"])
        self.assertEqual(config_dir.parent, Path(tempfile.gettempdir()))
        self.assertTrue(config_dir.name.startswith("stockcrewai"))
        self.assertTrue(os.access(config_dir, os.W_OK))

    def test_amount_conversion_uses_actual_usd_billions(self):
        module = importlib.import_module("stockcrewai.report_visuals")
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

    def test_chart_labels_and_historical_ticks_are_reader_friendly(self):
        module = importlib.import_module("stockcrewai.report_visuals")

        self.assertEqual(module._FINANCIAL_KPI_TITLE, "财务质量指标（已验证数据）")
        self.assertEqual(module._TTM_AXIS_LABEL, "金额（十亿美元）")

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
